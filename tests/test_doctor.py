from wiki_daemon.config import Config
from wiki_daemon.doctor import Check, overall_status, check_vault, check_pinned, check_auth
from wiki_daemon.health import AuthResult
from wiki_daemon.scaffold import init_vault
from wiki_daemon.cli import build_parser


def test_overall_status_precedence():
    assert overall_status([Check("a", "PASS", ""), Check("b", "WARN", "")]) == "WARN"
    assert overall_status([Check("a", "WARN", ""), Check("b", "FAIL", "")]) == "FAIL"
    assert overall_status([Check("a", "PASS", "")]) == "PASS"


def test_check_vault_flags_scaffold_and_non_icloud(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    by = {c.name: c for c in check_vault(cfg)}
    assert by["vault:scaffolded"].status == "PASS"
    assert by["vault:icloud"].status == "WARN"  # tmp_path is not under iCloud Drive


def test_check_vault_missing(tmp_path):
    cfg = Config(vault=tmp_path / "missing", state_root=tmp_path / "s")
    checks = check_vault(cfg)
    assert checks[0].status == "FAIL"


def test_check_pinned_uses_xattr_returncode(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    (tmp_path / "v").mkdir(parents=True)
    assert check_pinned(cfg, run=lambda cmd: (0, "")).status == "PASS"
    assert check_pinned(cfg, run=lambda cmd: (1, "")).status == "WARN"


def test_parser_has_doctor():
    ns = build_parser().parse_args(["doctor", "--vault", "/tmp/v"])
    assert ns.command == "doctor"
    assert ns.probe is None
    ns2 = build_parser().parse_args(["doctor", "--vault", "/tmp/v", "--probe", "/tmp/v/x"])
    assert ns2.probe == "/tmp/v/x"


def test_check_auth_pass(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    c = check_auth(cfg, probe_fn=lambda cfg: AuthResult("ok", "authenticated"))
    assert c.name == "tool:claude-auth" and c.status == "PASS"


def test_check_auth_fail_has_remediation(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    c = check_auth(cfg, probe_fn=lambda cfg: AuthResult("auth_failed", "401"))
    assert c.status == "FAIL" and "setup-token" in c.detail


def test_check_auth_unavailable_is_warn(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    c = check_auth(cfg, probe_fn=lambda cfg: AuthResult("unavailable", "timeout"))
    assert c.status == "WARN"


# --- vault:brain: AGENTS.md canonical + provider symlinks (2026-06-10) ---
from wiki_daemon.doctor import check_brain, _fix_brain
from wiki_daemon.maintainer import parse_version, template_text, template_version

_STALE = "<!-- wiki-template: v0 -->\n# x\n## INGEST operation\nold\n"


def test_check_brain_current_passes(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    c = check_brain(cfg)
    assert c.name == "vault:brain" and c.status == "PASS"
    assert "symlinks ok" in c.detail


def test_check_brain_absent_returns_none(tmp_path):
    cfg = Config(vault=tmp_path / "missing", state_root=tmp_path / "s")
    assert check_brain(cfg) is None


def test_check_brain_warns_on_broken_symlink(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    cfg.brain_links["CLAUDE.md"].unlink()   # iCloud "ate" the symlink
    c = check_brain(cfg)
    assert c.status == "WARN" and "CLAUDE.md" in c.detail


def test_check_brain_warns_on_stale_agents(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    cfg.agents_md.write_text(_STALE, encoding="utf-8")
    c = check_brain(cfg)
    assert c.status == "WARN" and "stale content" in c.detail


def test_check_brain_warns_on_legacy_claude_md(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.vault.mkdir(parents=True)
    cfg.claude_md.write_text(template_text(), encoding="utf-8")  # real file, no AGENTS.md
    c = check_brain(cfg)
    assert c.status == "WARN" and "legacy CLAUDE.md" in c.detail


def test_fix_brain_repairs_broken_symlink(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    cfg.brain_links["GEMINI.md"].unlink()
    rc = _fix_brain(cfg, [check_brain(cfg)], yes=True)
    assert rc is None
    assert check_brain(cfg).status == "PASS"   # symlink recreated


def test_fix_brain_migrates_legacy_claude_md(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.vault.mkdir(parents=True)
    cfg.claude_md.write_text(template_text(), encoding="utf-8")
    rc = _fix_brain(cfg, [check_brain(cfg)], yes=True)
    assert rc is None
    assert cfg.agents_md.is_file() and not cfg.agents_md.is_symlink()
    assert cfg.claude_md.is_symlink()
    assert cfg.claude_md.resolve() == cfg.agents_md.resolve()
    assert list(cfg.vault.glob("CLAUDE.md.bak-*"))
    assert check_brain(cfg).status == "PASS"
    assert "migrated CLAUDE.md" in capsys.readouterr().out


def test_fix_brain_stale_agents_backs_up_and_overwrites(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    cfg.agents_md.write_text(_STALE, encoding="utf-8")
    rc = _fix_brain(cfg, [check_brain(cfg)], yes=True)
    assert rc is None
    assert list(cfg.vault.glob("AGENTS.md.bak-*"))
    assert parse_version(cfg.agents_md.read_text(encoding="utf-8")) == template_version()
    assert check_brain(cfg).status == "PASS"


def test_fix_brain_noninteractive_without_yes_refuses(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    cfg.agents_md.write_text(_STALE, encoding="utf-8")
    before = cfg.agents_md.read_text(encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = _fix_brain(cfg, [check_brain(cfg)], yes=False)
    assert rc == 2
    assert cfg.agents_md.read_text(encoding="utf-8") == before
    assert "re-run with --yes" in capsys.readouterr().err
