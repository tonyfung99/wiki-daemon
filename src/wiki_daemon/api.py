"""HTTP API server for WikiReader and external clients."""
from __future__ import annotations

import re

_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\|]+?)(?:\|([^\[\]]+?))?\]\]")


def extract_citations(markdown: str) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    for m in _WIKI_LINK_RE.finditer(markdown):
        link = m.group(1).strip()
        alias = (m.group(2) or "").strip()
        if not link:
            continue
        if link in seen:
            continue
        seen.add(link)
        results.append({"wikiLink": link, "title": alias or link})
    return results
