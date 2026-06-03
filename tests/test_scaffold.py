# tests/test_scaffold.py
from wiki_daemon.config import Config
from wiki_daemon.scaffold import init_vault


def test_init_creates_structure(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    assert (cfg.vault / "CLAUDE.md").exists()
    assert (cfg.vault / "purpose.md").exists()
    assert (cfg.raw_sources).is_dir()
    for sub in ("entities", "concepts", "sources", "queries", "review"):
        assert (cfg.wiki / sub).is_dir()
    assert (cfg.wiki / "index.md").exists()
    assert (cfg.wiki / "log.md").exists()


def test_init_is_idempotent_and_preserves_content(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    (cfg.vault / "purpose.md").write_text("my custom purpose\n", encoding="utf-8")
    init_vault(cfg)  # second run must not clobber
    assert (cfg.vault / "purpose.md").read_text(encoding="utf-8") == "my custom purpose\n"
