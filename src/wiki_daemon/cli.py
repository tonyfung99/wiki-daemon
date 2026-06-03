"""`wiki` CLI. In M1 this runs ops in-process (no daemon)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_daemon.config import Config
from wiki_daemon.importer import import_source
from wiki_daemon.ops import apply_clarification, ingest, ingest_interactive
from wiki_daemon.review import list_items, write_answer
from wiki_daemon.runtime import StatusFile, is_pid_alive
from wiki_daemon.scaffold import init_vault
from wiki_daemon.state import StateStore


def _add_interactive_flags(parser: argparse.ArgumentParser) -> None:
    """Tri-state --interactive/--no-interactive: unset (None) auto-detects a TTY."""
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--interactive", dest="interactive", action="store_true",
                   default=None, help="ask clarifications live (default if a TTY)")
    g.add_argument("--no-interactive", dest="interactive", action="store_false",
                   help="headless: queue clarifications to wiki/review/")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wiki")
    sub = p.add_subparsers(dest="command", required=True)

    # --vault is shared by every subcommand (e.g. `wiki init --vault <path>`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="path to the vault", default=None)

    sub.add_parser("init", parents=[common], help="scaffold a new vault")
    ing = sub.add_parser("ingest", parents=[common], help="ingest one source file")
    ing.add_argument("file", help="path to a raw source .md")
    _add_interactive_flags(ing)
    imp = sub.add_parser("import", parents=[common],
                         help="copy a file into the vault and ingest it")
    imp.add_argument("file", help="path to any UTF-8 text file to import")
    _add_interactive_flags(imp)
    sub.add_parser("status", parents=[common], help="show processed count")
    doc = sub.add_parser("doctor", parents=[common],
                         help="validate iCloud + tooling on the daemon host")
    doc.add_argument("--probe", default=None,
                     help="path to an already-evicted file to test materialization")
    srv = sub.add_parser("serve", parents=[common],
                         help="run the daemon: watch raw/sources and ingest autonomously")
    srv.add_argument("--reconcile-interval", type=float, default=300.0)

    rev = sub.add_parser("review", parents=[common],
                         help="list/answer ingest clarifications")
    rev_sub = rev.add_subparsers(dest="review_cmd")
    ans = rev_sub.add_parser("answer", parents=[common],
                             help="answer a clarification and apply it")
    ans.add_argument("id", help="review item id (filename without .md)")
    ans.add_argument("text", help="your answer")
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


def _want_interactive(flag: bool | None) -> bool:
    """Resolve the tri-state flag: explicit wins, else auto-detect a TTY."""
    return sys.stdin.isatty() if flag is None else flag


def cmd_ingest(cfg: Config, file: str, *, interactive: bool | None = None) -> int:
    store = StateStore(cfg.processed_json)
    if _want_interactive(interactive):
        result = ingest_interactive(cfg, Path(file), store=store)
    else:
        result = ingest(cfg, Path(file), store=store)
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1


def cmd_import(cfg: Config, file: str, *, interactive: bool | None = None) -> int:
    try:
        dest = import_source(cfg, Path(file))
    except (FileNotFoundError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported {dest.name}")

    store = StateStore(cfg.processed_json)
    if _want_interactive(interactive):
        result = ingest_interactive(cfg, dest, store=store)
    else:
        result = ingest(cfg, dest, store=store)
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1


def _render_status(cfg: Config) -> str:
    status = StatusFile(cfg.state_dir / "status.json").read()

    pid = status.get("pid")
    if pid and is_pid_alive(pid):
        since = status.get("started_at", "?")
        daemon = f"running (pid {pid}, since {since})"
    elif pid:
        daemon = "not running (stale pid)"
    else:
        daemon = "not running"

    if status.get("auth_state") == "failing":
        err = status.get("last_error") or {}
        kind = err.get("kind", "auth")
        since = status.get("auth_since", "?")
        auth = f"FAILING since {since} ({kind}) — run `claude setup-token`"
    elif status.get("auth_state") == "ok":
        auth = "ok"
    else:
        auth = "unknown"

    qdir = cfg.queue_dir
    pending = len(list(qdir.glob("pending-*.json"))) if qdir.exists() else 0
    inflight = sorted(qdir.glob("inflight-*.json")) if qdir.exists() else []
    if inflight:
        try:
            payload = json.loads(inflight[0].read_text(encoding="utf-8"))["payload"]
        except (json.JSONDecodeError, KeyError, OSError):
            payload = "?"
        ingesting = f", 1 ingesting ({payload})"
    else:
        ingesting = ""

    store = StateStore(cfg.processed_json)
    processed = len(store._data)  # noqa: SLF001

    # Counts all review files. Answered items are transient (deleted on a
    # successful apply), so a lingering one means apply failed — still "needs
    # attention", fairly shown as open.
    review_open = len(list(cfg.review.glob("*.md"))) if cfg.review.is_dir() else 0

    lines = [
        f"daemon:     {daemon}",
        f"auth:       {auth}",
        f"queue:      {pending} pending{ingesting}",
        f"processed:  {processed} sources",
        f"review:     {review_open} open",
    ]
    if status.get("last_error"):
        e = status["last_error"]
        lines.append(f"last error: [{e.get('at','?')}] {e.get('msg','?')} "
                     f"({e.get('file','?')})")
    return "\n".join(lines)


def cmd_status(cfg: Config) -> int:
    print(_render_status(cfg))
    return 0


def _render_review(cfg: Config) -> str:
    items = list_items(cfg)
    if not items:
        return "no open clarifications"
    lines = []
    for it in items:
        lines.append(f"{it.id}  [{it.status}]  {it.source}\n    {it.question}")
    return "\n".join(lines)


def cmd_review_list(cfg: Config) -> int:
    print(_render_review(cfg))
    return 0


def cmd_review_answer(cfg: Config, item_id: str, text: str) -> int:
    try:
        write_answer(cfg, item_id, text)
    except FileNotFoundError as exc:
        print(f"review answer failed: {exc}", file=sys.stderr)
        return 1
    result = apply_clarification(cfg, item_id)
    if result.ok:
        print(f"resolved {item_id}")
        return 0
    print(f"apply failed: {result.reason}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    cfg = _config(ns)
    if ns.command == "init":
        return cmd_init(cfg)
    if ns.command == "ingest":
        return cmd_ingest(cfg, ns.file, interactive=ns.interactive)
    if ns.command == "import":
        return cmd_import(cfg, ns.file, interactive=ns.interactive)
    if ns.command == "status":
        return cmd_status(cfg)
    if ns.command == "doctor":
        from wiki_daemon.doctor import run_doctor
        return run_doctor(cfg, probe=Path(ns.probe) if ns.probe else None)
    if ns.command == "serve":
        # Lazy import: keeps watchdog/FSEvents out of the manual commands.
        from wiki_daemon.daemon import serve
        return serve(cfg, reconcile_interval=ns.reconcile_interval)
    if ns.command == "review":
        if ns.review_cmd == "answer":
            return cmd_review_answer(cfg, ns.id, ns.text)
        return cmd_review_list(cfg)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
