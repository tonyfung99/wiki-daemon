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
