"""Watcher logic: which files are relevant, and the reconcile diff.

The reconcile sweep stat-checks individual .md files (never a recursive ** glob)
so it does not accidentally materialize dataless directories on iCloud.
"""
from __future__ import annotations

from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.convert import CONVERT_EXTENSIONS
from wiki_daemon.sources import read_source
from wiki_daemon.state import StateStore


def is_relevant(cfg: Config, path: Path) -> bool:
    """A file in raw/sources/ the daemon should act on: Markdown (ingest) or a
    convertible document (normalize to Markdown, then ingest)."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext != ".md" and ext not in CONVERT_EXTENSIONS:
        return False
    try:
        path.relative_to(cfg.raw_sources)
    except ValueError:
        return False
    return True


def files_to_ingest(cfg: Config, store: StateStore) -> list[Path]:
    """Reconcile: unprocessed Markdown sources plus any convertible documents
    awaiting normalization. Convertibles are included unconditionally (they are
    never hash-checked — they get converted then archived out of raw/sources)."""
    out: list[Path] = []
    if not cfg.raw_sources.exists():
        return out
    for p in sorted(cfg.raw_sources.iterdir()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in CONVERT_EXTENSIONS:
            out.append(p)
        elif ext == ".md" and not store.is_processed(read_source(p).sha256):
            out.append(p)
    return out
