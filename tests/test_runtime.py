# tests/test_runtime.py
import os

from wiki_daemon.runtime import StatusFile, is_pid_alive, now_iso


def test_status_roundtrip(tmp_path):
    sf = StatusFile(tmp_path / "status.json")
    sf.update(pid=123, auth_state="ok")
    assert sf.read() == {"pid": 123, "auth_state": "ok"}


def test_status_update_merges(tmp_path):
    sf = StatusFile(tmp_path / "status.json")
    sf.update(pid=123)
    sf.update(auth_state="failing")
    data = sf.read()
    assert data["pid"] == 123 and data["auth_state"] == "failing"


def test_status_missing_is_empty(tmp_path):
    assert StatusFile(tmp_path / "nope.json").read() == {}


def test_status_corrupt_is_empty(tmp_path):
    p = tmp_path / "status.json"
    p.write_text("{not json", encoding="utf-8")
    assert StatusFile(p).read() == {}


def test_pid_alive_true_for_self():
    assert is_pid_alive(os.getpid()) is True


def test_pid_alive_false_for_dead():
    assert is_pid_alive(999_999) is False
    assert is_pid_alive(0) is False
    assert is_pid_alive(None) is False


def test_now_iso_format():
    s = now_iso()
    assert s.endswith("Z") and "T" in s and len(s) == 20
