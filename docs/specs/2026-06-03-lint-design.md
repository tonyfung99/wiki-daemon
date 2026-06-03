# Design: `wiki lint` — wiki health check, deep scan & repair

**Date:** 2026-06-03
**Status:** Approved (pending spec review)

## Problem

Karpathy's third core op is **Lint** — *"periodically health-check for
contradictions, stale claims, orphan pages, and data gaps."* Our `docs/design.md`
adds dead `[[links]]` and **iCloud conflict-duplicates** (`page 2.md`). We have
Ingest and Query; Lint is the missing piece that keeps a compounding wiki from
rotting. This builds detection **and** repair.

Scope is **CLI only** (in-process, like ingest/query today). HTTP API + weekly
cron wiring are deferred (you can call `wiki lint` from cron yourself).

## Command

```
wiki lint --vault <path> [--deep] [--fix] [--yes]
```

- **default** — run **mechanical checks** (pure Python, no `claude`/auth), print a
  findings report. Exit **0 if clean / 1 if any findings** (cron-friendly).
- **`--deep`** — additionally run an **LLM semantic detection** pass
  (contradictions, stale claims, data gaps), appended to the report. Read-only.
- **`--fix`** — resolve detected findings (see Repair). Mutates the vault, so it
  prints the plan and **requires confirmation** (typed `yes`, or `--yes` to skip).
  Non-interactive (no TTY) without `--yes` → refuse, exit non-zero with guidance.
- `--deep --fix` — detect semantic issues *and* repair them too.

## Decisions (from brainstorming)

- **Hybrid + repair, all in one feature.** Mechanical detection is pure Python
  (deterministic, no auth, cron-safe); semantic detection and all judgment
  *repairs* go through `claude -p`.
- **Mechanical fix = conflict-duplicate deletion only** (the one deterministic,
  safe-to-automate destructive fix). Every *other* fix (create-vs-remove a dead
  link, de-orphan, index gaps, contradictions) is **LLM repair** — it needs
  judgment.
- **Destructive/repair always confirmed** (`--fix` prints the plan; typed `yes` or
  `--yes`); non-TTY without `--yes` refuses — mirrors the daemon's caution about
  irreversible actions in the LLM-owned `wiki/`.

## Architecture

Clean seams keep each unit focused and independently testable.

### `lint.py` — pure mechanical detection

```python
@dataclass(frozen=True)
class Finding:
    check: str          # "dead_link" | "conflict_duplicate" | "orphan" | "index_integrity"
    severity: str       # "error" | "warning"
    path: str           # vault-relative path of the offending page/file
    message: str        # human description
    fixable: bool       # mechanically fixable (delete) — True only for conflict_duplicate
    fix_action: str     # "" or "delete_file"


def run_checks(cfg: Config) -> list[Finding]: ...
```

Scans `wiki/{entities,concepts,sources,queries}/*.md` plus `wiki/index.md` /
`wiki/log.md`. **Excludes** `wiki/review/` (transient clarifications, not catalog
pages). Each check is its own pure function; `run_checks` concatenates them,
sorted by (severity, check, path).

- **`_dead_links(cfg)`** — build the set of page titles from every page's `title:`
  frontmatter (whitespace-normalized via `" ".join(s.split())`). For each
  `[[Target]]` in each page body, if `Target` (normalized) isn't a known title →
  `Finding("dead_link", "error", <page>, "link [[Target]] resolves to no page",
  fixable=False)`. (Markdown link parsing: regex `\[\[([^\]]+)\]\]`; strip an
  optional `|alias`.)
- **`_conflict_duplicates(cfg)`** — for files matching `^(?P<base>.+) \d+\.md$` in
  any scanned dir, **only when `<base>.md` exists in the same dir** → `Finding(
  "conflict_duplicate", "warning", <dupe>, "iCloud conflict copy of <base>.md",
  fixable=True, fix_action="delete_file")`. (The base-exists guard avoids
  false-flagging a legit "Chapter 2.md".)
- **`_orphans(cfg)`** — an entity/concept/query page is an orphan if **no other
  page** `[[links]]` to its title **and** `index.md` does not mention its title.
  → `Finding("orphan", "warning", <page>, "not linked from anywhere or indexed",
  fixable=False)`. (Source pages are exempt — they're traced by `sources:`, not
  links.)
- **`_index_integrity(cfg)`** — (a) a scanned page whose title is absent from
  `index.md` → `Finding("index_integrity", "warning", <page>, "missing from
  index.md")`; (b) a `wiki/sources/*.md` whose `sources:` lists a
  `raw/sources/<file>` that doesn't exist → `Finding("index_integrity", "error",
  <page>, "sources: trace points to missing <file>")`; (c) `index.md` / `log.md`
  missing → one finding each. All `fixable=False`.

### `ops.lint_deep(cfg, *, runner=None) -> LintScan`

```python
@dataclass
class LintScan:
    ok: bool
    report: str = ""    # the LLM's semantic findings text
    reason: str = ""
    kind: str = ""
```

Runs `run_claude(lint_prompt(), cwd=cfg.vault, allowed_tools=_READ_ONLY_TOOLS)` —
read-only semantic scan for contradictions / stale claims / data gaps. Returns the
model's text as `report`. On claude failure → `ok=False, kind=classify_failure`.
No vault writes.

### `ops.lint_repair(cfg, findings, *, deep_report="", runner=None) -> ApplyResult`

Runs `run_claude(lint_repair_prompt(findings_text, deep_report), cwd=cfg.vault,
allowed_tools=_ALLOWED_TOOLS)` — Write/Edit access — instructing the maintainer to
fix the listed findings per the LINT REPAIR contract in `CLAUDE.md` (create a
missing page *or* remove a dead link by judgment; de-orphan via link/index;
reconcile contradictions; update `index.md`/`log.md`). Returns `ApplyResult(ok,
reason)` (reuse the existing dataclass). Verification is "re-run mechanical
checks" by the CLI (below), not inside this function.

### `prompts.py`

- `lint_prompt() -> str` — "Follow the LINT operation in CLAUDE.md. Read-only:
  scan the wiki for contradictions, stale claims, and data gaps. List each issue
  with the page(s) involved. Do not modify any files."
- `lint_repair_prompt(findings_text: str, deep_report: str = "") -> str` —
  "Follow the LINT REPAIR operation in CLAUDE.md. Fix these findings: <findings>.
  <deep_report if any>. Create missing pages or remove dead links by judgment,
  de-orphan, fix index gaps, reconcile contradictions; update index.md and append
  to log.md. Do not modify anything under raw/."

### `cli.cmd_lint(cfg, *, deep=False, fix=False, yes=False) -> int`

1. `findings = lint.run_checks(cfg)`.
2. If `deep`: `scan = lint_deep(cfg)`; if `not scan.ok` print `lint deep failed:
   …` to stderr (continue with mechanical results; deep is best-effort).
3. Print `_render_findings(findings, deep_report)` — grouped by check, each line
   `[severity] check  path — message`, then `N findings (M fixable)`; include the
   `Semantic findings (LLM)` section if `--deep`.
4. If `fix` and there is something to do (any `fixable` finding **or** any
   non-fixable finding **or** a deep report):
   - Print the plan: "will delete K conflict-duplicate file(s); will run an LLM
     repair pass over the remaining findings."
   - **Confirm:** if not `yes`: if no TTY → print "refusing to fix without
     confirmation; re-run with --yes" to stderr, return 2. Else prompt
     `Proceed? [type 'yes'] ` via `input`; anything but `yes` → abort, return 0.
   - Apply mechanical deletions: for each `fix_action == "delete_file"`, unlink
     the file (log each). 
   - If there are non-fixable findings or a deep report: `lint_repair(cfg,
     findings, deep_report=scan.report)`; on failure print `repair failed: …`,
     return 1.
   - Re-run `run_checks`; print the remaining findings.
5. Exit code: `0` if (after any fix) no findings remain; else `1`. (A pure
   detection run with findings → `1`.)

### Vault `CLAUDE.md` template

Add a **LINT operation** section: read-only health check — list contradictions,
stale claims, data gaps, citing pages. And a **LINT REPAIR** section: given a list
of findings, fix each (create missing page or remove the dead link by judgment;
add orphans to a relevant page/index; fill index gaps; reconcile contradictions),
always preserving `sources:` traceability, updating `index.md`, and appending
`## [<YYYY-MM-DD>] lint | <summary>` to `log.md`.

## Data flow

```
wiki lint            → run_checks (pure)               → report, exit 0/1
wiki lint --deep     → run_checks + lint_deep [ro LLM] → report (+semantic)
wiki lint --fix      → run_checks → confirm → delete conflict-dupes
                       → lint_repair [rw LLM] → re-run checks → report, exit 0/1
wiki lint --deep --fix → detect (mech+LLM) → confirm → fix (mech+LLM) → recheck
```

## Error handling

- Mechanical checks never need auth and don't crash on a malformed page
  (`frontmatter.parse` already tolerates missing/empty frontmatter; a page with no
  `title:` simply isn't a link target).
- `--deep` claude failure → reported, mechanical results still shown (best-effort).
- `--fix` repair claude failure → `repair failed: <reason>`, exit 1; mechanical
  deletions already applied are reported.
- Non-TTY `--fix` without `--yes` → refuse (exit 2), no mutations.

## Testing

Mechanical detection is the largest, fully deterministic surface — no claude.

- `lint.run_checks` on crafted tmp vaults: a dead `[[Link]]` flagged, a resolving
  link clean; `page 2.md` flagged only when `page.md` exists (and not otherwise);
  an unlinked/unindexed concept flagged orphan, a linked one clean; a page missing
  from index flagged; a `sources:` trace to a missing raw file flagged; missing
  `index.md`/`log.md` flagged.
- `_render_findings`: groups, severity, summary count, clean message.
- `cmd_lint`: clean vault → exit 0; findings → exit 1; `--fix --yes` deletes a
  conflict-dup and (via fake `lint_repair`) reports repair; `--fix` non-TTY
  without `--yes` → exit 2, file NOT deleted; `--deep` appends the LLM section
  (fake `lint_deep`).
- `lint_deep` / `lint_repair` with fake runners (read-only argv has no Write;
  repair argv has Write); `lint_prompt`/`lint_repair_prompt` content; template has
  LINT + LINT REPAIR sections.

## Out of scope (later)

- Weekly cron wiring / scheduling (call `wiki lint` from your own cron).
- HTTP API + hermes (M3).
- Auto-fix of conflict-dupes *choosing* a non-default canonical file (we always
  keep `<base>.md`, delete the ` N.md` copy).
- Graph metrics / link-density / freshness scoring (not in the gist's core lint).
