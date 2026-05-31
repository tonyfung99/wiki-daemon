# src/wiki_daemon/scaffold.py
"""Create a fresh vault from bundled templates. Idempotent: never clobbers."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from wiki_daemon.config import Config

_TEMPLATES = {
    "CLAUDE.md": "CLAUDE.md",
    "purpose.md": "purpose.md",
    "wiki/index.md": "index.md",
    "wiki/log.md": "log.md",
}
_DIRS = ["raw/sources", "wiki/entities", "wiki/concepts", "wiki/sources", "wiki/queries"]


def _template_text(name: str) -> str:
    return resources.files("wiki_daemon.templates").joinpath(name).read_text(encoding="utf-8")


def init_vault(cfg: Config) -> None:
    for d in _DIRS:
        (cfg.vault / d).mkdir(parents=True, exist_ok=True)
    for dest, tmpl in _TEMPLATES.items():
        target = cfg.vault / dest
        if target.exists():
            continue  # idempotent: preserve user edits
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_template_text(tmpl), encoding="utf-8")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
