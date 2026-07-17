# Daemon-side query persistence

**Status:** Design
**Date:** 2026-07-17

## Problem

Answering a wiki question and *saving* it are currently done in a single headless
agent invocation. `ops.query(save=True)` runs the agent in **workspace-write**
mode and asks it to both synthesize the answer and write the result files
(`wiki/queries/<slug>.md`, an `index.md` update, a `log.md` line, and an optional
Marp deck).

Measurement shows this path is unreliable for non-trivial questions:

- The **identical question run read-only** completes in ~111s with a full answer.
- The **same question run in workspace-write (save) mode** does not complete —
  observed timing out at 300s, 600s, and 1800s, and in one 1800s run it wrote
  **nothing** (no query page, `log.md` unchanged).
- This reproduces on a **local (non-iCloud) copy** of the vault, so it is not an
  iCloud/file-materialization problem and not a function of file contents. The
  only variable is read-only vs. workspace-write sandbox mode.
- Workspace-write runs spawn `codex-run-as-fs-helper` grandchildren that leak and
  live for a very long time (tens of minutes to days).

Root cause is that the agent CLI's workspace-write sandbox is unreliable for this
multi-file save workload. The answer generation itself (read-only) is fast and
correct.

## Goal

Make saved queries fast and reliable by **decoupling answer generation from
persistence**: the agent generates the answer read-only; the **daemon writes the
result files itself in plain Python**. The agent's workspace-write path is no
longer used for queries.

Non-goals:
- Generating companion Marp decks (dropped; may return later as a separate,
  non-blocking feature).
- Any change to the HTTP API response shape or the WikiReader client.

## Approach

`ops.query` always runs the agent **read-only**. For `save=True`, it then calls a
new persistence routine that writes the query page and updates the index and log.

```
ops.query(cfg, question, *, save=False, runner=None):
    result = run_agent(provider, query_prompt(question), cfg.vault,
                       write=False, timeout=cfg.query_timeout, ...)
    if not result.ok:
        return QueryResult(ok=False, kind=classify_failure(result), reason=...)
    answer = result.stdout
    if not save:
        return QueryResult(ok=True, answer=answer)
    saved, reason = persist_query(cfg, question, answer)
    return QueryResult(ok=True, answer=answer, saved=saved,
                       reason=("" if saved else reason))
```

Because Python file writes are effectively instantaneous, the answer and the
`saved` status return together at answer-time (~111s). No async/background save is
needed.

## Components

### `query_store.py` (new module)

Single responsibility: persist one query answer into the vault.

```
persist_query(cfg: Config, question: str, answer: str) -> tuple[bool, str]
```

Steps:

1. **Title & slug.** Derive a human-readable title from the question and a
   kebab-case slug from the title.
2. **Query page.** Write `wiki/queries/<slug>.md` = frontmatter + answer body.
   Frontmatter is built with the existing `frontmatter.dump` (YAML-safe
   serialization, so a colon or other special character in the title cannot
   produce invalid frontmatter):
   ```
   ---
   type: query
   title: <title>
   query: <the exact question>
   updated: <YYYY-MM-DD>
   ---
   <answer body, with [[wiki-link]] citations from the agent>
   ```
3. **index.md.** Insert one line under the existing `## Queries` section.
   Default line format: `- [[<slug>|<title>]]` (to be confirmed against the
   vault's actual convention at implementation time; see Open detail).
4. **log.md.** Append `## [<YYYY-MM-DD>] query | <question>`.
5. Return `(True, "")` on success, or `(False, "<error message>")` if any write
   fails (the answer is still returned to the caller; only `saved` is False).

**Collisions / re-asking the same question.** If an existing `wiki/queries/*.md`
page records the same question (normalized the same way `_query_recorded` does
today), update that page in place and bump `updated:` rather than creating a
duplicate. Otherwise use a fresh slug, appending a numeric suffix if the slug
filename already exists for a different question.

**Date.** `updated:` and the log line use `datetime.date.today()` (the daemon runs
in a normal process; no workflow-clock restriction applies).

### Concurrency / single-writer guarantee

The repository invariant is that the daemon is the single writer of `wiki/`.
`index.md` and `log.md` are shared with the ingest path (which writes `wiki/` via
the agent). `persist_query` therefore must be **serialized against ingest writes**
by holding the same write-serialization lock the ingest worker uses. The
implementation plan will pin the exact lock; the query page itself lives under
`wiki/queries/` (not touched by ingest), but the `index.md`/`log.md`
read-modify-write must be protected against lost updates.

### `prompts.query_prompt`

Drop the `save` branch. The function always returns the read-only prompt (no
SAVE-QUERY instructions, no Marp-deck suggestion). Callers pass only the question.

### `_verify_query` (removed)

The old grep-the-vault verification existed to confirm the agent actually wrote
the page. With the daemon doing the write, `saved` is simply the result of
`persist_query`. Remove `_verify_query` and its helpers where no longer used.

### Template `AGENTS.md`

Trim the `SAVE-QUERY` section (and its Marp-deck line) from the scaffold template,
since the daemon now owns persistence. Existing vaults are unaffected — their
stale `SAVE-QUERY` section is simply never triggered because the daemon sends the
read-only prompt.

## Data flow (save=True)

```
API/CLI ── question ──▶ ops.query
                         │
                         ├─ run_agent(read-only)  ──▶ answer (~111s)
                         │
                         └─ persist_query(cfg, question, answer)  [under write lock]
                              ├─ write wiki/queries/<slug>.md
                              ├─ insert line under ## Queries in index.md
                              └─ append line to log.md
                         │
                         ▼
             QueryResult(ok=True, answer, saved, reason)
                         │
                         ▼  (unchanged JSON shape)
             API: answerMarkdown, saved, saveError, citations
```

## Error handling

- Agent read-only failure → `QueryResult(ok=False, kind=…, reason=…)` (unchanged).
- Persistence I/O failure → `QueryResult(ok=True, answer=…, saved=False,
  reason=<error>)`. The user still receives the answer; the API surfaces
  `saveError`, exactly as today.
- Partial write (e.g. page written but `index.md` update fails) → report
  `saved=False` with the reason; the page on disk is harmless. Best-effort, never
  raises out of `ops.query`.

## Testing (TDD)

- `persist_query` writes a query page with correct, valid frontmatter + body.
- A title containing a colon still produces valid YAML frontmatter (regression for
  the frontmatter-poisoning class of bug).
- `index.md` gains exactly one line under `## Queries`; `log.md` gains one line.
- Re-asking the same question updates the existing page in place (no duplicate).
- A slug collision with a *different* question dedupes the filename.
- `ops.query(save=True)` returns `saved=True` on success and
  `saved=False` + reason on a simulated I/O error, with the answer still present.
- `ops.query` always invokes the agent read-only (`write=False`), verified via the
  runner/provider seam.

## Open detail

The exact line format under `## Queries` in `index.md` (wiki-link vs. relative
markdown link) should match the vault's actual maintainer convention. Default to
`- [[<slug>|<title>]]`; confirm against a real `index.md` when the daemon host is
reachable and adjust if the maintainer uses a different style.
