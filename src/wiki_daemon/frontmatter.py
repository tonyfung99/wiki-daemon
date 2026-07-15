# src/wiki_daemon/frontmatter.py
"""Parse and serialize YAML frontmatter at the head of a Markdown document."""
from __future__ import annotations

import logging

import yaml

_log = logging.getLogger("wiki_daemon.frontmatter")

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
    if not raw.strip():
        return {}, body
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # A single malformed page (e.g. an unquoted colon in a value) must not
        # crash a caller that parses many pages: treat frontmatter as empty.
        _log.warning("dropping malformed YAML frontmatter: %s", exc)
        return {}, body
    if not isinstance(meta, dict):
        _log.warning("dropping non-dict frontmatter (got %s)", type(meta).__name__)
        return {}, body
    return meta, body


def dump(meta: dict, body: str) -> str:
    """Serialize metadata + body back into a frontmatter document."""
    raw = yaml.safe_dump(meta, sort_keys=False, default_flow_style=False).strip()
    return f"{_DELIM}\n{raw}\n{_DELIM}\n{body}"
