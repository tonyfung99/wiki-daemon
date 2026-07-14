"""Tests for agent.py — the pluggable agentic-CLI provider abstraction."""
import logging
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from wiki_daemon.agent import (
    AgentResult, get_provider, run_agent, run_agent_interactive, PROVIDERS,
    _subprocess_runner,
)


# --- command construction per provider (write vs read-only) ---
def test_claude_headless_cmd_write_and_read():
    p = PROVIDERS["claude"]
    w = p.headless_cmd("PROMPT", write=True)
    assert w[0] == "claude" and "-p" in w and "PROMPT" in w
    assert "--dangerously-skip-permissions" in w
    assert "Write" in w and "Edit" in w            # write tool set
    r = p.headless_cmd("PROMPT", write=False)
    assert "Write" not in r and "Edit" not in r     # read-only tool set
    assert "Read" in r


def test_gemini_headless_cmd_yolo_only_on_write():
    p = PROVIDERS["gemini"]
    w = p.headless_cmd("PROMPT", write=True)
    assert w[0] == "gemini" and "-p" in w and "PROMPT" in w
    assert "--yolo" in w
    r = p.headless_cmd("PROMPT", write=False)
    assert "--yolo" not in r                         # read-only: no auto-approve


def test_codex_headless_cmd_sandbox_modes():
    p = PROVIDERS["codex"]
    w = p.headless_cmd("PROMPT", write=True)
    assert w[0] == "codex" and "exec" in w and "PROMPT" in w
    assert "workspace-write" in w and "--skip-git-repo-check" in w
    r = p.headless_cmd("PROMPT", write=False)
    assert "read-only" in r and "--skip-git-repo-check" in r


def test_brain_filenames():
    assert PROVIDERS["claude"].brain_filename == "CLAUDE.md"
    assert PROVIDERS["gemini"].brain_filename == "GEMINI.md"
    assert PROVIDERS["codex"].brain_filename == "AGENTS.md"


# --- failure classification (auth | quota | unavailable | error) ---
def test_classify_failure_buckets():
    p = PROVIDERS["claude"]
    assert p.classify_failure(AgentResult(False, 1, "", "401 invalid authentication")) == "auth"
    assert p.classify_failure(AgentResult(False, 1, "", "rate limit exceeded")) == "quota"
    assert p.classify_failure(AgentResult(False, 1, "", "quota exhausted")) == "quota"
    assert p.classify_failure(AgentResult(False, 127, "", "command not found")) == "unavailable"
    assert p.classify_failure(AgentResult(False, 1, "", "some other boom")) == "error"


def test_classify_failure_quota_claude_usage_credits():
    # "Usage credits required for 1M context" — real Claude error seen in the wild
    p = PROVIDERS["claude"]
    assert p.classify_failure(AgentResult(False, 1, "", "Usage credits required for 1M context")) == "quota"
    assert p.classify_failure(AgentResult(False, 1, "", "usage limit exceeded")) == "quota"
    assert p.classify_failure(AgentResult(False, 1, "", "402 payment required")) == "quota"


def test_classify_failure_checks_stdout_not_just_stderr():
    # Codex puts its diagnostics in stdout; classification must scan both streams
    p = PROVIDERS["codex"]
    assert p.classify_failure(AgentResult(False, 1, "not logged in\n", "")) == "auth"
    assert p.classify_failure(AgentResult(False, 1, "usage credits required\n", "")) == "quota"


def test_ingest_reason_captures_stdout_when_stderr_empty():
    import tempfile, pathlib
    from wiki_daemon.ops import ingest
    from wiki_daemon.state import StateStore
    from wiki_daemon.config import Config

    with tempfile.TemporaryDirectory() as tmp:
        vault = pathlib.Path(tmp)
        (vault / "raw" / "sources").mkdir(parents=True)
        src = vault / "raw" / "sources" / "x.md"
        src.write_text("# X\n")

        cfg = Config(vault=vault)

        def bad_runner(cmd, cwd, timeout):
            return 1, "stdout-only error detail", ""

        store = StateStore(cfg.processed_json)
        result = ingest(cfg, src, store=store, runner=bad_runner)
        assert not result.ok
        assert "stdout-only error detail" in result.reason


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- _subprocess_runner kills the whole process tree on timeout ---
def test_subprocess_runner_timeout_kills_grandchild(tmp_path):
    # The direct `sh` exits immediately, but forks a background `sleep 30`
    # grandchild that inherits the captured stdout pipe (mirrors codex's
    # `--codex-run-as-fs-helper` grandchild). subprocess.run's kill() reaps only
    # the direct child, so the grandchild is orphaned and lives on (we found
    # such codex orphans alive for days). The fix launches the child in its own
    # session and kills the whole process group on timeout, so the grandchild
    # dies too. The grandchild records its PID so the test can check it.
    pidfile = tmp_path / "grandchild.pid"
    cmd = ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; exit 0"]

    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _subprocess_runner(cmd, tmp_path, timeout=1)
    elapsed = time.monotonic() - start
    assert elapsed < 8, f"runner blocked {elapsed:.1f}s; process tree not killed"

    gpid = int(pidfile.read_text().strip())
    try:
        # Killing the group is async; allow a brief bounded window for reaping.
        deadline = time.monotonic() + 3
        while _alive(gpid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _alive(gpid), f"grandchild {gpid} survived timeout (orphan leak)"
    finally:
        if _alive(gpid):
            os.kill(gpid, 9)  # don't leak the sleep if the assertion failed


def test_subprocess_runner_normal_command_returns_output(tmp_path):
    # A fast, normal command returns real returncode + captured stdout unchanged.
    code, out, err = _subprocess_runner(["sh", "-c", "echo hello"], tmp_path, timeout=10)
    assert code == 0
    assert out.strip() == "hello"
    assert err == ""


def test_subprocess_runner_nonzero_returncode_preserved(tmp_path):
    code, out, err = _subprocess_runner(["sh", "-c", "exit 3"], tmp_path, timeout=10)
    assert code == 3


# --- run_agent dispatches through the injectable runner ---
def test_run_agent_invokes_runner_and_wraps_result():
    p = PROVIDERS["gemini"]
    seen = {}
    def fake_runner(cmd, cwd, timeout):
        seen["cmd"] = cmd
        return 0, "done", ""
    res = run_agent(p, "hi", cwd="/tmp", write=True, runner=fake_runner)
    assert isinstance(res, AgentResult) and res.ok and res.stdout == "done"
    assert seen["cmd"][0] == "gemini" and "--yolo" in seen["cmd"]


def test_run_agent_materializes_cwd_before_running():
    # The vault tree must be materialized (iCloud) before codex scans it.
    p = PROVIDERS["codex"]
    order = []

    def spy_materialize(root):
        order.append(("materialize", root))
        return SimpleNamespace(timed_out=False, still_dataless=0)

    def fake_runner(cmd, cwd, timeout):
        order.append(("run", cwd))
        return 0, "ok", ""

    res = run_agent(p, "hi", cwd="/vault", write=False,
                    runner=fake_runner, materialize_fn=spy_materialize)
    assert res.ok
    assert order[0] == ("materialize", Path("/vault"))   # before the run
    assert order[1][0] == "run"


def test_run_agent_noop_materialize_preserves_behavior():
    p = PROVIDERS["gemini"]
    res = run_agent(p, "hi", cwd="/tmp", write=True,
                    runner=lambda cmd, cwd, timeout: (0, "done", ""),
                    materialize_fn=lambda root: None)
    assert res.ok and res.stdout == "done"


def test_run_agent_warns_when_materialize_times_out(caplog):
    p = PROVIDERS["codex"]
    report = SimpleNamespace(timed_out=True, still_dataless=3,
                             scanned=5, materialized=0)
    with caplog.at_level(logging.WARNING, logger="wiki_daemon.agent"):
        run_agent(p, "hi", cwd="/vault", write=False,
                  runner=lambda cmd, cwd, timeout: (0, "ok", ""),
                  materialize_fn=lambda root: report)
    assert any("dataless" in r.message.lower() for r in caplog.records)


def test_run_agent_swallows_materialize_errors(caplog):
    # Materialize is best-effort: a raising materialize_fn (e.g. brctl missing
    # from PATH -> FileNotFoundError) must never crash the agent run.
    p = PROVIDERS["codex"]

    def boom_materialize(root):
        raise RuntimeError("brctl not found")

    with caplog.at_level(logging.WARNING, logger="wiki_daemon.agent"):
        res = run_agent(p, "hi", cwd="/vault", write=False,
                        runner=lambda cmd, cwd, timeout: (0, "ran anyway", ""),
                        materialize_fn=boom_materialize)
    assert res.ok and res.stdout == "ran anyway"     # the run still happened
    assert any("materialize failed" in r.message.lower() for r in caplog.records)


def test_run_agent_missing_binary_is_unavailable():
    p = PROVIDERS["codex"]
    def boom(cmd, cwd, timeout):
        raise FileNotFoundError("no codex")
    res = run_agent(p, "hi", cwd="/tmp", write=True, runner=boom)
    assert not res.ok and res.returncode == 127


def test_run_agent_interactive_returns_code():
    p = PROVIDERS["claude"]
    rc = run_agent_interactive(p, "hi", cwd="/tmp", write=True,
                               runner=lambda cmd, cwd: 0)
    assert rc == 0


# --- provider selection ---
def test_get_provider_default_is_claude():
    class C:
        provider = "claude"
    assert get_provider(C()).name == "claude"


def test_get_provider_unknown_raises():
    class C:
        provider = "nope"
    with pytest.raises(ValueError):
        get_provider(C())
