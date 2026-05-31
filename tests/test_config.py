# tests/test_config.py
from pathlib import Path
from wiki_daemon.config import Config


def test_paths_derive_from_vault(tmp_path):
    cfg = Config(vault=tmp_path, state_root=tmp_path / "state")
    assert cfg.raw_sources == tmp_path / "raw" / "sources"
    assert cfg.wiki == tmp_path / "wiki"
    assert cfg.claude_md == tmp_path / "CLAUDE.md"


def test_vault_id_is_stable_and_path_specific(tmp_path):
    a = Config(vault=tmp_path / "A", state_root=tmp_path)
    a2 = Config(vault=tmp_path / "A", state_root=tmp_path)
    b = Config(vault=tmp_path / "B", state_root=tmp_path)
    assert a.vault_id == a2.vault_id
    assert a.vault_id != b.vault_id


def test_state_dir_under_state_root(tmp_path):
    cfg = Config(vault=tmp_path / "A", state_root=tmp_path / "root")
    assert cfg.state_dir == (tmp_path / "root" / cfg.vault_id)
    assert cfg.processed_json == cfg.state_dir / "processed.json"
