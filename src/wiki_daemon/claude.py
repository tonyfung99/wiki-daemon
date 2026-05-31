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
    code, out, err = runner(cmd, Path(cwd), timeout)
    return ClaudeResult(ok=(code == 0), returncode=code, stdout=out, stderr=err)
