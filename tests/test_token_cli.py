from wiki_daemon.cli import build_parser, main


def test_token_parser_has_subcommands():
    p = build_parser()
    ns = p.parse_args(["token", "generate"])
    assert ns.command == "token" and ns.token_cmd == "generate"
    ns2 = p.parse_args(["token", "show"])
    assert ns2.token_cmd == "show"
    ns3 = p.parse_args(["token", "rotate"])
    assert ns3.token_cmd == "rotate"


def test_token_generate_creates_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "config.toml")
    rc = main(["token", "generate"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("wk_")
    assert len(out) == 3 + 32  # wk_ + 32 hex chars
    # Token was saved
    from wiki_daemon.vault import read_config_token
    assert read_config_token(tmp_path / "config.toml") == out


def test_token_generate_refuses_if_exists(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_token = "wk_existing"\n', encoding="utf-8")
    monkeypatch.setattr("wiki_daemon.cli._config_path", lambda: cfg)
    rc = main(["token", "generate"])
    assert rc == 1
    assert "already" in capsys.readouterr().err.lower()


def test_token_show_prints_token(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_token = "wk_abc"\n', encoding="utf-8")
    monkeypatch.setattr("wiki_daemon.cli._config_path", lambda: cfg)
    rc = main(["token", "show"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "wk_abc"


def test_token_show_no_token_returns_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "nope.toml")
    rc = main(["token", "show"])
    assert rc == 1
    assert "no token" in capsys.readouterr().err.lower()


def test_token_rotate_replaces(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text('api_token = "wk_old"\n', encoding="utf-8")
    monkeypatch.setattr("wiki_daemon.cli._config_path", lambda: cfg)
    rc = main(["token", "rotate"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("wk_") and out != "wk_old"
    from wiki_daemon.vault import read_config_token
    assert read_config_token(cfg) == out


def test_token_rotate_no_existing_still_creates(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("wiki_daemon.cli._config_path",
                        lambda: tmp_path / "config.toml")
    rc = main(["token", "rotate"])
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("wk_")
