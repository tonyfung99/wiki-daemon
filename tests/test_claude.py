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
