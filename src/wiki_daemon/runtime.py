# src/wiki_daemon/runtime.py
"""Daemon runtime status file (status.json) + small process helpers."""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_in(seconds: float) -> str:
    """UTC ISO-8601 timestamp `seconds` in the future."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def is_pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def daemon_owns_vault(cfg) -> bool:
    """True if a live daemon process is serving this vault — i.e. status.json
    records a pid that is still alive. The daemon is the single writer of wiki/,
    so manual import/ingest must defer to it rather than ingest in-process."""
    pid = StatusFile(cfg.state_dir / "status.json").read().get("pid")
    return is_pid_alive(pid)


class IngestLockBusy(Exception):
    """Raised when another process already holds the vault ingest lock."""


@contextmanager
def vault_ingest_lock(cfg):
    """Non-blocking exclusive flock at state_dir/ingest.lock, serializing
    in-process ingest on this host. Raises IngestLockBusy if another holder has
    it. The kernel releases the lock on context exit or process death. The lock
    lives in the local state dir (never the iCloud vault) so flock is reliable;
    it does NOT coordinate across machines (see the contention design doc)."""
    lock_path = cfg.state_dir / "ingest.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise IngestLockBusy(str(lock_path)) from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


class StatusFile:
    """Atomic, merge-on-update JSON status file. Missing/corrupt reads as {}."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def update(self, **fields) -> dict:
        data = self.read()
        data.update(fields)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        return data
