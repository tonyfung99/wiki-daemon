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


# --- vault:claude-md drift check ---
from wiki_daemon.doctor import check_claude_md
from wiki_daemon.maintainer import template_text


def test_check_claude_md_current_passes(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)  # scaffolds the current template
    c = check_claude_md(cfg)
    assert c.name == "vault:claude-md"
    assert c.status == "PASS"


def test_check_claude_md_stale_warns_with_hint(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    # downgrade to an old-prefix brain (INGEST only, no clarification/query/lint)
    tmpl = template_text()
    old = tmpl[: tmpl.index("## RAISE CLARIFICATION")].rstrip() + "\n"
    cfg.claude_md.write_text(old, encoding="utf-8")
    c = check_claude_md(cfg)
    assert c.status == "WARN"
    assert "RAISE CLARIFICATION" in c.detail
    assert "wiki doctor --fix" in c.detail


def test_check_claude_md_absent_returns_none(tmp_path):
    cfg = Config(vault=tmp_path / "missing", state_root=tmp_path / "s")
    assert check_claude_md(cfg) is None


# --- _fix_claude_md behavior (hermetic: no claude / icloud) ---
from wiki_daemon.doctor import _fix_claude_md


def _stale_vault(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    tmpl = template_text()
    old = tmpl[: tmpl.index("## RAISE CLARIFICATION")].rstrip() + "\n"
    cfg.claude_md.write_text(old, encoding="utf-8")
    return cfg


def test_fix_claude_md_appends_when_warned(tmp_path, capsys):
    cfg = _stale_vault(tmp_path)
    checks = [check_claude_md(cfg)]
    rc = _fix_claude_md(cfg, checks, yes=True)
    assert rc is None
    # CLAUDE.md is now complete and the fix was reported
    assert check_claude_md(cfg).status == "PASS"
    assert "fixed: appended" in capsys.readouterr().out


def test_fix_claude_md_noninteractive_without_yes_refuses(tmp_path, capsys, monkeypatch):
    cfg = _stale_vault(tmp_path)
    before = cfg.claude_md.read_text(encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = _fix_claude_md(cfg, [check_claude_md(cfg)], yes=False)
    assert rc == 2
    assert cfg.claude_md.read_text(encoding="utf-8") == before  # unchanged
    assert "re-run with --yes" in capsys.readouterr().err


def test_fix_claude_md_skips_when_current(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)  # current template -> PASS
    rc = _fix_claude_md(cfg, [check_claude_md(cfg)], yes=True)
    assert rc is None
    assert "fixed" not in capsys.readouterr().out


# --- CLAUDE.md version stamp (2026-06-08) ---
from wiki_daemon.maintainer import parse_version, template_version


def test_check_claude_md_passes_when_version_current(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)  # writes the stamped template -> current version
    c = check_claude_md(cfg)
    assert c.status == "PASS" and "up to date" in c.detail


def test_check_claude_md_warns_when_unversioned(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    # strip the stamp: a pre-versioning vault (all sections present, no stamp)
    text = cfg.claude_md.read_text(encoding="utf-8")
    cfg.claude_md.write_text(text.replace(text.splitlines()[0] + "\n", "", 1),
                             encoding="utf-8")
    c = check_claude_md(cfg)
    assert c.status == "WARN"
    assert "wiki doctor --fix" in c.detail


def test_fix_claude_md_stale_version_backs_up_and_overwrites(tmp_path, capsys):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    # downgrade: stamp an older version + drop the options instruction content
    cfg.claude_md.write_text("<!-- wiki-template: v0 -->\n# Wiki Maintainer Instructions\n"
                             "## INGEST operation\nold stale body\n", encoding="utf-8")
    rc = _fix_claude_md(cfg, [check_claude_md(cfg)], yes=True)
    assert rc is None
    # backup created, file overwritten with the current stamped template
    assert list(cfg.vault.glob("CLAUDE.md.bak-*")), "no backup written"
    assert parse_version(cfg.claude_md.read_text(encoding="utf-8")) == template_version()
    assert check_claude_md(cfg).status == "PASS"
    assert "backed up" in capsys.readouterr().out.lower()


def test_fix_claude_md_stale_noninteractive_without_yes_refuses(tmp_path, capsys, monkeypatch):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    cfg.claude_md.write_text("<!-- wiki-template: v0 -->\n# x\n## INGEST operation\nold\n",
                             encoding="utf-8")
    before = cfg.claude_md.read_text(encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    rc = _fix_claude_md(cfg, [check_claude_md(cfg)], yes=False)
    assert rc == 2
    assert cfg.claude_md.read_text(encoding="utf-8") == before  # untouched
    assert not list(cfg.vault.glob("CLAUDE.md.bak-*"))          # no backup written
