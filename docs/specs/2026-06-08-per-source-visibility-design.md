# Per-source ingest visibility — design

**Status:** approved (brainstorm)
**Date:** 2026-06-08

## Problem

When a daemon owns the vault, `wiki import`/`ingest` defer (land + queue) and
return immediately. Three observability gaps follow:

1. **No path to poll.** The defer message prints only the landed filename, so the
   user/agent has nothing to pass to `wiki review --source` / a status check.
2. **No progress visibility.** Large material takes time to ingest; nothing
   reports that the daemon is working on it.
3. **Ambiguous empty review.** `wiki review` printing "no open clarifications"
   conflates three states: still processing, processed-with-nothing-to-clarify,
   and all-cleared.

Root cause: there is no way to ask **"what is the state of *this* source?"** All
three gaps are messaging around that one missing capability.

## Goal

Make a source's ingest lifecycle observable, for both an agent (machine-readable,
poll-until-done) and a human:

- A pure `source_state(cfg, source)` deriving state from existing files.
- `wiki status --source <path>` → state word + exit code for poll loops.
- `wiki review --source <path>` disambiguates the empty case.
- The defer message prints the exact follow-up commands with the source path.

## Decisions

- **State is derived, not stored.** No new daemon bookkeeping; state comes from
  the queue files, `processed.json`, and `status.json` that already exist.
- **Five states, first-match precedence:** `processed` > `ingesting` > `queued`
  > `failed` > `untracked`.
- **Exit-code map** (for agent poll loops):
  | state | exit |
  |-------|------|
  | processed | 0 |
  | failed | 1 |
  | untracked | 2 |
  | queued / ingesting | 3 |
  `3` = in-progress is the loop signal ("poll again").
- **Empty `review --source` consults `source_state`** to disambiguate.
- **Plain `wiki review` (no `--source`) is unchanged** — no single source to
  report on.

## Non-goals (YAGNI)

- No `--json` output (state word + exit code chosen; can add later).
- No progress percentage / live bar — "ingesting" is as granular as the daemon
  knows.
- No push notification on completion — polling only.
- No new daemon writes.

## Architecture

### New module: `progress.py`

```python
from dataclasses import dataclass

@dataclass
class SourceState:
    state: str          # queued | ingesting | processed | failed | untracked
    detail: str = ""    # e.g. the failure reason

def source_state(cfg, source) -> SourceState:
    """Derive a source's ingest lifecycle state from existing files. `source`
    may be vault-relative (raw/sources/x.md) or absolute; it is normalized to the
    absolute form the queue stores as job payloads."""
```

Resolution (first match wins):

1. **processed** — `read_source(abs).sha256` is in `StateStore(cfg.processed_json)`.
   (Wrapped in try/except: if the file is gone, skip this check.)
2. **ingesting** — `str(abs)` appears as the `payload` of an `inflight-*.json`
   in `cfg.queue_dir`.
3. **queued** — `str(abs)` appears as the `payload` of a `pending-*.json`.
4. **failed** — `status.json` `last_error.file == str(abs)`; `detail` = its `msg`.
5. **untracked** — none of the above.

Notes:
- Payloads are stored as absolute path strings (both the CLI defer enqueue and
  the daemon watcher use `str(<absolute path>)`), so matching is on the absolute
  form. A vault-relative input is resolved against `cfg.vault`.
- Pure function, no `claude`, no network — fully unit-testable.

### `cli.py`

- **`status` subparser** gains `--source <path>`. When given, `cmd_status`
  switches to per-source mode:
  - compute `source_state`, print `<state>` and, if present, `  <detail>`.
  - return the exit code per the map above.
  - (No `--source` → the existing dashboard, unchanged, returns 0.)
- **`review` empty-state** (only when `--source` was passed and the filtered list
  is empty): consult `source_state` and print one of —
  - queued/ingesting → `still processing (<state>) — no clarifications yet`
  - processed → `processed — no open clarifications`
  - failed → `ingest failed — run \`wiki status --source <path>\``
  - untracked → `not found — is the path right?`
  Return 0 in all these cases (listing is not an error). Non-empty lists render
  as today.
- **`_defer_to_daemon` message** prints the vault-relative landed path and the
  two follow-up commands:
  ```
  queued for the running daemon (N pending)
    track:  wiki status --source <rel>
    review: wiki review --source <rel>
  ```
  where `<rel> = path.relative_to(cfg.vault).as_posix()`. The interactive
  headless note (when applicable) still follows.

### Docs

- `README`: document `wiki status --source <path>` (states + exit codes) and the
  poll-then-review loop.
- `skills/wiki/SKILL.md`: update the capture recipe so the agent, after a defer,
  polls `wiki status --source <path>` until `processed` (exit 0), then runs
  `wiki review --source <path>`. Clarify that an empty review now distinguishes
  "still processing" from "nothing to clarify".

## Testing strategy (TDD)

Pure-Python, no network / no `claude`:

- `tests/test_progress.py`
  - **processed**: a source whose sha is in `processed.json` → `processed`.
  - **ingesting**: payload in an `inflight-*.json` → `ingesting`.
  - **queued**: payload in a `pending-*.json` → `queued`.
  - **failed**: `status.json` `last_error.file` matches → `failed`, detail set.
  - **untracked**: none of the above → `untracked`.
  - precedence: processed wins over a lingering pending entry.
  - accepts vault-relative and absolute input equivalently.
- `tests/test_cli.py`
  - `status --source` parser; exit codes 0/1/2/3 for the four outcomes
    (monkeypatch `source_state`).
  - `review --source` empty-state messages for each state.
  - `_defer_to_daemon` message includes both `track:` and `review:` lines with
    the vault-relative path.

## Risks

- The queue scan reads JSON files each call; negligible at expected queue sizes.
- Path normalization must match the payload form exactly (absolute, resolved);
  covered by the relative-vs-absolute test. macOS `/tmp`→`/private/tmp` symlink
  resolution is handled by `.resolve()` as elsewhere in the codebase.
- `status.json` `last_error` is global (last error only), so `failed` reflects
  the most recent failure; an older failed source whose error was overwritten
  reads as `untracked` unless re-queued. Acceptable — the daemon re-enqueues via
  reconcile, so such a source is usually `queued` again.
