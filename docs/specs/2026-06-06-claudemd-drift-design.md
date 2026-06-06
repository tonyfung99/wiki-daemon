# Detect & fix stale vault `CLAUDE.md` — design

**Status:** approved (brainstorm)
**Date:** 2026-06-06

## Problem

`wiki init` never overwrites an existing vault `CLAUDE.md` (`scaffold.py`
preserves user edits). So a vault scaffolded from an older template keeps a
stale "maintainer brain" forever. When the brain is missing operation sections
the code depends on (e.g. `RAISE CLARIFICATION`), `claude` improvises and writes
malformed output — observed: a clarification written without the
`status/source/question/tentative` frontmatter the CLI reads, so `wiki review`
rendered blank. Nothing detects or repairs this today (`wiki doctor` only checks
that `CLAUDE.md` *exists*, and never writes).

## Goal

- **Detect** drift in `wiki doctor`: warn when the vault `CLAUDE.md` is missing
  any canonical operation section.
- **Fix** it with `wiki doctor --fix`: append the missing sections,
  non-destructively, preserving all existing content.

Mirror the existing `wiki lint` / `lint --fix` idiom (detect by default, repair
with `--fix`) so the surface stays uniform.

## Decisions

- **Detection = missing `## ` sections.** The bundled template is the source of
  truth. A vault brain is stale if any `## <header>` the template defines is
  absent. This maps exactly to "will this operation work" and never false-flags
  intentional prose customization (the file is meant to be customized).
- **Fix = append missing sections.** Only ever *adds* the absent sections, in
  template order, at the end of the file. Customized existing sections are
  untouched. Idempotent.
- **Fix lives in `wiki doctor --fix`,** not a new `wiki upgrade` command —
  consistent with `lint --fix`, more discoverable, smaller surface.
- **Scope: `## ` sections only.** The cosmetic `wiki/review/` bullet inside the
  `## Layers` section is out of scope (cannot append a mid-section bullet
  cleanly, and it is informational — the functional fix is the
  `RAISE CLARIFICATION` section).

## Non-goals

- Detecting/fixing a section that **exists but has stale content** (in-place
  drift). The dominant real case is *missing* sections (old templates were
  strict prefixes). A future template version-stamp could catch in-place drift;
  YAGNI for now. Documented as a known limitation.
- Backups. Appending is strictly additive — original content is preserved in
  place, so no `.bak` file is written.
- Fixing non-`CLAUDE.md` doctor findings (auth, pin, iCloud). Those remain
  guidance; the tool cannot repair them.

## Architecture

### New module: `src/wiki_daemon/maintainer.py`

Pure functions over the maintainer brain (no CLI, no daemon state). Easy to
unit-test.

```python
Section = namedtuple("Section", "header text")
# header = "## QUERY operation"; text = full block incl. header up to next "## "

def template_text() -> str
    # bundled template CLAUDE.md (reuses scaffold template access)

def sections(text: str) -> list[Section]
    # split on lines starting with "## ", each block runs to the next "## " / EOF

def missing_sections(current: str) -> list[Section]
    # template sections whose exact "## <header>" line is absent from `current`,
    # in template order

def apply_upgrade(current: str) -> tuple[str, list[str]]
    # returns (new_text, added_headers). Appends each missing section to the end
    # of `current` (blank-line separated), in template order. If nothing missing,
    # returns (current, []). Idempotent.
```

"Present" = the exact `## <header>` line appears in `current` (canonical headers
are stable). Append separates blocks with a single blank line and guarantees a
trailing newline.

### `wiki doctor` — detection

Add `check_claude_md(cfg)` and wire it into `run_doctor` after `check_vault`:

- `CLAUDE.md` absent → no Check emitted (already covered by the
  `vault:scaffolded` WARN).
- Missing sections → `Check("vault:claude-md", "WARN", "stale CLAUDE.md: missing
  N section(s) (<headers>) — run \`wiki doctor --fix\`")`.
- Complete → `Check("vault:claude-md", "PASS", "up to date")`.

WARN (not FAIL): a stale brain still partly works, and doctor's overall status
should not flip to FAIL for this alone.

### `wiki doctor --fix [--yes]` — the fix

Extend the `doctor` subparser with `--fix` and `--yes` (no new subcommand).
`run_doctor(cfg, *, probe, fix, yes, run)`:

1. Run all checks and print the report as today.
2. If `--fix` and the `vault:claude-md` check is WARN:
   - Compute missing sections via `maintainer.apply_upgrade`.
   - `CLAUDE.md` absent → print `no CLAUDE.md — run \`wiki init\`` and skip
     (exit unchanged).
   - Confirm before writing: TTY prompts `Append N section(s)? [type 'yes']`;
     `--yes` skips; non-interactive without `--yes` refuses
     (`re-run with --yes`, exit 2) — same pattern as `lint --fix`.
   - Write the upgraded text (atomic: temp + `os.replace`).
   - Print `fixed: appended N section(s) (<headers>)`.
3. Non-fixable findings (auth/pin/iCloud) remain in the report as guidance;
   `--fix` never implies it repaired them.

Exit code keeps doctor's convention: `0` unless overall status is `FAIL`.

### `cli.py`

- `doc` subparser: add `--fix` (`action="store_true"`) and `--yes`
  (`action="store_true"`).
- `main()` `doctor` branch: pass `fix=ns.fix, yes=ns.yes` through to
  `run_doctor`.

### README

Document `--fix` on the existing `wiki doctor` row in the Commands table; one
line in the "How it works" doctor bullet noting it can repair a stale
`CLAUDE.md`.

## Testing strategy (TDD)

All under `.venv/bin/pytest -q`; no network, no `claude`.

- `tests/test_maintainer.py` (new):
  - `sections` parses the bundled template into the expected headers.
  - `missing_sections` on an old-prefix vault (template truncated before
    `RAISE CLARIFICATION`) returns exactly the tail sections, in order.
  - `apply_upgrade` appends them; running it twice is a no-op (idempotent).
  - A section carrying custom prose is byte-identical after upgrade
    (customization preserved).
- `tests/test_doctor.py` (extend):
  - vault with all sections → `vault:claude-md` PASS.
  - vault missing sections → WARN, detail names a missing header and
    `wiki doctor --fix`.
- `tests/test_cli.py` (extend):
  - `doctor` parser accepts `--fix`/`--yes`.
  - `run_doctor(..., fix=True, yes=True)` on a stale vault appends sections and
    prints `fixed:`.
  - stale vault, non-interactive, no `--yes` → refuses, exit 2, nothing written.
  - already-current vault + `--fix` → no write, no `fixed:` line.

## Risks

- Header matching is exact-line; a user who reworded a `## ` header would see a
  false "missing" and get a duplicate appended. Acceptable: canonical headers
  are stable and documented as the contract.
- `check_auth` still runs a real `claude` probe as part of doctor; `--fix` does
  not change that. The `CLAUDE.md` repair is independent of the auth probe.
