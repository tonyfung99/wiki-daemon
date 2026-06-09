from wiki_daemon.config import Config
from wiki_daemon.queue import JobQueue, Job
from wiki_daemon.daemon import _backoff_decision, drain_once, enqueue_reconcile, DrainResult
from wiki_daemon.state import StateStore
from wiki_daemon.runtime import StatusFile


def test_backoff_decision_transient_raises_and_delays():
    n, delay, auth = _backoff_decision(0, DrainResult(ingested=0, transient_kind="auth"))
    assert n == 1 and delay == 30 and auth == "failing"
    n2, delay2, _ = _backoff_decision(n, DrainResult(transient_kind="auth"))
    assert n2 == 2 and delay2 == 60


def test_backoff_decision_clean_drain_resets():
    # a clean/idle drain (no transient failure) resets to healthy even with 0 ingested
    assert _backoff_decision(3, DrainResult(ingested=0, transient_kind=None)) == (0, None, "ok")
    assert _backoff_decision(3, DrainResult(ingested=2, transient_kind=None)) == (0, None, "ok")


def test_enqueue_reconcile_adds_unprocessed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    (cfg.raw_sources / "a.md").write_text("aaa")
    q = JobQueue(cfg.queue_dir)
    store = StateStore(cfg.processed_json)

    enqueue_reconcile(cfg, q, store)
    job = q.dequeue()
    assert job is not None and job.payload.endswith("a.md")


def test_drain_once_runs_and_completes(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    seen = []

    def fake_ingest(config, path):
        seen.append(path)
        class R:  # minimal result
            ok = True; skipped = False; reason = ""; kind = "ok"
        return R()

    # prepare_fn injected True: the payload path doesn't exist on disk here
    drained = drain_once(cfg, q, ingest_fn=fake_ingest, prepare_fn=lambda p: True)
    assert drained.ingested == 1
    assert str(seen[0]).endswith("a.md")
    assert q.dequeue() is None  # completed and removed


def test_drain_skips_when_not_ready(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    ingested = []

    def fake_ingest(config, path):
        ingested.append(path)
        class R: ok = True; skipped = False; reason = ""; kind = "ok"
        return R()

    drained = drain_once(cfg, q, ingest_fn=fake_ingest, prepare_fn=lambda p: False)
    assert drained.ingested == 0           # not ingested...
    assert ingested == []
    assert q.dequeue() is None    # ...but job removed; reconcile re-enqueues later


def _auth_fail_ingest(config, path):
    class R:
        ok = False; skipped = False; reason = "claude failed: 401"; kind = "auth"
    return R()


def test_drain_reports_transient_auth_failure(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    status = StatusFile(cfg.state_dir / "status.json")

    res = drain_once(cfg, q, ingest_fn=_auth_fail_ingest,
                     prepare_fn=lambda p: True, status=status)

    assert res.ingested == 0
    assert res.transient_kind == "auth"
    data = status.read()
    assert data["last_error"]["kind"] == "auth"
    assert data["last_error"]["file"].endswith("a.md")


def test_drain_records_success_in_status(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    status = StatusFile(cfg.state_dir / "status.json")

    def ok_ingest(config, path):
        class R: ok = True; skipped = False; reason = ""; kind = "ok"
        return R()

    res = drain_once(cfg, q, ingest_fn=ok_ingest, prepare_fn=lambda p: True,
                     status=status)
    assert res.ingested == 1 and res.transient_kind is None
    assert "last_success" in status.read()


# --- lifecycle logging (2026-06-08) ---
import logging


def _fake_ok(config, path):
    class R:
        ok = True; skipped = False; reason = ""; kind = "ok"
    return R()


def test_drain_logs_ingesting_before_ingested(tmp_path, caplog):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    with caplog.at_level(logging.INFO, logger="wiki_daemon"):
        drain_once(cfg, q, ingest_fn=_fake_ok, prepare_fn=lambda p: True)
    msgs = [r.getMessage() for r in caplog.records]
    start = next(i for i, m in enumerate(msgs) if m.startswith("ingesting "))
    done = next(i for i, m in enumerate(msgs) if m.startswith("ingested "))
    assert start < done  # start bracket precedes the terminal line


def test_drain_logs_deferred_when_not_ready(tmp_path, caplog):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    with caplog.at_level(logging.INFO, logger="wiki_daemon"):
        drain_once(cfg, q, ingest_fn=_fake_ok, prepare_fn=lambda p: False)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("deferred" in m and "not materialized" in m for m in msgs)
    assert not any(m.startswith("ingesting ") for m in msgs)


def test_enqueue_reconcile_logs_count_when_nonzero(tmp_path, caplog):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    (cfg.raw_sources / "a.md").write_text("aaa")
    q = JobQueue(cfg.queue_dir)
    store = StateStore(cfg.processed_json)
    with caplog.at_level(logging.INFO, logger="wiki_daemon"):
        enqueue_reconcile(cfg, q, store)
    assert any("reconcile: enqueued 1" in r.getMessage() for r in caplog.records)


def test_enqueue_reconcile_silent_when_zero(tmp_path, caplog):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)  # no files
    q = JobQueue(cfg.queue_dir)
    store = StateStore(cfg.processed_json)
    with caplog.at_level(logging.INFO, logger="wiki_daemon"):
        enqueue_reconcile(cfg, q, store)
    assert not any("reconcile:" in r.getMessage() for r in caplog.records)


# --- convertible files: convert (no ingest) + enqueue the .md (2026-06-09) ---
def test_drain_converts_pdf_then_ingests_only_the_md_once(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "paper.pdf")))
    import wiki_daemon.daemon as d
    # fake conversion: write the .md the daemon should then enqueue + ingest
    def fake_normalize(cfg, path):
        md = cfg.raw_sources / "paper.md"
        md.write_text("---\ntype: source\n---\nconverted\n", encoding="utf-8")
        return md
    monkeypatch.setattr(d, "normalize_in_place", fake_normalize)
    ingested = []
    def fake_ingest(c, p):
        ingested.append(str(p))
        class R:
            ok = True; skipped = False; reason = ""; kind = "ok"
        return R()
    drained = drain_once(cfg, q, ingest_fn=fake_ingest, prepare_fn=lambda p: True)
    # the PDF itself was never ingested; only the converted .md was, exactly once
    assert not any(p.endswith("paper.pdf") for p in ingested)
    assert [p for p in ingested if p.endswith("paper.md")] == [
        str(cfg.raw_sources / "paper.md")]
    assert drained.ingested == 1


def test_drain_md_path_ingests_normally(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    drained = drain_once(cfg, q, ingest_fn=_fake_ok, prepare_fn=lambda p: True)
    assert drained.ingested == 1
