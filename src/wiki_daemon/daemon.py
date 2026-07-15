"""Serve loop: FSEvents + periodic reconcile feed a serial write-worker."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from wiki_daemon.backoff import next_backoff
from wiki_daemon.config import Config
from wiki_daemon.convert import CONVERT_EXTENSIONS
from wiki_daemon.health import probe_auth
from wiki_daemon.icloud import prepare_source
from wiki_daemon.importer import normalize_in_place
from wiki_daemon.logging_setup import configure_logging
from wiki_daemon.ops import ingest as _ingest
from wiki_daemon.queue import Job, JobQueue
from wiki_daemon.runtime import StatusFile, iso_in, now_iso
from wiki_daemon.state import StateStore
from wiki_daemon.watcher import files_to_ingest, is_relevant

_log = logging.getLogger("wiki_daemon")


@dataclass
class DrainResult:
    ingested: int = 0
    transient_kind: str | None = None  # "auth"/"unavailable" if seen this drain


def _backoff_decision(consecutive: int, res: "DrainResult") -> tuple[int, int | None, str]:
    """Pure transition for the serve loop. Given the current consecutive-failure
    count and a drain result, return (new_consecutive, delay_seconds_or_None,
    auth_state). A transient (auth/unavailable) failure raises the count and
    yields a backoff delay; anything else is treated as healthy and resets."""
    if res.transient_kind:
        n = consecutive + 1
        return n, next_backoff(n), "failing"
    return 0, None, "ok"


def enqueue_reconcile(cfg: Config, q: JobQueue, store: StateStore) -> int:
    n = 0
    for p in files_to_ingest(cfg, store):
        q.enqueue(Job(type="ingest", payload=str(p)))
        n += 1
    if n:
        _log.info("reconcile: enqueued %d file(s)", n)
    return n


def drain_once(cfg: Config, q: JobQueue, *, ingest_fn=None,
               prepare_fn=prepare_source, status=None) -> DrainResult:
    """Run pending jobs serially. Materialize + stability-gate each file before
    ingest; if not ready, drop the job (reconcile re-enqueues later). Logs each
    job, records the latest success/error in `status`, and reports whether a
    transient (auth/unavailable) failure occurred so the caller can back off."""
    store = StateStore(cfg.processed_json)
    run = ingest_fn or (lambda config, path: _ingest(config, Path(path), store=store))
    result = DrainResult()
    while True:
        job = q.dequeue()
        if job is None:
            break
        if prepare_fn(Path(job.payload)):
            # A convertible document is normalized to Markdown (NOT ingested here):
            # convert -> .md + archive original, then enqueue the .md for its own
            # ingest job. Separating convert from ingest avoids double-ingest when
            # the new .md is re-seen by the watcher.
            if Path(job.payload).suffix.lower() in CONVERT_EXTENSIONS:
                try:
                    md = normalize_in_place(cfg, Path(job.payload))
                    _log.info("converted %s -> %s", job.payload, md.name)
                    q.enqueue(Job(type="ingest", payload=str(md)))
                except ValueError as exc:
                    _log.warning("convert FAILED %s — %s", job.payload, exc)
                q.complete(job)
                continue
            if status is not None:
                status.update(last_attempt=now_iso())
            _log.info("ingesting %s", job.payload)
            r = run(cfg, job.payload)
            kind = getattr(r, "kind", "") or ("ok" if r.ok else "claude_error")
            if r.ok and not r.skipped:
                result.ingested += 1
                _log.info("ingested %s", job.payload)
                if status is not None:
                    status.update(last_success=now_iso(), last_error=None,
                                  auth_state="ok")
            elif r.skipped:
                _log.info("skipped (already processed) %s", job.payload)
            else:
                _log.warning("ingest FAILED (%s) %s — %s", kind, job.payload, r.reason)
                if status is not None:
                    status.update(last_error={"msg": r.reason, "kind": kind,
                                              "file": job.payload, "at": now_iso()})
                if kind in ("auth", "unavailable"):
                    result.transient_kind = kind
        else:
            _log.info("deferred %s (not materialized/stable yet)", job.payload)
        q.complete(job)
    return result


class _Handler(FileSystemEventHandler):
    def __init__(self, cfg: Config, q: JobQueue):
        self._cfg = cfg
        self._q = q

    def _maybe(self, path_str: str) -> None:
        p = Path(path_str)
        if is_relevant(self._cfg, p):
            _log.debug("detected %s", p)
            self._q.enqueue(Job(type="ingest", payload=str(p)))

    def on_created(self, event):
        if not event.is_directory:
            self._maybe(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._maybe(event.dest_path)


def _run_setup_token(cfg: Config) -> int:
    """Launch the provider's interactive auth flow. Only Claude has a scriptable
    `setup-token`; other providers print their hint (the operator logs in)."""
    if cfg.provider == "claude":
        return subprocess.run([cfg.claude_bin, "setup-token"]).returncode
    from wiki_daemon.agent import get_provider
    _log.warning("auth recovery: %s", get_provider(cfg).auth_hint)
    return 1


def _preflight_auth(
    cfg: Config,
    *,
    probe_fn=probe_auth,
    isatty_fn=None,
    setup_token_fn=_run_setup_token,
    input_fn=input,
) -> bool:
    """Verify headless claude auth before watching. Returns True to proceed.
    Non-interactive failure -> False (caller exits non-zero). Interactive
    failure -> launch `claude setup-token`, re-probe, repeat until ok or abort."""
    from wiki_daemon.agent import get_provider
    hint = get_provider(cfg).auth_hint
    isatty_fn = isatty_fn or sys.stdin.isatty
    res = probe_fn(cfg)
    if res.state == "ok":
        _log.info("auth: ok")
        return True
    if not isatty_fn():
        _log.error("auth FAILED (%s): %s. %s, then restart. Exiting.",
                   res.state, res.detail, hint)
        return False
    while res.state != "ok":
        _log.warning("auth FAILED (%s): %s. Attempting recovery (%s)...",
                     res.state, res.detail, hint)
        setup_token_fn(cfg)
        res = probe_fn(cfg)
        if res.state == "ok":
            _log.info("auth: ok after setup-token")
            return True
        if input_fn("auth still failing — retry setup-token? [y/N] ").strip().lower() != "y":
            _log.error("auth not resolved; aborting startup.")
            return False
    return True


def serve(cfg: Config, *, reconcile_interval: float = 300.0, tick: float = 2.0,
          verbose: bool = False, no_api: bool = False,
          api_port: int | None = None, api_bind: str | None = None,
          config_path: Path | None = None) -> int:
    configure_logging(cfg, level=logging.DEBUG if verbose else logging.INFO)
    if not _preflight_auth(cfg):
        return 2
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)  # defensive: observer needs it
    status = StatusFile(cfg.state_dir / "status.json")
    status.update(pid=os.getpid(), started_at=now_iso(), auth_state="ok",
                  backoff_until=None, last_error=None)
    q = JobQueue(cfg.queue_dir)
    store = StateStore(cfg.processed_json)

    enqueue_reconcile(cfg, q, store)  # startup sweep (backstop)
    observer = Observer()
    observer.schedule(_Handler(cfg, q), str(cfg.raw_sources), recursive=False)
    observer.start()

    api_server = None
    if not no_api:
        from wiki_daemon.api import start_api_server
        from wiki_daemon.vault import read_config_api
        api_cfg = read_config_api(config_path) if config_path else {}
        host = api_bind or api_cfg.get("api_bind", "0.0.0.0")
        port = api_port or api_cfg.get("api_port", 7880)
        # query_timeout from config.toml feeds both ops.query (the agent run)
        # and the API's derived deadline/expiry (via cfg in start_api_server).
        if "query_timeout" in api_cfg:
            cfg.query_timeout = api_cfg["query_timeout"]
        api_server = start_api_server(cfg, config_path=config_path or Path(),
                                      host=host, port=port)

    _log.info("watching %s (reconcile every %ss)", cfg.raw_sources, reconcile_interval)
    last_reconcile = time.monotonic()
    consecutive = 0
    backoff_until = 0.0
    try:
        while True:
            if time.monotonic() < backoff_until:
                time.sleep(tick)
                continue
            res = drain_once(cfg, q, status=status)
            consecutive, delay, auth_state = _backoff_decision(consecutive, res)
            if delay is not None:
                backoff_until = time.monotonic() + delay
                _log.error("auth/%s failure — pausing ingest for %ss",
                           res.transient_kind, delay)
                fields = {"auth_state": auth_state, "backoff_until": iso_in(delay)}
                if consecutive == 1:  # record onset only on the first failing tick
                    fields["auth_since"] = now_iso()
                status.update(**fields)
            else:
                backoff_until = 0.0
                status.update(auth_state=auth_state, auth_since=None, backoff_until=None)
            if time.monotonic() - last_reconcile >= reconcile_interval:
                enqueue_reconcile(cfg, q, StateStore(cfg.processed_json))
                last_reconcile = time.monotonic()
            time.sleep(tick)
    finally:
        if api_server is not None:
            api_server.shutdown()
        observer.stop()
        observer.join()
        status.update(pid=None)
    return 0
