from wiki_daemon.config import Config
from wiki_daemon.queue import JobQueue, Job
from wiki_daemon.daemon import drain_once, enqueue_reconcile
from wiki_daemon.state import StateStore


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
            ok = True; skipped = False; reason = ""
        return R()

    # prepare_fn injected True: the payload path doesn't exist on disk here
    drained = drain_once(cfg, q, ingest_fn=fake_ingest, prepare_fn=lambda p: True)
    assert drained == 1
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
        class R: ok = True; skipped = False; reason = ""
        return R()

    drained = drain_once(cfg, q, ingest_fn=fake_ingest, prepare_fn=lambda p: False)
    assert drained == 0           # not ingested...
    assert ingested == []
    assert q.dequeue() is None    # ...but job removed; reconcile re-enqueues later
