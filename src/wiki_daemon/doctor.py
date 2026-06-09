"""`wiki doctor` — validate the daemon host's iCloud + tooling (Task 13).

Static checks (environment, tooling, vault, pin) always run. The iCloud dataless
round-trip is best-effort: a freshly written probe may not be uploaded yet, so a
failure to evict is reported WARN with guidance to retry against a file you
evicted manually in Finder (`--probe <path>`).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.health import probe_auth
from wiki_daemon.icloud import ensure_materialized, is_dataless
from wiki_daemon.maintainer import (
    apply_upgrade, missing_sections, parse_version, template_text, template_version,
)

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


def check_tooling(cfg: Config) -> list[Check]:
    from wiki_daemon.agent import get_provider
    have_brctl = shutil.which("brctl") is not None
    have_fpctl = shutil.which("fileproviderctl") is not None
    provider = get_provider(cfg)
    have_agent = shutil.which(provider.bin) is not None
    return [
        Check(
            "tool:materialize",
            "PASS" if (have_brctl or have_fpctl) else "FAIL",
            f"brctl={'yes' if have_brctl else 'no'}, "
            f"fileproviderctl={'yes' if have_fpctl else 'no'}",
        ),
        Check(
            f"tool:{provider.name}",
            "PASS" if have_agent else "FAIL",
            "found" if have_agent else "NOT on PATH — daemon can't ingest",
        ),
    ]


def check_auth(cfg: Config, *, probe_fn=probe_auth) -> Check:
    from wiki_daemon.agent import get_provider
    provider = get_provider(cfg)
    name = f"tool:{provider.name}-auth"
    res = probe_fn(cfg)
    if res.state == "ok":
        return Check(name, "PASS", "authenticated")
    if res.state == "auth_failed":
        return Check(name, "FAIL", f"{res.detail} — {provider.auth_hint}")
    return Check(name, "WARN", f"could not verify: {res.detail}")


def check_vault(cfg: Config) -> list[Check]:
    if not cfg.vault.exists():
        return [Check("vault", "FAIL", f"not found: {cfg.vault}")]
    scaffolded = ((cfg.agents_md.exists() or cfg.claude_md.exists())
                  and cfg.raw_sources.exists() and cfg.wiki.exists())
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


def _brain_is_stale(text: str) -> bool:
    """The brain content is behind the template (older or no version stamp)."""
    v = parse_version(text)
    return v is None or v < template_version()


def _broken_brain_links(cfg: Config) -> list[str]:
    """Provider brain filenames that are not intact symlinks to AGENTS.md."""
    bad = []
    for name, link in cfg.brain_links.items():
        if not (link.is_symlink() and link.exists()
                and link.resolve() == cfg.agents_md.resolve()):
            bad.append(name)
    return bad


def check_brain(cfg: Config) -> Check | None:
    """WARN if the maintainer brain is unhealthy: a legacy real CLAUDE.md awaiting
    migration, stale/incomplete AGENTS.md content, or broken provider symlinks.
    Returns None when nothing is scaffolded (covered by vault:scaffolded)."""
    if not cfg.agents_md.exists():
        if cfg.claude_md.exists() and not cfg.claude_md.is_symlink():
            return Check("vault:brain", "WARN",
                         "legacy CLAUDE.md (no AGENTS.md) — migrate with "
                         "`wiki doctor --fix`")
        return None
    text = cfg.agents_md.read_text(encoding="utf-8")
    missing = missing_sections(text)
    stale = _brain_is_stale(text)
    bad = _broken_brain_links(cfg)
    if not missing and not stale and not bad:
        return Check("vault:brain", "PASS",
                     f"up to date (v{template_version()}); provider symlinks ok")
    reasons = []
    if stale:
        v = parse_version(text)
        reasons.append("unversioned" if v is None
                       else f"stale content (v{v} < v{template_version()})")
    if missing:
        names = ", ".join(s.header.removeprefix("## ") for s in missing)
        reasons.append(f"missing {len(missing)} section(s) ({names})")
    if bad:
        reasons.append(f"broken symlink(s): {', '.join(bad)}")
    return Check("vault:brain", "WARN",
                 "brain: " + "; ".join(reasons) + " — run `wiki doctor --fix`")


def _confirm_or_refuse(plan: str, *, yes: bool) -> bool:
    """Shared --fix gate: True to proceed. Non-interactive without --yes refuses
    (caller returns 2); a declined TTY prompt aborts (caller returns None)."""
    if yes:
        return True
    if not sys.stdin.isatty():
        print("refusing to --fix without confirmation; re-run with --yes",
              file=sys.stderr)
        return False  # caller distinguishes via sys.stdin.isatty()
    if input(f"{plan}. Proceed? [type 'yes'] ").strip() != "yes":
        print("aborted")
        return False
    return True


def _backup_path(cfg: Config, name: str) -> Path:
    """A non-clobbering <name>.bak-<date>[-N] path in the vault."""
    base = cfg.vault / f"{name}.bak-{date.today().isoformat()}"
    cand, n = base, 2
    while cand.exists():
        cand = cfg.vault / f"{base.name}-{n}"
        n += 1
    return cand


def _fix_brain(cfg: Config, checks: list[Check], *, yes: bool) -> int | None:
    """Repair the maintainer brain in one pass: migrate a legacy CLAUDE.md to the
    canonical AGENTS.md, refresh stale/incomplete AGENTS.md content (backup +
    overwrite, or append missing sections), and (re)create the provider symlinks.
    Returns 2 if refused without confirmation, else None."""
    from wiki_daemon.scaffold import ensure_brain_link

    cmd = next((c for c in checks if c.name == "vault:brain"), None)
    if cmd is None or cmd.status != "WARN":
        return None
    if not _confirm_or_refuse("will repair the maintainer brain "
                              "(AGENTS.md + provider symlinks)", yes=yes):
        return None if sys.stdin.isatty() else 2  # declined=None, refused=2

    # 1) Migrate a legacy real CLAUDE.md → AGENTS.md (preserve content; back up).
    if not cfg.agents_md.exists() and cfg.claude_md.exists() \
            and not cfg.claude_md.is_symlink():
        legacy = cfg.claude_md.read_text(encoding="utf-8")
        backup = _backup_path(cfg, "CLAUDE.md")
        backup.write_text(legacy, encoding="utf-8")
        cfg.agents_md.write_text(legacy, encoding="utf-8")
        cfg.claude_md.unlink()  # recreated as a symlink in step 3
        print(f"migrated CLAUDE.md -> AGENTS.md (backup {backup.name})")

    # 2) Refresh AGENTS.md content if it is behind the template.
    if cfg.agents_md.exists():
        text = cfg.agents_md.read_text(encoding="utf-8")
        if _brain_is_stale(text):
            backup = _backup_path(cfg, "AGENTS.md")
            backup.write_text(text, encoding="utf-8")
            cfg.agents_md.write_text(template_text(), encoding="utf-8")
            print(f"backed up AGENTS.md -> {backup.name}; "
                  f"wrote template v{template_version()}")
        else:
            new_text, added = apply_upgrade(text)
            if added:
                cfg.agents_md.write_text(new_text, encoding="utf-8")
                names = ", ".join(h.removeprefix("## ") for h in added)
                print(f"appended {len(added)} section(s) ({names})")

    # 3) (Re)create provider symlinks → AGENTS.md (heals iCloud-broken links).
    repaired = []
    for name, link in cfg.brain_links.items():
        before = link.is_symlink() and link.exists()
        ensure_brain_link(link)
        if not before:
            repaired.append(name)
    if repaired:
        print(f"linked: {', '.join(repaired)} -> AGENTS.md")
    return None


def run_doctor(cfg: Config, *, probe: Path | None = None, fix: bool = False,
               yes: bool = False, run=_run) -> int:
    checks: list[Check] = [check_environment()]
    checks += check_tooling(cfg)
    if cfg.vault.exists():
        checks.append(check_auth(cfg))
    checks += check_vault(cfg)
    if cfg.vault.exists():
        checks.append(check_pinned(cfg, run))
        cmd = check_brain(cfg)
        if cmd is not None:
            checks.append(cmd)
    if probe is not None:
        checks.append(probe_existing(probe))
    elif cfg.vault.exists() and (cfg.agents_md.exists() or cfg.claude_md.exists()):
        tmp = cfg.vault / ".wiki-doctor-probe"
        try:
            tmp.write_text("probe", encoding="utf-8")
            checks.append(_roundtrip(tmp, run))
        finally:
            tmp.unlink(missing_ok=True)
    _print(checks)
    if fix:
        override = _fix_brain(cfg, checks, yes=yes)
        if override is not None:
            return override
    return 0 if overall_status(checks) != "FAIL" else 1


def _print(checks: list[Check]) -> None:
    icon = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}
    for c in checks:
        print(f"  {icon.get(c.status, '?')} [{c.status}] {c.name}: {c.detail}")
    print(f"\noverall: {overall_status(checks)}")
