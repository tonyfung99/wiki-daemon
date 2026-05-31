"""`wiki-daemon serve --vault <path>` entrypoint."""
from __future__ import annotations

import argparse
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.daemon import serve


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="wiki-daemon")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve", help="watch the vault and ingest autonomously")
    s.add_argument("--vault", required=True)
    s.add_argument("--reconcile-interval", type=float, default=300.0)
    ns = p.parse_args(argv)
    cfg = Config(vault=Path(ns.vault))
    serve(cfg, reconcile_interval=ns.reconcile_interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
