# src/wiki_daemon/review.py
"""The wiki/review/ clarification queue: one markdown file per open question.

Pure file operations over the vault — no LLM, no daemon state. The maintainer
(claude) creates these during ingest; `write_answer` records the user's answer;
ops.apply_clarification runs a maintainer pass that resolves (deletes) the file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
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
    options: list[str]
    recommended: int | None  # 1-based index into options, or None


def _item_from_file(path: Path) -> ReviewItem:
    meta, _ = parse(path.read_text(encoding="utf-8"))
    raw_opts = meta.get("options") or []
    options = [str(o) for o in raw_opts] if isinstance(raw_opts, list) else []
    rec = meta.get("recommended")
    recommended = int(rec) if isinstance(rec, int) and 1 <= rec <= len(options) else None
    return ReviewItem(
        id=path.stem,
        path=path,
        status=str(meta.get("status", "open")),
        source=str(meta.get("source", "")),
        question=str(meta.get("question", "")),
        tentative=str(meta.get("tentative", "")),
        answer=(str(meta["answer"]) if meta.get("answer") is not None else None),
        options=options,
        recommended=recommended,
    )


def list_items(cfg: Config, *, source: str | None = None) -> list[ReviewItem]:
    """Every wiki/review/*.md, sorted by id. Empty if the dir is absent.
    `source` filters to items whose `source:` matches that vault-relative path."""
    if not cfg.review.is_dir():
        return []
    items = [_item_from_file(p) for p in sorted(cfg.review.glob("*.md"))]
    if source is not None:
        items = [it for it in items if it.source == source]
    return items


def option_to_answer(item: ReviewItem, pick: int) -> str:
    """Resolve a 1-based option index to its answer text. Raises ValueError if
    the item has no options or the index is out of range."""
    if not item.options:
        raise ValueError(f"review item {item.id} has no options to pick from")
    if not 1 <= pick <= len(item.options):
        raise ValueError(
            f"--pick {pick} out of range (1..{len(item.options)}) for {item.id}")
    return item.options[pick - 1]


def accept_item(cfg: Config, item_id: str) -> ReviewItem:
    """Accept the already-applied tentative choice: log it and remove the review
    file. Pure file ops — no LLM, since the wiki already reflects the tentative.
    Raises FileNotFoundError if the item is absent."""
    item = read_item(cfg, item_id)  # raises if missing
    log = cfg.wiki / "log.md"
    line = f"\n## [{date.today().isoformat()}] review (accepted) | {item.question}\n"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line)
    item.path.unlink()
    return item


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
