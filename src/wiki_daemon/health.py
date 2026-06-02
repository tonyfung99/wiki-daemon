# src/wiki_daemon/health.py
"""Probe whether headless `claude -p` can authenticate. Shared by the daemon
startup preflight and `wiki doctor`."""
from __future__ import annotations

from dataclasses import dataclass

from wiki_daemon.claude import Runner, classify_failure, run_claude
from wiki_daemon.config import Config


@dataclass
class AuthResult:
    state: str   # "ok" | "auth_failed" | "unavailable"
    detail: str


def probe_auth(cfg: Config, *, runner: Runner | None = None) -> AuthResult:
    """Run a tiny `claude -p` probe and classify the outcome."""
    kwargs = {} if runner is None else {"runner": runner}
    res = run_claude(
        prompt="Reply with exactly: ok",
        cwd=cfg.vault,
        allowed_tools=["Read"],
        claude_bin=cfg.claude_bin,
        timeout=60,
        **kwargs,
    )
    if res.ok:
        return AuthResult("ok", "authenticated")
    detail = (res.stderr or res.stdout or "no output").strip()[:160]
    kind = classify_failure(res)
    if kind == "auth":
        return AuthResult("auth_failed", detail)
    return AuthResult("unavailable", detail)
