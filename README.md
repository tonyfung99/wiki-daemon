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

A source is plain Markdown. **Documents** (PDF, DOCX, PPTX, XLSX, HTML, CSV,
JSON, XML) are auto-converted to Markdown on entry — both by `wiki import` and by
the daemon when one is dropped into `raw/sources/` directly; the converted `.md`
becomes the source of record and the original is archived to `raw/originals/`.

The daemon is the **single writer** of `wiki/`: it serializes ingest jobs,
de-dupes by content hash, recovers from crashes, and handles iCloud "dataless"
files. The `raw/` → `wiki/` boundary is a firewall (the watcher only watches
`raw/`; `claude` only writes `wiki/`). To keep that invariant, when a daemon is
running, a manual `wiki import`/`ingest` does **not** ingest in-process — it
lands the file and queues it for the daemon (then check `wiki review`). Without a
daemon, manual ingest takes a local lock so two commands can't double-write.

## Requirements

- **macOS** (the production host is Intel x86_64 on macOS 15.7.3 Sequoia).
- **Python 3.12+**.
- An **agentic CLI** installed and authenticated — one of
  [`claude`](https://docs.claude.com/en/docs/claude-code) (default),
  [`gemini`](https://github.com/google-gemini/gemini-cli), or
  [`codex`](https://developers.openai.com/codex/cli). wiki-daemon drives it to
  read sources and write the wiki.

## Providers (which LLM CLI drives the wiki)

wiki-daemon uses an agentic CLI that edits the vault's files. Pick one with
`--provider` (or `WIKI_PROVIDER`, or `provider = "…"` in
`~/.config/wiki/config.toml`); default is `claude`:

| `--provider` | CLI | brain file it reads | auth |
|---|---|---|---|
| `claude` | Claude Code | `CLAUDE.md` | `claude setup-token` |
| `gemini` | Gemini CLI | `GEMINI.md` | `GOOGLE_API_KEY` / `gemini` login (free tier) |
| `codex` | Codex CLI | `AGENTS.md` | `codex login` / `OPENAI_API_KEY` |

The maintainer instructions live in **one canonical `AGENTS.md`**; `CLAUDE.md`
and `GEMINI.md` are symlinks to it, so every provider gets identical reasoning.
`wiki doctor` verifies the brain is current and the symlinks are intact, and
`wiki doctor --fix` repairs any iCloud-broken symlink (and migrates a legacy
`CLAUDE.md` vault to `AGENTS.md`).

## Install

**Editable (development):**

```bash
git clone https://github.com/tonyfung99/wiki-daemon.git
cd wiki-daemon
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
# run it as: .venv/bin/wiki …
```

**pipx (recommended for daily use)** — puts `wiki` on your PATH everywhere:

```bash
pipx install ~/workspace/wiki-daemon     # path to the cloned repo
wiki --version
```

Either way you also need the **[`claude` CLI](https://docs.claude.com/en/docs/claude-code)**
installed and authenticated — `wiki` shells out to headless `claude -p` for
ingest/query/lint (for an unattended daemon use `claude setup-token`).

This installs the **`wiki`** console script — manual commands (`init`, `ingest`,
`import`, `status`, `query`, `lint`, `doctor`) plus the daemon (`wiki serve`).

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
wiki serve --vault "$VAULT"

# Anytime: how many sources have been ingested?
wiki status --vault "$VAULT"

# Ask the wiki a question (read-only); add --save to keep the answer as a page.
wiki query --vault "$VAULT" "What do my sources say about X?"
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

## Choosing the vault

`--vault <path>` works on every command, but is optional — `wiki` finds the
vault by, in order: the `--vault` flag, the `WIKI_VAULT` environment variable, an
upward search from the current directory (so just `cd` into a vault), then a
`default_vault` in `~/.config/wiki/config.toml` (set it with
`wiki init --set-default`).

```bash
cd "$VAULT" && wiki status        # discovered from the current directory
export WIKI_VAULT="$VAULT"        # or set it once in your shell profile
```

## Commands

`--vault` is optional when the vault is discoverable (see *Choosing the vault*).

| Command | What it does |
|---|---|
| `wiki init --vault <path>` | Scaffold a vault (`CLAUDE.md`, `purpose.md`, `raw/`, `wiki/`). Idempotent. |
| `wiki ingest --vault <path> [--interactive\|--no-interactive] <file>` | Ingest one source now. Interactive (the default in a terminal) asks clarifications live; headless queues them to `wiki/review/`. |
| `wiki import --vault <path> [--interactive\|--no-interactive] <file>` | Bring a file into `raw/sources/` and ingest it. Markdown/text lands as-is (frontmatter added if missing); **documents (PDF, DOCX, PPTX, XLSX, HTML, CSV, JSON, XML) are converted to Markdown** on the way in via [markitdown](https://github.com/microsoft/markitdown). The original is left in place. Same interactive/headless behavior as `ingest`. |
| `wiki query --vault <path> [--save] "<question>"` | Ask the wiki a question — reads `index.md`, opens relevant pages, prints a cited answer. `--save` files it as a `wiki/queries/` page. |
| `wiki lint --vault <path> [--deep] [--fix] [--yes]` | Health-check the wiki: dead links, iCloud conflict-dupes, orphans, index/log integrity. `--deep` adds an LLM semantic scan; `--fix` repairs (confirmed). |
| `wiki status --vault <path> [--source <file>]` | Show daemon health: running?, auth state, queue depth, processed count, open clarifications, last error. With `--source`, report just that source's ingest state — `queued`/`ingesting`/`processed`/`failed`/`untracked` — and set an exit code (`0` processed, `1` failed, `2` untracked, `3` in progress) so an agent can poll until done. |
| `wiki review --vault <path>` | List open ingest clarifications. `wiki review answer <id> "…"` records your answer and applies it. |
| `wiki doctor --vault <path> [--probe <file>] [--fix] [--yes]` | Validate environment, tooling, and iCloud handling on the host. Also flags a stale vault `CLAUDE.md` (missing maintainer sections); `--fix` repairs it by appending the missing sections (`--yes` skips the prompt). |
| `wiki serve --vault <path> [--reconcile-interval N] [--verbose]` | Run the daemon: watch `raw/sources/` and ingest autonomously. Logs lifecycle events (startup, `ingesting`/`ingested` per file, deferred not-ready files, reconcile sweeps) to stdout + `daemon.log`; `--verbose` adds per-file watcher (`detected`) events at DEBUG. |
| `wiki token {generate\|show\|rotate}` | Manage the API bearer token for WikiReader / external clients. `generate` creates and prints a new token, `show` prints the current one, `rotate` replaces it. |

The vault's `CLAUDE.md` is the **maintainer brain** — it defines the page
templates, naming, and the ingest algorithm `claude -p` follows. Tune it to
change how your wiki is built.

## HTTP API

`wiki serve` includes an HTTP API server that lets [WikiReader](https://github.com/tonyfung99/WikiReader)
(or any client) query the wiki vault over the network.

### Setup

```bash
# 1. Generate an API token.
wiki token generate
# prints: wk_a1b2c3...  (copy this into the WikiReader app settings)

# 2. Start the daemon (API starts automatically).
wiki serve --vault "$VAULT"
# logs: api: listening on 0.0.0.0:7880
```

The API binds to `0.0.0.0:7880` by default. Configure in
`~/.config/wiki/config.toml`:

```toml
api_token = "wk_..."       # managed by `wiki token`
api_port = 7880             # default
api_bind = "0.0.0.0"        # default
```

Or override with `--api-port` / `--api-bind` on `wiki serve`.

### Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET`  | `/api/v1/health` | No | Connection test — version, vault, provider status |
| `POST` | `/api/v1/query` | Yes | Start a query job (returns `jobId` immediately) |
| `GET`  | `/api/v1/query/{jobId}` | Yes | Poll for query result |

Queries are **async**: POST returns a `jobId`, poll GET until `status` is `done`
or `failed`. Answers are returned as Markdown with `[[wiki-link]]` citations
extracted into a structured `citations` array.

### Private network access

The daemon runs on your home machine. To reach it from WikiReader on iOS:

- **[Tailscale](https://tailscale.com)** (recommended): install on both devices,
  connect to the same tailnet, use the Mac's Tailscale IP as the daemon URL.
- **LAN**: use the Mac's local IP (same Wi-Fi network).
- **Disable with `--no-api`** if you don't need the HTTP server.

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
  `claude setup-token` for an unattended daemon) and flags a stale vault
  `CLAUDE.md` whose maintainer sections lag the current template
  (`wiki doctor --fix` appends the missing ones, since `wiki init` never
  overwrites an existing brain).
- **Clarifications** = when a structural decision is ambiguous, an interactive
  ingest asks you live; otherwise (scripts, the daemon) the maintainer files an
  open question under `wiki/review/`. Resolve later with `wiki review` and
  `wiki review answer <id> "…"`, which runs a maintainer pass to apply it.
- **Query** = `wiki query "…"` runs `claude -p` read-only (Read/Glob/Grep) in the
  vault: it reads `index.md`, opens the relevant pages, and prints a cited answer
  (Markdown tables, Mermaid, code snippets — no code execution). `--save` files
  the answer as a `wiki/queries/` page so explorations compound like sources.
- **Lint** = `wiki lint` runs pure-Python checks (dead `[[links]]`, iCloud
  conflict-duplicates like `page 2.md`, orphan pages, index/log integrity) and
  prints a report — exit 1 if anything is found, so it's cron-friendly. `--deep`
  adds an LLM scan for contradictions/stale claims; `--fix` deletes conflict-dupes
  and runs an LLM repair pass (always confirmed) to resolve the rest.

## Hermes Agent Integration

If you drive your Mac with a [Hermes agent](https://hermes-agent.nousresearch.com),
this repo ships a skill at [`skills/wiki/`](skills/wiki/SKILL.md) so the agent can
operate the wiki naturally ("clip this", "what do my notes say about X?", "is the
daemon healthy?"). It wraps the same `wiki` CLI documented above.

Register the in-repo skill directory and your vault path in `~/.hermes/config.yaml`
(the skill stays in sync with the code — no copy to drift, just `git pull`):

```yaml
skills:
  external_dirs:
    - ~/workspace/wiki-daemon/skills    # your clone path
  config:
    wiki:
      vault_path: "/path/to/your/vault"
```

Or set the vault from the CLI:

```bash
hermes config set skills.config.wiki.vault_path "/path/to/your/vault"
```

Then invoke it from any Hermes interface (Telegram, CLI, …):

```
/wiki import ~/Downloads/some-article.md
/wiki query "What do my sources say about ERC-4337?"
/wiki status
```

On first use the skill records your vault as the `wiki` default
(`wiki init --set-default`), after which it runs commands without `--vault`.

## Docs

- **Design / rationale:** [`docs/design.md`](docs/design.md)
- **Hermes skill design:** [`docs/specs/2026-06-05-hermes-wiki-skill-design.md`](docs/specs/2026-06-05-hermes-wiki-skill-design.md)
- **Implementation plan (M1+M2):** [`docs/plans/2026-05-31-ingest.md`](docs/plans/2026-05-31-ingest.md)
- **Host validation runbook:** [`docs/RUNBOOK-intel-host-validation.md`](docs/RUNBOOK-intel-host-validation.md)

## Status

M1 + M2 (autonomous ingest) and M3 (query / HTTP API / hermes integration) are
implemented and tested; ingest is validated against the real LLM. Lint / iOS
querying (M4–M5) are planned — see the design doc.

## License

[MIT](LICENSE).
