# wiki-daemon

The Mac-side brain for a personal, **LLM-maintained knowledge base** — in the
spirit of Karpathy's [LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

You clip raw sources (tweets, articles, notes) into a plain-Markdown vault;
`wiki-daemon` watches the vault and uses headless **`claude -p`** to transform
each clip into an interconnected wiki of Markdown pages (entities, concepts,
source summaries) that **compounds** over time — with `[[wiki-links]]`, an
`index.md` catalog, and an append-only `log.md`.

Because the vault is a plain folder you can keep in **iCloud Drive**, it syncs
to all your Apple devices; the companion iOS app
[WikiReader](https://github.com/tonyfung99/WikiReader) clips into it and browses
it, and any other tool (Obsidian, scripts, an agent) can read it too.

## How it fits together

```
WikiReader (iOS)  ──writes──▶  raw/sources/        (a clip lands as Markdown)
                                   │  iCloud syncs to the Mac
                                   ▼
wiki-daemon  ──watches raw/──▶  claude -p  ──writes──▶  wiki/   (LLM-owned pages)
```

The daemon is the **single writer** of `wiki/`: it serializes ingest jobs,
de-dupes by content hash, recovers from crashes, and handles iCloud "dataless"
files. The `raw/` → `wiki/` boundary is a firewall (the watcher only watches
`raw/`; `claude` only writes `wiki/`).

## Requirements

- **macOS** (the production host is Intel x86_64 on macOS 15.7.3 Sequoia).
- **Python 3.12+**.
- The **[`claude` CLI](https://docs.claude.com/en/docs/claude-code)** installed
  and authenticated (`claude --version`).

## Install

```bash
git clone https://github.com/tonyfung99/wiki-daemon.git
cd wiki-daemon
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

This installs two console scripts: **`wiki`** (manual commands) and
**`wiki-daemon`** (the watcher).

## Quickstart

```bash
# 1. Pick a vault folder (e.g. inside iCloud Drive) and scaffold it.
VAULT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/MyWiki"
wiki init --vault "$VAULT"

# 2. (Recommended) edit the vault's purpose.md to describe what you collect,
#    and pin the folder in Finder: right-click ▸ Keep Downloaded.

# 3. Check the host is set up correctly (tooling + iCloud).
wiki doctor --vault "$VAULT"

# 4. Ingest a single clip by hand (drop a .md into raw/sources/ first).
wiki ingest --vault "$VAULT" "$VAULT/raw/sources/2026-06-01-example.md"

# 4b. Or import a file from anywhere — copies it into raw/sources/ then ingests.
wiki import --vault "$VAULT" ~/Downloads/some-note.md

# 5. Or run the daemon to ingest automatically as clips arrive.
wiki-daemon serve --vault "$VAULT"

# Anytime: how many sources have been ingested?
wiki status --vault "$VAULT"
```

A raw source is just Markdown with YAML frontmatter:

```markdown
---
type: source
source_url: https://x.com/...
captured_at: 2026-06-01T10:00:00Z
title: Example post
---
The clipped text goes here.
```

## Commands

| Command | What it does |
|---|---|
| `wiki init --vault <path>` | Scaffold a vault (`CLAUDE.md`, `purpose.md`, `raw/`, `wiki/`). Idempotent. |
| `wiki ingest --vault <path> <file>` | Ingest one source file now (runs `claude -p`, verifies, records it). |
| `wiki import --vault <path> <file>` | Copy any UTF-8 text file into `raw/sources/` (adds frontmatter if missing) and ingest it. The original is left in place. |
| `wiki status --vault <path>` | Show daemon health: running?, auth state, queue depth, processed count, last error. |
| `wiki doctor --vault <path> [--probe <file>]` | Validate environment, tooling, and iCloud handling on the host. |
| `wiki-daemon serve --vault <path> [--reconcile-interval N]` | Watch `raw/sources/` and ingest autonomously. |

The vault's `CLAUDE.md` is the **maintainer brain** — it defines the page
templates, naming, and the ingest algorithm `claude -p` follows. Tune it to
change how your wiki is built.

## How it works (briefly)

- **Ingest** = `claude -p --dangerously-skip-permissions` (headless) runs in the
  vault, follows `CLAUDE.md`, then the daemon **verifies** the result (a source
  summary exists and is traced via `sources:` frontmatter; `index.md`/`log.md`
  updated) before marking the source done.
- **Reliability** = a persisted serial queue + content-hash dedupe + crash
  recovery + a periodic reconcile sweep (FSEvents in iCloud folders is lossy).
- **iCloud** = detects not-downloaded "dataless" files and materializes them
  (`brctl download` / `fileproviderctl materialize`) before reading.
- **Observability** = the daemon logs to stdout and a rotating `daemon.log` in
  its state dir; `wiki status` surfaces health; `wiki doctor` verifies `claude`
  is authenticated (headless `claude -p` needs its own valid login — use
  `claude setup-token` for an unattended daemon).

## Docs

- **Design / rationale:** [`docs/design.md`](docs/design.md)
- **Implementation plan (M1+M2):** [`docs/plans/2026-05-31-ingest.md`](docs/plans/2026-05-31-ingest.md)
- **Host validation runbook:** [`docs/RUNBOOK-intel-host-validation.md`](docs/RUNBOOK-intel-host-validation.md)

## Status

M1 + M2 (autonomous ingest) are implemented and tested; ingest is validated
against the real LLM. Query / HTTP API / hermes integration (M3) and lint / iOS
querying (M4–M5) are planned — see the design doc.

## License

[MIT](LICENSE).
