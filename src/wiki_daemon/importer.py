"""Land an arbitrary text file into the vault's raw/sources/ as a Markdown
source, then let ops.ingest process it. Pure file-handling: no LLM, no state."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.convert import CONVERT_EXTENSIONS, convert_to_markdown
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


def _wrap_as_source(text: str, stem: str, now: str) -> str:
    """Return `text` with our minimal source frontmatter, synthesized from the
    filename `stem` when the text has none."""
    if _has_frontmatter(text):
        return text
    title = _slugify(stem).replace("-", " ").title()
    return dump({"type": "source", "captured_at": now, "title": title}, text)


def _unique_dest(cfg: Config, name: str) -> Path:
    """A non-clobbering raw/sources/<name>; appends -2/-3 before `.md`."""
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    dest = cfg.raw_sources / name
    counter = 2
    while dest.exists():
        dest = cfg.raw_sources / f"{Path(name).stem}-{counter}.md"
        counter += 1
    return dest


def _read_as_markdown(path: Path) -> str:
    """Document -> Markdown text. Convertible formats go through markitdown;
    everything else must be UTF-8 text. Raises ValueError otherwise."""
    if path.suffix.lower() in CONVERT_EXTENSIONS:
        return convert_to_markdown(path)
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"not a UTF-8 text file: {path}") from exc


def import_source(cfg: Config, src_path: Path) -> Path:
    """Bring an external file into raw/sources/ as a Markdown source and return
    the landed path. Convertible documents (PDF/DOCX/…) are converted to Markdown
    via markitdown; text files are taken as-is. Frontmatter is synthesized when
    absent. The original (external) file is left in place. Raises
    FileNotFoundError for a missing path and ValueError for unconvertible/non-UTF-8
    input."""
    src_path = Path(src_path)
    if not src_path.is_file():
        raise FileNotFoundError(f"not a file: {src_path}")
    text = _read_as_markdown(src_path)
    now = _now_iso()
    out = _wrap_as_source(text, src_path.stem, now)
    dest = _unique_dest(cfg, _dest_name(src_path.stem, now[:10]))
    dest.write_text(out, encoding="utf-8")
    return dest


def normalize_in_place(cfg: Config, path: Path) -> Path:
    """Convert a document already sitting in raw/sources/ to a sibling Markdown
    source and archive the original to raw/originals/. Returns the new .md path.
    Keeps raw/sources/ all-Markdown. Raises ValueError on conversion failure."""
    path = Path(path)
    text = _wrap_as_source(convert_to_markdown(path), path.stem, _now_iso())
    dest = _unique_dest(cfg, f"{path.stem}.md")
    dest.write_text(text, encoding="utf-8")
    cfg.raw_originals.mkdir(parents=True, exist_ok=True)
    archived = cfg.raw_originals / path.name
    counter = 2
    while archived.exists():
        archived = cfg.raw_originals / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    path.rename(archived)
    return dest
