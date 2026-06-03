# tests/test_lint.py
from wiki_daemon.config import Config
from wiki_daemon.lint import Finding, _iter_pages, _titles, _links_in


def _cfg(tmp_path):
    cfg = Config(vault=tmp_path / "v")
    for sub in ("entities", "concepts", "sources", "queries", "review"):
        (cfg.wiki / sub).mkdir(parents=True, exist_ok=True)
    return cfg


def _page(cfg, sub, name, title=None, body="", extra=""):
    fm = "---\ntype: concept\n"
    if title is not None:
        fm += f"title: {title}\n"
    fm += extra + "---\n"
    (cfg.wiki / sub / name).write_text(fm + body, encoding="utf-8")
    return cfg.wiki / sub / name


def test_iter_pages_scans_catalog_dirs_excludes_review(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "a.md", title="A")
    _page(cfg, "review", "r.md", title="R")  # excluded
    names = sorted(p.name for p in _iter_pages(cfg))
    assert names == ["a.md"]


def test_titles_collects_frontmatter_titles_normalized(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "a.md", title="Calvin   Cycle")
    _page(cfg, "entities", "b.md", title="Acme Corp")
    assert _titles(cfg) == {"Calvin Cycle", "Acme Corp"}


def test_links_in_extracts_wikilinks_and_strips_alias(tmp_path):
    body = "See [[Calvin Cycle]] and [[Chlorophyll|the pigment]]."
    assert _links_in(body) == ["Calvin Cycle", "Chlorophyll"]


def test_finding_is_frozen_dataclass():
    f = Finding(check="dead_link", severity="error", path="wiki/x.md",
                message="m", fixable=False, fix_action="")
    assert f.check == "dead_link" and f.fixable is False
