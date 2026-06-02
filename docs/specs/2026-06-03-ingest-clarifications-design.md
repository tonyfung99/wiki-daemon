# Design: Ingest clarifications & review

**Date:** 2026-06-03
**Status:** Approved (pending spec review)

## Problem

Karpathy's LLM-wiki ingest is interactive — the LLM *"discusses key takeaways with
you"* and he prefers to *"ingest sources one at a time and stay involved."* Our
ingest is the opposite: headless `claude -p`, single-shot, no input channel. When
the maintainer hits a genuine judgment call (which entity a name refers to,
conflicting facts, whether two things are the same concept, missing context) it
silently guesses, and the user never learns a decision was made.

The reference implementation (`nashsu/llm_wiki`, same watch-folder model) solves
this by *"flagging items needing human judgment during ingest… user handles
reviews at their convenience — doesn't block ingest."* We adopt that, plus an
interactive mode for hands-on curation.

## Core model

**Interactive when a human is present (TTY); queue when not.** One rule covers
every path:

| Trigger | Mode | Clarifications |
|---|---|---|
| `wiki ingest`/`import` in a terminal | interactive `claude` (no `-p`) | asked live, resolved on the spot |
| `wiki ingest`/`import` piped/scripted (no TTY) | headless `claude -p` | queued to `wiki/review/` |
| daemon (`wiki serve`) | headless `claude -p` | queued to `wiki/review/` |

`--interactive` / `--no-interactive` force the manual mode. The daemon is always
headless (no one is at the terminal). The **review queue is the universal
fallback** — nothing is ever lost; interactive is the live override available
only when someone is present.

Ingest always **completes best-effort** — the maintainer makes a tentative
decision and the source is marked processed. The open question is tracked
separately, so the daemon never blocks or retries forever.

## Decisions (from brainstorming)

- Build **both** mechanisms (interactive + async review queue), selected by TTY.
- Clarifications live in **`wiki/review/`**, one markdown file per item.
- Source still counts as **ingested** when questions are open (track separately).
- Answering runs a **follow-up apply pass in-process** (`wiki review answer`),
  consistent with the other manual write-commands. The daemon stays ingest-only.

## Architecture

### 1. Vault `CLAUDE.md` template + scaffold

Add a `wiki/review/` sub-layer and two algorithm changes (content only — the
daemon and headless ingest need no code change to *produce* clarifications):

- **RAISE CLARIFICATION rule (in INGEST):** when genuinely uncertain about a
  structural decision, the maintainer (a) makes a best-effort choice and notes it
  on the affected page, **and** (b) writes `wiki/review/<slug>.md`. Ingest still
  completes. Never block.
- **APPLY CLARIFICATION operation:** given an *answered* review file, read its
  question + answer, update the relevant wiki pages, append a `log.md` line, then
  **delete** the review file (resolution = removal; `log.md` is the audit trail).
- `scaffold.py` adds `wiki/review` to `_DIRS`.

The prompt (not CLAUDE.md) selects behavior per run: the **interactive** ingest
prompt tells the maintainer to *ask directly and wait*; the **headless** ingest
prompt tells it to *never block — raise a clarification file instead*.

### 2. Review item format — `wiki/review/<slug>.md`

```yaml
---
type: review
status: open                 # open | answered  (resolved = file deleted)
source: raw/sources/2026-06-02-photosynthesis.md
question: "Is 'Calvin cycle' the same concept as 'dark reactions'?"
tentative: "Treated as the same; titled the page 'Calvin Cycle'."
created: 2026-06-03
answer:                      # absent while open; filled by `wiki review answer`
---
<optional human-readable context about the ambiguity>
```

The maintainer creates these (status `open`). `wiki review answer` sets
`status: answered` and fills `answer:`. The apply pass deletes the file on
success. `status`/`source`/`question`/`tentative`/`answer` live in frontmatter
(simple, robust parsing); the body is human context.

### 3. New `review.py` module

`ReviewItem(id, path, status, source, question, tentative, answer)` + pure file
ops, all unit-testable:
- `list_items(cfg) -> list[ReviewItem]` — every `wiki/review/*.md`, sorted.
- `read_item(cfg, id) -> ReviewItem` — raises `FileNotFoundError` if absent.
- `write_answer(cfg, id, answer) -> ReviewItem` — set `status: answered`, set
  `answer:`, atomic write (temp + `os.replace`).

`Config` gains a `review` property (`self.wiki / "review"`), beside `wiki` /
`raw_sources`.

### 4. `ops.py` / `claude.py` — run paths

- `claude.run_claude_interactive(prompt, cwd, allowed_tools, claude_bin, runner=_interactive_runner) -> int`:
  launches `claude <prompt> --allowed-tools …` **without `-p`**, default runner
  `subprocess.run(cmd, cwd=...)` (inherits stdio, no capture), returns the exit
  code. Injectable for tests.
- `ops.ingest_interactive(cfg, source_path, *, store, runner=None) -> IngestResult`:
  dedupe check → `run_claude_interactive` seeded with the interactive ingest
  prompt → after the session exits, run the existing `_verify` → mark processed
  on pass (`kind="ok"`), else `kind="verify_error"` (don't mark; the user may not
  have finished).
- `ops.apply_clarification(cfg, review_id, *, runner=None) -> ApplyResult(ok, reason)`:
  read the item (must be `answered`) → `run_claude` with the APPLY prompt →
  verify the review file was deleted → ok; else fail with a classified reason.
- `prompts.py`: `ingest_prompt(rel, *, interactive=False)` appends the
  ask-directly vs raise-clarification line; new `apply_clarification_prompt(review_rel)`.

### 5. CLI surface (`cli.py`)

- `wiki ingest <file>` / `wiki import <file>` gain a mutually-exclusive
  `--interactive` / `--no-interactive` (tri-state `dest="interactive"`,
  default `None`). The handler picks the path:
  `interactive = ns.interactive if ns.interactive is not None else sys.stdin.isatty()`.
  Interactive → `ingest_interactive`; headless → existing `ingest`.
- `wiki review` — list open/answered clarifications: `id · status · source · question`.
- `wiki review answer <id> "<text>"` — `write_answer` then run
  `apply_clarification` in-process; print resolved / failed.
- `wiki status` — add a `review: N open` line (count of `wiki/review/*.md`).

### 6. Data flow

```
manual ingest/import:
  interactive? (flag, else isatty)
    yes → run_claude_interactive(ingest_prompt(rel, interactive=True))  → verify → processed
    no  → ingest() [claude -p, ingest_prompt(rel)]  → maintainer may write wiki/review/*.md → processed

daemon: ingest() [headless]  → maintainer may write wiki/review/*.md → processed

review:
  wiki review                → review.list_items
  wiki review answer id "…"   → write_answer (status: answered)
                             → apply_clarification [claude -p] → verify file deleted → resolved
```

## Error handling

- Interactive `claude` exiting non-zero → report; do not mark processed.
- `wiki review answer <unknown id>` → `FileNotFoundError` → clear CLI error.
- Answering an item with no `wiki/review/` dir or already-resolved (file gone) →
  clear error.
- Apply pass that leaves the file in place → `ApplyResult(ok=False)`, status stays
  `answered` so it can be retried.
- Non-TTY + `--interactive` explicitly forced → still launches interactive; if
  there's truly no TTY the child `claude` handles it (user's explicit choice).

## Testing

All with injectable fake runners; no real `claude`.
- `review.py`: parse/list round-trip; `read_item` missing → raises; `write_answer`
  sets status+answer atomically; ignores non-`.md` / malformed files gracefully.
- `apply_clarification`: fake runner that deletes the file → ok; one that leaves it
  → fail; unanswered item → fail before running.
- `ingest_interactive`: injected interactive runner (writes the summary page like
  the headless fakes) → verify passes → processed; runner returns non-zero → not
  processed.
- `run_claude_interactive`: builds the right argv (no `-p`), returns the runner's
  code.
- CLI: `--interactive`/`--no-interactive` parsing + TTY auto-detect dispatch
  (monkeypatch `sys.stdin.isatty`); `wiki review` listing; `wiki review answer`
  happy/again paths; `wiki status` review count.
- `scaffold` creates `wiki/review/`; template `CLAUDE.md` contains the RAISE/APPLY
  sections (`init` then grep).

## Out of scope (YAGNI)

- Routing answers through the daemon queue (apply runs in-process).
- Editing-the-file-then-auto-reingest resolution (only `wiki review answer`).
- A TUI/web review panel; bulk-answer; per-item priorities.
- Query / Lint operations (separate follow-up — see the other gap noted vs
  Karpathy's idea).
