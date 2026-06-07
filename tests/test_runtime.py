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


def test_iso_in_is_future_and_formatted():
    from wiki_daemon.runtime import iso_in, now_iso
    later = iso_in(60)
    assert later.endswith("Z") and "T" in later and len(later) == 20
    assert later > now_iso()  # 60s ahead sorts after now (ISO lexical == chronological)


# --- daemon ownership detection + vault ingest lock (2026-06-07) ---
import pytest

from wiki_daemon.config import Config
from wiki_daemon.runtime import IngestLockBusy, daemon_owns_vault, vault_ingest_lock


def test_daemon_owns_vault_true_when_pid_alive(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(pid=os.getpid())
    assert daemon_owns_vault(cfg) is True


def test_daemon_owns_vault_false_no_status(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    assert daemon_owns_vault(cfg) is False


def test_daemon_owns_vault_false_dead_pid(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(pid=999_999)
    assert daemon_owns_vault(cfg) is False


def test_vault_ingest_lock_blocks_second_acquire(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    with vault_ingest_lock(cfg):
        with pytest.raises(IngestLockBusy):
            with vault_ingest_lock(cfg):
                pass


def test_vault_ingest_lock_releases_after_block(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    with vault_ingest_lock(cfg):
        pass
    # re-acquire succeeds once released
    with vault_ingest_lock(cfg):
        pass
