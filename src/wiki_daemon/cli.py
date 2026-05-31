"""`wiki` CLI. In M1 this runs ops in-process (no daemon)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.ops import ingest
from wiki_daemon.scaffold import init_vault
from wiki_daemon.state import StateStore


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wiki")
    sub = p.add_subparsers(dest="command", required=True)

    # --vault is shared by every subcommand (e.g. `wiki init --vault <path>`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="path to the vault", default=None)

    sub.add_parser("init", parents=[common], help="scaffold a new vault")
    ing = sub.add_parser("ingest", parents=[common], help="ingest one source file")
    ing.add_argument("file", help="path to a raw source .md")
    sub.add_parser("status", parents=[common], help="show processed count")
    return p


def _config(ns) -> Config:
    if not ns.vault:
        print("error: --vault is required", file=sys.stderr)
        raise SystemExit(2)
    return Config(vault=Path(ns.vault))


def cmd_init(cfg: Config) -> int:
    init_vault(cfg)
    print(f"initialized vault at {cfg.vault}")
    return 0


def cmd_ingest(cfg: Config, file: str) -> int:
    store = StateStore(cfg.processed_json)
    result = ingest(cfg, Path(file), store=store)
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1


def cmd_status(cfg: Config) -> int:
    store = StateStore(cfg.processed_json)
    print(f"processed: {len(store._data)}")  # noqa: SLF001
    return 0


def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    cfg = _config(ns)
    if ns.command == "init":
        return cmd_init(cfg)
    if ns.command == "ingest":
        return cmd_ingest(cfg, ns.file)
    if ns.command == "status":
        return cmd_status(cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
