"""File-backed serial job queue with crash recovery.

Each job is a JSON file in the queue dir. Status is encoded in the filename
prefix: `pending-` or `inflight-`. On reload, inflight jobs are re-pending so a
crash mid-job re-runs it (ingest is idempotent). Ordering is by enqueue index.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Job:
    type: str
    payload: str


class JobQueue:
    def __init__(self, queue_dir: Path):
        self._dir = Path(queue_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._seq = self._max_seq()
        self._recover()

    def _max_seq(self) -> int:
        best = 0
        for f in self._dir.glob("*.json"):
            try:
                best = max(best, int(f.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
        return best

    def _recover(self) -> None:
        for f in self._dir.glob("inflight-*.json"):
            f.rename(f.with_name(f.name.replace("inflight-", "pending-", 1)))

    def _pending_payloads(self) -> set[str]:
        out = set()
        for f in self._dir.glob("pending-*.json"):
            out.add(json.loads(f.read_text())["payload"])
        return out

    def enqueue(self, job: Job) -> None:
        if job.payload in self._pending_payloads():
            return  # dedupe identical pending payloads
        self._seq += 1
        name = f"pending-{self._seq:08d}-{job.type}.json"
        (self._dir / name).write_text(json.dumps(asdict(job)), encoding="utf-8")

    def dequeue(self) -> Job | None:
        pend = sorted(self._dir.glob("pending-*.json"))
        if not pend:
            return None
        f = pend[0]
        inflight = f.with_name(f.name.replace("pending-", "inflight-", 1))
        f.rename(inflight)
        data = json.loads(inflight.read_text())
        return Job(**data)

    def complete(self, job: Job) -> None:
        for f in self._dir.glob("inflight-*.json"):
            if json.loads(f.read_text())["payload"] == job.payload:
                f.unlink()
                return
