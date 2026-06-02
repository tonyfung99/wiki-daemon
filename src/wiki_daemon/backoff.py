# src/wiki_daemon/backoff.py
"""Pure exponential-backoff schedule shared by the daemon's pause logic."""
from __future__ import annotations


def next_backoff(consecutive_failures: int, *, base: int = 30, factor: int = 2,
                 cap: int = 900) -> int:
    """Seconds to wait after `consecutive_failures` in a row. n<=1 -> base;
    doubles each step; never exceeds `cap`."""
    n = max(1, consecutive_failures)
    return min(cap, base * factor ** (n - 1))
