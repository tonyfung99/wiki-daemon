# tests/test_health.py
from wiki_daemon.config import Config
from wiki_daemon.health import probe_auth, AuthResult


def _cfg(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.vault.mkdir(parents=True, exist_ok=True)
    return cfg


def test_probe_ok(tmp_path):
    cfg = _cfg(tmp_path)
    res = probe_auth(cfg, runner=lambda cmd, cwd, timeout: (0, "ok", ""))
    assert isinstance(res, AuthResult)
    assert res.state == "ok"


def test_probe_auth_failed(tmp_path):
    cfg = _cfg(tmp_path)
    res = probe_auth(cfg, runner=lambda cmd, cwd, timeout:
                     (1, "", "API Error: 401 Invalid authentication credentials"))
    assert res.state == "auth_failed"
    assert "401" in res.detail


def test_probe_unavailable_on_missing_binary(tmp_path):
    cfg = _cfg(tmp_path)
    def boom(cmd, cwd, timeout):
        raise FileNotFoundError("claude")
    res = probe_auth(cfg, runner=boom)
    assert res.state == "unavailable"


def test_probe_unavailable_on_generic_error(tmp_path):
    cfg = _cfg(tmp_path)
    res = probe_auth(cfg, runner=lambda cmd, cwd, timeout: (1, "", "weird boom"))
    assert res.state == "unavailable"
