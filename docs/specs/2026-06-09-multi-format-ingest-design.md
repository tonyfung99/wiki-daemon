# Multi-format ingest (document conversion) — design

**Status:** approved (brainstorm); implement-and-push authorized
**Date:** 2026-06-09

## Problem

`wiki import`/the daemon only accept Markdown/UTF-8 text. A clipped PDF, DOCX,
PPTX, XLSX, HTML, etc. cannot be ingested — a real capability gap vs. comparable
LLM-wiki tools (e.g. SamurAIGPT/llm-wiki-agent, which converts via markitdown at
ingest).

## Goal

Accept common document formats by converting them to Markdown as they enter the
vault, **wherever they enter** — via `wiki import` or dropped/synced into
`raw/sources/` and caught by the watcher. Preserve the project invariant that
`raw/sources/` is plain Markdown.

## Decisions

- **Converter: markitdown** (Microsoft, MIT, offline, no API key). Core
  dependency `markitdown[pdf,docx,pptx,xlsx]`; HTML/CSV/JSON/XML need no extra.
- **Scope: documents only** — `.pdf .docx .pptx .xlsx .html .htm .csv .json
  .xml`. No image OCR / audio transcription (heavier extras, needs an LLM).
- **Conversion lives in the ingest pipeline** (import + watcher), not just
  `import`, so any convertible arrival is handled consistently.
- **Converted `.md` is canonical; original archived.** `raw/sources/` stays
  all-Markdown. A binary that lands in `raw/sources/` is converted to a sibling
  `<stem>.md` and the original is moved to `raw/originals/`. `import`'s original
  stays outside the vault (the user already has it) and is not copied.
- **Conversion and ingestion are SEPARATE steps (no double-ingest).** A
  convertible file is *only* converted (→ `.md` + archive original); it is not
  ingested in that pass. The resulting `.md` is ingested by its own job via the
  normal watch/reconcile path. This avoids the duplicate where converting and
  ingesting in one pass, then having the watcher re-enqueue the new `.md`, would
  ingest it twice.

## Non-goals

- Images (OCR) / audio (speech-to-text) and the LLM-described-image path.
- Converting `.txt` or unknown text dropped directly into `raw/sources/` (use
  `import`); `.md` remains the primary watched type alongside the convertibles.
- Conversion in `wiki ingest <file>` of an arbitrary path is unchanged beyond
  the shared routing (it ingests a vault file; a convertible there is normalized
  by the same code path).

## Architecture

### `convert.py` (new)
```python
CONVERT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx",
                      ".html", ".htm", ".csv", ".json", ".xml"}

def convert_to_markdown(path: Path) -> str:
    """Convert a document to Markdown via markitdown. Raises ValueError on
    conversion failure or empty output."""
```
Thin wrapper over `MarkItDown().convert(str(path)).text_content`. Isolates the
markitdown import so the rest of the package and tests have one seam.

### `importer.py`
- Extract `_wrap_as_source(text, stem, now) -> str` from the existing
  synthesize-frontmatter logic (shared by import + normalize).
- `import_source(cfg, src_path)`: if `src_path.suffix.lower()` is convertible →
  `text = convert_to_markdown(src_path)`; else decode UTF-8 (today's behavior).
  Then `_wrap_as_source` → land `<date>-<slug>.md`. Original not copied.
- New `normalize_in_place(cfg, path) -> Path`: for a convertible file already in
  `raw/sources/` — `text = convert_to_markdown(path)`; write
  `raw/sources/<path.stem>.md` (via `_wrap_as_source`); move the original to
  `cfg.raw_originals / path.name`; return the new `.md` path.

### `watcher.py`
- `is_relevant` and `files_to_ingest` match `.md` OR `CONVERT_EXTENSIONS`. The
  watcher only watches `raw/sources/` (non-recursive), so `raw/originals/` is not
  re-scanned.

### `daemon.py` (`drain_once`)
`drain_once` branches on the payload extension — convert OR ingest, never both
in one pass:
- **Convertible payload** → after `prepare_fn(...)` (materialize the possibly
  dataless original + stability), `md = normalize_in_place(cfg, path)` (convert →
  write `<stem>.md`, move original to `raw/originals/`), then **enqueue an ingest
  job for `md`** and complete the convert job. It does NOT ingest here.
  Conversion errors are caught and logged like a failure (the job completes; the
  file is not silently lost — the original remains for inspection).
- **`.md` payload** → ingest as today.

The separately-enqueued `.md` job is ingested on a later drain iteration. If the
watcher also enqueues the same `.md` (FSEvents), `JobQueue.enqueue`'s identical-
payload dedup collapses them; content-hash dedup in `ops.ingest` is the final
backstop. The original is already in `raw/originals/`, so reconcile never
re-finds it to re-convert.

### `config.py` / `scaffold.py`
- `Config.raw_originals` → `vault/raw/originals`. Add to scaffold `_DIRS`.

### `pyproject.toml`
- Add `markitdown[pdf,docx,pptx,xlsx]` to `dependencies`.

## No duplication (three guards)

1. **Convert ≠ ingest.** A convertible job only converts; the `.md` is ingested
   by its own job. Converting and ingesting in one pass would double-count when
   the new `.md` is re-seen by the watcher.
2. **Queue payload dedup.** `JobQueue.enqueue` drops an enqueue whose payload
   already has a pending job, so the normalize-enqueue and a watcher-enqueue of
   the same `.md` collapse to one.
3. **Content-hash dedup.** `ops.ingest` skips a source whose content hash is
   already processed — the final backstop.

The original binary is moved to `raw/originals/` during conversion, so reconcile
sweeps never re-find it to re-convert.

## Behavior notes (intentional)

- The daemon now **writes** `raw/sources/` (normalized `.md`) and
  `raw/originals/` (archive). This does not touch the claude↔`wiki/` firewall,
  but `raw/sources/` is no longer purely user-owned — the daemon normalizes it.
- Import vs drop asymmetry: `import`'s original is external (not archived); a
  dropped original is archived to `raw/originals/`.
- With a daemon running, `import paper.pdf` converts + lands the `.md` first,
  then defers the **ingest** to the daemon (the daemon ingests the `.md`, no
  re-conversion).

## Testing (TDD; no network — markitdown is offline)

- `tests/test_convert.py`: real conversion of a small `.html` and `.csv` fixture
  to Markdown (asserts expected text present); a corrupt/empty input raises
  `ValueError`.
- `tests/test_importer.py`: convertible extension routes through
  `convert_to_markdown` (monkeypatched) → lands `.md` with synthesized
  frontmatter; `.md`/`.txt` passthrough unchanged; `normalize_in_place` writes
  the `.md` and moves the original to `raw/originals/`.
- `tests/test_watcher.py`: `is_relevant`/`files_to_ingest` include convertibles.
- `tests/test_daemon.py`: `drain_once` on a convertible job converts (mock) +
  archives the original + **enqueues an ingest job for the `.md`** and does NOT
  ingest in that pass; a follow-up `drain_once` ingests the `.md` exactly once.
- `tests/test_cli.py`: existing `import` tests still pass; an `import` of a
  convertible (mock convert) lands a `.md`.

## Risks

- markitdown brings transitive deps (pdfminer, python-docx, openpyxl, etc.) into
  the core install. Accepted (user chose core dependency).
- Conversion fidelity varies by format/source; the maintainer LLM ingests
  whatever Markdown markitdown produces. Out of scope to improve per-format.
