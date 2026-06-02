"""Land an arbitrary text file into the vault's raw/sources/ as a Markdown
source, then let ops.ingest process it. Pure file-handling: no LLM, no state."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import dump

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _slugify(stem: str) -> str:
    """Lowercase, non-alphanumeric runs -> single '-', trimmed. Empty -> 'source'."""
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return slug or "source"


def _dest_name(stem: str, today: str) -> str:
    """`YYYY-MM-DD-<slug>.md`. Keeps the stem's own date prefix when it already
    has one (avoids `2026-06-02-2026-06-01-foo`), but always slugifies the rest
    so spaces/case/punctuation never reach the filename."""
    m = _DATE_PREFIX.match(stem)
    if m:
        date, rest = m.group(0)[:-1], stem[m.end():]
        return f"{date}-{_slugify(rest)}.md"
    return f"{today}-{_slugify(stem)}.md"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _has_frontmatter(text: str) -> bool:
    # Match frontmatter.parse's own rule for what counts as frontmatter.
    return text.startswith("---\n")


def import_source(cfg: Config, src_path: Path) -> Path:
    """Copy `src_path` into the vault's raw/sources/ as a Markdown source and
    return the destination path. Always copies (never moves). Synthesizes minimal
    frontmatter when the file has none. Raises FileNotFoundError for a missing/
    non-file path and ValueError for non-UTF-8 input."""
    src_path = Path(src_path)
    if not src_path.is_file():
        raise FileNotFoundError(f"not a file: {src_path}")
    try:
        text = src_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not a UTF-8 text file: {src_path}") from exc

    stem = src_path.stem
    now = _now_iso()
    name = _dest_name(stem, now[:10])  # now[:10] is the YYYY-MM-DD date

    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    dest = cfg.raw_sources / name
    counter = 2
    while dest.exists():
        dest = cfg.raw_sources / f"{Path(name).stem}-{counter}.md"
        counter += 1

    if _has_frontmatter(text):
        out = text
    else:
        title = _slugify(stem).replace("-", " ").title()
        out = dump({"type": "source", "captured_at": now, "title": title}, text)

    dest.write_text(out, encoding="utf-8")
    return dest
