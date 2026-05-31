# tests/test_state.py
from wiki_daemon.state import StateStore


def test_unprocessed_then_processed(tmp_path):
    store = StateStore(tmp_path / "processed.json")
    assert store.is_processed("abc") is False
    store.mark_processed("abc", "raw/sources/x.md")
    assert store.is_processed("abc") is True


def test_persists_across_reload(tmp_path):
    path = tmp_path / "processed.json"
    StateStore(path).mark_processed("deadbeef", "raw/sources/y.md")
    reloaded = StateStore(path)
    assert reloaded.is_processed("deadbeef") is True


def test_atomic_write_no_temp_left_behind(tmp_path):
    path = tmp_path / "processed.json"
    StateStore(path).mark_processed("abc", "raw/sources/x.md")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []
