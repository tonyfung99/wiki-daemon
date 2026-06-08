# Vault CLAUDE.md version stamp — design

**Status:** approved (brainstorm); implement-and-push authorized
**Date:** 2026-06-08

## Problem

`wiki doctor --fix` only detects/appends **missing** `## ` sections. A vault
`CLAUDE.md` whose sections are all present but whose **content is stale** (behind
the bundled template) is not detected — the "exists-but-stale-content" gap. This
bit the user twice: an old `RAISE CLARIFICATION` section lacking the new
`options:`/`recommended:` instruction silently produced option-less review
items, and `doctor --fix` reported success while changing nothing.

## Goal

Detect stale content via a template **version stamp**, and let `doctor --fix`
re-sync it without silently destroying user customizations.

## Decisions

- **Stamp = hidden comment.** First line of the bundled template:
  `<!-- wiki-template: v1 -->`. Invisible in rendered Markdown, ignored by the
  maintainer LLM (it never edits CLAUDE.md). Bump the integer whenever the
  template changes.
- **Detection.** `doctor`'s `vault:claude-md` check WARNs when the vault stamp is
  **behind or absent** OR when sections are missing; PASS only when the stamp
  equals the template version and no sections are missing.
- **Fix = backup + overwrite for stale content.** When the version is
  behind/absent, `doctor --fix` writes `CLAUDE.md.bak-<date>` then overwrites
  `CLAUDE.md` with the fresh (stamped) template. Non-destructive (old content is
  in the backup to re-merge). Confirmed before writing (TTY prompt), `--yes`
  skips, non-interactive without `--yes` refuses (exit 2) — same pattern as the
  existing `--fix`.
- **Append stays for the current-version-but-missing-section edge.** If the
  stamp is current yet a section is missing (hand-edited), `--fix` appends the
  missing sections (existing `apply_upgrade`), no overwrite.
- **Back-compat / migration.** Existing unstamped vaults parse as version
  `None` → treated as behind → one-time `doctor --fix` backs up + overwrites,
  stamping them. The backup makes this safe.

## Non-goals

- No content-hash or per-section drift detection (would false-flag legitimate
  customization). Relies on manual version-bump discipline.
- No auto-merge of customizations — backup + overwrite, user re-merges.

## Architecture

### Template
`src/wiki_daemon/templates/CLAUDE.md` gains `<!-- wiki-template: v1 -->` as line
1, then a blank line, then the existing `# Wiki Maintainer Instructions`.

### `maintainer.py`
```python
_STAMP = re.compile(r"<!--\s*wiki-template:\s*v(\d+)\s*-->")

def template_version() -> int          # parse the bundled template's stamp
def parse_version(text: str) -> int|None  # stamp in a CLAUDE.md, or None
```
`sections`/`missing_sections`/`apply_upgrade` unchanged (stamp sits before the
first `## `, so section parsing is unaffected).

### `doctor.py`
- `check_claude_md(cfg)`:
  - file absent → None (unchanged).
  - `missing = missing_sections(text)`; `stale = parse_version(text) is None or
    parse_version(text) < template_version()`.
  - neither → PASS `up to date (vN)`.
  - else WARN, naming the reason(s): `stale (v? < vN)` / `unversioned` and/or
    `missing K section(s)`, hint `run \`wiki doctor --fix\``.
- `_fix_claude_md(cfg, checks, *, yes)`:
  - act only when `vault:claude-md` is WARN.
  - **stale version** → confirm; backup `CLAUDE.md.bak-<date>` (unique suffix if
    it exists); write `template_text()`; print
    `backed up old CLAUDE.md -> <name>; wrote template vN`.
  - **current version, missing sections** → existing append path.
  - confirmation/`--yes`/non-interactive-refuse identical to today.

### `scaffold.py` / `init`
Unchanged — `init` copies the template verbatim, so new vaults are stamped.

## Testing (TDD, no claude/network)

- `tests/test_maintainer.py`: `template_version()` returns the int; `parse_version`
  on stamped / unstamped / malformed; `sections`/`apply_upgrade` still pass with
  the stamped template.
- `tests/test_doctor.py`: PASS when init'd vault matches template version; WARN
  when stamp behind; WARN when unstamped; `_fix_claude_md` stale → backup file
  created + file overwritten with stamped template + "backed up" printed;
  non-interactive without `--yes` refuses (exit 2, nothing written); current+
  missing-section still appends.

## Risks

- Manual version-bump discipline: a future template edit that forgets to bump the
  stamp won't be flagged. Mitigation: a `tests/` guard could compare the stamp to
  a content hash later; out of scope now.
- Heavy customizers get their edits moved to the backup on a version bump; they
  re-merge from `CLAUDE.md.bak-<date>`. Documented tradeoff (chosen over
  refuse-and-diff).
