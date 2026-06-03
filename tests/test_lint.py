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


# append to tests/test_lint.py
from wiki_daemon.lint import _dead_links, _conflict_duplicates


def test_dead_link_flagged_resolving_link_clean(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "cycle.md", title="Calvin Cycle")
    _page(cfg, "concepts", "p.md", title="Photosynthesis",
          body="Uses [[Calvin Cycle]] and [[Dark Reactions]].")
    findings = _dead_links(cfg)
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "dead_link" and "Dark Reactions" in f.message
    assert f.path.endswith("p.md") and f.fixable is False


def test_conflict_duplicate_flagged_only_when_base_exists(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "photosynthesis.md", title="Photosynthesis")
    _page(cfg, "concepts", "photosynthesis 2.md", title="Photosynthesis")  # dupe
    _page(cfg, "concepts", "chapter 2.md", title="Chapter 2")  # NOT a dupe (no base)
    findings = _conflict_duplicates(cfg)
    assert len(findings) == 1
    f = findings[0]
    assert f.path.endswith("photosynthesis 2.md")
    assert f.fixable is True and f.fix_action == "delete_file"


# append to tests/test_lint.py
from wiki_daemon.lint import _orphans, _index_integrity, run_checks


def _index(cfg, *titles):
    body = "# Index\n" + "".join(f"- [[{t}]] — x\n" for t in titles)
    (cfg.wiki / "index.md").write_text(body, encoding="utf-8")


def _log(cfg):
    (cfg.wiki / "log.md").write_text("# Log\n", encoding="utf-8")


def test_orphan_flagged_linked_or_indexed_clean(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "lonely.md", title="Lonely")     # orphan
    _page(cfg, "concepts", "seen.md", title="Seen")          # indexed
    _page(cfg, "concepts", "ref.md", title="Ref",
          body="see [[Linked]]")
    _page(cfg, "concepts", "linked.md", title="Linked")      # linked-to
    _index(cfg, "Seen")
    _log(cfg)
    paths = {f.path for f in _orphans(cfg)}
    assert any(p.endswith("lonely.md") for p in paths)
    assert not any(p.endswith("seen.md") for p in paths)
    assert not any(p.endswith("linked.md") for p in paths)


def test_index_integrity_missing_entry_and_broken_source_trace(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "a.md", title="A")  # not in index
    (cfg.wiki / "sources" / "s.md").write_text(
        "---\ntype: source\ntitle: S\nsources: [raw/sources/missing.md]\n---\nx\n",
        encoding="utf-8")
    _index(cfg, "S")  # A missing; S present
    _log(cfg)
    findings = _index_integrity(cfg)
    msgs = " ".join(f.message for f in findings)
    assert "missing from index" in msgs
    assert "missing.md" in msgs


def test_index_integrity_flags_missing_index_and_log(tmp_path):
    cfg = _cfg(tmp_path)  # no index.md/log.md written
    findings = _index_integrity(cfg)
    joined = " ".join(f.message for f in findings)
    assert "index.md" in joined and "log.md" in joined


def test_run_checks_aggregates_sorted(tmp_path):
    cfg = _cfg(tmp_path)
    _page(cfg, "concepts", "p.md", title="P", body="[[Nope]]")
    _index(cfg, "P")
    _log(cfg)
    findings = run_checks(cfg)
    assert any(f.check == "dead_link" for f in findings)
    assert findings == sorted(findings, key=lambda f: (f.severity, f.check, f.path))
