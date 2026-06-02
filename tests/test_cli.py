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
