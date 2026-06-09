# src/wiki_daemon/scaffold.py
"""Create a fresh vault from bundled templates. Idempotent: never clobbers."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from wiki_daemon.config import Config

_TEMPLATES = {
    "AGENTS.md": "AGENTS.md",   # canonical maintainer brain (provider files symlink here)
    "purpose.md": "purpose.md",
    "wiki/index.md": "index.md",
    "wiki/log.md": "log.md",
}
_DIRS = ["raw/sources", "raw/originals", "wiki/entities", "wiki/concepts",
         "wiki/sources", "wiki/queries", "wiki/review"]


def _template_text(name: str) -> str:
    return resources.files("wiki_daemon.templates").joinpath(name).read_text(encoding="utf-8")


def ensure_brain_link(link: Path, canonical: str = "AGENTS.md") -> None:
    """Make `link` a relative symlink to the canonical brain. Idempotent:
    leaves a correct symlink alone, replaces a broken/wrong one, and never
    clobbers a real (non-symlink) file — doctor --fix migrates those."""
    if link.is_symlink():
        if link.readlink() == Path(canonical) and link.exists():
            return
        link.unlink()  # broken or wrong target → recreate
    elif link.exists():
        return  # a real file (e.g. legacy CLAUDE.md) — leave for doctor to migrate
    link.symlink_to(canonical)


def init_vault(cfg: Config) -> None:
    for d in _DIRS:
        (cfg.vault / d).mkdir(parents=True, exist_ok=True)
    for dest, tmpl in _TEMPLATES.items():
        target = cfg.vault / dest
        if target.exists():
            continue  # idempotent: preserve user edits
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_template_text(tmpl), encoding="utf-8")
    for link in cfg.brain_links.values():
        ensure_brain_link(link)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
