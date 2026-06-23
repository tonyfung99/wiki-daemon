"""HTTP API server for WikiReader and external clients."""
from __future__ import annotations

import re
import secrets
import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from wiki_daemon.ops import QueryResult

_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]\|]+?)(?:\|([^\[\]]+?))?\]\]")


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
