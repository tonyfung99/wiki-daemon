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
