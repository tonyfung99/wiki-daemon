# src/wiki_daemon/health.py
"""Probe whether headless `claude -p` can authenticate. Shared by the daemon
startup preflight and `wiki doctor`."""
from __future__ import annotations

from dataclasses import dataclass

from wiki_daemon.agent import Runner, get_provider, run_agent
from wiki_daemon.config import Config


@dataclass
class AuthResult:
    state: str   # "ok" | "auth_failed" | "unavailable"
    detail: str


def probe_auth(cfg: Config, *, runner: Runner | None = None) -> AuthResult:
    """Run a tiny read-only probe through the configured provider and classify."""
    provider = get_provider(cfg)
    kwargs = {} if runner is None else {"runner": runner}
    res = run_agent(provider, "Reply with exactly: ok", cfg.vault,
                    write=False, timeout=60, **kwargs)
    if res.ok:
        return AuthResult("ok", "authenticated")
    detail = (res.stderr or res.stdout or "no output").strip()[:160]
    kind = provider.classify_failure(res)
    if kind in ("auth", "quota"):
        return AuthResult("auth_failed", detail)
    return AuthResult("unavailable", detail)
