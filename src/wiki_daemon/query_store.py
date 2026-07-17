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


def _find_existing_page(qdir: Path, question: str) -> Path | None:
    """Return the queries page whose `query:` frontmatter matches `question`
    (whitespace-normalized), or None. Malformed pages are skipped."""
    if not qdir.is_dir():
        return None
    target = _normalize_q(question)
    for page in sorted(qdir.glob("*.md")):
        try:
            meta, _ = parse(page.read_text(encoding="utf-8"))
        except OSError:
            continue
        q = meta.get("query")
        if q is not None and _normalize_q(str(q)) == target:
            return page
    return None


def _unique_slug(qdir: Path, slug: str) -> str:
    """A slug whose `<slug>.md` does not yet exist in qdir (append -2, -3, ...)."""
    if not (qdir / f"{slug}.md").exists():
        return slug
    n = 2
    while (qdir / f"{slug}-{n}.md").exists():
        n += 1
    return f"{slug}-{n}"


def _write_index_line(cfg: Config, slug: str, title: str) -> None:
    index = cfg.wiki / "index.md"
    text = index.read_text(encoding="utf-8") if index.exists() else "# Index\n"
    line = f"- [[{slug}|{title}]]"
    if line in text:
        return
    index.write_text(_insert_under_section(text, "## Queries", line), encoding="utf-8")


def _append_log(cfg: Config, question: str, today: str) -> None:
    log = cfg.wiki / "log.md"
    text = log.read_text(encoding="utf-8") if log.exists() else "# Log\n"
    if not text.endswith("\n"):
        text += "\n"
    log.write_text(text + f"## [{today}] query | {question}\n", encoding="utf-8")


def persist_query(cfg: Config, question: str, answer: str) -> tuple[bool, str]:
    """Write the query answer into the vault: a `wiki/queries/<slug>.md` page,
    a line under `## Queries` in index.md, and a log.md line. Returns
    (True, "") on success, or (False, reason) on any I/O failure — the answer is
    still returned to the caller; only `saved` is False."""
    with _write_lock:
        try:
            qdir = cfg.wiki / "queries"
            qdir.mkdir(parents=True, exist_ok=True)
            title = _title_from_question(question)
            today = date.today().isoformat()
            existing = _find_existing_page(qdir, question)
            if existing is not None:
                path = existing
                is_new = False
            else:
                slug = _unique_slug(qdir, _slugify(title))
                path = qdir / f"{slug}.md"
                is_new = True
            meta = {"type": "query", "title": title, "query": question,
                    "updated": today}
            path.write_text(dump(meta, answer), encoding="utf-8")
            if is_new:
                _write_index_line(cfg, path.stem, title)
            _append_log(cfg, question, today)
            return True, ""
        except OSError as exc:
            return False, f"save failed: {exc}"
