"""`wiki` CLI. In M1 this runs ops in-process (no daemon)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from wiki_daemon import __version__
from wiki_daemon import lint as lintmod
from wiki_daemon.config import Config
from wiki_daemon.importer import import_source
from wiki_daemon.ops import apply_clarification, ingest, ingest_interactive, query
from wiki_daemon.ops import lint_deep, lint_repair
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
    p.add_argument("--version", action="version", version=f"wiki {__version__}")
    sub = p.add_subparsers(dest="command")

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

    qry = sub.add_parser("query", parents=[common],
                         help="ask the wiki a question (read-only; --save files the answer)")
    qry.add_argument("question", help="the question to answer from the wiki")
    qry.add_argument("--save", action="store_true",
                     help="also file the answer as a wiki/queries/ page")

    lnt = sub.add_parser("lint", parents=[common],
                         help="health-check the wiki (--deep LLM scan, --fix repair)")
    lnt.add_argument("--deep", action="store_true",
                     help="also run an LLM semantic scan (contradictions, stale claims)")
    lnt.add_argument("--fix", action="store_true",
                     help="repair findings (deletes conflict-dupes + LLM repair pass)")
    lnt.add_argument("--yes", action="store_true",
                     help="skip the confirmation prompt for --fix")
    return p


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "wiki" / "config.toml"


def _config(ns) -> Config:
    # `init` CREATES a vault — target an explicit path or the current directory.
    if ns.command == "init":
        return Config(vault=Path(ns.vault) if ns.vault else Path.cwd())
    # every other command DISCOVERS an existing vault via the resolution chain.
    from wiki_daemon.vault import VaultNotFound, resolve_vault
    try:
        vault = resolve_vault(ns.vault, env=os.environ.get("WIKI_VAULT"),
                              start_dir=Path.cwd(), config_path=_config_path())
    except VaultNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    return Config(vault=vault)


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


def _render_findings(findings, deep_report: str) -> str:
    lines: list[str] = []
    if not findings:
        lines.append("wiki is clean — no mechanical findings")
    else:
        for f in findings:
            lines.append(f"[{f.severity}] {f.check}  {f.path} — {f.message}")
        fixable = sum(1 for f in findings if f.fixable)
        lines.append(f"\n{len(findings)} findings ({fixable} fixable)")
    if deep_report:
        lines.append("\nSemantic findings (LLM):\n" + deep_report)
    return "\n".join(lines)


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


def cmd_lint(cfg: Config, *, deep: bool = False, fix: bool = False,
             yes: bool = False) -> int:
    findings = lintmod.run_checks(cfg)
    deep_report = ""
    if deep:
        scan = lint_deep(cfg)
        if scan.ok:
            deep_report = scan.report
        else:
            print(f"lint deep failed: {scan.reason}", file=sys.stderr)
    print(_render_findings(findings, deep_report))

    if not fix:
        return 1 if findings else 0

    deletions = [f for f in findings if f.fix_action == "delete_file"]
    needs_repair = any(not f.fixable for f in findings) or bool(deep_report)
    if not deletions and not needs_repair:
        return 0  # nothing to fix

    # Confirm before mutating the LLM-owned wiki.
    if not yes:
        if not sys.stdin.isatty():
            print("refusing to --fix without confirmation; re-run with --yes",
                  file=sys.stderr)
            return 2
        plan = (f"will delete {len(deletions)} conflict-duplicate file(s)"
                + (" and run an LLM repair pass" if needs_repair else ""))
        if input(f"{plan}. Proceed? [type 'yes'] ").strip() != "yes":
            print("aborted")
            return 0

    for f in deletions:
        (cfg.vault / f.path).unlink(missing_ok=True)
        print(f"deleted {f.path}")

    if needs_repair:
        repairable = [f for f in findings if not f.fixable]
        text = "\n".join(f"- [{f.check}] {f.path}: {f.message}" for f in repairable)
        result = lint_repair(cfg, text, deep_report=deep_report)
        if not result.ok:
            print(f"repair failed: {result.reason}", file=sys.stderr)
            return 1

    remaining = lintmod.run_checks(cfg)
    print(_render_findings(remaining, ""))
    return 1 if remaining else 0


def cmd_query(cfg: Config, question: str, *, save: bool = False) -> int:
    result = query(cfg, question, save=save)
    if not result.ok:
        print(f"query failed: {result.reason}", file=sys.stderr)
        return 1
    print(result.answer)
    if save:
        if result.saved:
            print("saved")
        else:
            print(f"save failed: {result.reason}", file=sys.stderr)
            return 1
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    if ns.command is None:
        parser.print_help()
        return 0
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
    if ns.command == "query":
        return cmd_query(cfg, ns.question, save=ns.save)
    if ns.command == "lint":
        return cmd_lint(cfg, deep=ns.deep, fix=ns.fix, yes=ns.yes)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
