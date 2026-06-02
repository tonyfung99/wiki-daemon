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
