# src/wiki_daemon/frontmatter.py
"""Parse and serialize YAML frontmatter at the head of a Markdown document."""
from __future__ import annotations

import yaml

_DELIM = "---"


def parse(text: str) -> tuple[dict, str]:
    """Return (metadata, body). Missing/empty frontmatter yields {}."""
    if not text.startswith(_DELIM + "\n"):
        return {}, text
    rest = text[len(_DELIM) + 1 :]
    # The closing delimiter may be at the very start (empty frontmatter)
    # or after a newline.
    if rest.startswith(_DELIM + "\n"):
        raw = ""
        body = rest[len(_DELIM) + 1 :]
    else:
        end = rest.find("\n" + _DELIM + "\n")
        if end == -1:
            return {}, text
        raw = rest[:end]
        body = rest[end + len("\n" + _DELIM + "\n") :]
    meta = yaml.safe_load(raw) if raw.strip() else None
    return (meta or {}), body


def dump(meta: dict, body: str) -> str:
    """Serialize metadata + body back into a frontmatter document."""
    raw = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False).strip()
    return f"{_DELIM}\n{raw}\n{_DELIM}\n{body}"
