from wiki_daemon.watcher import is_relevant, files_to_ingest
from wiki_daemon.config import Config
from wiki_daemon.state import StateStore
from wiki_daemon.sources import read_source


def test_is_relevant_only_md_in_sources(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    md = cfg.raw_sources / "a.md"
    md.write_text("x")
    assert is_relevant(cfg, md) is True
    assert is_relevant(cfg, cfg.raw_sources / ".DS_Store") is False
    assert is_relevant(cfg, cfg.raw_sources / "note.txt") is False
    assert is_relevant(cfg, cfg.wiki / "index.md") is False  # wiki/ is not watched


def test_files_to_ingest_excludes_processed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    a = cfg.raw_sources / "a.md"; a.write_text("aaa")
    b = cfg.raw_sources / "b.md"; b.write_text("bbb")
    store = StateStore(cfg.processed_json)
    store.mark_processed(read_source(a).sha256, str(a))

    pending = files_to_ingest(cfg, store)
    names = {p.name for p in pending}
    assert names == {"b.md"}


# --- convertible formats (2026-06-09) ---
def test_is_relevant_accepts_convertibles(tmp_path):
    cfg = Config(vault=tmp_path / "v")
    cfg.raw_sources.mkdir(parents=True)
    assert is_relevant(cfg, cfg.raw_sources / "a.md")
    assert is_relevant(cfg, cfg.raw_sources / "paper.pdf")
    assert is_relevant(cfg, cfg.raw_sources / "deck.PPTX")   # case-insensitive
    assert not is_relevant(cfg, cfg.raw_sources / "note.txt")  # text: import-only
    assert not is_relevant(cfg, cfg.raw_sources / "img.png")   # out of scope


def test_files_to_ingest_includes_convertibles_without_hashing(tmp_path):
    cfg = Config(vault=tmp_path / "v")
    cfg.raw_sources.mkdir(parents=True)
    (cfg.raw_sources / "paper.pdf").write_bytes(b"%PDF binary-not-utf8\xff\xfe")
    (cfg.raw_sources / "a.md").write_text("---\ntype: source\n---\nbody\n", encoding="utf-8")
    from wiki_daemon.state import StateStore
    out = [p.name for p in files_to_ingest(cfg, StateStore(cfg.processed_json))]
    assert "paper.pdf" in out and "a.md" in out  # binary included, did not crash
