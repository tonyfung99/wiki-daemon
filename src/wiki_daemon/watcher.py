"""Watcher logic: which files are relevant, and the reconcile diff.

The reconcile sweep stat-checks individual .md files (never a recursive ** glob)
so it does not accidentally materialize dataless directories on iCloud.
"""
from __future__ import annotations

from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.sources import read_source
from wiki_daemon.state import StateStore


def is_relevant(cfg: Config, path: Path) -> bool:
    path = Path(path)
    if path.suffix != ".md":
        return False
    try:
        path.relative_to(cfg.raw_sources)
    except ValueError:
        return False
    return True


def files_to_ingest(cfg: Config, store: StateStore) -> list[Path]:
    """Reconcile: every .md in raw/sources not yet processed (by content hash)."""
    out: list[Path] = []
    if not cfg.raw_sources.exists():
        return out
    for p in sorted(cfg.raw_sources.glob("*.md")):
        if not p.is_file():
            continue
        if not store.is_processed(read_source(p).sha256):
            out.append(p)
    return out
