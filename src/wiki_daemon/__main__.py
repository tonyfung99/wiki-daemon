"""`python -m wiki_daemon ...` delegates to the unified `wiki` CLI."""
from __future__ import annotations

from wiki_daemon.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
