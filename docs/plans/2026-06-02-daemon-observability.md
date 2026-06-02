# Daemon Reliability & Observability (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the daemon's auth/ingest failures (startup preflight + mid-run backoff) and make its health visible via logging and an enriched `wiki status`, without changing the queue/retry model.

**Architecture:** New single-responsibility modules — `backoff.py` (pure schedule), `health.py` (`probe_auth`), `logging_setup.py`, `runtime.py` (`status.json`) — plus failure classification in `claude.py`/`ops.py` and wiring in `daemon.py`, `doctor.py`, `cli.py`. Everything is unit-tested with the existing injectable fake `runner`; no real `claude` calls in tests.

**Tech Stack:** Python 3.12, stdlib `logging`/`logging.handlers`, `pytest`. Run tests with `.venv/bin/pytest`.

**Reference spec:** `docs/specs/2026-06-02-daemon-observability-design.md`

---

## File Structure

- **Create** `src/wiki_daemon/backoff.py` — pure `next_backoff(n)`.
- **Create** `src/wiki_daemon/health.py` — `AuthResult` + `probe_auth(cfg, runner=None)`.
- **Create** `src/wiki_daemon/logging_setup.py` — `configure_logging(cfg)`.
- **Create** `src/wiki_daemon/runtime.py` — `now_iso()`, `is_pid_alive()`, `StatusFile`.
- **Modify** `src/wiki_daemon/claude.py` — `classify_failure()` + harden `run_claude`.
- **Modify** `src/wiki_daemon/ops.py` — add `kind` to `IngestResult`.
- **Modify** `src/wiki_daemon/daemon.py` — preflight, `DrainResult`, logging/status/backoff, `serve()->int`.
- **Modify** `src/wiki_daemon/__main__.py` — propagate `serve()` exit code.
- **Modify** `src/wiki_daemon/doctor.py` — `tool:claude-auth` row.
- **Modify** `src/wiki_daemon/cli.py` — enriched `cmd_status`.
- **Tests:** new `tests/test_backoff.py`, `tests/test_health.py`, `tests/test_logging_setup.py`, `tests/test_runtime.py`, `tests/test_preflight.py`; extend `tests/test_claude.py`, `tests/test_ops.py`, `tests/test_daemon.py`, `tests/test_doctor.py`, `tests/test_cli.py`.

---

### Task 1: `backoff.py` — pure schedule

**Files:**
- Create: `src/wiki_daemon/backoff.py`
- Test: `tests/test_backoff.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backoff.py
from wiki_daemon.backoff import next_backoff


def test_first_failure_is_base():
    assert next_backoff(1) == 30


def test_doubles_each_failure():
    assert next_backoff(2) == 60
    assert next_backoff(3) == 120
    assert next_backoff(4) == 240


def test_capped():
    assert next_backoff(100) == 900


def test_zero_or_negative_is_base():
    assert next_backoff(0) == 30
    assert next_backoff(-5) == 30
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_backoff.py -v`
Expected: FAIL with `ModuleNotFoundError: wiki_daemon.backoff`.

- [ ] **Step 3: Implement**

```python
# src/wiki_daemon/backoff.py
"""Pure exponential-backoff schedule shared by the daemon's pause logic."""
from __future__ import annotations


def next_backoff(consecutive_failures: int, *, base: int = 30, factor: int = 2,
                 cap: int = 900) -> int:
    """Seconds to wait after `consecutive_failures` in a row. n<=1 -> base;
    doubles each step; never exceeds `cap`."""
    n = max(1, consecutive_failures)
    return min(cap, base * factor ** (n - 1))
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_backoff.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/backoff.py tests/test_backoff.py
git commit -m "feat: pure exponential backoff schedule"
```

End every commit body in this plan with:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 2: `claude.py` — failure classification + hardening

**Files:**
- Modify: `src/wiki_daemon/claude.py`
- Test: `tests/test_claude.py`

- [ ] **Step 1: Read the current file**

Read `src/wiki_daemon/claude.py`. It defines `ClaudeResult(ok, returncode, stdout, stderr)`, `_subprocess_runner`, and `run_claude(...)` which calls `runner(cmd, Path(cwd), timeout)` directly (no exception handling).

- [ ] **Step 2: Write the failing tests (append to tests/test_claude.py)**

```python
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
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_claude.py -v`
Expected: FAIL with `cannot import name 'classify_failure'`.

- [ ] **Step 4: Implement in `src/wiki_daemon/claude.py`**

Add `import subprocess` is already present. Add the classifier after `ClaudeResult`:

```python
_AUTH_SIGNS = ("401", "authenticate", "credentials", "invalid authentication")


def classify_failure(result: "ClaudeResult") -> str:
    """Bucket a failed ClaudeResult: 'auth' | 'unavailable' | 'claude_error'."""
    blob = f"{result.stdout}\n{result.stderr}".lower()
    if any(s in blob for s in _AUTH_SIGNS):
        return "auth"
    if result.returncode == 127 or "not found" in blob or "timeout" in blob:
        return "unavailable"
    return "claude_error"
```

Replace the body of `run_claude` that calls the runner (the line
`code, out, err = runner(cmd, Path(cwd), timeout)`) with a guarded version:

```python
    try:
        code, out, err = runner(cmd, Path(cwd), timeout)
    except subprocess.TimeoutExpired:
        return ClaudeResult(ok=False, returncode=-1, stdout="", stderr="timeout")
    except FileNotFoundError:
        return ClaudeResult(ok=False, returncode=127, stdout="",
                            stderr="claude binary not found")
    return ClaudeResult(ok=(code == 0), returncode=code, stdout=out, stderr=err)
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_claude.py -v`
Expected: PASS (all, including pre-existing claude tests).

- [ ] **Step 6: Commit**

```bash
git add src/wiki_daemon/claude.py tests/test_claude.py
git commit -m "feat: classify claude failures and harden run_claude"
```

---

### Task 3: `ops.py` — `kind` on `IngestResult`

**Files:**
- Modify: `src/wiki_daemon/ops.py`
- Test: `tests/test_ops.py`

- [ ] **Step 1: Write the failing tests (append to tests/test_ops.py)**

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_ops.py -v`
Expected: FAIL (`kind` attribute missing / wrong value).

- [ ] **Step 3: Implement in `src/wiki_daemon/ops.py`**

Add `kind` to the dataclass:

```python
@dataclass
class IngestResult:
    ok: bool
    skipped: bool = False
    reason: str = ""
    kind: str = ""
```

Import the classifier at the top (next to the other `from wiki_daemon.claude import`):

```python
from wiki_daemon.claude import Runner, run_claude, classify_failure
```

Set `kind` on each return path inside `ingest(...)`:

```python
    if store.is_processed(src.sha256):
        return IngestResult(ok=True, skipped=True, kind="skipped")
```
```python
    if not result.ok:
        return IngestResult(ok=False, kind=classify_failure(result),
                            reason=f"claude failed: {result.stderr[:200]}")
```
```python
    ok, reason = _verify(cfg, rel)
    if not ok:
        return IngestResult(ok=False, reason=reason, kind="verify_error")

    store.mark_processed(src.sha256, str(source_path))
    return IngestResult(ok=True, kind="ok")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_ops.py -v`
Expected: PASS (all ops tests).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/ops.py tests/test_ops.py
git commit -m "feat: tag IngestResult with a failure kind"
```

---

### Task 4: `health.py` — auth probe

**Files:**
- Create: `src/wiki_daemon/health.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_health.py
from wiki_daemon.config import Config
from wiki_daemon.health import probe_auth, AuthResult


def _cfg(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.vault.mkdir(parents=True, exist_ok=True)
    return cfg


def test_probe_ok(tmp_path):
    cfg = _cfg(tmp_path)
    res = probe_auth(cfg, runner=lambda cmd, cwd, timeout: (0, "ok", ""))
    assert isinstance(res, AuthResult)
    assert res.state == "ok"


def test_probe_auth_failed(tmp_path):
    cfg = _cfg(tmp_path)
    res = probe_auth(cfg, runner=lambda cmd, cwd, timeout:
                     (1, "", "API Error: 401 Invalid authentication credentials"))
    assert res.state == "auth_failed"
    assert "401" in res.detail


def test_probe_unavailable_on_missing_binary(tmp_path):
    cfg = _cfg(tmp_path)
    def boom(cmd, cwd, timeout):
        raise FileNotFoundError("claude")
    res = probe_auth(cfg, runner=boom)
    assert res.state == "unavailable"


def test_probe_unavailable_on_generic_error(tmp_path):
    cfg = _cfg(tmp_path)
    res = probe_auth(cfg, runner=lambda cmd, cwd, timeout: (1, "", "weird boom"))
    assert res.state == "unavailable"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_health.py -v`
Expected: FAIL with `ModuleNotFoundError: wiki_daemon.health`.

- [ ] **Step 3: Implement**

```python
# src/wiki_daemon/health.py
"""Probe whether headless `claude -p` can authenticate. Shared by the daemon
startup preflight and `wiki doctor`."""
from __future__ import annotations

from dataclasses import dataclass

from wiki_daemon.claude import Runner, classify_failure, run_claude
from wiki_daemon.config import Config


@dataclass
class AuthResult:
    state: str   # "ok" | "auth_failed" | "unavailable"
    detail: str


def probe_auth(cfg: Config, *, runner: Runner | None = None) -> AuthResult:
    """Run a tiny `claude -p` probe and classify the outcome."""
    kwargs = {} if runner is None else {"runner": runner}
    res = run_claude(
        prompt="Reply with exactly: ok",
        cwd=cfg.vault,
        allowed_tools=["Read"],
        claude_bin=cfg.claude_bin,
        timeout=60,
        **kwargs,
    )
    if res.ok:
        return AuthResult("ok", "authenticated")
    detail = (res.stderr or res.stdout or "no output").strip()[:160]
    kind = classify_failure(res)
    if kind == "auth":
        return AuthResult("auth_failed", detail)
    return AuthResult("unavailable", detail)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_health.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/health.py tests/test_health.py
git commit -m "feat: claude auth probe"
```

---

### Task 5: `logging_setup.py`

**Files:**
- Create: `src/wiki_daemon/logging_setup.py`
- Test: `tests/test_logging_setup.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_logging_setup.py
import logging

from wiki_daemon.config import Config
from wiki_daemon.logging_setup import configure_logging


def test_configure_is_idempotent(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    logger = configure_logging(cfg)
    n = len(logger.handlers)
    logger2 = configure_logging(cfg)
    assert logger2 is logger
    assert len(logger2.handlers) == n  # not doubled
    assert n == 2  # stdout + rotating file


def test_writes_to_daemon_log(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    logger = configure_logging(cfg)
    logger.info("hello daemon")
    for h in logger.handlers:
        h.flush()
    log_path = cfg.state_dir / "daemon.log"
    assert log_path.exists()
    assert "hello daemon" in log_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_logging_setup.py -v`
Expected: FAIL with `ModuleNotFoundError: wiki_daemon.logging_setup`.

- [ ] **Step 3: Implement**

```python
# src/wiki_daemon/logging_setup.py
"""Configure the `wiki_daemon` logger: stdout + a rotating daemon.log."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from wiki_daemon.config import Config

_FMT = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")


def configure_logging(cfg: Config, *, level: int = logging.INFO) -> logging.Logger:
    """Idempotent: wires a stdout handler and a rotating file handler
    (1 MB x 3) at state_dir/daemon.log. Returns the `wiki_daemon` logger."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("wiki_daemon")
    logger.setLevel(level)
    if logger.handlers:
        return logger
    sh = logging.StreamHandler()
    sh.setFormatter(_FMT)
    fh = RotatingFileHandler(
        cfg.state_dir / "daemon.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(_FMT)
    logger.addHandler(sh)
    logger.addHandler(fh)
    logger.propagate = False
    return logger
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_logging_setup.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/logging_setup.py tests/test_logging_setup.py
git commit -m "feat: daemon logging to stdout + rotating file"
```

---

### Task 6: `runtime.py` — status file + pid + timestamp

**Files:**
- Create: `src/wiki_daemon/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runtime.py
import os

from wiki_daemon.runtime import StatusFile, is_pid_alive, now_iso


def test_status_roundtrip(tmp_path):
    sf = StatusFile(tmp_path / "status.json")
    sf.update(pid=123, auth_state="ok")
    assert sf.read() == {"pid": 123, "auth_state": "ok"}


def test_status_update_merges(tmp_path):
    sf = StatusFile(tmp_path / "status.json")
    sf.update(pid=123)
    sf.update(auth_state="failing")
    data = sf.read()
    assert data["pid"] == 123 and data["auth_state"] == "failing"


def test_status_missing_is_empty(tmp_path):
    assert StatusFile(tmp_path / "nope.json").read() == {}


def test_status_corrupt_is_empty(tmp_path):
    p = tmp_path / "status.json"
    p.write_text("{not json", encoding="utf-8")
    assert StatusFile(p).read() == {}


def test_pid_alive_true_for_self():
    assert is_pid_alive(os.getpid()) is True


def test_pid_alive_false_for_dead():
    assert is_pid_alive(999_999) is False
    assert is_pid_alive(0) is False
    assert is_pid_alive(None) is False


def test_now_iso_format():
    s = now_iso()
    assert s.endswith("Z") and "T" in s and len(s) == 20
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_runtime.py -v`
Expected: FAIL with `ModuleNotFoundError: wiki_daemon.runtime`.

- [ ] **Step 3: Implement**

```python
# src/wiki_daemon/runtime.py
"""Daemon runtime status file (status.json) + small process helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class StatusFile:
    """Atomic, merge-on-update JSON status file. Missing/corrupt reads as {}."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def update(self, **fields) -> dict:
        data = self.read()
        data.update(fields)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        return data
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_runtime.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/runtime.py tests/test_runtime.py
git commit -m "feat: runtime status file + pid/timestamp helpers"
```

---

### Task 7: `daemon.py` — startup auth preflight + `serve() -> int`

**Files:**
- Modify: `src/wiki_daemon/daemon.py`
- Modify: `src/wiki_daemon/__main__.py`
- Test: `tests/test_preflight.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_preflight.py
from wiki_daemon.config import Config
from wiki_daemon.daemon import _preflight_auth
from wiki_daemon.health import AuthResult


def _cfg(tmp_path):
    return Config(vault=tmp_path / "v", state_root=tmp_path / "s")


def test_preflight_ok_proceeds(tmp_path):
    calls = {"setup": 0}
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: AuthResult("ok", "authenticated"),
        isatty_fn=lambda: True,
        setup_token_fn=lambda cfg: calls.__setitem__("setup", calls["setup"] + 1),
    )
    assert ok is True and calls["setup"] == 0


def test_preflight_non_tty_failure_returns_false_without_setup(tmp_path):
    calls = {"setup": 0}
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: AuthResult("auth_failed", "401"),
        isatty_fn=lambda: False,
        setup_token_fn=lambda cfg: calls.__setitem__("setup", calls["setup"] + 1),
    )
    assert ok is False and calls["setup"] == 0


def test_preflight_tty_recovers_after_setup(tmp_path):
    seq = iter([AuthResult("auth_failed", "401"), AuthResult("ok", "authenticated")])
    calls = {"setup": 0}
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: next(seq),
        isatty_fn=lambda: True,
        setup_token_fn=lambda cfg: calls.__setitem__("setup", calls["setup"] + 1),
    )
    assert ok is True and calls["setup"] == 1


def test_preflight_tty_user_aborts(tmp_path):
    ok = _preflight_auth(
        _cfg(tmp_path),
        probe_fn=lambda cfg: AuthResult("auth_failed", "401"),
        isatty_fn=lambda: True,
        setup_token_fn=lambda cfg: None,
        input_fn=lambda prompt: "n",
    )
    assert ok is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_preflight.py -v`
Expected: FAIL with `cannot import name '_preflight_auth'`.

- [ ] **Step 3: Implement in `src/wiki_daemon/daemon.py`**

Add imports near the top (after the existing imports):

```python
import logging
import os
import subprocess
import sys

from wiki_daemon.backoff import next_backoff
from wiki_daemon.health import AuthResult, probe_auth
from wiki_daemon.logging_setup import configure_logging
from wiki_daemon.runtime import StatusFile, now_iso

_log = logging.getLogger("wiki_daemon")
```

Add the setup-token runner and the preflight function (above `serve`):

```python
def _run_setup_token(cfg: Config) -> int:
    """Launch the interactive `claude setup-token` flow (inherits stdio)."""
    return subprocess.run([cfg.claude_bin, "setup-token"]).returncode


def _preflight_auth(
    cfg: Config,
    *,
    probe_fn=probe_auth,
    isatty_fn=None,
    setup_token_fn=_run_setup_token,
    input_fn=input,
) -> bool:
    """Verify headless claude auth before watching. Returns True to proceed.
    Non-interactive failure -> False (caller exits non-zero). Interactive
    failure -> launch `claude setup-token`, re-probe, repeat until ok or abort."""
    isatty_fn = isatty_fn or sys.stdin.isatty
    res = probe_fn(cfg)
    if res.state == "ok":
        _log.info("auth: ok")
        return True
    if not isatty_fn():
        _log.error("auth FAILED (%s): %s. Run `claude setup-token`, then restart. "
                   "Exiting.", res.state, res.detail)
        return False
    while res.state != "ok":
        _log.warning("auth FAILED (%s): %s. Launching `claude setup-token`...",
                     res.state, res.detail)
        setup_token_fn(cfg)
        res = probe_fn(cfg)
        if res.state == "ok":
            _log.info("auth: ok after setup-token")
            return True
        if input_fn("auth still failing — retry setup-token? [y/N] ").strip().lower() != "y":
            _log.error("auth not resolved; aborting startup.")
            return False
    return True
```

Change the `serve` signature/return type. Replace the current `def serve(cfg: Config, *, reconcile_interval: float = 300.0, tick: float = 2.0) -> None:` and its first lines with:

```python
def serve(cfg: Config, *, reconcile_interval: float = 300.0, tick: float = 2.0) -> int:
    configure_logging(cfg)
    if not _preflight_auth(cfg):
        return 2
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.raw_sources.mkdir(parents=True, exist_ok=True)  # defensive: observer needs it
    status = StatusFile(cfg.state_dir / "status.json")
    status.update(pid=os.getpid(), started_at=now_iso(), auth_state="ok",
                  backoff_until=None, last_error=None)
    q = JobQueue(cfg.queue_dir)
    store = StateStore(cfg.processed_json)
```

> NOTE: The remainder of `serve` (observer + loop) is updated in Task 8. For
> this task, make `serve` compile and return: keep the existing observer/loop
> body but ensure all paths return an `int` (the infinite loop only exits via
> `KeyboardInterrupt`; wrap it so the `finally` still runs and `return 0` at the
> end). Minimal change for this task:

```python
    enqueue_reconcile(cfg, q, store)  # startup sweep (backstop)
    observer = Observer()
    observer.schedule(_Handler(cfg, q), str(cfg.raw_sources), recursive=False)
    observer.start()
    last_reconcile = time.monotonic()
    try:
        while True:
            drain_once(cfg, q)
            if time.monotonic() - last_reconcile >= reconcile_interval:
                enqueue_reconcile(cfg, q, StateStore(cfg.processed_json))
                last_reconcile = time.monotonic()
            time.sleep(tick)
    finally:
        observer.stop()
        observer.join()
        status.update(pid=None)
    return 0
```

Update `src/wiki_daemon/__main__.py` to propagate the exit code. Replace the two
lines `    serve(cfg, reconcile_interval=ns.reconcile_interval)` and `    return 0`
with:

```python
    return serve(cfg, reconcile_interval=ns.reconcile_interval)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_preflight.py tests/test_daemon.py -v`
Expected: PASS (preflight tests pass; existing daemon tests still pass).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/daemon.py src/wiki_daemon/__main__.py tests/test_preflight.py
git commit -m "feat: daemon startup auth preflight; serve returns exit code"
```

---

### Task 8: `daemon.py` — `DrainResult`, logging/status, mid-run backoff

**Files:**
- Modify: `src/wiki_daemon/daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Update the two existing drain tests + add failure tests**

In `tests/test_daemon.py`, the existing `test_drain_once_runs_and_completes` and
`test_drain_skips_when_not_ready` assert on an `int` return. Update both to read
`.ingested`, and give their fake result objects a `kind`:

Replace `class R:  # minimal result\n            ok = True; skipped = False; reason = ""`
(and the analogous one) so each fake result has `kind = "ok"`, e.g.:

```python
        class R:
            ok = True; skipped = False; reason = ""; kind = "ok"
        return R()
```

Replace `assert drained == 1` with `assert drained.ingested == 1`, and
`assert drained == 0` with `assert drained.ingested == 0`.

Then append new tests:

```python
# append to tests/test_daemon.py
from wiki_daemon.runtime import StatusFile


def _auth_fail_ingest(config, path):
    class R:
        ok = False; skipped = False; reason = "claude failed: 401"; kind = "auth"
    return R()


def test_drain_reports_transient_auth_failure(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    status = StatusFile(cfg.state_dir / "status.json")

    res = drain_once(cfg, q, ingest_fn=_auth_fail_ingest,
                     prepare_fn=lambda p: True, status=status)

    assert res.ingested == 0
    assert res.transient_kind == "auth"
    data = status.read()
    assert data["last_error"]["kind"] == "auth"
    assert data["last_error"]["file"].endswith("a.md")


def test_drain_records_success_in_status(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    cfg.raw_sources.mkdir(parents=True)
    q = JobQueue(cfg.queue_dir)
    q.enqueue(Job(type="ingest", payload=str(cfg.raw_sources / "a.md")))
    status = StatusFile(cfg.state_dir / "status.json")

    def ok_ingest(config, path):
        class R: ok = True; skipped = False; reason = ""; kind = "ok"
        return R()

    res = drain_once(cfg, q, ingest_fn=ok_ingest, prepare_fn=lambda p: True,
                     status=status)
    assert res.ingested == 1 and res.transient_kind is None
    assert "last_success" in status.read()
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_daemon.py -v`
Expected: FAIL (`DrainResult`/`transient_kind`/`status=` not present).

- [ ] **Step 3: Implement in `src/wiki_daemon/daemon.py`**

Add a `DrainResult` dataclass near the top (after imports). Requires
`from dataclasses import dataclass`:

```python
from dataclasses import dataclass


@dataclass
class DrainResult:
    ingested: int = 0
    transient_kind: str | None = None  # "auth"/"unavailable" if seen this drain
```

Replace the whole `drain_once` function with a version that logs, writes status,
and returns a `DrainResult`:

```python
def drain_once(cfg: Config, q: JobQueue, *, ingest_fn=None,
               prepare_fn=prepare_source, status=None) -> DrainResult:
    """Run pending jobs serially. Materialize + stability-gate each file before
    ingest; if not ready, drop the job (reconcile re-enqueues later). Logs each
    job, records the latest success/error in `status`, and reports whether a
    transient (auth/unavailable) failure occurred so the caller can back off."""
    store = StateStore(cfg.processed_json)
    run = ingest_fn or (lambda config, path: _ingest(config, Path(path), store=store))
    result = DrainResult()
    while True:
        job = q.dequeue()
        if job is None:
            break
        if prepare_fn(Path(job.payload)):
            if status is not None:
                status.update(last_attempt=now_iso())
            r = run(cfg, job.payload)
            kind = getattr(r, "kind", "") or ("ok" if r.ok else "claude_error")
            if r.ok and not r.skipped:
                result.ingested += 1
                _log.info("ingested %s", job.payload)
                if status is not None:
                    status.update(last_success=now_iso(), last_error=None,
                                  auth_state="ok")
            elif r.skipped:
                _log.info("skipped (already processed) %s", job.payload)
            else:
                _log.warning("ingest FAILED (%s) %s — %s", kind, job.payload, r.reason)
                if status is not None:
                    status.update(last_error={"msg": r.reason, "kind": kind,
                                              "file": job.payload, "at": now_iso()})
                if kind in ("auth", "unavailable"):
                    result.transient_kind = kind
        q.complete(job)
    return result
```

Now wire backoff into the `serve` loop. Replace the `try:`/`while True:` block
from Task 7 with this version (it tracks consecutive transient failures and
skips draining while paused):

```python
    last_reconcile = time.monotonic()
    consecutive = 0
    backoff_until = 0.0
    try:
        while True:
            if time.monotonic() < backoff_until:
                time.sleep(tick)
                continue
            res = drain_once(cfg, q, status=status)
            if res.transient_kind:
                consecutive += 1
                delay = next_backoff(consecutive)
                backoff_until = time.monotonic() + delay
                _log.error("auth/%s failure — pausing ingest for %ss",
                           res.transient_kind, delay)
                status.update(auth_state="failing", auth_since=now_iso(),
                              backoff_until=now_iso())
            elif res.ingested > 0:
                consecutive = 0
                backoff_until = 0.0
                status.update(auth_state="ok", backoff_until=None)
            if time.monotonic() - last_reconcile >= reconcile_interval:
                enqueue_reconcile(cfg, q, StateStore(cfg.processed_json))
                last_reconcile = time.monotonic()
            time.sleep(tick)
    finally:
        observer.stop()
        observer.join()
        status.update(pid=None)
    return 0
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_daemon.py -v`
Expected: PASS (updated + new daemon tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/wiki_daemon/daemon.py tests/test_daemon.py
git commit -m "feat: daemon logs jobs, records status, backs off on auth failure"
```

---

### Task 9: `doctor.py` — `tool:claude-auth` row

**Files:**
- Modify: `src/wiki_daemon/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing tests (append to tests/test_doctor.py)**

```python
# append to tests/test_doctor.py
from wiki_daemon.config import Config
from wiki_daemon.doctor import check_auth
from wiki_daemon.health import AuthResult


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_doctor.py -v`
Expected: FAIL with `cannot import name 'check_auth'`.

- [ ] **Step 3: Implement in `src/wiki_daemon/doctor.py`**

Add the import at the top (next to the existing `from wiki_daemon...` lines):

```python
from wiki_daemon.health import probe_auth
```

Add the check function (after `check_tooling`):

```python
def check_auth(cfg: Config, *, probe_fn=probe_auth) -> Check:
    res = probe_fn(cfg)
    if res.state == "ok":
        return Check("tool:claude-auth", "PASS", "authenticated")
    if res.state == "auth_failed":
        return Check("tool:claude-auth", "FAIL",
                     f"{res.detail} — run `claude setup-token`")
    return Check("tool:claude-auth", "WARN", f"could not verify: {res.detail}")
```

Wire it into `run_doctor` so it runs only when the vault exists (the probe `cwd`
is the vault). After the `checks += check_tooling()` line, add:

```python
    if cfg.vault.exists():
        checks.append(check_auth(cfg))
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_doctor.py -v`
Expected: PASS (new + existing doctor tests).

- [ ] **Step 5: Commit**

```bash
git add src/wiki_daemon/doctor.py tests/test_doctor.py
git commit -m "feat: wiki doctor checks claude auth"
```

---

### Task 10: `cli.py` — enriched `wiki status`

**Files:**
- Modify: `src/wiki_daemon/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests (append to tests/test_cli.py)**

```python
# append to tests/test_cli.py
import os

from wiki_daemon.cli import _render_status
from wiki_daemon.runtime import StatusFile


def test_render_status_running_and_auth_ok(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(
        pid=os.getpid(), started_at="2026-06-02T15:00:00Z", auth_state="ok")
    out = _render_status(cfg)
    assert "running" in out and f"pid {os.getpid()}" in out
    assert "auth:" in out and "ok" in out


def test_render_status_auth_failing(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(
        pid=os.getpid(), auth_state="failing", auth_since="2026-06-02T15:10:00Z",
        last_error={"msg": "claude failed: 401", "kind": "auth",
                    "file": "raw/sources/x.md", "at": "2026-06-02T15:10:00Z"})
    out = _render_status(cfg)
    assert "FAILING" in out and "setup-token" in out
    assert "last error" in out and "x.md" in out


def test_render_status_not_running_stale_pid(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    StatusFile(cfg.state_dir / "status.json").update(pid=999_999)
    # seed queue + processed so disk-derived counts still show
    cfg.queue_dir.mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / "pending-00000001-ingest.json").write_text(
        '{"type":"ingest","payload":"raw/sources/p.md"}', encoding="utf-8")
    out = _render_status(cfg)
    assert "not running" in out
    assert "1 pending" in out


def test_render_status_no_status_file(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    out = _render_status(cfg)  # no status.json at all
    assert "not running" in out
    assert "processed:" in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL with `cannot import name '_render_status'`.

- [ ] **Step 3: Implement in `src/wiki_daemon/cli.py`**

Add imports near the top:

```python
from wiki_daemon.runtime import StatusFile, is_pid_alive
```

Add the renderer and rewrite `cmd_status`:

```python
def _render_status(cfg: Config) -> str:
    status = StatusFile(cfg.state_dir / "status.json").read()

    pid = status.get("pid")
    if pid and is_pid_alive(pid):
        since = status.get("started_at", "?")
        daemon = f"running (pid {pid}, since {since})"
    elif pid:
        daemon = "not running (stale pid)"
    else:
        daemon = "not running"

    if status.get("auth_state") == "failing":
        err = status.get("last_error") or {}
        kind = err.get("kind", "auth")
        since = status.get("auth_since", "?")
        auth = f"FAILING since {since} ({kind}) — run `claude setup-token`"
    elif status.get("auth_state") == "ok":
        auth = "ok"
    else:
        auth = "unknown"

    qdir = cfg.queue_dir
    pending = len(list(qdir.glob("pending-*.json"))) if qdir.exists() else 0
    inflight = sorted(qdir.glob("inflight-*.json")) if qdir.exists() else []
    if inflight:
        try:
            payload = json.loads(inflight[0].read_text(encoding="utf-8"))["payload"]
        except (json.JSONDecodeError, KeyError, OSError):
            payload = "?"
        ingesting = f", 1 ingesting ({payload})"
    else:
        ingesting = ""

    store = StateStore(cfg.processed_json)
    processed = len(store._data)  # noqa: SLF001

    lines = [
        f"daemon:     {daemon}",
        f"auth:       {auth}",
        f"queue:      {pending} pending{ingesting}",
        f"processed:  {processed} sources",
    ]
    if status.get("last_error"):
        e = status["last_error"]
        lines.append(f"last error: [{e.get('at','?')}] {e.get('msg','?')} "
                     f"({e.get('file','?')})")
    return "\n".join(lines)


def cmd_status(cfg: Config) -> int:
    print(_render_status(cfg))
    return 0
```

Add `import json` at the top of `cli.py` if not already present.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS (new render tests + existing CLI tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wiki_daemon/cli.py tests/test_cli.py
git commit -m "feat: enrich wiki status with daemon health"
```

---

### Task 11: Docs — README status/doctor + CLAUDE.md note

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the `wiki status` table row**

In `README.md`, replace the `wiki status` command-table row:

```markdown
| `wiki status --vault <path>` | Show daemon health: running?, auth state, queue depth, processed count, last error. |
```

- [ ] **Step 2: Note the daemon logfile and auth in "How it works"**

In the "How it works (briefly)" list in `README.md`, append a bullet:

```markdown
- **Observability** = the daemon logs to stdout and a rotating `daemon.log` in
  its state dir; `wiki status` surfaces health; `wiki doctor` verifies `claude`
  is authenticated (headless `claude -p` needs its own valid login — use
  `claude setup-token` for an unattended daemon).
```

- [ ] **Step 3: Verify the suite still passes**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document daemon health, logging, and auth"
```

---

## Self-Review Notes

- **Spec coverage:** logging (Task 5), auth probe (Task 4), startup preflight TTY/non-TTY (Task 7), doctor auth row (Task 9), failure classification + `kind` (Tasks 2–3), mid-run backoff (Tasks 1, 8), status file (Task 6), enriched `wiki status` with disk fallback (Task 10), `run_claude` hardening (Task 2), README (Task 11). All Phase 1 spec sections mapped. Phase 2 intentionally excluded.
- **Placeholder scan:** none — every code/test step is complete. The Task 7 NOTE shows the exact interim `serve` body to keep it compiling before Task 8 finalizes the loop.
- **Type consistency:** `AuthResult(state, detail)`, `probe_auth(cfg, runner=)`, `classify_failure(result)->str`, `IngestResult(..., kind)`, `DrainResult(ingested, transient_kind)`, `StatusFile(path).read()/.update(**)`, `is_pid_alive(pid)`, `now_iso()`, `_preflight_auth(cfg, probe_fn, isatty_fn, setup_token_fn, input_fn)`, `_render_status(cfg)` — names/signatures used consistently across tasks. `drain_once` returns `DrainResult` (Task 8 updates the two existing tests that previously expected an int).
```
