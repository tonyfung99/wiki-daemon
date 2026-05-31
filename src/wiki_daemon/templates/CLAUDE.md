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
4. Ensure `wiki/sources/<source-slug>.md` exists summarizing this source and
   linking the entities/concepts it touches.
5. Update `wiki/index.md` so every page is listed with a one-line summary.
6. Append one line to `wiki/log.md`:
   `## [<YYYY-MM-DD>] ingest | <source title or url>`
7. Do not edit anything under `raw/`.
