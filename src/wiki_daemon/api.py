"""HTTP API server for WikiReader and external clients."""
from __future__ import annotations

import hmac
import json as _json
import logging
import re
import secrets
import threading
import time as _time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wiki_daemon import __version__
from wiki_daemon.config import Config
from wiki_daemon.ops import QueryResult
from wiki_daemon.ops import query as _ops_query

_log = logging.getLogger("wiki_daemon.api")

_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\|]+?)(?:\|([^\[\]]+?))?\]\]")

# Server-side deadline: once a query job has been "running" longer than this,
# GET reports it as a failed timeout instead of an eternal "running" (defense
# against a wedged worker that never calls JobStore.complete()). Must sit
# strictly between the agent subprocess timeout (300s) and the JobStore expiry
# (600s): > 300 so a normal slow query is never failed early, < 600 so the
# client receives this failed response before the job is evicted (which would
# otherwise surface a confusing 404).
QUERY_RUNNING_DEADLINE_SECONDS = 420.0


def extract_citations(markdown: str) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    for m in _WIKI_LINK_RE.finditer(markdown):
        link = m.group(1).strip()
        alias = (m.group(2) or "").strip()
        if not link:
            continue
        if link in seen:
            continue
        seen.add(link)
        results.append({"wikiLink": link, "title": alias or link})
    return results


@dataclass
class QueryJob:
    job_id: str
    question: str
    save: bool
    status: str = "running"  # running | done | failed
    result: QueryResult | None = None
    started_at: str = ""
    completed_at: str = ""
    created: float = field(default_factory=_time.monotonic)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_job_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = secrets.token_hex(2)
    return f"qry_{ts}_{suffix}"


class JobStore:
    def __init__(self, *, max_jobs: int = 50, expiry_seconds: float = 600.0):
        self._lock = threading.Lock()
        self._jobs: dict[str, QueryJob] = {}
        self._max = max_jobs
        self._expiry = expiry_seconds

    def create(self, question: str, *, save: bool) -> str:
        job_id = _make_job_id()
        job = QueryJob(job_id=job_id, question=question, save=save,
                       started_at=_now_iso())
        with self._lock:
            self._evict_expired()
            while len(self._jobs) >= self._max:
                oldest = min(self._jobs, key=lambda k: self._jobs[k].created)
                del self._jobs[oldest]
            self._jobs[job_id] = job
        return job_id

    def get(self, job_id: str) -> QueryJob | None:
        with self._lock:
            self._evict_expired()
            return self._jobs.get(job_id)

    def complete(self, job_id: str, result: QueryResult) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.result = result
            job.status = "done" if result.ok else "failed"
            job.completed_at = _now_iso()

    def _evict_expired(self) -> None:
        now = _time.monotonic()
        expired = [k for k, v in self._jobs.items()
                   if now - v.created > self._expiry]
        for k in expired:
            del self._jobs[k]


# ---------------------------------------------------------------------------
# Token helper
# ---------------------------------------------------------------------------

def _read_token(config_path: Path) -> str | None:
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    v = data.get("api_token")
    return v if isinstance(v, str) and v.strip() else None


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class ApiHandler(BaseHTTPRequestHandler):
    cfg: Config
    config_path: Path
    job_store: JobStore
    query_fn: object  # callable(cfg, question, *, save) -> QueryResult

    def log_message(self, format, *args):  # noqa: A002
        _log.info(format, *args)

    # --- JSON helpers ---

    def _send_json(self, status: int, body: dict) -> None:
        data = _json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, code: str, message: str,
                    *, retryable: bool = False,
                    details: dict | None = None) -> None:
        body: dict = {
            "schemaVersion": 1,
            "error": {"code": code, "message": message, "retryable": retryable},
        }
        if details:
            body["error"]["details"] = details
        self._send_json(status, body)

    # --- Auth ---

    def _check_auth(self) -> bool:
        token = _read_token(self.config_path)
        if token is None:
            self._send_error(401, "unauthorized",
                             "API token not configured on the daemon.")
            return False
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_error(401, "unauthorized", "Missing bearer token.")
            return False
        provided = auth[7:]
        if not hmac.compare_digest(provided, token):
            self._send_error(401, "unauthorized", "Invalid token.")
            return False
        return True

    # --- Body reading ---

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return _json.loads(self.rfile.read(length))
        except (_json.JSONDecodeError, ValueError):
            self._send_error(400, "bad_request", "Invalid JSON.")
            return None

    # --- Routing ---

    def do_GET(self):  # noqa: N802
        path = self.path.rstrip("/")
        if path == "/api/v1/health":
            return self._handle_health()
        if path.startswith("/api/v1/query/"):
            job_id = path[len("/api/v1/query/"):]
            if not self._check_auth():
                return
            return self._handle_query_get(job_id)
        self._send_error(404, "not_found", "Not found.")

    def do_POST(self):  # noqa: N802
        path = self.path.rstrip("/")
        if path == "/api/v1/query":
            if not self._check_auth():
                return
            return self._handle_query_post()
        self._send_error(404, "not_found", "Not found.")

    # --- Handlers ---

    def _handle_health(self) -> None:
        self._send_json(200, {
            "schemaVersion": 1,
            "status": "ok",
            "daemonVersion": __version__,
            "vaultName": self.cfg.vault.name,
            "queryAvailable": True,
            "provider": self.cfg.provider,
        })

    def _handle_query_post(self) -> None:
        body = self._read_body()
        if body is None:
            return
        question = body.get("question", "")
        if not isinstance(question, str) or not question.strip():
            self._send_error(400, "bad_request", "Missing or empty question.")
            return
        save = body.get("save", True)
        job_id = self.job_store.create(question.strip(), save=bool(save))
        # Run query in a background thread
        t = threading.Thread(target=self._run_query,
                             args=(job_id, question.strip(), bool(save)),
                             daemon=True)
        t.start()
        self._send_json(202, {
            "schemaVersion": 1,
            "jobId": job_id,
            "status": "queued",
        })

    def _run_query(self, job_id: str, question: str, save: bool) -> None:
        try:
            result = self.query_fn(self.cfg, question, save=save)
        except Exception as exc:
            _log.exception("query job %s failed with exception", job_id)
            result = QueryResult(ok=False, kind="error",
                                 reason=f"internal error: {exc}")
        self.job_store.complete(job_id, result)

    def _handle_query_get(self, job_id: str) -> None:
        job = self.job_store.get(job_id)
        if job is None:
            self._send_error(404, "not_found", f"Job {job_id} not found.")
            return
        body: dict = {
            "schemaVersion": 1,
            "jobId": job.job_id,
            "status": job.status,
        }
        if job.status == "running":
            if _time.monotonic() - job.created > QUERY_RUNNING_DEADLINE_SECONDS:
                # Wedged worker: synthesize a terminal failed/timeout response
                # so the client stops polling. The stored job is left as-is; if
                # the worker ever completes, a later GET reflects its real
                # result.
                body["status"] = "failed"
                body["ok"] = False
                body["error"] = {
                    "code": "provider_failed",
                    "message": "Query exceeded the time limit and was abandoned.",
                    "retryable": True,
                    "details": {"kind": "timeout", "provider": self.cfg.provider},
                }
                self._send_json(200, body)
                return
            self._send_json(200, body)
            return
        if job.status == "done":
            r = job.result
            body["ok"] = True
            body["answerMarkdown"] = r.answer
            body["saved"] = r.saved
            body["saveError"] = r.reason if (r.reason and not r.saved) else None
            body["citations"] = extract_citations(r.answer)
            body["provider"] = self.cfg.provider
            body["startedAt"] = job.started_at
            body["completedAt"] = job.completed_at
            self._send_json(200, body)
            return
        # failed
        r = job.result
        body["ok"] = False
        body["error"] = {
            "code": "provider_failed",
            "message": r.reason if r else "Unknown failure.",
            "retryable": r.kind in ("auth", "quota", "unavailable") if r else False,
            "details": {"kind": r.kind if r else "error",
                        "provider": self.cfg.provider},
        }
        self._send_json(200, body)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def start_api_server(cfg: Config, *, config_path: Path,
                     host: str = "0.0.0.0", port: int = 7880,
                     query_fn=None) -> HTTPServer:
    """Create and start the API server in a daemon thread.

    Returns the server instance (call server.shutdown() to stop).
    """
    store = JobStore()
    qfn = query_fn or _ops_query

    class Handler(ApiHandler):
        pass

    Handler.cfg = cfg
    Handler.config_path = config_path
    Handler.job_store = store
    Handler.query_fn = staticmethod(qfn)

    server = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    _log.info("api: listening on %s:%d", host, server.server_address[1])
    return server
