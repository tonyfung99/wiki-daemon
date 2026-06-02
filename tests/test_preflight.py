# tests/test_preflight.py
from wiki_daemon.config import Config
from wiki_daemon.daemon import _preflight_auth
from wiki_daemon.health import AuthResult


def _cfg(tmp_path):
    return Config(vault=tmp_path / "v", state_root=tmp_path / "s")


def test_preflight_ok_proceeds(tmp_path):
    calls = {"setup": 0}
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: AuthResult("ok", "authenticated"),
        isatty_fn=lambda: True,
        setup_token_fn=lambda cfg: calls.__setitem__("setup", calls["setup"] + 1),
    )
    assert ok is True and calls["setup"] == 0


def test_preflight_non_tty_failure_returns_false_without_setup(tmp_path):
    calls = {"setup": 0}
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: AuthResult("auth_failed", "401"),
        isatty_fn=lambda: False,
        setup_token_fn=lambda cfg: calls.__setitem__("setup", calls["setup"] + 1),
    )
    assert ok is False and calls["setup"] == 0


def test_preflight_tty_recovers_after_setup(tmp_path):
    seq = iter([AuthResult("auth_failed", "401"), AuthResult("ok", "authenticated")])
    calls = {"setup": 0}
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: next(seq),
        isatty_fn=lambda: True,
        setup_token_fn=lambda cfg: calls.__setitem__("setup", calls["setup"] + 1),
    )
    assert ok is True and calls["setup"] == 1


def test_preflight_tty_user_aborts(tmp_path):
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: AuthResult("auth_failed", "401"),
        isatty_fn=lambda: True,
        setup_token_fn=lambda cfg: None,
        input_fn=lambda prompt: "n",
    )
    assert ok is False
