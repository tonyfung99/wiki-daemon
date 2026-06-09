from wiki_daemon.cli import build_parser, cmd_init
from wiki_daemon.config import Config


def test_parser_has_subcommands():
    parser = build_parser()
    ns = parser.parse_args(["init", "--vault", "/tmp/v"])
    assert ns.command == "init"
    assert ns.vault == "/tmp/v"


def test_cmd_init_scaffolds(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    rc = cmd_init(cfg)
    assert rc == 0
    assert (cfg.vault / "CLAUDE.md").exists()
    out = capsys.readouterr().out
    assert "initialized" in out.lower()


# append to tests/test_cli.py
from wiki_daemon.cli import cmd_import
from wiki_daemon.scaffold import init_vault


def test_parser_has_import_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["import", "--vault", "/tmp/v", "/tmp/clip.md"])
    assert ns.command == "import"
    assert ns.file == "/tmp/clip.md"


def _good_claude(cfg):
    """Fake runner: writes a compliant source summary for whatever lands in
    raw/sources/ (matches the pattern in tests/test_ops.py)."""
    def runner(cmd, cwd, timeout):
        src = next(cfg.raw_sources.glob("*.md"))
        rel = src.relative_to(cfg.vault).as_posix()
        (cfg.wiki / "sources").mkdir(parents=True, exist_ok=True)
        (cfg.wiki / "sources" / "clip.md").write_text(
            f"---\ntype: source\nsources: [{rel}]\n---\nsummary\n", encoding="utf-8")
        (cfg.wiki / "index.md").write_text("# Index\n", encoding="utf-8")
        (cfg.wiki / "log.md").write_text("# Log\n", encoding="utf-8")
        return 0, "ok\n", ""
    return runner


def _patch_ingest(monkeypatch, cfg):
    """Route cmd_import's `ingest` call through the fake claude runner. cmd_import
    uses the module-level `ingest` symbol, so we patch it on `wiki_daemon.cli`."""
    import wiki_daemon.cli as cli
    real_ingest = cli.ingest
    monkeypatch.setattr(cli, "ingest",
                        lambda cfg, path, *, store: real_ingest(
                            cfg, path, store=store, runner=_good_claude(cfg)))


def test_cmd_import_lands_and_ingests(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    external = tmp_path / "outside.md"
    external.write_text("some clipped text\n", encoding="utf-8")
    _patch_ingest(monkeypatch, cfg)

    rc = cmd_import(cfg, str(external))

    assert rc == 0
    assert external.exists()  # original left in place
    assert list(cfg.raw_sources.glob("*-outside.md"))  # landed copy
    out = capsys.readouterr().out.lower()
    assert "imported" in out and "ingested" in out


def test_cmd_import_missing_file_fails(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)

    rc = cmd_import(cfg, str(tmp_path / "nope.md"))

    assert rc == 1
    assert "import failed" in capsys.readouterr().err.lower()


def test_cmd_import_reimport_same_content_is_skipped(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    # A file WITH frontmatter is copied verbatim, so re-imports are byte-identical
    # and content-hash dedupe is deterministic (a synthesized captured_at would
    # otherwise vary between imports).
    external = tmp_path / "outside.md"
    external.write_text("---\ntype: source\ntitle: Outside\n---\nclipped text\n",
                        encoding="utf-8")
    _patch_ingest(monkeypatch, cfg)

    assert cmd_import(cfg, str(external)) == 0  # first import ingests
    capsys.readouterr()  # drain
    rc = cmd_import(cfg, str(external))  # same content again

    assert rc == 0
    # content-hash dedupe in ops.ingest skips it even though a new file landed
    assert "skipped (already processed)" in capsys.readouterr().out.lower()


# append to tests/test_cli.py
import os

from wiki_daemon.cli import _render_status
from wiki_daemon.runtime import StatusFile


def test_render_status_running_and_auth_ok(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(
        pid=os.getpid(), started_at="2026-06-02T15:00:00Z", auth_state="ok")
    out = _render_status(cfg)
    assert "running" in out and f"pid {os.getpid()}" in out
    assert "auth:" in out and "ok" in out


def test_render_status_auth_failing(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(
        pid=os.getpid(), auth_state="failing", auth_since="2026-06-02T15:10:00Z",
        last_error={"msg": "claude failed: 401", "kind": "auth",
                    "file": "raw/sources/x.md", "at": "2026-06-02T15:10:00Z"})
    out = _render_status(cfg)
    assert "FAILING" in out and "setup-token" in out
    assert "last error" in out and "x.md" in out


def test_render_status_not_running_stale_pid(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(pid=999_999)
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / "pending-00000001-ingest.json").write_text(
        '{"type":"ingest","payload":"raw/sources/p.md"}', encoding="utf-8")
    out = _render_status(cfg)
    assert "not running" in out
    assert "1 pending" in out


def test_render_status_no_status_file(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    out = _render_status(cfg)  # no status.json at all
    assert "not running" in out
    assert "processed:" in out


def test_parser_has_serve_subcommand():
    parser = build_parser()
    ns = parser.parse_args(["serve", "--vault", "/tmp/v", "--reconcile-interval", "10"])
    assert ns.command == "serve"
    assert ns.vault == "/tmp/v"
    assert ns.reconcile_interval == 10.0


def test_main_serve_dispatches_to_daemon(monkeypatch, tmp_path):
    import wiki_daemon.daemon as daemon
    from wiki_daemon.cli import main

    called = {}

    def fake_serve(cfg, *, reconcile_interval, verbose=False):
        called["ri"] = reconcile_interval
        return 0

    monkeypatch.setattr(daemon, "serve", fake_serve)
    rc = main(["serve", "--vault", str(tmp_path), "--reconcile-interval", "5"])
    assert rc == 0 and called["ri"] == 5.0


def test_ingest_flags_parse_tristate():
    parser = build_parser()
    assert parser.parse_args(["ingest", "--vault", "/v", "f.md"]).interactive is None
    assert parser.parse_args(["ingest", "--vault", "/v", "--interactive", "f.md"]).interactive is True
    assert parser.parse_args(["ingest", "--vault", "/v", "--no-interactive", "f.md"]).interactive is False


def test_cmd_ingest_uses_interactive_when_forced(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")

    import wiki_daemon.cli as cli
    called = {"interactive": False}
    def fake_interactive(cfg, path, *, store):
        called["interactive"] = True
        from wiki_daemon.ops import IngestResult
        return IngestResult(ok=True, kind="ok")
    monkeypatch.setattr(cli, "ingest_interactive", fake_interactive)

    rc = cli.cmd_ingest(cfg, str(src), interactive=True)
    assert rc == 0 and called["interactive"] is True


def test_cmd_ingest_headless_when_forced_off(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")

    import wiki_daemon.cli as cli
    called = {"headless": False}
    def fake_ingest(cfg, path, *, store):
        called["headless"] = True
        from wiki_daemon.ops import IngestResult
        return IngestResult(ok=True, kind="ok")
    monkeypatch.setattr(cli, "ingest", fake_ingest)

    rc = cli.cmd_ingest(cfg, str(src), interactive=False)
    assert rc == 0 and called["headless"] is True


from wiki_daemon.cli import _render_review, cmd_review_answer


def _seed_review(cfg, item_id="q1", status="open"):
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{item_id}.md").write_text(
        "---\ntype: review\nstatus: " + status + "\n"
        "source: raw/sources/x.md\nquestion: \"Same concept?\"\n"
        "tentative: \"t\"\ncreated: 2026-06-03\n---\nbody\n", encoding="utf-8")


def test_render_review_lists_open(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    _seed_review(cfg)
    out = _render_review(cfg)
    assert "q1" in out and "open" in out and "Same concept?" in out


def test_render_review_empty(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    out = _render_review(cfg)
    assert "no open" in out.lower()


def test_cmd_review_answer_runs_apply(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    _seed_review(cfg, "q1")

    import wiki_daemon.cli as cli
    from wiki_daemon.ops import ApplyResult
    seen = {}
    def fake_apply(cfg, rid):
        seen["id"] = rid
        from wiki_daemon.review import read_item
        seen["status"] = read_item(cfg, rid).status
        return ApplyResult(ok=True)
    monkeypatch.setattr(cli, "apply_clarification", fake_apply)

    rc = cli.cmd_review_answer(cfg, "q1", "they are the same")
    assert rc == 0 and seen["id"] == "q1" and seen["status"] == "answered"
    assert "resolved" in capsys.readouterr().out.lower()


def test_cmd_review_answer_unknown_id(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    import wiki_daemon.cli as cli
    rc = cli.cmd_review_answer(cfg, "nope", "x")
    assert rc == 1 and "no such" in capsys.readouterr().err.lower()


def test_render_status_shows_review_count(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "q1.md").write_text(
        "---\ntype: review\nstatus: open\n---\nx\n", encoding="utf-8")
    (cfg.review / "q2.md").write_text(
        "---\ntype: review\nstatus: open\n---\nx\n", encoding="utf-8")
    out = _render_status(cfg)
    assert "review:" in out and "2 open" in out


def test_want_interactive_autodetects_tty(monkeypatch):
    import wiki_daemon.cli as cli
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert cli._want_interactive(None) is True
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli._want_interactive(None) is False
    # explicit flag always wins over the TTY check
    assert cli._want_interactive(True) is True
    assert cli._want_interactive(False) is False


from wiki_daemon.cli import cmd_query


def test_query_parser_accepts_question_and_save():
    parser = build_parser()
    ns = parser.parse_args(["query", "--vault", "/v", "what is X?"])
    assert ns.command == "query" and ns.question == "what is X?" and ns.save is False
    ns2 = parser.parse_args(["query", "--vault", "/v", "--save", "what is X?"])
    assert ns2.save is True


def test_cmd_query_prints_answer(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import QueryResult
    monkeypatch.setattr(cli, "query",
                        lambda cfg, q, *, save: QueryResult(ok=True, answer="ANS"))
    rc = cli.cmd_query(cfg, "q?", save=False)
    assert rc == 0
    assert "ANS" in capsys.readouterr().out


def test_cmd_query_save_reports_saved(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import QueryResult
    monkeypatch.setattr(cli, "query",
                        lambda cfg, q, *, save: QueryResult(ok=True, answer="A", saved=True))
    rc = cli.cmd_query(cfg, "q?", save=True)
    out = capsys.readouterr().out
    assert rc == 0 and "A" in out and "saved" in out


def test_cmd_query_save_failure_returns_1(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import QueryResult
    monkeypatch.setattr(cli, "query", lambda cfg, q, *, save:
                        QueryResult(ok=True, answer="A", saved=False, reason="no query page"))
    rc = cli.cmd_query(cfg, "q?", save=True)
    err = capsys.readouterr().err
    assert rc == 1 and "save failed" in err


def test_cmd_query_failure_returns_1(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import QueryResult
    monkeypatch.setattr(cli, "query", lambda cfg, q, *, save:
                        QueryResult(ok=False, reason="claude failed: 401", kind="auth"))
    rc = cli.cmd_query(cfg, "q?", save=False)
    assert rc == 1 and "query failed" in capsys.readouterr().err


from wiki_daemon.cli import _render_findings
from wiki_daemon.lint import Finding


def test_lint_parser_flags():
    parser = build_parser()
    ns = parser.parse_args(["lint", "--vault", "/v"])
    assert ns.command == "lint" and ns.deep is False and ns.fix is False and ns.yes is False
    ns2 = parser.parse_args(["lint", "--vault", "/v", "--deep", "--fix", "--yes"])
    assert ns2.deep and ns2.fix and ns2.yes


def test_render_findings_clean():
    assert "clean" in _render_findings([], "").lower()


def test_render_findings_groups_and_counts():
    fs = [
        Finding("dead_link", "error", "wiki/a.md", "bad link", False, ""),
        Finding("conflict_duplicate", "warning", "wiki/b 2.md", "dupe", True, "delete_file"),
    ]
    out = _render_findings(fs, "")
    assert "wiki/a.md" in out and "wiki/b 2.md" in out
    assert "2 findings" in out and "1 fixable" in out


def test_render_findings_includes_deep_section():
    out = _render_findings([], "Contradiction between A and B")
    assert "Semantic findings" in out and "Contradiction between A and B" in out


from wiki_daemon.cli import cmd_lint


def _seed_clean_vault(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    (cfg.wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    (cfg.wiki / "log.md").write_text("# Log\n", encoding="utf-8")
    return cfg


def test_cmd_lint_clean_returns_0(tmp_path, capsys):
    cfg = _seed_clean_vault(tmp_path)
    rc = cmd_lint(cfg, deep=False, fix=False, yes=False)
    assert rc == 0 and "clean" in capsys.readouterr().out.lower()


def test_cmd_lint_findings_return_1(tmp_path, capsys):
    cfg = _seed_clean_vault(tmp_path)
    (cfg.wiki / "concepts" / "p.md").write_text(
        "---\ntype: concept\ntitle: P\n---\n[[Nope]]\n", encoding="utf-8")
    rc = cmd_lint(cfg, deep=False, fix=False, yes=False)
    out = capsys.readouterr().out
    assert rc == 1 and "Nope" in out


def test_cmd_lint_fix_yes_deletes_conflict_dup(tmp_path, monkeypatch, capsys):
    cfg = _seed_clean_vault(tmp_path)
    (cfg.wiki / "concepts" / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nx\n", encoding="utf-8")
    dupe = cfg.wiki / "concepts" / "a 2.md"
    dupe.write_text("---\ntype: concept\ntitle: A\n---\nx\n", encoding="utf-8")
    (cfg.wiki / "index.md").write_text("# Index\n- [[A]] — x\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import ApplyResult
    monkeypatch.setattr(cli, "lint_repair", lambda cfg, t, *, deep_report="": ApplyResult(ok=True))

    rc = cmd_lint(cfg, deep=False, fix=True, yes=True)
    assert not dupe.exists()  # deleted
    assert "deleted" in capsys.readouterr().out.lower()


def test_cmd_lint_fix_no_tty_without_yes_refuses(tmp_path, monkeypatch, capsys):
    cfg = _seed_clean_vault(tmp_path)
    (cfg.wiki / "concepts" / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nx\n", encoding="utf-8")
    dupe = cfg.wiki / "concepts" / "a 2.md"
    dupe.write_text("---\ntype: concept\ntitle: A\n---\nx\n", encoding="utf-8")
    (cfg.wiki / "index.md").write_text("# Index\n- [[A]] — x\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    rc = cmd_lint(cfg, deep=False, fix=True, yes=False)
    assert rc == 2 and dupe.exists()  # refused, nothing deleted
    assert "refus" in capsys.readouterr().err.lower()


def test_cmd_lint_deep_appends_section(tmp_path, monkeypatch, capsys):
    cfg = _seed_clean_vault(tmp_path)
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import LintScan
    monkeypatch.setattr(cli, "lint_deep",
                        lambda cfg: LintScan(ok=True, report="Contradiction X vs Y"))
    rc = cmd_lint(cfg, deep=True, fix=False, yes=False)
    assert "Contradiction X vs Y" in capsys.readouterr().out


def test_cmd_lint_fix_typed_abort_deletes_nothing(tmp_path, monkeypatch, capsys):
    cfg = _seed_clean_vault(tmp_path)
    (cfg.wiki / "concepts" / "a.md").write_text(
        "---\ntype: concept\ntitle: A\n---\nx\n", encoding="utf-8")
    dupe = cfg.wiki / "concepts" / "a 2.md"
    dupe.write_text("---\ntype: concept\ntitle: A\n---\nx\n", encoding="utf-8")
    (cfg.wiki / "index.md").write_text("# Index\n- [[A]] — x\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a: "no")  # decline at the prompt

    rc = cmd_lint(cfg, deep=False, fix=True, yes=False)
    assert rc == 0 and dupe.exists()  # aborted, nothing deleted
    assert "aborted" in capsys.readouterr().out.lower()


def test_cmd_lint_fix_repair_failure_returns_1(tmp_path, monkeypatch, capsys):
    cfg = _seed_clean_vault(tmp_path)
    # a dead link → a non-fixable finding → triggers the LLM repair pass
    (cfg.wiki / "concepts" / "p.md").write_text(
        "---\ntype: concept\ntitle: P\n---\n[[Nope]]\n", encoding="utf-8")
    (cfg.wiki / "index.md").write_text("# Index\n- [[P]] — x\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import ApplyResult
    monkeypatch.setattr(cli, "lint_repair",
                        lambda cfg, t, *, deep_report="": ApplyResult(ok=False, reason="boom"))

    rc = cmd_lint(cfg, deep=False, fix=True, yes=True)
    assert rc == 1 and "repair failed" in capsys.readouterr().err.lower()


import pytest
from wiki_daemon.cli import main


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_bare_wiki_prints_help(capsys):
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "usage" in out and "ingest" in out


def test_command_resolves_vault_from_cwd(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))  # keep daemon state off real home
    cfg0 = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg0)
    monkeypatch.setattr("pathlib.Path.cwd", lambda: cfg0.vault / "wiki")
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "noconfig.toml")
    rc = main(["status"])
    assert rc == 0
    assert "processed:" in capsys.readouterr().out


def test_command_no_vault_anywhere_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.cwd", lambda: tmp_path / "empty")
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "noconfig.toml")
    with pytest.raises(SystemExit) as e:
        main(["status"])
    assert e.value.code == 2
    assert "no vault found" in capsys.readouterr().err.lower()


def test_explicit_vault_still_works(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg0 = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg0)
    rc = main(["status", "--vault", str(cfg0.vault)])
    assert rc == 0


def test_init_scaffolds_cwd_without_vault(tmp_path, monkeypatch, capsys):
    target = tmp_path / "newvault"
    target.mkdir()
    monkeypatch.setattr("pathlib.Path.cwd", lambda: target)
    rc = main(["init"])
    assert rc == 0
    assert (target / "CLAUDE.md").exists() and (target / "wiki").is_dir()


def test_init_set_default_writes_config(tmp_path, monkeypatch, capsys):
    target = tmp_path / "v"
    cfg_path = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr("wiki_daemon.cli._config_path", lambda: cfg_path)
    rc = main(["init", "--vault", str(target), "--set-default"])
    assert rc == 0
    from wiki_daemon.vault import read_config_vault
    assert read_config_vault(cfg_path) == target.resolve()
    out = capsys.readouterr().out.lower()
    assert "default vault" in out


def test_init_parser_has_set_default():
    parser = build_parser()
    ns = parser.parse_args(["init", "--vault", "/v", "--set-default"])
    assert ns.set_default is True
    ns2 = parser.parse_args(["init", "--vault", "/v"])
    assert ns2.set_default is False


# --- doctor --fix wiring ---
def test_doctor_parser_has_fix_yes():
    p = build_parser()
    ns = p.parse_args(["doctor", "--vault", "/v"])
    assert ns.fix is False and ns.yes is False
    ns2 = p.parse_args(["doctor", "--vault", "/v", "--fix", "--yes"])
    assert ns2.fix is True and ns2.yes is True


def test_main_doctor_passes_fix_yes(monkeypatch, tmp_path):
    import wiki_daemon.doctor as doctor
    from wiki_daemon.cli import main
    seen = {}
    def fake_run_doctor(cfg, *, probe, fix, yes, **kw):
        seen.update(probe=probe, fix=fix, yes=yes)
        return 0
    monkeypatch.setattr(doctor, "run_doctor", fake_run_doctor)
    rc = main(["doctor", "--vault", str(tmp_path), "--fix", "--yes"])
    assert rc == 0 and seen["fix"] is True and seen["yes"] is True


# --- review options/accept/--pick/--source (2026-06-07) ---
from wiki_daemon.cli import cmd_review_accept


def _seed_review_opts(cfg, item_id, source, options, recommended=1):
    cfg.review.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: review", "status: open", f"source: {source}",
             'question: "Granularity?"', "options:"]
    lines += [f'  - "{o}"' for o in options]
    lines += [f"recommended: {recommended}", f'tentative: "{options[recommended-1]}"',
              "created: 2026-06-07", "---", "body", ""]
    (cfg.review / f"{item_id}.md").write_text("\n".join(lines), encoding="utf-8")


def test_review_parser_source_and_accept_and_pick():
    p = build_parser()
    ns = p.parse_args(["review", "--vault", "/v", "--source", "raw/sources/a.md"])
    assert ns.source == "raw/sources/a.md"
    acc = p.parse_args(["review", "--vault", "/v", "accept", "id1"])
    assert acc.review_cmd == "accept" and acc.id == "id1"
    assert acc.vault == "/v"   # --vault before the subcommand must survive
    pk = p.parse_args(["review", "--vault", "/v", "answer", "id1", "--pick", "2"])
    assert pk.pick == 2 and pk.text is None
    assert pk.vault == "/v"
    tx = p.parse_args(["review", "--vault", "/v", "answer", "id1", "free text"])
    assert tx.text == "free text" and tx.pick is None


def test_render_review_groups_by_source_and_marks_recommended(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    _seed_review_opts(cfg, "a1", "raw/sources/a.md", ["Moderate", "Fine"], recommended=1)
    _seed_review_opts(cfg, "b1", "raw/sources/b.md", ["Keep", "Split"], recommended=2)
    out = _render_review(cfg)
    assert "raw/sources/a.md" in out and "raw/sources/b.md" in out
    assert "1) Moderate" in out and "2) Fine" in out
    assert "★" in out  # recommended marked


def test_render_review_source_filter(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    _seed_review_opts(cfg, "a1", "raw/sources/a.md", ["x", "y"])
    _seed_review_opts(cfg, "b1", "raw/sources/b.md", ["x", "y"])
    out = _render_review(cfg, source="raw/sources/a.md")
    assert "a1" in out and "b1" not in out


def test_cmd_review_accept_removes_and_no_apply(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    _seed_review_opts(cfg, "g", "raw/sources/a.md", ["Moderate", "Fine"])
    import wiki_daemon.cli as cli
    called = {"apply": False}
    monkeypatch.setattr(cli, "apply_clarification",
                        lambda *a, **k: called.__setitem__("apply", True))
    rc = cmd_review_accept(cfg, "g")
    assert rc == 0 and called["apply"] is False     # accept never calls claude
    assert not (cfg.review / "g.md").exists()
    assert "accepted" in capsys.readouterr().out.lower()


def test_cmd_review_answer_pick_feeds_option_text(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    _seed_review_opts(cfg, "g", "raw/sources/a.md", ["Moderate", "Fine", "Coarse"])
    import wiki_daemon.cli as cli
    from wiki_daemon.ops import ApplyResult
    seen = {}
    def fake_apply(cfg, rid):
        from wiki_daemon.review import read_item
        seen["answer"] = read_item(cfg, rid).answer
        return ApplyResult(ok=True)
    monkeypatch.setattr(cli, "apply_clarification", fake_apply)
    rc = cli.cmd_review_answer(cfg, "g", text=None, pick=2)
    assert rc == 0 and seen["answer"] == "Fine"     # option 2 expanded


def test_cmd_review_answer_pick_out_of_range(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    _seed_review_opts(cfg, "g", "raw/sources/a.md", ["Moderate", "Fine"])
    rc = cmd_review_answer(cfg, "g", text=None, pick=5)
    assert rc == 2 and "range" in capsys.readouterr().err.lower()


# --- defer-to-daemon + vault ingest lock (2026-06-07) ---
def test_cmd_import_defers_when_daemon_alive(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    external = tmp_path / "outside.md"
    external.write_text("clip\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: True)
    spy = {"ingest": False}
    monkeypatch.setattr(cli, "ingest", lambda *a, **k: spy.__setitem__("ingest", True))

    rc = cli.cmd_import(cfg, str(external), interactive=False)

    assert rc == 0 and spy["ingest"] is False           # did NOT ingest in-process
    assert list(cfg.raw_sources.glob("*-outside.md"))   # landed the file
    assert list(cfg.queue_dir.glob("pending-*.json"))   # enqueued for daemon
    assert "queued for the running daemon" in capsys.readouterr().out.lower()


def test_cmd_ingest_defers_when_daemon_alive(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: True)
    spy = {"ingest": False}
    monkeypatch.setattr(cli, "ingest", lambda *a, **k: spy.__setitem__("ingest", True))

    rc = cli.cmd_ingest(cfg, str(src), interactive=False)

    assert rc == 0 and spy["ingest"] is False
    assert list(cfg.queue_dir.glob("pending-*.json"))
    assert "queued for the running daemon" in capsys.readouterr().out.lower()


def test_cmd_ingest_interactive_defer_notes_headless(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: True)
    # explicit --interactive -> prominent headless note
    rc = cli.cmd_ingest(cfg, str(src), interactive=True)
    out = capsys.readouterr().out.lower()
    assert rc == 0 and "headless" in out and "wiki review" in out


def test_cmd_ingest_lock_contention_returns_1(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    src = cfg.raw_sources / "a.md"
    src.write_text("---\ntype: source\ntitle: A\n---\nbody\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    from wiki_daemon.runtime import vault_ingest_lock
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: False)
    with vault_ingest_lock(cfg):                         # pre-hold the lock
        rc = cli.cmd_ingest(cfg, str(src), interactive=False)
    assert rc == 1
    assert "another ingest is in progress" in capsys.readouterr().err.lower()


# --- per-source visibility: status --source, review empty-state, defer msg (2026-06-08) ---
from wiki_daemon.cli import cmd_status_source


def test_status_parser_has_source():
    ns = build_parser().parse_args(["status", "--vault", "/v", "--source", "raw/sources/a.md"])
    assert ns.source == "raw/sources/a.md"


def test_cmd_status_source_exit_codes(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    import wiki_daemon.cli as cli
    from wiki_daemon.progress import SourceState
    cases = {"processed": 0, "failed": 1, "untracked": 2, "queued": 3, "ingesting": 3}
    for state, code in cases.items():
        monkeypatch.setattr(cli, "source_state",
                            lambda cfg, s, _st=state: SourceState(_st, detail="d"))
        rc = cmd_status_source(cfg, "raw/sources/a.md")
        assert rc == code, f"{state} -> {rc} != {code}"
        assert state in capsys.readouterr().out


def test_main_status_source_routes(tmp_path, monkeypatch):
    import wiki_daemon.cli as cli
    seen = {}
    def fake(cfg, src):
        seen["src"] = src
        return 0
    monkeypatch.setattr(cli, "cmd_status_source", fake)
    init_vault(Config(vault=tmp_path / "v", state_root=tmp_path / "s"))
    rc = cli.main(["status", "--vault", str(tmp_path / "v"), "--source", "raw/sources/a.md"])
    assert rc == 0 and seen["src"] == "raw/sources/a.md"


def test_render_review_source_empty_states(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    import wiki_daemon.cli as cli
    from wiki_daemon.progress import SourceState
    checks = {
        "ingesting": "still processing",
        "processed": "no open clarifications",
        "failed": "ingest failed",
        "untracked": "not found",
    }
    for state, expect in checks.items():
        monkeypatch.setattr(cli, "source_state", lambda cfg, s, _st=state: SourceState(_st))
        out = cli._render_review(cfg, source="raw/sources/a.md")
        assert expect in out.lower(), f"{state}: {out!r}"


def test_defer_message_prints_track_and_review_paths(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    external = tmp_path / "outside.md"
    external.write_text("clip\n", encoding="utf-8")
    import wiki_daemon.cli as cli
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: True)
    monkeypatch.setattr(cli, "ingest", lambda *a, **k: None)  # must not be called
    rc = cli.cmd_import(cfg, str(external), interactive=False)
    out = capsys.readouterr().out
    assert rc == 0
    assert "track:" in out and "wiki status --source raw/sources/" in out
    assert "review:" in out and "wiki review --source raw/sources/" in out


# --- serve --verbose (2026-06-08) ---
def test_serve_parser_has_verbose():
    p = build_parser()
    assert p.parse_args(["serve", "--vault", "/v"]).verbose is False
    assert p.parse_args(["serve", "--vault", "/v", "--verbose"]).verbose is True
    assert p.parse_args(["serve", "--vault", "/v", "-v"]).verbose is True


def test_main_serve_passes_verbose(monkeypatch, tmp_path):
    import wiki_daemon.daemon as daemon
    from wiki_daemon.cli import main
    seen = {}
    def fake_serve(cfg, *, reconcile_interval, verbose):
        seen["verbose"] = verbose
        return 0
    monkeypatch.setattr(daemon, "serve", fake_serve)
    rc = main(["serve", "--vault", str(tmp_path), "--verbose"])
    assert rc == 0 and seen["verbose"] is True


# --- ingest of a convertible normalizes (parity with daemon) (2026-06-09) ---
def test_cmd_ingest_convertible_daemon_off_converts_then_ingests(tmp_path, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    pdf = cfg.raw_sources / "report.pdf"
    pdf.write_bytes(b"%PDF fake")
    import wiki_daemon.cli as cli
    import wiki_daemon.importer as imp
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: False)
    monkeypatch.setattr(imp, "convert_to_markdown", lambda p: "# R\nbody\n")
    ingested = []
    def fake_ingest(cfg, path, *, store):
        ingested.append(str(path))
        from wiki_daemon.ops import IngestResult
        return IngestResult(ok=True, kind="ok")
    monkeypatch.setattr(cli, "ingest", fake_ingest)
    rc = cli.cmd_ingest(cfg, str(pdf), interactive=False)
    assert rc == 0
    assert len(ingested) == 1 and ingested[0].endswith("report.md")  # converted md, not pdf
    assert (cfg.raw_originals / "report.pdf").exists()   # original archived
    assert not pdf.exists()


def test_cmd_ingest_external_document_directs_to_import(tmp_path, monkeypatch, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    ext = tmp_path / "outside.pdf"
    ext.write_bytes(b"%PDF")
    import wiki_daemon.cli as cli
    monkeypatch.setattr(cli, "daemon_owns_vault", lambda cfg: False)
    rc = cli.cmd_ingest(cfg, str(ext), interactive=False)
    assert rc == 2
    assert "import" in capsys.readouterr().err.lower()
    assert ext.exists()  # external file untouched


# --- provider selection (2026-06-10) ---
def test_provider_parser_and_resolution(tmp_path, monkeypatch):
    import wiki_daemon.cli as cli
    p = build_parser()
    ns = p.parse_args(["status", "--vault", "/v", "--provider", "gemini"])
    assert ns.provider == "gemini"
    # default when absent
    ns2 = p.parse_args(["status", "--vault", "/v"])
    assert ns2.provider is None


def test_config_resolves_provider_flag_env_default(tmp_path, monkeypatch):
    import wiki_daemon.cli as cli
    cfg0 = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg0)
    monkeypatch.setattr(cli, "_config_path", lambda: tmp_path / "noconfig.toml")
    monkeypatch.delenv("WIKI_PROVIDER", raising=False)
    # flag wins
    ns = build_parser().parse_args(["status", "--vault", str(cfg0.vault), "--provider", "codex"])
    assert cli._config(ns).provider == "codex"
    # env next
    monkeypatch.setenv("WIKI_PROVIDER", "gemini")
    ns2 = build_parser().parse_args(["status", "--vault", str(cfg0.vault)])
    assert cli._config(ns2).provider == "gemini"
    # default
    monkeypatch.delenv("WIKI_PROVIDER", raising=False)
    ns3 = build_parser().parse_args(["status", "--vault", str(cfg0.vault)])
    assert cli._config(ns3).provider == "claude"


def test_config_unknown_provider_errors(tmp_path, monkeypatch, capsys):
    import wiki_daemon.cli as cli
    cfg0 = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg0)
    monkeypatch.setattr(cli, "_config_path", lambda: tmp_path / "noconfig.toml")
    monkeypatch.delenv("WIKI_PROVIDER", raising=False)
    ns = build_parser().parse_args(["status", "--vault", str(cfg0.vault), "--provider", "bogus"])
    import pytest
    with pytest.raises(SystemExit):
        cli._config(ns)
    assert "bogus" in capsys.readouterr().err.lower()
