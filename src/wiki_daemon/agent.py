# src/wiki_daemon/agent.py
"""Pluggable agentic-CLI providers.

wiki-daemon drives an agentic CLI that edits the vault's files with its own
tools (not a text LLM API). Claude Code, Gemini CLI, and Codex CLI share one
shape — run headless in the vault dir, read a project-instructions file, edit
files, exit — so they sit behind one `Provider`. Everything else (verify, queue,
dedup, review, lint) is provider-agnostic because the daemon checks files, not
who wrote them.

The runner is injectable for testing (no real CLI is spawned in unit tests).
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import icloud

logger = logging.getLogger("wiki_daemon.agent")

# runner(cmd, cwd, timeout) -> (returncode, stdout, stderr)
Runner = Callable[[list[str], Path, int], tuple[int, str, str]]
# interactive runner(cmd, cwd) -> returncode (stdio inherited, no capture)
InteractiveRunner = Callable[[list[str], Path], int]
# materialize(cwd) -> report; readies an iCloud vault before the agent scans it
MaterializeFn = Callable[[Path], Any]

_WRITE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep"]
_READ_TOOLS = ["Read", "Glob", "Grep"]

_AUTH_SIGNS = ("401", "authenticate", "credentials", "invalid authentication",
               "unauthorized", "not logged in")
_QUOTA_SIGNS = ("rate limit", "quota", "exhausted", "429", "resource_exhausted",
                "overloaded", "too many requests", "usage credits", "usage limit",
                "402", "payment required")


@dataclass
class AgentResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str


def classify_failure(result: "AgentResult") -> str:
    """Bucket a failed result: 'auth' | 'quota' | 'unavailable' | 'error'."""
    blob = f"{result.stdout}\n{result.stderr}".lower()
    if any(s in blob for s in _AUTH_SIGNS):
        return "auth"
    if any(s in blob for s in _QUOTA_SIGNS):
        return "quota"
    if result.returncode == 127 or "not found" in blob or "timeout" in blob:
        return "unavailable"
    return "error"


@dataclass(frozen=True)
class Provider:
    name: str
    bin: str
    brain_filename: str   # the file THIS CLI reads: CLAUDE.md / GEMINI.md / AGENTS.md
    auth_hint: str
    # builders take the prompt + whether the op may write files
    _headless: Callable[["Provider", str, bool], list[str]]
    _interactive: Callable[["Provider", str, bool], list[str]]

    def headless_cmd(self, prompt: str, *, write: bool) -> list[str]:
        return self._headless(self, prompt, write)

    def interactive_cmd(self, prompt: str, *, write: bool) -> list[str]:
        return self._interactive(self, prompt, write)

    def classify_failure(self, result: "AgentResult") -> str:
        return classify_failure(result)


def _claude_headless(p: Provider, prompt: str, write: bool) -> list[str]:
    tools = _WRITE_TOOLS if write else _READ_TOOLS
    return [p.bin, "-p", prompt, "--allowed-tools", *tools,
            "--dangerously-skip-permissions"]


def _claude_interactive(p: Provider, prompt: str, write: bool) -> list[str]:
    tools = _WRITE_TOOLS if write else _READ_TOOLS
    return [p.bin, prompt, "--allowed-tools", *tools,
            "--dangerously-skip-permissions"]


def _gemini_headless(p: Provider, prompt: str, write: bool) -> list[str]:
    # --yolo auto-approves all actions (writes). Omit it for read-only ops: the
    # CLI cannot write headlessly without approval, so behavior stays read-only.
    cmd = [p.bin, "-p", prompt]
    if write:
        cmd.append("--yolo")
    return cmd


def _gemini_interactive(p: Provider, prompt: str, write: bool) -> list[str]:
    cmd = [p.bin, prompt]
    if write:
        cmd.append("--yolo")
    return cmd


def _codex_headless(p: Provider, prompt: str, write: bool) -> list[str]:
    # `codex exec` is non-interactive (no approval prompt). --skip-git-repo-check
    # lets it run in a non-git vault; the sandbox mode gates writes.
    sandbox = "workspace-write" if write else "read-only"
    return [p.bin, "exec", "--skip-git-repo-check", "--sandbox", sandbox, prompt]


def _codex_interactive(p: Provider, prompt: str, write: bool) -> list[str]:
    sandbox = "workspace-write" if write else "read-only"
    return [p.bin, "--skip-git-repo-check", "--sandbox", sandbox, prompt]


PROVIDERS: dict[str, Provider] = {
    "claude": Provider("claude", "claude", "CLAUDE.md",
                       "run `claude setup-token`", _claude_headless, _claude_interactive),
    "gemini": Provider("gemini", "gemini", "GEMINI.md",
                       "set GOOGLE_API_KEY or run `gemini` to log in",
                       _gemini_headless, _gemini_interactive),
    "codex": Provider("codex", "codex", "AGENTS.md",
                      "run `codex login` or set OPENAI_API_KEY",
                      _codex_headless, _codex_interactive),
}


def get_provider(cfg) -> Provider:
    """Resolve the configured provider. Raises ValueError on an unknown name."""
    name = getattr(cfg, "provider", "claude") or "claude"
    try:
        return PROVIDERS[name]
    except KeyError:
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider {name!r} (valid: {valid})")


def _subprocess_runner(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    # Close stdin (DEVNULL): headless agent CLIs like `codex exec` otherwise wait
    # for "additional input from stdin" and hang/misbehave.
    proc = subprocess.run(cmd, cwd=str(cwd), timeout=timeout,
                          capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout, proc.stderr


def run_agent(provider: Provider, prompt: str, cwd, *, write: bool,
              timeout: int = 300, runner: Runner = _subprocess_runner,
              materialize_fn: MaterializeFn = icloud.ensure_tree_materialized) -> AgentResult:
    # The agent CLI scans cwd on startup; an iCloud-evicted (dataless) file there
    # EINTRs and aborts the run. Materialize the whole tree first. Bounded and
    # best-effort: if it times out with files still dataless, log and proceed
    # (the run will fail gracefully via the existing timeout handling).
    try:
        report = materialize_fn(Path(cwd))
    except Exception:  # materialize is best-effort; never let it fail the run
        logger.warning("materialize failed before agent run in %s", cwd, exc_info=True)
        report = None
    if report is not None and getattr(report, "timed_out", False) \
            and getattr(report, "still_dataless", 0):
        logger.warning(
            "materialize incomplete before agent run in %s: %s files still dataless",
            cwd, report.still_dataless)
    cmd = provider.headless_cmd(prompt, write=write)
    try:
        code, out, err = runner(cmd, Path(cwd), timeout)
    except subprocess.TimeoutExpired:
        return AgentResult(False, -1, "", "timeout")
    except FileNotFoundError:
        return AgentResult(False, 127, "", f"{provider.bin} binary not found")
    return AgentResult(ok=(code == 0), returncode=code, stdout=out, stderr=err)


def _interactive_subprocess_runner(cmd: list[str], cwd: Path) -> int:
    return subprocess.run(cmd, cwd=str(cwd)).returncode


def run_agent_interactive(provider: Provider, prompt: str, cwd, *, write: bool,
                          runner: InteractiveRunner = _interactive_subprocess_runner) -> int:
    """Launch the agent CLI interactively (stdio inherited) so the model can ask
    and the user can answer live. Returns the process exit code."""
    return runner(provider.interactive_cmd(prompt, write=write), Path(cwd))
