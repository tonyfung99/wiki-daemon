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
from wiki_daemon.progress import source_state
from wiki_daemon.review import list_items, write_answer
from wiki_daemon.queue import Job, JobQueue
from wiki_daemon.runtime import (
    IngestLockBusy, StatusFile, daemon_owns_vault, is_pid_alive, vault_ingest_lock,
)
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

    ini = sub.add_parser("init", parents=[common],
                         help="scaffold a new vault (defaults to the current dir)")
    ini.add_argument("--set-default", action="store_true",
                     help="record this vault as the default in ~/.config/wiki/config.toml")
    ing = sub.add_parser("ingest", parents=[common], help="ingest one source file")
    ing.add_argument("file", help="path to a raw source .md")
    _add_interactive_flags(ing)
    imp = sub.add_parser("import", parents=[common],
                         help="copy a file into the vault and ingest it")
    imp.add_argument("file", help="path to any UTF-8 text file to import")
    _add_interactive_flags(imp)
    sts = sub.add_parser("status", parents=[common], help="show processed count")
    sts.add_argument("--source", default=None,
                     help="report one source's ingest state (queued/ingesting/"
                          "processed/failed/untracked) and set an exit code")
    doc = sub.add_parser("doctor", parents=[common],
                         help="validate iCloud + tooling on the daemon host")
    doc.add_argument("--probe", default=None,
                     help="path to an already-evicted file to test materialization")
    doc.add_argument("--fix", action="store_true",
                     help="repair a stale vault CLAUDE.md (append missing sections)")
    doc.add_argument("--yes", action="store_true",
                     help="skip the confirmation prompt for --fix")
    srv = sub.add_parser("serve", parents=[common],
                         help="run the daemon: watch raw/sources and ingest autonomously")
    srv.add_argument("--reconcile-interval", type=float, default=300.0)

    rev = sub.add_parser("review", parents=[common],
                         help="list/accept/answer ingest clarifications")
    rev.add_argument("--source", default=None,
                     help="list only items raised by this source (vault-relative path)")
    rev_sub = rev.add_subparsers(dest="review_cmd")
    # NB: no parents=[common] here — the `review` parent already provides --vault;
    # re-declaring it on the sub-subparser clobbers the parsed value back to None.
    acc = rev_sub.add_parser("accept",
                             help="accept the recommended default (no LLM; just resolves)")
    acc.add_argument("id", help="review item id (filename without .md)")
    ans = rev_sub.add_parser("answer",
                             help="answer a clarification and apply it")
    ans.add_argument("id", help="review item id (filename without .md)")
    ans.add_argument("text", nargs="?", default=None, help="your free-text answer")
    ans.add_argument("--pick", type=int, default=None,
                     help="choose option N (1-based) instead of free text")

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


def cmd_init(cfg: Config, *, set_default: bool = False) -> int:
    init_vault(cfg)
    print(f"initialized vault at {cfg.vault}")
    if set_default:
        from wiki_daemon.vault import write_config_vault
        write_config_vault(_config_path(), cfg.vault)
        print(f"set default vault → {cfg.vault}")
    return 0


def _want_interactive(flag: bool | None) -> bool:
    """Resolve the tri-state flag: explicit wins, else auto-detect a TTY."""
    return sys.stdin.isatty() if flag is None else flag


def _defer_to_daemon(cfg: Config, path: Path, interactive: bool | None) -> int:
    """The daemon owns this vault (single writer), so don't ingest in-process —
    enqueue the file for it and return. Interactive ingest isn't possible while
    the daemon runs; note that clarifications will land in the review queue."""
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(path)))
    pending = len(list(cfg.queue_dir.glob("pending-*.json")))
    rel = path.resolve().relative_to(cfg.vault).as_posix()
    print(f"queued for the running daemon ({pending} pending)")
    print(f"  track:  wiki status --source {rel}")
    print(f"  review: wiki review --source {rel}")
    if _want_interactive(interactive):
        # explicit --interactive (True) gets a prominent note; auto-detected is softer
        lead = "NOTE: " if interactive is True else ""
        print(f"{lead}daemon is serving this vault; your file will be ingested "
              "headlessly — answer any clarifications with `wiki review`.")
    return 0


def _ingest_locked(cfg: Config, path: Path, interactive: bool | None) -> int:
    """Ingest in-process under the per-vault lock (no daemon running). The lock
    fails fast if another manual ingest holds it, instead of double-writing."""
    store = StateStore(cfg.processed_json)
    try:
        with vault_ingest_lock(cfg):
            if _want_interactive(interactive):
                result = ingest_interactive(cfg, path, store=store)
            else:
                result = ingest(cfg, path, store=store)
    except IngestLockBusy:
        print("another ingest is in progress for this vault", file=sys.stderr)
        return 1
    if result.skipped:
        print("skipped (already processed)")
        return 0
    if result.ok:
        print("ingested")
        return 0
    print(f"ingest failed: {result.reason}", file=sys.stderr)
    return 1


def cmd_ingest(cfg: Config, file: str, *, interactive: bool | None = None) -> int:
    path = Path(file)
    if daemon_owns_vault(cfg):
        return _defer_to_daemon(cfg, path, interactive)
    return _ingest_locked(cfg, path, interactive)


def cmd_import(cfg: Config, file: str, *, interactive: bool | None = None) -> int:
    try:
        dest = import_source(cfg, Path(file))
    except (FileNotFoundError, ValueError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    print(f"imported {dest.name}")
    if daemon_owns_vault(cfg):
        return _defer_to_daemon(cfg, dest, interactive)
    return _ingest_locked(cfg, dest, interactive)


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


# exit codes for `wiki status --source`: 0 done, 1 failed, 2 unknown, 3 in-progress
_SOURCE_EXIT = {"processed": 0, "failed": 1, "untracked": 2,
                "queued": 3, "ingesting": 3}


def cmd_status_source(cfg: Config, source: str) -> int:
    """Report one source's ingest state and return a state-coded exit so an agent
    can poll until done (exit 3 = in progress)."""
    st = source_state(cfg, source)
    line = st.state if not st.detail else f"{st.state}  {st.detail}"
    print(line)
    return _SOURCE_EXIT.get(st.state, 2)


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


def _render_review(cfg: Config, source: str | None = None) -> str:
    items = list_items(cfg, source=source)
    if not items:
        if source is None:
            return "no open clarifications"
        # Disambiguate the empty case for a specific source via its ingest state.
        st = source_state(cfg, source).state
        if st in ("queued", "ingesting"):
            return f"still processing ({st}) — no clarifications yet"
        if st == "failed":
            return f"ingest failed — run `wiki status --source {source}`"
        if st == "untracked":
            return "not found — is the path right?"
        return "processed — no open clarifications"
    by_source: dict[str, list] = {}
    for it in items:
        by_source.setdefault(it.source, []).append(it)
    lines: list[str] = []
    for src, group in by_source.items():
        lines.append(f"# {src}")
        for it in group:
            lines.append(f"  {it.id}  [{it.status}]\n    {it.question}")
            for i, opt in enumerate(it.options, start=1):
                star = " ★" if it.recommended == i else ""
                lines.append(f"      {i}) {opt}{star}")
        lines.append("")
    return "\n".join(lines).rstrip()


def cmd_review_list(cfg: Config, source: str | None = None) -> int:
    print(_render_review(cfg, source=source))
    return 0


def cmd_review_accept(cfg: Config, item_id: str) -> int:
    from wiki_daemon.review import accept_item
    try:
        accept_item(cfg, item_id)
    except FileNotFoundError as exc:
        print(f"review accept failed: {exc}", file=sys.stderr)
        return 1
    print(f"accepted {item_id}")
    return 0


def cmd_review_answer(cfg: Config, item_id: str, text: str | None = None,
                      *, pick: int | None = None) -> int:
    from wiki_daemon.review import option_to_answer, read_item
    try:
        item = read_item(cfg, item_id)
    except FileNotFoundError as exc:
        print(f"review answer failed: {exc}", file=sys.stderr)
        return 1
    if pick is not None:
        try:
            answer = option_to_answer(item, pick)
        except ValueError as exc:
            print(f"review answer failed: {exc}", file=sys.stderr)
            return 2
    elif text is not None:
        answer = text
    else:
        print("review answer failed: provide an answer or --pick N", file=sys.stderr)
        return 2

    write_answer(cfg, item_id, answer)
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
        return cmd_init(cfg, set_default=ns.set_default)
    if ns.command == "ingest":
        return cmd_ingest(cfg, ns.file, interactive=ns.interactive)
    if ns.command == "import":
        return cmd_import(cfg, ns.file, interactive=ns.interactive)
    if ns.command == "status":
        if ns.source is not None:
            return cmd_status_source(cfg, ns.source)
        return cmd_status(cfg)
    if ns.command == "doctor":
        from wiki_daemon import doctor
        return doctor.run_doctor(cfg, probe=Path(ns.probe) if ns.probe else None,
                                 fix=ns.fix, yes=ns.yes)
    if ns.command == "serve":
        # Lazy import: keeps watchdog/FSEvents out of the manual commands.
        from wiki_daemon.daemon import serve
        return serve(cfg, reconcile_interval=ns.reconcile_interval)
    if ns.command == "review":
        if ns.review_cmd == "accept":
            return cmd_review_accept(cfg, ns.id)
        if ns.review_cmd == "answer":
            return cmd_review_answer(cfg, ns.id, ns.text, pick=ns.pick)
        return cmd_review_list(cfg, source=ns.source)
    if ns.command == "query":
        return cmd_query(cfg, ns.question, save=ns.save)
    if ns.command == "lint":
        return cmd_lint(cfg, deep=ns.deep, fix=ns.fix, yes=ns.yes)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
