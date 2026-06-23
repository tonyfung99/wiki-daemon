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

### Capture a source — then review immediately

Ingest is a **two-step recipe**. Run the ingest, then IMMEDIATELY surface that
material's clarifications while the user is still looking at what they sent —
this is the best moment to walk through any open questions.

```bash
# 1. Import any file from disk (lands it in the vault, then ingests).
#    Documents (PDF/DOCX/PPTX/XLSX/HTML/CSV/JSON/XML) are auto-converted to
#    Markdown; the original is archived under raw/originals/.
wiki import --no-interactive ~/Downloads/report.pdf
# (or ingest a clip already in the vault: wiki ingest --no-interactive "raw/sources/<file>.md")

# 2. Right away, surface the clarifications THIS material raised:
wiki review --source raw/sources/<the-landed-file>.md
```

Ingest finishes instantly with the maintainer's best-guess choices already
applied. Present any clarifications to the user in chat (see Review below) and
resolve them in the conversation. The user is never blocked — if they ignore the
questions, the best-guess choices stand.

**If a daemon is serving this vault** (`wiki status` shows `daemon: running`),
`import`/`ingest` will not ingest in-process — they queue the file for the
daemon and print "queued for the running daemon" plus the exact `track:` and
`review:` commands (with the landed `raw/sources/<file>.md` path). That's
expected — don't re-run the command. Poll until the daemon finishes, then review:

```bash
# poll the source's state until it leaves "in progress" (exit 3):
while wiki status --source raw/sources/<file>.md; [ $? -eq 3 ]; do sleep 2; done
#   prints: queued | ingesting | processed | failed | untracked
#   exit:   3 in-progress · 0 processed · 1 failed · 2 untracked

# then surface that material's clarifications:
wiki review --source raw/sources/<file>.md
```

`wiki review --source` now disambiguates the empty case: "still processing
(...)" vs "processed — no open clarifications" vs "ingest failed ..." — so an
empty list no longer means "maybe still working".

### Query the wiki

```bash
wiki query "What do my sources say about X?"
# add --save to file the answer as a wiki/queries/ page:
wiki query --save "What do my sources say about X?"
```

### Review open clarifications

The review queue is an **audit log of decisions the maintainer already made and
applied** — not a list of blocking questions. Each item shows numbered options
with a ★ recommended default. Usually you just `accept`.

```bash
wiki review                                  # list all open questions, grouped by source
wiki review --source raw/sources/<file>.md   # only the questions from one material

# Resolve (escalating cost):
wiki review accept <id>                       # take the ★ default — instant, NO LLM
wiki review answer <id> --pick 2              # choose option 2     — runs an LLM apply pass
wiki review answer <id> "custom answer"       # free-text override  — runs an LLM apply pass
```

Present each question's options to the user and let the conversation decide.
Prefer `accept` for the common "that's fine" case — it's free and needs no auth.
One material can raise several independent questions; resolve each by its `<id>`.

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

### API token management

```bash
wiki token generate     # create a token (prints it once — copy to the iOS app)
wiki token show         # print the current token
wiki token rotate       # replace the token with a new one
```

When starting `wiki serve` for a user who wants iOS app access:
1. Check if a token exists: `wiki token show`
2. If not, generate one: `wiki token generate`
3. Tell the user to paste the token into the WikiReader app settings.
4. Start the daemon: `wiki serve`

The API server starts automatically when `wiki serve` runs. Use `--no-api` to
disable it.

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
