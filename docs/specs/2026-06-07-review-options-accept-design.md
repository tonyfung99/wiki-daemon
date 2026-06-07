# Option-bearing, accept-by-default review — design

**Status:** approved (brainstorm)
**Date:** 2026-06-07

## Problem

The `wiki review` queue reads like a list of blocking questions, but it is
really an **audit log of decisions the LLM already made**: during ingest the
maintainer picks a best-effort `tentative` choice, applies it to the wiki, and
records the open question for optional human review. Two gaps:

1. **No options when answering directly.** A review item carries only
   `question` + `tentative` + free-text body. The "options" a user sees when an
   agent drives the CLI are invented by the *agent*, not stored in the item — so
   calling the CLI directly forces composing a free-text answer with nothing to
   pick from.
2. **Accepting the default is expensive.** The only resolution path
   (`review answer`) always runs a Claude `apply_clarification` pass — even when
   the user just agrees with the choice the LLM already applied.

## Goal

Make review **option-bearing and accept-by-default**, aligning with the
Karpathy LLM-wiki idea — cheap to accept the machine's judgment, pay (an LLM
call) only when you override:

- The LLM enumerates concrete candidate answers (`options`) at ingest time and
  marks the one it applied as `recommended`.
- Accepting the default is instant and free (delete + log line, no LLM).
- Picking an option or writing custom text runs the existing apply pass.
- One source may raise multiple independent questions; the listing groups them
  by source.
- **Interactive ingest gets the same option shape.** When a human runs
  `wiki ingest` in a terminal, the live "ASK" presents the same numbered
  options + recommended default (and accepts a number or free text) instead of
  free-form prose — a prompt/template change only, no new protocol.

## Decisions

- **Schema gains `options` + `recommended`.** `options` is a YAML list of
  candidate answer strings; `recommended` is the 1-based index of the option the
  LLM already applied (== `tentative`). Both optional — items without them fall
  back to today's free-text flow (back-compat).
- **The LLM produces the options.** The vault `CLAUDE.md` RAISE CLARIFICATION
  section instructs the maintainer to write 2–4 concrete options and set
  `recommended`. Intelligence lives in the maintainer brain, not the CLI, so it
  works whether a human or an agent answers later.
- **Accept = delete + log, no LLM.** The `tentative` was already applied during
  ingest, so the wiki already reflects it. Accepting removes the review file and
  appends `## [<date>] review (accepted) | <question>` to `wiki/log.md` (pure
  Python). No Claude call, no auth needed.
- **Pick / custom = apply pass.** `--pick N` expands option N's text into the
  answer; free text is used verbatim. Both run the existing
  `apply_clarification` (Claude rewrites pages, then deletes the file).
- **List groups by source.** `wiki review` clusters items under their `source:`
  so "this material raised these N questions" is visible.

## Non-goals (YAGNI)

- No interactive TUI / arrow-key picker — answers stay scriptable
  (number/flag args), agent-friendly. (Interactive ingest's numbered ask is
  Claude-rendered text over stdio, not a CLI picker.)
- No resumable "sync structured ingest" handshake for agents (model B). The
  review queue is the agent's interactive surface; interactive ingest's option
  parity is for humans at a terminal only.
- No bulk accept (`wiki review accept --source <file>` / `--all`) yet. The
  per-source grouping is designed so this is an easy fast-follow.
- Ingest autonomy is unchanged — it still applies the tentative and completes.
- The cosmetic `wiki/review/` content for *old* items is not migrated; they
  simply render as free-text-only (no options).

## Schema

```yaml
---
type: review
status: open                      # open | answered
source: raw/sources/<file>.md
question: "<the specific question>"
options:                          # optional; 2–4 concrete candidate answers
  - "<option 1 — the applied/tentative choice>"
  - "<option 2>"
  - "<option 3>"
recommended: 1                    # optional; 1-based index of the applied option
tentative: "<best-effort choice already applied>"   # == options[recommended-1]
created: <YYYY-MM-DD>
answer: "<filled on answer>"       # added by write_answer (pick/custom path)
---
<body: rationale>
```

## CLI surface

Three verbs, escalating cost:

```
wiki review                          # list open items, grouped by source;
                                     #   each shows question + numbered options,
                                     #   ★ marks the recommended default
wiki review accept <id>              # take the default → delete file + log line, NO LLM
wiki review answer <id> --pick N     # choose option N      → apply pass (LLM)
wiki review answer <id> "free text"  # custom override      → apply pass (LLM)
```

- `--pick N` and a positional answer string are mutually exclusive; `--pick`
  avoids guessing whether `2` means "option 2" or the literal text. Out-of-range
  `N` errors cleanly (exit 2) without calling Claude.
- `accept` on an item with no `recommended`/`options` still works: it accepts the
  `tentative` (delete + log), since that is what was applied.

## Interactive ingest parity (human terminal)

The two ingest paths reuse one concept — "enumerate 2–4 options + a recommended
default" — on two surfaces:

| Path | Surface | Mechanism |
|------|---------|-----------|
| Headless (`--no-interactive`, agent/daemon) | review file `options:`/`recommended:` | RAISE CLARIFICATION template |
| Interactive (`wiki ingest` in a TTY, human) | live numbered ask in the terminal | the ASK branch of the same template + interactive prompt |

This is a **prompt/template change only — no code protocol**: interactive ingest
is already Claude conversing over inherited stdio (`run_claude_interactive`), so
we just instruct it that, when it asks live, it should present the candidate
answers as a numbered list with a marked recommended default and accept either a
number or free text. The agent path is unchanged (async via the review queue,
model A); this only upgrades the human terminal experience from free prose to
pickable options.

Not in scope: a resumable "sync structured ingest" handshake for agents
(model B) — the review queue is the agent's interactive surface.

## Code shape

- `prompts.py` — `ingest_prompt(interactive=True)` gains an instruction: when a
  decision is ambiguous, ASK with 2–4 **numbered options** and a marked
  recommended default, and accept a number or free text. Headless branch
  unchanged (still writes a review file per the template).
- `review.py`
  - `ReviewItem` gains `options: list[str]` and `recommended: int | None`.
  - `_item_from_file` parses them (tolerant: missing → `[]` / `None`).
  - new `accept_item(cfg, id) -> ReviewItem|raises`: assert the file exists,
    append the accepted-log line to `wiki/log.md`, delete the review file. Pure
    file ops, no runner.
  - `write_answer` unchanged for free text; add a helper to resolve `--pick N`
    to `options[N-1]` before calling it.
- `ops.py` — `apply_clarification` unchanged (the pick path just feeds it the
  selected option text via `answer`).
- `cli.py`
  - `review` renderer: group by `source`, number options, mark `recommended`
    with ★.
  - `accept` subcommand → `review.accept_item` then print `accepted <id>`.
  - `answer` subcommand gains `--pick N` (mutually exclusive with the positional
    `text`); validate range; map to option text; then the existing answer→apply
    flow.
- Back-compat: items without `options` render exactly as today and only support
  free-text `answer` (and `accept` of the tentative).

## Vault template (`src/wiki_daemon/templates/CLAUDE.md`)

Extend RAISE CLARIFICATION on both branches:
- **Headless branch** (write a review file): also write `options:` (2–4 concrete
  candidate answers, the applied one first) and `recommended:` (its 1-based
  index, matching `tentative`). Keep the "make a best-effort choice and proceed"
  autonomy.
- **Interactive branch** (ASK the user live): present the same 2–4 candidate
  answers as a **numbered list** with the recommended default marked, and accept
  a number or free text before proceeding.

> This is a template change. Existing vaults pick it up via
> `wiki doctor --fix` only if the whole RAISE CLARIFICATION section is *missing*;
> a vault that already has the section but lacks the options instruction is the
> "exists-but-stale-content" case the drift checker does not catch (documented
> limitation). Such vaults can be refreshed manually. Noted as a known gap.

## Skill (`skills/wiki/SKILL.md`)

Update the Review section to teach the new flow:

- Frame review as "an audit log of decisions the maintainer already applied —
  usually you just `accept`."
- Document `wiki review` (grouped, numbered options, ★ default),
  `wiki review accept <id>` (cheap, no LLM — prefer this for the common case),
  `wiki review answer <id> --pick N`, and `wiki review answer <id> "text"`.
- Note that one source can raise multiple questions, each resolved
  independently.

## Testing strategy (TDD)

Pure-Python, no network / no `claude`:

- `tests/test_review.py`
  - parse an item *with* `options`/`recommended`; and *without* (back-compat →
    `[]`/`None`).
  - `accept_item` deletes the file and appends the accepted-log line, with **no
    runner invoked**; raises on unknown id.
  - `--pick` resolution: `N` → `options[N-1]`; out-of-range raises; `--pick`
    with no `options` errors.
- `tests/test_cli.py`
  - `accept` parser + `answer --pick N` parser (mutually exclusive with text).
  - `cmd_review_accept` prints `accepted` and removes the item (monkeypatched
    log path); `cmd_review_answer --pick N` feeds the right option text to a
    faked `apply_clarification`.
  - `_render_review` groups by source and marks the recommended option.
- `tests/test_prompts.py` — `ingest_prompt(interactive=True)` instructs a
  numbered-options ask with a recommended default; headless
  `ingest_prompt(interactive=False)` still instructs the review-file path.
- `tests/test_skill.py` — existing drift guard still passes (new subcommand
  `accept` is real).

## Risks

- Template "exists-but-stale-content" drift (a vault with RAISE CLARIFICATION but
  no options instruction) is not auto-detected — documented limitation; manual
  refresh or a future version-stamp.
- `recommended` index must stay 1-based and within `options`; the renderer and
  `accept` validate and degrade gracefully (treat as "no recommended") rather
  than crash.
