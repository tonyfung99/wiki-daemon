from wiki_daemon.queue import JobQueue, Job


def test_enqueue_dequeue_fifo(tmp_path):
    q = JobQueue(tmp_path / "queue")
    q.enqueue(Job(type="ingest", payload="a.md"))
    q.enqueue(Job(type="ingest", payload="b.md"))
    assert q.dequeue().payload == "a.md"
    assert q.dequeue().payload == "b.md"
    assert q.dequeue() is None


def test_dedupe_same_payload_pending(tmp_path):
    q = JobQueue(tmp_path / "queue")
    q.enqueue(Job(type="ingest", payload="a.md"))
    q.enqueue(Job(type="ingest", payload="a.md"))  # duplicate while pending
    assert q.dequeue().payload == "a.md"
    assert q.dequeue() is None


def test_inflight_recovered_on_reload(tmp_path):
    qdir = tmp_path / "queue"
    q = JobQueue(qdir)
    q.enqueue(Job(type="ingest", payload="a.md"))
    job = q.dequeue()        # now in-flight, not completed
    assert job.payload == "a.md"
    q2 = JobQueue(qdir)      # simulate crash + restart
    recovered = q2.dequeue()
    assert recovered is not None
    assert recovered.payload == "a.md"


def test_complete_removes_job(tmp_path):
    qdir = tmp_path / "queue"
    q = JobQueue(qdir)
    q.enqueue(Job(type="ingest", payload="a.md"))
    job = q.dequeue()
    q.complete(job)
    q2 = JobQueue(qdir)
    assert q2.dequeue() is None
