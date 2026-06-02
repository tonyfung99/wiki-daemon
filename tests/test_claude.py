# tests/test_claude.py
from wiki_daemon.claude import run_claude, ClaudeResult


def test_builds_command_and_runs_in_vault(tmp_path):
    captured = {}

    def fake_runner(cmd, cwd, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return 0, "done\n", ""

    result = run_claude(
        prompt="ingest this",
        cwd=tmp_path,
        allowed_tools=["Read", "Write", "Edit"],
        claude_bin="claude",
        timeout=120,
        runner=fake_runner,
    )
    assert isinstance(result, ClaudeResult)
    assert result.ok is True
    assert result.stdout == "done\n"
    assert captured["cwd"] == tmp_path
    assert captured["cmd"][0] == "claude"
    assert "-p" in captured["cmd"]
    assert "ingest this" in captured["cmd"]
    # allowed-tools passed as a comma-joined value
    assert "Read,Write,Edit" in captured["cmd"]
    # headless daemon must not block on permission prompts
    assert "--dangerously-skip-permissions" in captured["cmd"]


def test_nonzero_exit_is_not_ok(tmp_path):
    def fake_runner(cmd, cwd, timeout):
        return 1, "", "boom"

    result = run_claude("x", tmp_path, ["Read"], "claude", 10, runner=fake_runner)
    assert result.ok is False
    assert result.stderr == "boom"


# append to tests/test_claude.py
import subprocess

from wiki_daemon.claude import classify_failure, run_claude, ClaudeResult


def _res(returncode=1, stdout="", stderr=""):
    return ClaudeResult(ok=(returncode == 0), returncode=returncode,
                        stdout=stdout, stderr=stderr)


def test_classify_auth_from_401():
    assert classify_failure(_res(stderr="API Error: 401 Invalid authentication")) == "auth"


def test_classify_auth_from_credentials_word():
    assert classify_failure(_res(stdout="Failed to authenticate. credentials")) == "auth"


def test_classify_unavailable_from_127():
    assert classify_failure(_res(returncode=127, stderr="claude: not found")) == "unavailable"


def test_classify_unavailable_from_timeout():
    assert classify_failure(_res(returncode=-1, stderr="timeout")) == "unavailable"


def test_classify_generic_claude_error():
    assert classify_failure(_res(stderr="some other problem")) == "claude_error"


def test_run_claude_catches_timeout():
    def boom(cmd, cwd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    r = run_claude("p", cwd=".", allowed_tools=["Read"], runner=boom)
    assert r.ok is False and r.returncode == -1 and "timeout" in r.stderr


def test_run_claude_catches_missing_binary():
    def boom(cmd, cwd, timeout):
        raise FileNotFoundError("claude")
    r = run_claude("p", cwd=".", allowed_tools=["Read"], runner=boom)
    assert r.ok is False and r.returncode == 127 and "not found" in r.stderr.lower()
