# Design: `wiki import` — manual CLI source import

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Problem

Today a raw source reaches the vault one of two ways: the WikiReader iOS app
writes it into `raw/sources/`, or you manually drop a `.md` into that folder.
Once it's there you run `wiki ingest --vault <path> <file>` to process it.

`wiki ingest` only works on files **already inside the vault** — it computes
`source_path.relative_to(cfg.vault)`, so a path outside the vault raises. There
is no one-shot way to take an arbitrary file from anywhere on disk and get it
into the wiki.

## Goal

A convenience command:

```
wiki import --vault <path> <file>
```

that copies `<file>` into the vault's `raw/sources/` as a well-formed Markdown
source and then runs the existing ingest pipeline on it.

## Decisions (from brainstorming)

- **Copy, never move.** The original file is always left in place. (No
  `--move`/`--copy` flags in this iteration — YAGNI.)
- **Command name:** `wiki import`.
- **Accept any UTF-8 text file**, written into the vault with a `.md`
  extension. Binary/non-text input is rejected.
- **Name collision:** normalize to `YYYY-MM-DD-<slug>.md`; if that exists,
  append `-2`, `-3`, … until free.
- **Frontmatter:** if the file has none, prepend a minimal block
  (`type: source`, `captured_at`, `title`). If it already has frontmatter, copy
  it verbatim.
- **`captured_at`:** import time (now, UTC).

## Architecture

Two clean responsibilities, mirroring the existing `ops` boundary:

- **`importer.import_source(cfg, src_path) -> Path`** (new module) — "get a
  valid source into the vault." Pure file-landing; returns the in-vault
  destination path. No claude, no state.
- **`ops.ingest(cfg, path, store=...)`** (unchanged) — "ingest a file already in
  the vault." Reused as-is.

The CLI command (`cli.cmd_import`) wires them: `dest = import_source(...)` then
`ingest(cfg, dest, store=...)`.

This keeps `ingest` and its tests untouched, and keeps `import_source`
fast/pure to test (no LLM).

## `import_source` behavior, in order

1. **Validate input.** `src_path` must exist and be a regular file. Read its
   bytes and decode as UTF-8. If it doesn't exist, isn't a file, or isn't
   UTF-8-decodable, raise a clear error (no binary/PDF support — out of scope).

2. **Compute destination name** `raw/sources/YYYY-MM-DD-<slug>.md`:
   - `slug` = the source filename **stem**, lowercased, with runs of
     non-alphanumeric characters collapsed to a single `-`, trimmed of leading/
     trailing `-`. Empty slug falls back to `source`.
   - Date prefix = today in UTC (`YYYY-MM-DD`). **Skip** the prefix if the stem
     already begins with a `YYYY-MM-DD-` pattern (avoids
     `2026-06-02-2026-06-01-foo`).
   - Extension forced to `.md`.

3. **Resolve collision.** If the computed name already exists in
   `raw/sources/`, append `-2`, `-3`, … before the `.md` until the name is free.

4. **Synthesize frontmatter if absent.** Parse the text with
   `frontmatter.parse`. Detect "has frontmatter" by whether the text starts with
   the `---\n` delimiter (matching `parse`'s own rule), not by whether `meta` is
   non-empty. If absent, build the document with `frontmatter.dump`:
   ```yaml
   type: source
   captured_at: <now UTC, ISO 8601, e.g. 2026-06-02T13:45:00Z>
   title: <derived from stem: words title-cased>
   ```
   followed by the original text as the body. If present, write the text
   verbatim.

5. **Write.** `mkdir -p raw/sources/`, write the (possibly augmented) text to the
   destination path, return the path.

## CLI surface & output

```
wiki import --vault <path> <file>
```

`cmd_import`:
- `dest = import_source(cfg, Path(file))` — on error, print
  `import failed: <reason>` to stderr, return 1.
- `result = ingest(cfg, dest, store=StateStore(cfg.processed_json))`.
- Print `imported <dest.name>` then one of: `ingested` /
  `skipped (already processed)`; or `ingest failed: <reason>` (return 1).

Content-hash dedupe in `ingest` still catches true duplicates: re-importing the
same content lands a new uniquely-named file but `ingest` reports
`skipped (already processed)` because the sha256 matches.

## Edge cases

- Missing path / not a regular file → fail before any copy.
- Non-UTF-8 (binary) input → fail with a clear reason.
- Vault not initialized → `raw/sources/` is created by `mkdir -p`; ingest's
  verify step surfaces any missing-vault state naturally (unchanged behavior).

## Testing

`tests/test_importer.py` (pure, no LLM) and a `cmd_import` case in
`tests/test_cli.py` (fake claude runner, mirroring `tests/test_ops.py`):

- copies file in with normalized `YYYY-MM-DD-<slug>.md` name; **original left in
  place**
- stem already date-prefixed → no double prefix
- collision → `-2` suffix
- file lacking frontmatter → gets `type: source` / `captured_at` / `title`
- file with existing frontmatter → copied verbatim
- non-existent path → fails cleanly
- non-UTF-8 input → fails cleanly
- end-to-end: import → ingest succeeds and marks processed
- re-import of identical content → `skipped (already processed)`

## Docs

- Add a `wiki import` row to the README command table.
- Add a `wiki import` line to the Quickstart.

## Out of scope (YAGNI)

- `--move` / `--copy` flags.
- Non-text input (PDF, images, HTML-to-markdown conversion).
- Importing multiple files / globs in one invocation.
- URL fetching.
