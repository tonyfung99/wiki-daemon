"""Tests for agent.py — the pluggable agentic-CLI provider abstraction."""
import pytest

from wiki_daemon.agent import (
    AgentResult, get_provider, run_agent, run_agent_interactive, PROVIDERS,
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
