"""Tests for progress.source_state — deriving a source's ingest lifecycle state
from existing files (queue, processed.json, status.json). No claude/network."""
import json

from wiki_daemon.config import Config
from wiki_daemon.progress import SourceState, source_state
from wiki_daemon.runtime import StatusFile
from wiki_daemon.sources import read_source
from wiki_daemon.state import StateStore


def _cfg(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    return cfg


def _src(cfg, name="a.md"):
    p = cfg.raw_sources / name
    p.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")
    return p


def _abs(p):
    return str(p.resolve())


def test_untracked_when_nothing_known(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    assert source_state(cfg, src).state == "untracked"


def test_queued_when_pending_job_present(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / "pending-00000001-ingest.json").write_text(
        json.dumps({"type": "ingest", "payload": _abs(src)}), encoding="utf-8")
    assert source_state(cfg, src).state == "queued"


def test_ingesting_when_inflight_job_present(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / "inflight-00000001-ingest.json").write_text(
        json.dumps({"type": "ingest", "payload": _abs(src)}), encoding="utf-8")
    assert source_state(cfg, src).state == "ingesting"


def test_processed_when_hash_recorded(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    store = StateStore(cfg.processed_json)
    store.mark_processed(read_source(src).sha256, _abs(src))
    assert source_state(cfg, src).state == "processed"


def test_failed_when_last_error_matches(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    StatusFile(cfg.state_dir / "status.json").update(
        last_error={"msg": "claude failed: 401", "kind": "auth",
                    "file": _abs(src), "at": "2026-06-08T00:00:00Z"})
    st = source_state(cfg, src)
    assert st.state == "failed"
    assert "401" in st.detail


def test_processed_precedence_over_pending(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    StateStore(cfg.processed_json).mark_processed(read_source(src).sha256, _abs(src))
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / "pending-00000001-ingest.json").write_text(
        json.dumps({"type": "ingest", "payload": _abs(src)}), encoding="utf-8")
    assert source_state(cfg, src).state == "processed"


def test_accepts_vault_relative_path(tmp_path):
    cfg = _cfg(tmp_path)
    src = _src(cfg)
    StateStore(cfg.processed_json).mark_processed(read_source(src).sha256, _abs(src))
    rel = src.resolve().relative_to(cfg.vault).as_posix()  # raw/sources/a.md
    assert source_state(cfg, rel).state == "processed"


def test_returns_sourcestate_instance(tmp_path):
    cfg = _cfg(tmp_path)
    assert isinstance(source_state(cfg, _src(cfg)), SourceState)
