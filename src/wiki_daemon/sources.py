# src/wiki_daemon/sources.py
"""Model a raw source file and hash its content for dedupe."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.frontmatter import parse


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SourceFile:
    path: Path
    sha256: str
    meta: dict
    body: str


def read_source(path: Path) -> SourceFile:
    data = Path(path).read_bytes()
    meta, body = parse(data.decode("utf-8"))
    return SourceFile(path=Path(path), sha256=content_sha256(data), meta=meta, body=body)
