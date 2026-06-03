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


def _rel(cfg: Config, p: Path) -> str:
    return p.relative_to(cfg.vault).as_posix()


def _dead_links(cfg: Config) -> list[Finding]:
    titles = _titles(cfg)
    out: list[Finding] = []
    for p in _iter_pages(cfg):
        _, body = parse(p.read_text(encoding="utf-8"))
        for target in _links_in(body):
            if target not in titles:
                out.append(Finding(
                    "dead_link", "error", _rel(cfg, p),
                    f"link [[{target}]] resolves to no page"))
    return out


_DUP = re.compile(r"^(?P<base>.+) \d+\.md$")


def _conflict_duplicates(cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    for sub in _CATALOG:
        d = cfg.wiki / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            m = _DUP.match(p.name)
            if m and (d / f"{m.group('base')}.md").exists():
                out.append(Finding(
                    "conflict_duplicate", "warning", _rel(cfg, p),
                    f"iCloud conflict copy of {m.group('base')}.md",
                    fixable=True, fix_action="delete_file"))
    return out


def _index_titles(cfg: Config) -> set[str]:
    """Titles mentioned (as [[Title]]) anywhere in index.md."""
    idx = cfg.wiki / "index.md"
    if not idx.is_file():
        return set()
    return set(_links_in(idx.read_text(encoding="utf-8")))


def _orphans(cfg: Config) -> list[Finding]:
    indexed = _index_titles(cfg)
    linked: set[str] = set()
    pages = list(_iter_pages(cfg))
    for p in pages:
        _, body = parse(p.read_text(encoding="utf-8"))
        linked.update(_links_in(body))
    out: list[Finding] = []
    for p in pages:
        # sources/ pages are traced by sources:, not links — exempt
        if p.parent.name == "sources":
            continue
        meta, _ = parse(p.read_text(encoding="utf-8"))
        t = meta.get("title")
        if t is None:
            continue
        title = _norm(str(t))
        if title not in linked and title not in indexed:
            out.append(Finding(
                "orphan", "warning", _rel(cfg, p),
                "not linked from anywhere or indexed"))
    return out


def _index_integrity(cfg: Config) -> list[Finding]:
    out: list[Finding] = []
    idx, log = cfg.wiki / "index.md", cfg.wiki / "log.md"
    if not idx.is_file():
        out.append(Finding("index_integrity", "error", "wiki/index.md",
                           "index.md missing"))
    if not log.is_file():
        out.append(Finding("index_integrity", "error", "wiki/log.md",
                           "log.md missing"))
    indexed = _index_titles(cfg)
    for p in _iter_pages(cfg):
        meta, _ = parse(p.read_text(encoding="utf-8"))
        t = meta.get("title")
        if t is not None and _norm(str(t)) not in indexed:
            out.append(Finding("index_integrity", "warning", _rel(cfg, p),
                               f"'{_norm(str(t))}' missing from index.md"))
        if p.parent.name == "sources":
            refs = meta.get("sources") or []
            if isinstance(refs, str):
                refs = [refs]
            for r in refs:
                if not (cfg.vault / str(r)).exists():
                    out.append(Finding("index_integrity", "error", _rel(cfg, p),
                                       f"sources: trace points to missing {r}"))
    return out


def run_checks(cfg: Config) -> list[Finding]:
    findings = (_dead_links(cfg) + _conflict_duplicates(cfg)
                + _orphans(cfg) + _index_integrity(cfg))
    return sorted(findings, key=lambda f: (f.severity, f.check, f.path))
