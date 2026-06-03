# src/wiki_daemon/claude.py
"""Headless `claude -p` invocation. The runner is injectable for testing."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# runner(cmd, cwd, timeout) -> (returncode, stdout, stderr)
Runner = Callable[[list[str], Path, int], tuple[int, str, str]]


@dataclass
class ClaudeResult:
    ok: bool
    returncode: int
    stdout: str
    stderr: str


_AUTH_SIGNS = ("401", "authenticate", "credentials", "invalid authentication")


def classify_failure(result: "ClaudeResult") -> str:
    """Bucket a failed ClaudeResult: 'auth' | 'unavailable' | 'claude_error'."""
    blob = f"{result.stdout}\n{result.stderr}".lower()
    if any(s in blob for s in _AUTH_SIGNS):
        return "auth"
    if result.returncode == 127 or "not found" in blob or "timeout" in blob:
        return "unavailable"
    return "claude_error"


def _subprocess_runner(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, cwd=str(cwd), timeout=timeout,
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_claude(
    prompt: str,
    cwd: Path,
    allowed_tools: list[str],
    claude_bin: str = "claude",
    timeout: int = 300,
    skip_permissions: bool = True,
    runner: Runner = _subprocess_runner,
) -> ClaudeResult:
    cmd = [claude_bin, "-p", prompt, "--allowed-tools", ",".join(allowed_tools)]
    if skip_permissions:
        # Headless: the daemon cannot answer interactive permission prompts.
        cmd.append("--dangerously-skip-permissions")
    try:
        code, out, err = runner(cmd, Path(cwd), timeout)
    except subprocess.TimeoutExpired:
        return ClaudeResult(ok=False, returncode=-1, stdout="", stderr="timeout")
    except FileNotFoundError:
        return ClaudeResult(ok=False, returncode=127, stdout="",
                            stderr="claude binary not found")
    return ClaudeResult(ok=(code == 0), returncode=code, stdout=out, stderr=err)


# Interactive runner(cmd, cwd) -> returncode. No capture: stdio is inherited so
# the user can converse with claude in their terminal.
InteractiveRunner = Callable[[list[str], Path], int]


def _interactive_subprocess_runner(cmd: list[str], cwd: Path) -> int:
    return subprocess.run(cmd, cwd=str(cwd)).returncode


def run_claude_interactive(
    prompt: str,
    cwd: Path,
    allowed_tools: list[str],
    claude_bin: str = "claude",
    skip_permissions: bool = True,
    runner: InteractiveRunner = _interactive_subprocess_runner,
) -> int:
    """Launch `claude` WITHOUT -p so the model can ask and the user can answer
    live. Returns the process exit code."""
    cmd = [claude_bin, prompt, "--allowed-tools", ",".join(allowed_tools)]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return runner(cmd, Path(cwd))
