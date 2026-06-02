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
    """`YYYY-MM-DD-<slug>.md`, skipping the date prefix when the stem already
    starts with one (avoids `2026-06-02-2026-06-01-foo`)."""
    if _DATE_PREFIX.match(stem):
        return f"{stem}.md"
    return f"{today}-{_slugify(stem)}.md"
