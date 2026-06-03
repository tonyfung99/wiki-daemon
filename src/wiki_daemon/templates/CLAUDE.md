# Wiki Maintainer Instructions

You maintain an LLM wiki: a compounding, interconnected Markdown knowledge base.
You are invoked headlessly for ONE operation at a time, with the vault as your
working directory.

## Layers
- `raw/sources/` — immutable inputs. READ ONLY. Never edit or delete these.
- `wiki/` — your output. You own it entirely.
  - `wiki/entities/` — people, orgs, products, places (one page each)
  - `wiki/concepts/` — ideas, theories, methods (one page each)
  - `wiki/sources/` — exactly one summary page per raw source
  - `wiki/queries/` — saved query answers
  - `wiki/review/` — open clarifications you raised during ingest (one file each)
  - `wiki/index.md` — catalog of every page with a one-line summary, by category
  - `wiki/log.md` — append-only operation history

## Page rules
- Every wiki page starts with YAML frontmatter:
  ```
  ---
  type: entity | concept | source | query
  title: <Human Title>
  sources: [raw/sources/<file>.md, ...]
  updated: <YYYY-MM-DD>
  ---
  ```
- Filenames are kebab-case of the title: `Acme Corp` -> `acme-corp.md`.
- Cross-link related pages with `[[wiki-link]]` using the target's title.
- Prefer UPDATING an existing page (match by title) over creating a duplicate.

## INGEST operation
Given one source file path:
1. Read the source.
2. Identify key entities and concepts.
3. For each, create a new page OR update the existing page (by title), adding
   the source to its `sources:` list and weaving `[[cross-refs]]`.
4. Ensure a summary page exists under `wiki/sources/` for this source (filename
   = kebab-case of its title), summarizing it and linking the entities/concepts
   it touches. Its `sources:` frontmatter MUST list this raw source's path
   (e.g. `raw/sources/<file>.md`) — this is how the source is traced.
5. Update `wiki/index.md` so every page is listed with a one-line summary.
6. Append one line to `wiki/log.md`:
   `## [<YYYY-MM-DD>] ingest | <source title or url>`
7. Do not edit anything under `raw/`.

## RAISE CLARIFICATION (during INGEST)
When a structural decision is genuinely ambiguous — which entity a name refers
to, conflicting facts across sources, whether two things are the same concept,
or missing context you cannot infer — do NOT stall:
1. Make a best-effort choice and note it briefly on the affected page.
2. Record the open question as `wiki/review/<kebab-slug>.md` with frontmatter:
   ```
   ---
   type: review
   status: open
   source: raw/sources/<file>.md
   question: "<the specific question for the user>"
   tentative: "<the best-effort choice you made>"
   created: <YYYY-MM-DD>
   ---
   <optional context>
   ```
Ingest still completes normally; the source is fully processed. If you are told
this is an interactive session, ASK the user directly and wait instead of
writing a review file.

## APPLY CLARIFICATION
Given one answered review file (`status: answered`, with an `answer:` field):
1. Read its `question`, `tentative`, and `answer`.
2. Apply the answer to the relevant wiki pages (rename/merge/split/relink as
   needed), preserving the `sources:` traceability.
3. Append one line to `wiki/log.md`:
   `## [<YYYY-MM-DD>] review | <question>`
4. DELETE the review file (resolution = removal).
