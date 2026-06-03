# src/wiki_daemon/review.py
"""The wiki/review/ clarification queue: one markdown file per open question.

Pure file operations over the vault — no LLM, no daemon state. The maintainer
(claude) creates these during ingest; `write_answer` records the user's answer;
ops.apply_clarification runs a maintainer pass that resolves (deletes) the file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import dump, parse


@dataclass(frozen=True)
class ReviewItem:
    id: str
    path: Path
    status: str
    source: str
    question: str
    tentative: str
    answer: str | None


def _item_from_file(path: Path) -> ReviewItem:
    meta, _ = parse(path.read_text(encoding="utf-8"))
    return ReviewItem(
        id=path.stem,
        path=path,
        status=str(meta.get("status", "open")),
        source=str(meta.get("source", "")),
        question=str(meta.get("question", "")),
        tentative=str(meta.get("tentative", "")),
        answer=(str(meta["answer"]) if meta.get("answer") is not None else None),
    )


def list_items(cfg: Config) -> list[ReviewItem]:
    """Every wiki/review/*.md, sorted by id. Empty if the dir is absent."""
    if not cfg.review.is_dir():
        return []
    return [_item_from_file(p) for p in sorted(cfg.review.glob("*.md"))]


def read_item(cfg: Config, item_id: str) -> ReviewItem:
    """Load one item by id (filename stem). Raises FileNotFoundError if absent."""
    path = cfg.review / f"{item_id}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no such review item: {item_id}")
    return _item_from_file(path)


def write_answer(cfg: Config, item_id: str, answer: str) -> ReviewItem:
    """Record the user's answer: set status=answered + answer, preserving body.
    Atomic write (temp + os.replace)."""
    path = cfg.review / f"{item_id}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no such review item: {item_id}")
    meta, body = parse(path.read_text(encoding="utf-8"))
    meta["status"] = "answered"
    meta["answer"] = answer
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump(meta, body), encoding="utf-8")
    os.replace(tmp, path)
    return _item_from_file(path)
