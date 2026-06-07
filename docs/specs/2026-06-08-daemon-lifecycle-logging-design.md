# Daemon lifecycle logging — design

**Status:** approved (brainstorm)
**Date:** 2026-06-08

## Problem

`wiki serve` only logs **terminal** outcomes (`ingested` / `skipped` /
`ingest FAILED`) plus auth and backoff. Everything leading up to an ingest is
silent, so an operator watching `daemon.log` sees nothing while a file is
detected, queued, and processed — most painfully, a large source is silent for
the entire `claude` run, with no signal that work is underway.

Silent events today:

- **File detected/enqueued** — the watcher (`_Handler._maybe` → enqueue) logs
  nothing when a clip lands.
- **Ingest start** — `drain_once` logs only *after* `claude` returns.
- **Not-ready defer** — when a file isn't materialized/stable, the job is dropped
  for reconcile to retry, with no log.
- **Reconcile sweep** — `enqueue_reconcile` returns a count but logs nothing.
- **Startup** — no "watching … reconcile every Ns" line.

## Goal

Add lifecycle logging so each file has a visible start→end bracket and the
operator can see the daemon working, without flooding the log:

- Key transitions at **INFO** (visible by default).
- Per-file watcher events at **DEBUG**, surfaced on demand via `wiki serve
  --verbose`.

## Events

| Event | Level | Location | Message |
|-------|-------|----------|---------|
| Startup | INFO | `serve()` after `observer.start()` | `watching <raw_sources> (reconcile every Ns)` |
| Ingest start | INFO | `drain_once`, before `run(...)` | `ingesting <file>` |
| Not-ready defer | INFO | `drain_once`, `prepare_fn` False branch | `deferred <file> (not materialized/stable yet)` |
| Reconcile sweep | INFO | `enqueue_reconcile`, when `n > 0` | `reconcile: enqueued N file(s)` |
| Detected | DEBUG | `_Handler._maybe`, when relevant | `detected <file>` |

Existing terminal lines (`ingested` / `skipped` / `ingest FAILED` / auth /
backoff) are unchanged. `ingesting <file>` before the `claude` call plus the
existing `ingested <file>` after gives each file a start and end bracket.

## Decisions

- **Reconcile logs only when it enqueued something** (`n > 0`), so idle sweeps
  every `reconcile_interval` stay silent.
- **Not-ready defer at INFO.** It can repeat across reconcile cycles while a file
  stays dataless, but that is genuinely useful signal ("still waiting on iCloud")
  and materialization is usually quick. Accepted over DEBUG.
- **`--verbose` sets the logger to DEBUG.** `configure_logging` already takes a
  `level` and applies `setLevel` even when re-invoked, so only `serve` needs to
  choose the level.

## Non-goals (YAGNI)

- No structured/JSON logs — keep the `asctime level message` format.
- No per-file progress percentage (the daemon cannot see inside `claude`).
- No new log file or rotation change — same `daemon.log`.

## Architecture

### `daemon.py`

- `enqueue_reconcile`: after enqueuing, `if n: _log.info("reconcile: enqueued %d
  file(s)", n)`. Return value unchanged.
- `drain_once`:
  - in the `prepare_fn(...)` True branch, before `run(...)`:
    `_log.info("ingesting %s", job.payload)`.
  - add the False branch: `else: _log.info("deferred %s (not materialized/stable
    yet)", job.payload)`.
- `_Handler._maybe`: when `is_relevant`, `_log.debug("detected %s", p)` (before
  enqueue).
- `serve(cfg, *, reconcile_interval=300.0, tick=2.0, verbose=False)`:
  - `configure_logging(cfg, level=logging.DEBUG if verbose else logging.INFO)`.
  - after `observer.start()`:
    `_log.info("watching %s (reconcile every %ss)", cfg.raw_sources,
    reconcile_interval)`.

### `cli.py`

- `serve` subparser: `srv.add_argument("--verbose", "-v", action="store_true")`.
- `main` serve branch: `serve(cfg, reconcile_interval=ns.reconcile_interval,
  verbose=ns.verbose)`.

## Testing strategy (TDD)

Use pytest `caplog` against the `wiki_daemon` logger; reuse the existing
fake-runner / `prepare_fn` injection in `drain_once` tests. No real `claude`.

- `tests/test_daemon.py`
  - `drain_once` logs `ingesting <payload>` before the result (a successful fake
    run leaves both `ingesting` and `ingested` in the records, in that order).
  - `drain_once` with `prepare_fn=lambda p: False` logs `deferred … (not
    materialized` and does not log `ingesting`.
  - `enqueue_reconcile` logs `reconcile: enqueued N` when it finds files, and
    logs nothing when it finds zero.
- `tests/test_cli.py`
  - `serve --verbose` / `-v` parses to `verbose=True`; default `False`.
  - `main(["serve", …, "--verbose"])` passes `verbose=True` to a faked `serve`.

## Risks

- `caplog` requires the logger to propagate to the root handler pytest installs.
  `configure_logging` sets `propagate = False` and is idempotent (returns early
  if handlers exist). The `drain_once` / `enqueue_reconcile` tests do not call
  `configure_logging`, so the `wiki_daemon` logger keeps default propagation and
  `caplog` captures it. The `serve` startup-line path is exercised via the
  existing fake-serve CLI test, not `caplog`, so this does not bite.
