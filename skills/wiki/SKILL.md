---
name: wiki
description: >
  Operate a local LLM-maintained Markdown knowledge base (wiki-daemon) via the
  `wiki` CLI: ingest/import sources, query the wiki, review clarifications, run
  health checks (lint), and check daemon status/health. Use when the user wants
  to capture, search, or maintain their personal wiki.
version: 0.1.0
license: MIT
platforms: [macos]
metadata:
  hermes:
    tags: [knowledge-base, markdown, personal, icloud]
    requires_toolsets: [terminal]
    config:
      - key: wiki.vault_path
        description: "Absolute path to the wiki-daemon vault folder"
        prompt: "Path to your wiki vault (e.g. an iCloud Drive folder)"
---

# wiki — personal LLM knowledge base

Drive `wiki-daemon`: a Mac daemon that turns clipped Markdown sources into an
interconnected, LLM-maintained wiki. You operate it through the `wiki` CLI by
shelling out from the terminal.

## When to use

Load this skill when the user wants to:

- **Capture** something into their wiki — "clip this", "save this article",
  "add this note" → `import` / `ingest`.
- **Query** their wiki — "what do my notes say about X?", "search my wiki" →
  `query`.
- **Check** the system — "is the daemon running?", "is it healthy?" →
  `status` / `doctor`.
- **Review** open questions the ingest left behind → `review`.
- **Maintain** the wiki — "health check", "fix broken links" → `lint`.

## Setup check (first run)

1. Confirm the CLI is installed:

   ```bash
   wiki --version
   ```

   If that fails, install it from the cloned repo with pipx:

   ```bash
   pipx install <path-to-wiki-daemon-repo>
   ```

2. Record the vault as the default once, so later commands need no `--vault`.
   The vault path is provided to you as the `wiki.vault_path` config value:

   ```bash
   wiki init --set-default --vault "<wiki.vault_path>"
   ```

   `init` is idempotent — it scaffolds a new vault or just records the default
   for an existing one. After this, run every command **bare** (no `--vault`);
   `wiki` discovers the vault automatically.

## Core operations

Each block shows the common form. Run `wiki <command> --help` for the exact
flags — that is always the source of truth.

### Capture a source

```bash
# Import any file from disk (copies it into the vault, then ingests):
wiki import --no-interactive ~/Downloads/article.md

# Or ingest a clip already sitting in the vault's raw/sources/:
wiki ingest --no-interactive "raw/sources/2026-06-01-example.md"
```

### Query the wiki

```bash
wiki query "What do my sources say about X?"
# add --save to file the answer as a wiki/queries/ page:
wiki query --save "What do my sources say about X?"
```

### Review open clarifications

```bash
wiki review                                  # list open questions
wiki review answer <id> "Your answer here"   # answer one and apply it
```

### Health check (lint)

```bash
wiki lint            # mechanical checks (dead links, dupes, orphans); exit 1 if any
wiki lint --deep     # also run an LLM semantic scan
wiki lint --fix --yes  # repair (deletes conflict-dupes + LLM repair pass)
```

### Status & environment

```bash
wiki status          # daemon running? auth? queue depth? processed count? last error?
wiki doctor          # validate tooling + iCloud + headless auth
wiki serve           # run the daemon in the foreground (watch + auto-ingest)
```

## Headless agent notes

You have no terminal to answer prompts interactively, so:

- Always pass **`--no-interactive`** on `ingest` / `import`. Clarifying
  questions are then queued to `wiki/review/` instead of blocking; surface them
  later with `wiki review`.
- Always pass **`--yes`** on `lint --fix`; it refuses to mutate the wiki without
  confirmation otherwise.

## Pitfalls

- **`wiki: command not found`** — it is not on PATH. Verify with `which wiki`;
  (re)install with `pipx install <repo>`.
- **`auth: FAILING` in `wiki status`** — headless `claude -p` auth is separate
  from interactive Claude. Fix with `claude setup-token`. Interactive Claude
  working does **not** mean headless works.
- **Nothing ingesting** — run `wiki status` and check queue depth + last error.
  The source must be a `*.md` file under `raw/sources/`.
- **Vault not found** — if you skipped the set-default step, pass
  `--vault "<wiki.vault_path>"` explicitly on the command.

## Verify your work

After an ingest, confirm the side effects instead of trusting the exit code:

```bash
wiki status              # the processed count should have increased
tail wiki/log.md         # the most recent ingest line
```
