# src/wiki_daemon/state.py
"""Persisted SHA-256 -> source-path map; makes ingest idempotent."""
from __future__ import annotations

import json
import os
from pathlib import Path


class StateStore:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, str] = {}
        if self._path.exists():
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def is_processed(self, sha: str) -> bool:
        return sha in self._data

    def mark_processed(self, sha: str, source_path: str) -> None:
        self._data[sha] = source_path
        self._flush()

    def _flush(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)  # atomic rename
