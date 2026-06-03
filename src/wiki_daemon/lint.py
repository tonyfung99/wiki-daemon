# src/wiki_daemon/lint.py
"""Pure mechanical health checks over the wiki. No LLM, no mutations — every
function reads files and returns Findings. The CLI renders/repairs them."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.frontmatter import parse

# Catalog dirs that hold linkable/indexable pages. review/ is transient.
_CATALOG = ("entities", "concepts", "sources", "queries")
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str        # "error" | "warning"
    path: str            # vault-relative
    message: str
    fixable: bool = False
    fix_action: str = ""  # "" | "delete_file"


def _norm(s: str) -> str:
    return " ".join(s.split())


def _iter_pages(cfg: Config):
    """Every *.md under the catalog dirs (sorted, review/ excluded)."""
    for sub in _CATALOG:
        d = cfg.wiki / sub
        if d.is_dir():
            for p in sorted(d.glob("*.md")):
                yield p


def _titles(cfg: Config) -> set[str]:
    out: set[str] = set()
    for p in _iter_pages(cfg):
        meta, _ = parse(p.read_text(encoding="utf-8"))
        t = meta.get("title")
        if t is not None:
            out.add(_norm(str(t)))
    return out


def _links_in(body: str) -> list[str]:
    """Targets of [[Link]] / [[Link|alias]] in order, alias stripped."""
    return [_norm(m.split("|", 1)[0]) for m in _WIKILINK.findall(body)]
