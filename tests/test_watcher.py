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
