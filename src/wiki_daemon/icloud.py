"""iCloud Drive handling for macOS Sequoia (Intel x86_64 host).

Not-downloaded files are "dataless" APFS files (no .icloud stubs on Sonoma+).
Detect via SF_DATALESS in st_flags; materialize via `brctl download`
(fallback `fileproviderctl materialize`). All side effects are injectable.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

SF_DATALESS = 0x40000000  # APFS dataless flag (Apple TN3150)

# A stuck brctl/fileproviderctl must never wedge the daemon.
_DOWNLOAD_TIMEOUT = 120  # seconds

StatFn = Callable[[Path], os.stat_result]
RunFn = Callable[[list[str]], int]
SleepFn = Callable[[float], None]
# walk(root) -> iterable of (dirpath, dirnames, filenames), like os.walk
WalkFn = Callable[[Path], Iterable[tuple[str, list[str], list[str]]]]


def _default_run(cmd: list[str], *, timeout: float = _DOWNLOAD_TIMEOUT) -> int:
    # Bounded: a hung materialize command returns nonzero instead of blocking.
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).returncode
    except subprocess.TimeoutExpired:
        return 1


def is_dataless(path: Path, *, stat_fn: StatFn = os.stat) -> bool:
    st = stat_fn(path)
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def ensure_materialized(
    path: Path,
    *,
    stat_fn: StatFn = os.stat,
    run_fn: RunFn = _default_run,
    sleep_fn: SleepFn = time.sleep,
    max_polls: int = 30,
    interval: float = 0.5,
) -> None:
    path = Path(path)
    if not is_dataless(path, stat_fn=stat_fn):
        return
    if run_fn(["brctl", "download", str(path)]) != 0:
        run_fn(["fileproviderctl", "materialize", str(path)])
    for _ in range(max_polls):
        if not is_dataless(path, stat_fn=stat_fn):
            return
        sleep_fn(interval)
    # fall through: caller will read; a still-dataless read will raise on access


def wait_stable(
    path: Path,
    *,
    window_checks: int = 2,
    interval: float = 1.0,
    max_checks: int = 20,
    stat_fn: StatFn = os.stat,
    sleep_fn: SleepFn = time.sleep,
) -> bool:
    """True once size+mtime are unchanged for `window_checks` consecutive reads."""
    last = None
    stable_run = 0
    for _ in range(max_checks):
        st = stat_fn(path)
        sig = (st.st_size, st.st_mtime)
        if sig == last:
            stable_run += 1
            if stable_run >= window_checks:
                return True
        else:
            stable_run = 0
            last = sig
        sleep_fn(interval)
    return False


def prepare_source(
    path: Path,
    *,
    stat_fn: StatFn = os.stat,
    run_fn: RunFn = _default_run,
    sleep_fn: SleepFn = time.sleep,
) -> bool:
    """Make a raw source ready to read: materialize, then confirm it's stable.

    Returns True when the file is materialized and stable; False if it never
    settled (caller leaves it for the next reconcile sweep).
    """
    ensure_materialized(path, stat_fn=stat_fn, run_fn=run_fn, sleep_fn=sleep_fn)
    return wait_stable(path, stat_fn=stat_fn, sleep_fn=sleep_fn)


@dataclass
class MaterializeReport:
    scanned: int          # files walked on the initial scan
    materialized: int     # dataless files that became materialized
    still_dataless: int   # files left dataless when we gave up
    timed_out: bool       # True if we hit the poll bound with files remaining


def _count_dataless(root: Path, stat_fn: StatFn, walk_fn: WalkFn) -> tuple[int, int]:
    """Return (files_scanned, dataless_count) over the tree under `root`."""
    scanned = dataless = 0
    for dirpath, _dirnames, filenames in walk_fn(root):
        for name in filenames:
            scanned += 1
            try:
                if is_dataless(Path(dirpath) / name, stat_fn=stat_fn):
                    dataless += 1
            except OSError:
                # broken symlink / unreadable entry: not ours to materialize
                continue
    return scanned, dataless


def ensure_tree_materialized(
    root: Path,
    *,
    stat_fn: StatFn = os.stat,
    walk_fn: WalkFn = os.walk,
    run_fn: RunFn = _default_run,
    sleep_fn: SleepFn = time.sleep,
    max_polls: int = 30,
    interval: float = 0.5,
) -> MaterializeReport:
    """Materialize every dataless file under `root` before an agent scans it.

    Agent CLIs (e.g. `codex exec`) scan their working directory on startup;
    reading an iCloud-evicted (dataless) file EINTRs and aborts the run. This
    triggers a single recursive download of `root` and polls until the tree is
    materialized.

    Strictly bounded: at most one download command plus `max_polls` sleeps of
    `interval`, so it can never hang. No-op when nothing is dataless (and thus a
    no-op on non-macOS, where `st_flags` lacks SF_DATALESS). Best-effort: if the
    poll bound is hit with files still dataless it returns `timed_out=True`
    rather than raising — the caller logs and proceeds. `run_fn` must be bounded
    (the default `_default_run` applies a timeout).
    """
    root = Path(root)
    scanned, dataless = _count_dataless(root, stat_fn, walk_fn)
    if dataless == 0:
        return MaterializeReport(scanned, 0, 0, False)

    # Recursive download of the whole tree in one shot; fall back if brctl fails.
    if run_fn(["brctl", "download", str(root)]) != 0:
        run_fn(["fileproviderctl", "materialize", str(root)])

    remaining = dataless
    timed_out = True
    for _ in range(max_polls):
        _scanned, remaining = _count_dataless(root, stat_fn, walk_fn)
        if remaining == 0:
            timed_out = False
            break
        sleep_fn(interval)

    return MaterializeReport(scanned, dataless - remaining, remaining, timed_out)
