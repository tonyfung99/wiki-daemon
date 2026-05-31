"""iCloud Drive handling for macOS Sequoia (Intel x86_64 host).

Not-downloaded files are "dataless" APFS files (no .icloud stubs on Sonoma+).
Detect via SF_DATALESS in st_flags; materialize via `brctl download`
(fallback `fileproviderctl materialize`). All side effects are injectable.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

SF_DATALESS = 0x40000000  # APFS dataless flag (Apple TN3150)

StatFn = Callable[[Path], os.stat_result]
RunFn = Callable[[list[str]], int]
SleepFn = Callable[[float], None]


def _default_run(cmd: list[str]) -> int:
    return subprocess.run(cmd, capture_output=True, text=True).returncode


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
