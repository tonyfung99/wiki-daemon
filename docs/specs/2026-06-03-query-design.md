# Design: Query & save-query CLI ops

**Date:** 2026-06-03
**Status:** Approved (pending spec review)

## Problem

Karpathy's LLM-wiki has three core operations — **Ingest, Query, Lint**. We have
Ingest (plus clarifications). The wiki is write-only so far: you can build it but
not ask it anything. This adds the **read side** — query the wiki for a cited
answer, and optionally file valuable answers back as pages that compound — which
is the whole point of the pattern ("knowledge compiles once and stays current").

Scope is **CLI ops only** (like ingest today, in-process). The HTTP API + hermes/
Telegram layer the original `docs/design.md` envisions (M3) is deferred. **Lint**
is a separate follow-up spec.

## Decisions (from brainstorming)

- `wiki query "<q>"` is **read-only**; `--save` persists in the **same pass** (no
  caching, no separate command). To save an answer after seeing it, re-run with
  `--save` (a second LLM call — the simple/stateless tradeoff).
- A saved query page is verified **by contract** (a `query:` frontmatter field
  echoing the question), not by guessing a filename — mirroring how ingest
  verifies via `sources:`. No `cites:` enforcement; cross-links live in the body.

## Commands

- **`wiki query --vault <path> "<question>"`** — read-only. Runs `claude -p` in
  the vault with **Read/Glob/Grep only**, following the QUERY op in `CLAUDE.md`:
  read `index.md` → open relevant pages → synthesize an answer **with citations**.
  Prints the answer to stdout. Exit 0 on success; 1 on claude failure (auth etc.,
  classified like ingest).
- **`wiki query --vault <path> "<question>" --save`** — same pass, but the
  maintainer **also** writes `wiki/queries/<slug>.md`, updates `index.md`, appends
  to `log.md`. Then `_verify_query` confirms it landed before reporting `saved`.

Read-only query is safe to run anytime, including while the daemon writes `wiki/`
— reads are free in this design (no queue, no lock). A query may transiently read
a page mid-write; acceptable for a personal tool (the design states "Reads are
free for anyone").

## Architecture

### `ops.py` — `query` + `QueryResult`

```python
@dataclass
class QueryResult:
    ok: bool
    answer: str = ""
    saved: bool = False
    reason: str = ""
    kind: str = ""


def query(cfg, question, *, save=False, runner=None) -> QueryResult: ...
```

- `allowed_tools`: `["Read", "Glob", "Grep"]` (read-only) by default;
  `["Read", "Glob", "Grep", "Write", "Edit"]` when `save=True`.
- prompt: `query_prompt(question, save=save)`.
- Run `run_claude(...)`. On failure → `QueryResult(ok=False,
  kind=classify_failure(result), reason=f"claude failed: {result.stderr[:200]}")`.
- On success → `answer = result.stdout`.
  - `save=False` → `QueryResult(ok=True, answer=answer)`.
  - `save=True` → `ok2, reason = _verify_query(cfg, question)`; return
    `QueryResult(ok=True, answer=answer, saved=ok2, reason=("" if ok2 else reason))`.
    (The answer is still returned/printed even if the save verification fails — the
    user sees the answer; only `saved` is False.)

### `_verify_query(cfg, question) -> (bool, str)`

Mirrors `ops._verify` / `_source_referenced` (verify by contract):

- A `wiki/queries/*.md` exists whose `query:` frontmatter equals the asked
  question after whitespace-normalization (`" ".join(s.split())`, case-sensitive
  on content but tolerant of surrounding whitespace). → else
  `(False, "no query page records this question")`.
- `index.md` present → else `(False, "index.md missing")`.
- `log.md` present → else `(False, "log.md missing")`.
- else `(True, "")`.

### Saved query page format (documented in template)

```yaml
---
type: query
title: <Human Title>
query: "<the exact question asked>"
updated: <YYYY-MM-DD>
---
<answer prose with [[wiki-links]] to the pages it cited>
```

### `prompts.py`

```python
def query_prompt(question: str, *, save: bool = False) -> str: ...
```
- Base: "Follow the QUERY operation in CLAUDE.md. Answer this question from the
  wiki, citing the pages you used: <question>. Read index.md, open relevant
  pages, synthesize with [[wiki-links]] citations. Do not modify anything under
  raw/."
- `save=False` adds: "This is READ-ONLY — do not create or edit any files; just
  print the answer."
- `save=True` adds: "Then SAVE-QUERY: write the answer as wiki/queries/<kebab>.md
  with frontmatter `type: query` and `query: \"<the question>\"`, update
  wiki/index.md, and append to wiki/log.md."

### `cli.py`

- New `query` subparser: positional `question`, flag `--save`.
- `cmd_query(cfg, question, *, save=False) -> int`:
  - `result = ops.query(cfg, question, save=save)` (via module symbol, monkeypatchable).
  - On `not result.ok` → print `query failed: {reason}` to stderr, return 1.
  - Print `result.answer` to stdout.
  - If `save`: print `saved` if `result.saved` else `save failed: {reason}` to
    stderr (and return 1 on save failure; the answer was already printed).
  - Return 0.

### Vault `CLAUDE.md` template

Add two sections (the `type: query` enum and `wiki/queries/` layer already exist):

- **QUERY operation** — read `index.md`, open the relevant pages, synthesize an
  answer that **cites** the pages used via `[[wiki-links]]`. Read-only by default.
- **SAVE-QUERY** — when asked to save, also write `wiki/queries/<kebab-slug>.md`
  (`type: query`, `query:` = the question, `title`, `updated`), update
  `wiki/index.md`, and append `## [<YYYY-MM-DD>] query | <question>` to
  `wiki/log.md`.

## Data flow

```
wiki query "Q"            → ops.query(save=False) → claude -p [Read,Glob,Grep] → print stdout
wiki query "Q" --save     → ops.query(save=True)  → claude -p [+Write,Edit]
                            → writes wiki/queries/<slug>.md + index/log
                            → _verify_query (query: field matches Q) → print answer + "saved"
```

## Error handling

- claude failure (auth/unavailable/error) → `ok=False`, classified `kind`, exit 1,
  no answer printed.
- `--save` but verification fails (maintainer wrote nothing / wrong page) → answer
  still printed; `save failed: <reason>`; exit 1.
- Empty question string → argparse requires the positional; an empty `""` is
  passed through to claude (which will respond it can't answer) — no special-case.

## Testing

Fake runner, no real claude (mirrors `tests/test_ops.py`).

- read-only `query`: fake runner returns `(0, "the answer", "")` → `ok=True`,
  `answer=="the answer"`, `saved is False`; assert the command argv contained no
  `Write` (read-only tools) — checked via a runner that records `cmd`.
- save `query`: fake runner writes `wiki/queries/q.md` with `query: "<Q>"` +
  `index.md`/`log.md` → `saved=True`; runner that writes nothing → `saved=False`
  with a verify reason; answer still returned.
- `_verify_query`: matches by normalized `query:` field; whitespace-tolerant;
  mismatched question → fail; missing index/log → fail.
- claude failure → `ok=False`, `kind` set, no answer.
- `prompts.query_prompt`: contains the question + "QUERY"; read-only variant says
  read-only/no files; save variant mentions wiki/queries and `type: query`.
- CLI: parser accepts `query "<q>"` and `--save`; `cmd_query` prints the answer;
  `--save` prints `saved`/`save failed`; failure path returns 1.
- template: `init` then assert `CLAUDE.md` contains "QUERY" and "SAVE-QUERY".

## Out of scope (YAGNI / later)

- HTTP API + hermes/Telegram (M3, original design.md §HTTP).
- Caching answers / deciding to save after the fact (re-run with `--save`).
- `cites: [...]` frontmatter enforcement (cross-links live in the body).
- Read-lane/queue coordination with the daemon (reads are lock-free).
- **Lint** (`wiki lint`) — separate follow-up spec to complete the third core op.
