# tests/test_vault.py
import pytest

from pathlib import Path
from wiki_daemon.vault import (
    VaultNotFound, is_vault, find_vault_upward, read_config_vault,
    write_config_vault, resolve_vault, read_config_token, write_config_token,
    read_config_api,
)


def _make_vault(root: Path) -> Path:
    (root / "raw").mkdir(parents=True)
    (root / "wiki").mkdir()
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    return root


def test_is_vault_true_for_scaffold(tmp_path):
    v = _make_vault(tmp_path / "v")
    assert is_vault(v) is True


def test_is_vault_false_for_plain_dir(tmp_path):
    assert is_vault(tmp_path) is False


def test_find_vault_upward_from_nested_subdir(tmp_path):
    v = _make_vault(tmp_path / "v")
    nested = v / "wiki" / "concepts"
    nested.mkdir(parents=True, exist_ok=True)
    assert find_vault_upward(nested) == v.resolve()


def test_find_vault_upward_none_when_absent(tmp_path):
    assert find_vault_upward(tmp_path) is None


def test_config_roundtrip(tmp_path):
    cfg = tmp_path / "c" / "config.toml"
    write_config_vault(cfg, tmp_path / "myvault")
    assert read_config_vault(cfg) == (tmp_path / "myvault").resolve()


def test_read_config_missing_or_malformed_returns_none(tmp_path):
    assert read_config_vault(tmp_path / "nope.toml") is None
    bad = tmp_path / "bad.toml"
    bad.write_text("this is = = not toml", encoding="utf-8")
    assert read_config_vault(bad) is None


def test_resolve_precedence_explicit_wins(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault("/explicit", env="/env", start_dir=v,
                        config_path=tmp_path / "c.toml")
    assert got == Path("/explicit")


def test_resolve_env_beats_discovery(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault(None, env="/env", start_dir=v, config_path=tmp_path / "c.toml")
    assert got == Path("/env")


def test_resolve_upward_search(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault(None, env=None, start_dir=v / "wiki",
                        config_path=tmp_path / "c.toml")
    assert got == v.resolve()


def test_resolve_config_fallback(tmp_path):
    cfg = tmp_path / "c.toml"
    write_config_vault(cfg, tmp_path / "cfgvault")
    got = resolve_vault(None, env=None, start_dir=tmp_path / "empty",
                        config_path=cfg)
    assert got == (tmp_path / "cfgvault").resolve()


def test_resolve_raises_when_nothing(tmp_path):
    with pytest.raises(VaultNotFound):
        resolve_vault(None, env=None, start_dir=tmp_path / "empty",
                      config_path=tmp_path / "nope.toml")


def test_config_roundtrip_path_with_space(tmp_path):
    cfg = tmp_path / "c.toml"
    vault = tmp_path / "Mobile Documents" / "My Wiki"
    write_config_vault(cfg, vault)
    assert read_config_vault(cfg) == vault.resolve()


def test_config_roundtrip_path_with_quote(tmp_path):
    cfg = tmp_path / "c.toml"
    vault = tmp_path / 'weird"name'
    write_config_vault(cfg, vault)
    assert read_config_vault(cfg) == vault.resolve()


def test_read_config_non_string_default_returns_none(tmp_path):
    bad = tmp_path / "n.toml"
    bad.write_text("default_vault = 42\n", encoding="utf-8")
    assert read_config_vault(bad) is None
    bad2 = tmp_path / "n2.toml"
    bad2.write_text("default_vault = true\n", encoding="utf-8")
    assert read_config_vault(bad2) is None


def test_read_config_empty_string_default_returns_none(tmp_path):
    p = tmp_path / "e.toml"
    p.write_text('default_vault = ""\n', encoding="utf-8")
    assert read_config_vault(p) is None


def test_resolve_whitespace_env_falls_through_to_discovery(tmp_path):
    v = _make_vault(tmp_path / "v")
    got = resolve_vault(None, env="   ", start_dir=v / "wiki",
                        config_path=tmp_path / "c.toml")
    assert got == v.resolve()


def test_read_config_token_missing_file(tmp_path):
    assert read_config_token(tmp_path / "nope.toml") is None


def test_read_config_token_present(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_token = "wk_abc123"\n', encoding="utf-8")
    assert read_config_token(cfg) == "wk_abc123"


def test_write_config_token_creates(tmp_path):
    cfg = tmp_path / "config.toml"
    write_config_token(cfg, "wk_new")
    assert read_config_token(cfg) == "wk_new"


def test_write_config_token_preserves_other_keys(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('default_vault = "/v"\nprovider = "gemini"\n', encoding="utf-8")
    write_config_token(cfg, "wk_new")
    text = cfg.read_text(encoding="utf-8")
    assert 'default_vault = "/v"' in text
    assert 'provider = "gemini"' in text
    assert 'api_token = "wk_new"' in text


def test_write_config_token_replaces_existing(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_token = "wk_old"\n', encoding="utf-8")
    write_config_token(cfg, "wk_new")
    assert read_config_token(cfg) == "wk_new"
    assert "wk_old" not in cfg.read_text(encoding="utf-8")


def test_read_config_api(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_port = 9000\napi_bind = "127.0.0.1"\n', encoding="utf-8")
    d = read_config_api(cfg)
    assert d["api_port"] == 9000
    assert d["api_bind"] == "127.0.0.1"


def test_read_config_api_defaults_on_missing(tmp_path):
    d = read_config_api(tmp_path / "nope.toml")
    assert d == {}


def test_read_config_api_query_timeout(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('query_timeout = 900\n', encoding="utf-8")
    d = read_config_api(cfg)
    assert d["query_timeout"] == 900


def test_read_config_api_query_timeout_rejects_bool(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('query_timeout = true\n', encoding="utf-8")
    assert "query_timeout" not in read_config_api(cfg)


def test_read_config_api_query_timeout_rejects_zero(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('query_timeout = 0\n', encoding="utf-8")
    assert "query_timeout" not in read_config_api(cfg)


def test_read_config_api_query_timeout_rejects_negative(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('query_timeout = -5\n', encoding="utf-8")
    assert "query_timeout" not in read_config_api(cfg)
