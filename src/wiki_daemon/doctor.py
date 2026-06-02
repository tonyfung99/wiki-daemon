"""`wiki doctor` — validate the daemon host's iCloud + tooling (Task 13).

Static checks (environment, tooling, vault, pin) always run. The iCloud dataless
round-trip is best-effort: a freshly written probe may not be uploaded yet, so a
failure to evict is reported WARN with guidance to retry against a file you
evicted manually in Finder (`--probe <path>`).
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.health import probe_auth
from wiki_daemon.icloud import ensure_materialized, is_dataless

_ICLOUD_MARKER = "Mobile Documents/com~apple~CloudDocs"


@dataclass
class Check:
    name: str
    status: str  # "PASS" | "WARN" | "FAIL"
    detail: str


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def overall_status(checks: list[Check]) -> str:
    if any(c.status == "FAIL" for c in checks):
        return "FAIL"
    if any(c.status == "WARN" for c in checks):
        return "WARN"
    return "PASS"


def check_environment() -> Check:
    arch = platform.machine()
    mac = platform.mac_ver()[0] or "unknown"
    note = "" if arch == "x86_64" else "  (note: production host is Intel x86_64)"
    return Check("environment", "PASS", f"arch={arch}, macOS={mac}{note}")


def check_tooling() -> list[Check]:
    have_brctl = shutil.which("brctl") is not None
    have_fpctl = shutil.which("fileproviderctl") is not None
    have_claude = shutil.which("claude") is not None
    return [
        Check(
            "tool:materialize",
            "PASS" if (have_brctl or have_fpctl) else "FAIL",
            f"brctl={'yes' if have_brctl else 'no'}, "
            f"fileproviderctl={'yes' if have_fpctl else 'no'}",
        ),
        Check(
            "tool:claude",
            "PASS" if have_claude else "FAIL",
            "found" if have_claude else "NOT on PATH — daemon can't ingest",
        ),
    ]


def check_auth(cfg: Config, *, probe_fn=probe_auth) -> Check:
    res = probe_fn(cfg)
    if res.state == "ok":
        return Check("tool:claude-auth", "PASS", "authenticated")
    if res.state == "auth_failed":
        return Check("tool:claude-auth", "FAIL",
                     f"{res.detail} — run `claude setup-token`")
    return Check("tool:claude-auth", "WARN", f"could not verify: {res.detail}")


def check_vault(cfg: Config) -> list[Check]:
    if not cfg.vault.exists():
        return [Check("vault", "FAIL", f"not found: {cfg.vault}")]
    scaffolded = cfg.claude_md.exists() and cfg.raw_sources.exists() and cfg.wiki.exists()
    in_icloud = _ICLOUD_MARKER in str(cfg.vault)
    return [
        Check("vault:scaffolded", "PASS" if scaffolded else "WARN",
              "ok" if scaffolded else "run `wiki init` first"),
        Check("vault:icloud", "PASS" if in_icloud else "WARN",
              "under iCloud Drive" if in_icloud
              else "NOT under iCloud Drive — cross-device sync won't work"),
    ]


def check_pinned(cfg: Config, run=_run) -> Check:
    rc, _ = run(["xattr", "-p", "com.apple.fileprovider.pinned", str(cfg.vault)])
    if rc == 0:
        return Check("vault:pinned", "PASS", "Keep Downloaded is on")
    return Check("vault:pinned", "WARN",
                 "not pinned — Finder ▸ right-click the vault ▸ Keep Downloaded")


def _roundtrip(probe: Path, run=_run, *, polls: int = 10, interval: float = 1.0,
               sleep=time.sleep) -> Check:
    if run(["brctl", "evict", str(probe)])[0] != 0:
        run(["fileproviderctl", "evict", str(probe)])
    became_dataless = False
    for _ in range(polls):
        if is_dataless(probe):
            became_dataless = True
            break
        sleep(interval)
    if not became_dataless:
        return Check("icloud:roundtrip", "WARN",
                     "probe didn't evict to dataless (likely not uploaded yet). "
                     "Retry against a synced file: evict it in Finder, then "
                     "`wiki doctor --vault <v> --probe <that-file>`")
    ensure_materialized(probe)
    if is_dataless(probe):
        return Check("icloud:roundtrip", "FAIL",
                     "evicted to dataless but could NOT re-materialize")
    return Check("icloud:roundtrip", "PASS",
                 "evict → dataless detected → download → materialized")


def probe_existing(path: Path) -> Check:
    if not path.exists():
        return Check("icloud:probe", "FAIL", f"not found: {path}")
    if not is_dataless(path):
        return Check("icloud:probe", "WARN",
                     f"{path.name} is already materialized — evict it first to test")
    ensure_materialized(path)
    ok = not is_dataless(path)
    return Check("icloud:probe", "PASS" if ok else "FAIL",
                 "dataless detected → materialized" if ok
                 else "detected dataless but could NOT materialize")


def run_doctor(cfg: Config, *, probe: Path | None = None, run=_run) -> int:
    checks: list[Check] = [check_environment()]
    checks += check_tooling()
    if cfg.vault.exists():
        checks.append(check_auth(cfg))
    checks += check_vault(cfg)
    if cfg.vault.exists():
        checks.append(check_pinned(cfg, run))
    if probe is not None:
        checks.append(probe_existing(probe))
    elif cfg.vault.exists() and cfg.claude_md.exists():
        tmp = cfg.vault / ".wiki-doctor-probe"
        try:
            tmp.write_text("probe", encoding="utf-8")
            checks.append(_roundtrip(tmp, run))
        finally:
            tmp.unlink(missing_ok=True)
    _print(checks)
    return 0 if overall_status(checks) != "FAIL" else 1


def _print(checks: list[Check]) -> None:
    icon = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}
    for c in checks:
        print(f"  {icon.get(c.status, '?')} [{c.status}] {c.name}: {c.detail}")
    print(f"\noverall: {overall_status(checks)}")
