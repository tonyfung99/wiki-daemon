"""Serve loop: FSEvents + periodic reconcile feed a serial write-worker."""
from __future__ import annotations

import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from wiki_daemon.config import Config
from wiki_daemon.icloud import prepare_source
from wiki_daemon.ops import ingest as _ingest
from wiki_daemon.queue import Job, JobQueue
from wiki_daemon.state import StateStore
from wiki_daemon.watcher import files_to_ingest, is_relevant


def enqueue_reconcile(cfg: Config, q: JobQueue, store: StateStore) -> int:
    n = 0
    for p in files_to_ingest(cfg, store):
        q.enqueue(Job(type="ingest", payload=str(p)))
        n += 1
    return n


def drain_once(cfg: Config, q: JobQueue, *, ingest_fn=None, prepare_fn=prepare_source) -> int:
    """Run pending jobs serially. Materialize + stability-gate each file before
    ingest; if not ready, drop the job (the reconcile sweep re-enqueues later).
    Returns the number of files actually ingested."""
    store = StateStore(cfg.processed_json)
    run = ingest_fn or (lambda config, path: _ingest(config, Path(path), store=store))
    count = 0
    while True:
        job = q.dequeue()
        if job is None:
            break
        if prepare_fn(Path(job.payload)):
            run(cfg, job.payload)
            count += 1
        q.complete(job)
    return count


class _Handler(FileSystemEventHandler):
    def __init__(self, cfg: Config, q: JobQueue):
        self._cfg = cfg
        self._q = q

    def _maybe(self, path_str: str) -> None:
        p = Path(path_str)
        if is_relevant(self._cfg, p):
            self._q.enqueue(Job(type="ingest", payload=str(p)))

    def on_created(self, event):
        if not event.is_directory:
            self._maybe(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._maybe(event.dest_path)


def serve(cfg: Config, *, reconcile_interval: float = 300.0, tick: float = 2.0) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)  # defensive: observer needs it
    q = JobQueue(cfg.queue_dir)
    store = StateStore(cfg.processed_json)

    enqueue_reconcile(cfg, q, store)  # startup sweep (backstop)
    observer = Observer()
    observer.schedule(_Handler(cfg, q), str(cfg.raw_sources), recursive=False)
    observer.start()
    last_reconcile = time.monotonic()
    try:
        while True:
            drain_once(cfg, q)
            if time.monotonic() - last_reconcile >= reconcile_interval:
                enqueue_reconcile(cfg, q, StateStore(cfg.processed_json))
                last_reconcile = time.monotonic()
            time.sleep(tick)
    finally:
        observer.stop()
        observer.join()
