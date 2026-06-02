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

    def fake_serve(cfg, *, reconcile_interval):
        called["ri"] = reconcile_interval
        return 0

    monkeypatch.setattr(daemon, "serve", fake_serve)
    rc = main(["serve", "--vault", str(tmp_path), "--reconcile-interval", "5"])
    assert rc == 0 and called["ri"] == 5.0
