# src/wiki_daemon/progress.py
"""Derive a source's ingest lifecycle state from existing files.

No new daemon bookkeeping: state comes from the job queue (pending/inflight),
processed.json (content-hash dedup), and status.json (last error). Pure and
testable — no claude, no network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.sources import read_source
from wiki_daemon.state import StateStore
from wiki_daemon.runtime import StatusFile


@dataclass
class SourceState:
    state: str  # queued | ingesting | processed | failed | untracked
    detail: str = ""


def _abs(cfg: Config, source) -> Path:
    """Normalize a vault-relative or absolute source path to the absolute,
    resolved form the queue stores as job payloads."""
    p = Path(source)
    if not p.is_absolute():
        p = cfg.vault / p
    return p.resolve()


def _payloads(cfg: Config, prefix: str) -> set[str]:
    out: set[str] = set()
    if not cfg.queue_dir.is_dir():
        return out
    for f in cfg.queue_dir.glob(f"{prefix}-*.json"):
        try:
            out.add(json.loads(f.read_text(encoding="utf-8"))["payload"])
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return out


def source_state(cfg: Config, source) -> SourceState:
    """Lifecycle state of one source. Precedence: processed > ingesting > queued
    > failed > untracked."""
    abs_path = _abs(cfg, source)
    key = str(abs_path)

    # processed — content-hash recorded (skip if the file is gone)
    try:
        sha = read_source(abs_path).sha256
        if StateStore(cfg.processed_json).is_processed(sha):
            return SourceState("processed")
    except (FileNotFoundError, OSError):
        pass

    if key in _payloads(cfg, "inflight"):
        return SourceState("ingesting")
    if key in _payloads(cfg, "pending"):
        return SourceState("queued")

    err = StatusFile(cfg.state_dir / "status.json").read().get("last_error") or {}
    if err.get("file") == key:
        return SourceState("failed", detail=str(err.get("msg", "")))

    return SourceState("untracked")
