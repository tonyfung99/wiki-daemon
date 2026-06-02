# Design: Daemon reliability & observability — Phase 1

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Problem

The daemon swallows failures. `ops.ingest` already returns a `reason` (e.g.
`"claude failed: …401…"`), but `daemon.drain_once` discards the `IngestResult`,
and `serve()` has no logging at all. A real incident proved the cost: the
headless `claude -p` OAuth token expired, every ingest returned `401`, and the
daemon silently retried forever while producing nothing — with no signal to the
operator. See memory `daemon-needs-headless-claude-auth`.

Two gaps to close in Phase 1:

1. **Surface auth/ingest failures** — fail loudly when `claude` can't
   authenticate, both at daemon startup and mid-run.
2. **Make the daemon's health visible** — `wiki status` should show whether the
   daemon is running, its auth state, what it's ingesting, and the last error.

Phase 2 (first-class failed-task tracking, dead-letter, `wiki jobs`/`wiki
retry`) is deferred — see the bottom of this doc. Phase 1 ships as working
software on its own and does **not** change the queue/retry model.

## Decisions (from brainstorming)

- **Startup auth preflight:** run a real `claude -p` probe before the watch
  loop. If it fails: in an interactive TTY, launch `claude setup-token`, re-probe,
  and only enter the loop once auth passes; with no TTY (launchd), log a fatal
  error + remediation and **exit non-zero**.
- **Mid-run auth failure:** log prominently, enter a global pause + exponential
  backoff (30 s → ×2 → cap 900 s), stay alive, auto-recover on the next
  successful drain. (Transient/global, so it throttles the loop rather than
  killing the daemon.)
- **Logging:** to stdout AND a rotating logfile `state_dir/daemon.log`
  (1 MB × 3 backups).
- **Status:** enrich the existing `wiki status` command (no new command in
  Phase 1); `status.json` runtime file is the source of truth.

## Architecture

New single-responsibility modules, each unit-testable with the existing
injectable fake `runner`:

- **`logging_setup.py`** — `configure_logging(cfg)`: idempotent root-logger setup
  with a stdout `StreamHandler` and a `RotatingFileHandler` at
  `state_dir/daemon.log` (maxBytes=1_000_000, backupCount=3). Ensures
  `state_dir` exists. Format: `%(asctime)s %(levelname)s %(message)s`.

- **`health.py`** — `AuthResult(state, detail)` where `state` ∈
  {`ok`, `auth_failed`, `unavailable`}; `probe_auth(cfg, *, runner=None) ->
  AuthResult` runs `run_claude("Reply with exactly: ok", cwd=cfg.vault,
  allowed_tools=["Read"], claude_bin=cfg.claude_bin, timeout=60, runner=...)`,
  then maps the result: success → `ok`; otherwise `classify_failure(result)` →
  `auth`→`auth_failed`, `unavailable`→`unavailable`, `claude_error`→
  `unavailable` (probe can't confirm auth, treat as unavailable). `detail`
  carries a short message for display.

- **`backoff.py`** — `next_backoff(consecutive_failures, *, base=30, factor=2,
  cap=900) -> int`: pure function returning seconds (`min(cap,
  base*factor**(n-1))`). Shared, side-effect free.

- **`runtime.py`** — owns `state_dir/status.json`. `StatusFile(path)` with
  `.update(**fields)` (merge + atomic write via temp+`os.replace`, like
  `StateStore`) and `.read() -> dict` (`{}` if absent/corrupt). Helper
  `is_pid_alive(pid) -> bool` (`os.kill(pid, 0)`, False on `ProcessLookupError`/
  `PermissionError=True`). Fields written: `pid`, `started_at`, `last_attempt`,
  `last_success`, `last_error` (`{msg, kind, file, at}` or null), `auth_state`,
  `auth_since`, `backoff_until`.

### Modified files

- **`claude.py`** — add `classify_failure(result: ClaudeResult) -> str`
  returning `auth` (stdout+stderr lowercased contains `401` / `authenticate` /
  `credentials` / `invalid authentication`), `unavailable`
  (returncode 127 or stderr contains `not found` / `timeout`), else
  `claude_error`. Also harden `run_claude`: wrap the runner call so
  `subprocess.TimeoutExpired` → `ClaudeResult(ok=False, returncode=-1,
  stderr="timeout")` and `FileNotFoundError` → `returncode=127,
  stderr="claude binary not found"` (prevents the daemon crashing and feeds
  `unavailable` classification).

- **`ops.py`** — `IngestResult` gains `kind: str = ""`. Set it on every path:
  `skipped`→`"skipped"`, claude failure→`classify_failure(result)`, verify
  failure→`"verify_error"`, success→`"ok"`. `reason` is unchanged.

- **`daemon.py`** —
  - `serve()` calls `configure_logging(cfg)` first, then `_preflight_auth(cfg,
    *, isatty_fn=sys.stdin.isatty, setup_token_fn=…, probe_fn=probe_auth)`.
    Preflight: probe; if `ok`, log and continue. If not: when `isatty_fn()` is
    true, log the failure + remediation, run `setup_token_fn(cfg)` (subprocess
    `claude setup-token` with inherited stdio), re-probe, repeat until `ok` or
    the user aborts (`setup_token_fn` returns non-zero / re-probe still fails →
    prompt to retry; abort → return exit code 2). When `isatty_fn()` is false,
    log fatal + remediation and return exit code 2.
  - `serve()` returns an `int` exit code; `__main__.main()` returns it.
  - Write `status.json` on start (`pid`, `started_at`, `auth_state="ok"`).
  - `drain_once(...)` is extended to log each job (start/success/failure with
    `reason`), update `status.json` (`last_attempt`, `last_success`/
    `last_error`), and report transient failures. On a result with `kind` in
    {`auth`, `unavailable`}, the serve loop increments a consecutive-failure
    counter and sets `backoff_until = now + next_backoff(n)`; any success resets
    it. The loop skips draining while `now < backoff_until`. (The queue's
    complete-on-failure + reconcile-rediscovery behavior is unchanged in
    Phase 1; backoff only throttles the loop during an outage so it stops
    hammering `claude` every tick.)
  - `finally`: best-effort clear `pid` from `status.json` on clean shutdown.

- **`doctor.py`** — add a `tool:claude-auth` row driven by `probe_auth(cfg)`:
  PASS when `ok`; FAIL with remediation (`run \`claude setup-token\``) when
  `auth_failed`; WARN when `unavailable`.

- **`cli.py`** — enrich `cmd_status(cfg)` to read `status.json` + the queue dir +
  `processed.json` and print a health block:
  ```
  daemon:     running (pid 12345, since 2026-06-02 15:00)
  auth:       FAILING since 15:10 (401) — run `claude setup-token`
  queue:      2 pending, 1 ingesting (raw/sources/photosynthesis.md)
  processed:  7 sources
  last error: [15:10] claude failed: 401 … (photosynthesis.md)
  ```
  When `status.json` is absent or its `pid` is dead → `daemon: not running
  (stale pid)`, but still show queue (`pending-*`/`inflight-*` counts) and
  processed counts read straight from disk so the command is always useful.
  Queue counts come from globbing `cfg.queue_dir`; "ingesting" file = the
  `inflight-*` job's payload.

## Data flow

```
serve()
  → configure_logging → status.json{pid,started_at}
  → _preflight_auth (probe; setup-token if TTY; else exit 2)
  → loop:
       if now < backoff_until: sleep, continue
       drain_once: per job → ingest → log + status.json update
                   on auth/unavailable kind → backoff_until = now+next_backoff
                   on success → reset backoff
       periodic reconcile (unchanged)
```

`wiki status` and `wiki doctor` are read-only consumers: `status` reads
`status.json`+queue+processed; `doctor` calls `probe_auth`.

## Error handling

- Probe/ingest never crash the daemon: `run_claude` swallows
  `TimeoutExpired`/`FileNotFoundError` into a `ClaudeResult`.
- Corrupt/missing `status.json` → `StatusFile.read()` returns `{}`; `wiki
  status` degrades to disk-derived counts.
- Stale pid (daemon died without clearing) → `is_pid_alive` false → reported as
  not running.

## Testing

All with the injectable fake `runner`; no real `claude` calls.

- `classify_failure`: auth signatures vs generic stderr vs not-found/timeout.
- `run_claude` hardening: timeout and missing-binary map to ClaudeResult.
- `probe_auth`: ok / auth_failed / unavailable via fake runners.
- `next_backoff`: schedule values and cap.
- `runtime.StatusFile`: update/read round-trip, atomic overwrite, missing/corrupt
  file → `{}`; `is_pid_alive` for live (own pid) and dead pid.
- `_preflight_auth`: (a) probe ok → proceeds; (b) non-TTY + auth_failed → returns
  exit 2, no setup-token; (c) TTY + auth_failed then ok → calls `setup_token_fn`
  once, proceeds; (d) TTY + persistent failure + abort → exit 2. All via injected
  `isatty_fn`/`setup_token_fn`/`probe_fn`.
- `ops.ingest`: `kind` set correctly on skipped/auth/verify_error/ok paths.
- enriched `cmd_status`: running, not-running/stale-pid, and auth-failing
  renders (capsys), with a seeded `status.json` + queue files.
- `configure_logging`: idempotent; creates `daemon.log`.

## Out of scope (Phase 1 / YAGNI)

- Token auto-refresh; `wiki status --watch`; HTTP/metrics endpoint;
  desktop notifications.

## Phase 2 (deferred — separate plan)

First-class failed-task tracking, to replace today's complete-on-failure +
reconcile-rediscovery:

- Richer job JSON (`attempts`, `last_error`, `last_kind`, `next_retry_at`) and
  new `failed-*` / `deadletter-*` queue states.
- `drain_once` transitions: success→complete; deterministic failure
  (`verify_error`/`claude_error`)→`failed-` with per-task backoff, dead-letter
  after **N=5** attempts (to be confirmed at Phase 2 time); transient
  (`auth`/`unavailable`)→no attempt burned + global backoff (Phase 1 behavior).
- Eligibility-aware `dequeue` (pending or due `failed`; skip dead-letter/not-due).
- `enqueue` dedupes across **all** job states so reconcile can't resurrect a
  dead-lettered file.
- New `wiki jobs` (list states + errors) and `wiki retry [file]` (requeue
  failed/dead-letter).
