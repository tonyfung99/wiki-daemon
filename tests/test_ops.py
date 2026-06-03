# tests/test_ops.py
import pytest
from wiki_daemon.config import Config
from wiki_daemon.ops import ingest, IngestResult
from wiki_daemon.state import StateStore


def _make_source(cfg, name="2026-05-31-acme.md"):
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)
    p = cfg.raw_sources / name
    p.write_text("---\ntype: source\ntitle: Acme\n---\nAcme makes widgets.\n",
                 encoding="utf-8")
    return p


def _good_claude(cfg, source_name):
    """A fake runner that behaves like a compliant maintainer: it names the
    summary page by title and records the raw source in `sources:` frontmatter."""
    def runner(cmd, cwd, timeout):
        (cfg.wiki / "sources" / "acme-corp.md").write_text(
            "---\ntype: source\ntitle: Acme\n"
            f"sources: [raw/sources/{source_name}]\n---\nsummary\n",
            encoding="utf-8")
        (cfg.wiki / "index.md").write_text("# Index\n- Acme\n", encoding="utf-8")
        (cfg.wiki / "log.md").write_text("# Log\n## [2026-05-31] ingest | Acme\n",
                                         encoding="utf-8")
        return 0, "ok\n", ""
    return runner


def _lazy_claude(cmd, cwd, timeout):
    """A fake runner that returns success but writes nothing (misbehaves)."""
    return 0, "ok\n", ""


def test_ingest_success_marks_processed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    result = ingest(cfg, src, store=store, runner=_good_claude(cfg, src.name))

    assert isinstance(result, IngestResult)
    assert result.ok is True
    assert (cfg.wiki / "sources" / "acme-corp.md").exists()  # title-based name
    # sha recorded so a re-run is skipped
    from wiki_daemon.sources import read_source
    assert store.is_processed(read_source(src).sha256)


def test_ingest_verification_fails_when_no_summary(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    result = ingest(cfg, src, store=store, runner=_lazy_claude)

    assert result.ok is False
    assert "summary" in result.reason.lower()
    # NOT marked processed -> will be retried later
    from wiki_daemon.sources import read_source
    assert store.is_processed(read_source(src).sha256) is False


def test_ingest_resolves_symlinked_source_path(tmp_path):
    # mimics macOS /tmp -> /private/tmp: vault resolves but the passed path doesn't
    from wiki_daemon.scaffold import init_vault
    real = tmp_path / "real"
    cfg = Config(vault=real / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    link = tmp_path / "link"
    link.symlink_to(real)
    src = link / "v" / "raw" / "sources" / "x.md"
    src.write_text("---\ntype: source\ntitle: X\n---\nbody\n", encoding="utf-8")
    store = StateStore(cfg.processed_json)

    def runner(cmd, cwd, timeout):
        (cfg.wiki / "sources" / "x.md").write_text(
            "---\ntype: source\nsources: [raw/sources/x.md]\n---\nsummary\n",
            encoding="utf-8")
        return 0, "ok\n", ""

    result = ingest(cfg, src, store=store, runner=runner)
    assert result.ok is True


def test_ingest_skips_already_processed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)
    from wiki_daemon.sources import read_source
    store.mark_processed(read_source(src).sha256, str(src))

    called = {"n": 0}
    def counting_runner(cmd, cwd, timeout):
        called["n"] += 1
        return 0, "", ""

    result = ingest(cfg, src, store=store, runner=counting_runner)
    assert result.skipped is True
    assert called["n"] == 0


# append to tests/test_ops.py
def test_ingest_success_kind_is_ok(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)
    result = ingest(cfg, src, store=store, runner=_good_claude(cfg, src.name))
    assert result.kind == "ok"


def test_ingest_verify_failure_kind(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)
    result = ingest(cfg, src, store=store, runner=_lazy_claude)
    assert result.kind == "verify_error"


def test_ingest_auth_failure_kind(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    def auth_fail(cmd, cwd, timeout):
        return 1, "", "API Error: 401 Invalid authentication credentials"

    result = ingest(cfg, src, store=store, runner=auth_fail)
    assert result.ok is False and result.kind == "auth"


def test_ingest_skip_kind(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)
    from wiki_daemon.sources import read_source
    store.mark_processed(read_source(src).sha256, str(src))
    result = ingest(cfg, src, store=store, runner=_lazy_claude)
    assert result.skipped is True and result.kind == "skipped"


from wiki_daemon.ops import ingest_interactive, apply_clarification, ApplyResult


def test_ingest_interactive_success(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    def interactive_runner(cmd, cwd):
        (cfg.wiki / "sources" / "acme-corp.md").write_text(
            "---\ntype: source\nsources: [raw/sources/" + src.name + "]\n---\nsummary\n",
            encoding="utf-8")
        (cfg.wiki / "index.md").write_text("# Index\n", encoding="utf-8")
        (cfg.wiki / "log.md").write_text("# Log\n", encoding="utf-8")
        return 0

    result = ingest_interactive(cfg, src, store=store, runner=interactive_runner)
    assert result.ok is True and result.kind == "ok"
    from wiki_daemon.sources import read_source
    assert store.is_processed(read_source(src).sha256)


def test_ingest_interactive_nonzero_not_processed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)

    result = ingest_interactive(cfg, src, store=store, runner=lambda cmd, cwd: 1)
    assert result.ok is False and result.kind == "claude_error"
    from wiki_daemon.sources import read_source
    assert store.is_processed(read_source(src).sha256) is False


def _seed_answered_review(cfg, item_id="q1"):
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / f"{item_id}.md").write_text(
        "---\ntype: review\nstatus: answered\n"
        "source: raw/sources/x.md\nquestion: \"q?\"\ntentative: \"t\"\n"
        "answer: \"a\"\ncreated: 2026-06-03\n---\nbody\n", encoding="utf-8")
    return cfg.review / f"{item_id}.md"


def test_apply_clarification_success_deletes_file(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    path = _seed_answered_review(cfg)

    def runner(cmd, cwd, timeout):
        path.unlink()
        return 0, "ok\n", ""

    result = apply_clarification(cfg, "q1", runner=runner)
    assert isinstance(result, ApplyResult)
    assert result.ok is True
    assert not path.exists()


def test_apply_clarification_unanswered_fails(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    cfg.review.mkdir(parents=True, exist_ok=True)
    (cfg.review / "q2.md").write_text(
        "---\ntype: review\nstatus: open\nsource: raw/sources/x.md\n"
        "question: \"q?\"\ntentative: \"t\"\ncreated: 2026-06-03\n---\nbody\n",
        encoding="utf-8")
    calls = {"n": 0}
    result = apply_clarification(cfg, "q2", runner=lambda *a, **k: calls.__setitem__("n", 1) or (0, "", ""))
    assert result.ok is False and "answered" in result.reason
    assert calls["n"] == 0


def test_apply_clarification_file_not_removed_fails(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    _seed_answered_review(cfg, "q3")
    result = apply_clarification(cfg, "q3", runner=lambda cmd, cwd, timeout: (0, "ok", ""))
    assert result.ok is False and "not removed" in result.reason


def test_ingest_interactive_skips_already_processed(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    from wiki_daemon.scaffold import init_vault
    init_vault(cfg)
    src = _make_source(cfg)
    store = StateStore(cfg.processed_json)
    from wiki_daemon.sources import read_source
    store.mark_processed(read_source(src).sha256, str(src))

    called = {"n": 0}
    def counting_runner(cmd, cwd):
        called["n"] += 1
        return 0

    result = ingest_interactive(cfg, src, store=store, runner=counting_runner)
    assert result.skipped is True and result.kind == "skipped"
    assert called["n"] == 0  # never launched claude
