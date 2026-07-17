"""Daemon-side persistence of saved query answers.

The agent generates the answer read-only; the daemon writes the result files
itself (query page + index line + log line) instead of using the agent's
unreliable workspace-write path.
"""
from __future__ import annotations

import re
import threading
from datetime import date
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import dump, parse

_SLUG_MAX = 60
_TITLE_MAX = 80

# Serializes persist_query's read-modify-write of index.md / log.md so
# concurrent queries (multiple API threads) can't corrupt those shared files.
_write_lock = threading.Lock()


def _normalize_q(s: str) -> str:
    return " ".join(s.split())


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > _SLUG_MAX:
        s = s[:_SLUG_MAX].rsplit("-", 1)[0] if "-" in s[:_SLUG_MAX] else s[:_SLUG_MAX]
        s = s.strip("-")
    return s or "query"


def _title_from_question(question: str) -> str:
    t = _normalize_q(question)
    if len(t) > _TITLE_MAX:
        t = t[:_TITLE_MAX].rsplit(" ", 1)[0].rstrip()
    return t[:1].upper() + t[1:] if t else "Query"


def _insert_under_section(text: str, section: str, line: str) -> str:
    """Insert `line` immediately after the `section` header (newest first). If
    the section is absent, append the section header and the line at the end."""
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for l in lines:
        out.append(l)
        if not inserted and l.strip() == section:
            out.append(line)
            inserted = True
    if not inserted:
        if out and out[-1].strip() != "":
            out.append("")
        out.append(section)
        out.append(line)
    return "\n".join(out) + "\n"
