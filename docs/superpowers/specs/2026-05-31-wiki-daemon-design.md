# wiki-daemon — Design Spec

**Date:** 2026-05-31
**Status:** Approved for planning
**Repo:** `~/workspace/wiki-daemon`

## 0. Target environment

- **Daemon host (production):** an older **Intel (x86_64)** MacBook on **macOS
  15.7.3 (Sequoia)**. This is the primary target — **must work on x86_64 first**,
  then arm64. All iCloud handling (Section 6) must be validated here.
- **Dev/build machine:** a separate **arm64** Mac on macOS 26. **Do not assume
  parity** on either OS *or* architecture — iCloud behavior differs across
  releases, and arch differs, so iCloud and arch-sensitive paths are validated on
  the Intel Sequoia host, not the dev machine.
- **Arch rule:** prefer pure-Python deps; any compiled dep must ship an x86_64
  macOS wheel (watchdog, PyObjC, uvicorn all do). No arm-only dependencies.
- **LLM:** headless `claude -p` (Claude Code CLI, Node-based) — must be installed
  and runnable on the Intel host (Node 18+ has x86_64 macOS builds).

## 1. Purpose

A personal LLM-maintained knowledge base in the spirit of Karpathy's "LLM wiki"
([gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)).
Raw clips (tweets, articles, notes) are ingested by an LLM and transformed into
an interconnected wiki of Markdown pages that **compounds** over time. The wiki
is queryable, and queries themselves can be filed back as pages.

The **iOS WikiReader app** (separate repo, already built) is the capture tool:
it writes raw clips into the vault. **This project is the Mac-side brain**: it
watches for new clips, ingests them into the wiki via an LLM, and serves
queries — including queries routed from Telegram through the `hermes-agent`.

Reference implementation studied but **not** reused (too heavy, fragile to run):
[nashsu/llm_wiki](https://github.com/nashsu/llm_wiki).

## 2. Architecture (locked)

```
Telegram ──▶ hermes-agent ──shell out──▶ wiki daemon ──invoke──▶ claude -p
 (front door)  (orchestrator/        (plumbing: watcher,     (executor: reads
                delegator)            queue, single writer)   raw/, writes wiki/)
                                              │
                                              ▼
                                   iCloud Drive vault  ──iCloud sync──▶ all Apple devices
                                   (raw/sources + wiki/)                 (iOS app reads)
```

**Role split:**
- **hermes-agent** — front door (Telegram) + orchestrator. For any wiki request
  it *delegates* by shelling out to the `wiki` CLI; it never knows the schema.
  Designed as a delegator, not an executor.
- **wiki daemon** — deterministic plumbing. Owns the job queue, the
  single-writer invariant, iCloud handling, and verification. Invokes Claude;
  is **not** itself an LLM.
- **`claude -p`** — the executor *inside* the daemon. Reads raw sources, writes
  wiki pages, following the vault's `CLAUDE.md` as its schema.
- **iCloud Drive vault** — the shared data layer; syncs to every device on the
  Apple ID. The iOS app reads it (browser, markdown, graph).

**Two triggers, one queue:** the file **watcher** (autonomous ingest) and
**hermes/CLI** (query, save-query, lint) both submit jobs to the same daemon.

### Core principles

1. **Single-writer.** Only the daemon (via serialized `claude -p` runs) writes
   `wiki/`. Reads are free for anyone (hermes, Claude Code, the iOS app).
2. **Every mutation is a daemon job.** Ingest, save-query, and lint flow through
   the identical serial pipeline. *A saved query is just another ingest* whose
   "source" is a Q&A pair — no second code path.
3. **`raw/` → `wiki/` is a firewall.** The watcher watches `raw/sources/` only;
   Claude writes `wiki/` only. This prevents Claude's own writes from
   retriggering ingest.
4. **LLM proposes, daemon disposes.** `claude -p` is non-deterministic, so the
   daemon **always verifies** the result (did `index.md` change? does the source
   have a summary page? any dangling links?) and may retry.

## 3. Vault layout (the shared contract)

```
vault/                         ← a plain iCloud Drive folder (synced everywhere)
├── CLAUDE.md                  ← THE maintainer brain: the algorithm claude -p follows
├── purpose.md                 ← what this KB is for + interests (steers synthesis)
├── raw/
│   └── sources/               ← immutable inputs. iOS app + clippers write here. WATCHED.
│       └── 2026-05-31-<slug>.md
└── wiki/                      ← LLM-owned. claude writes here. NOT watched.
    ├── index.md               ← catalog: every page + 1-line summary, by category
    ├── log.md                 ← append-only: "## [2026-05-31] ingest | <title>"
    ├── entities/              ← people, orgs, products, places
    ├── concepts/              ← ideas, theories, methods
    ├── sources/               ← one summary page per raw source (traceability)
    └── queries/               ← saved query answers (these compound the wiki)
```

**Daemon state lives OUTSIDE the vault** at `~/.wiki-daemon/<vault-id>/` — never
in iCloud (would conflict and pollute content). Holds the SHA-256 dedupe cache,
the persisted job queue, and logs.

### Frontmatter contract

Raw source (written by the iOS app / clippers):
```yaml
---
type: source
source_url: https://x.com/...
captured_at: 2026-05-31T14:03:00Z
via: ios-share
title: <optional>
---
```

Wiki page (written by Claude):
```yaml
---
type: entity | concept | source | query
title: Acme Corp
sources: [raw/sources/2026-05-31-acme.md]   # traceability back to inputs
updated: 2026-05-31
---
```

### The schema lives in the vault

`CLAUDE.md` + `purpose.md` are the highest-leverage artifacts — they define the
ingest/query/lint algorithms, page templates, naming (kebab-case titles),
`[[wiki-link]]` conventions, and the rule to always update `index.md` + append
`log.md`. They live **with the vault**, not in this repo. The repo ships a
`wiki init` that scaffolds a fresh vault from templates.

## 4. The four operations

Each operation is **one `claude -p` invocation** with `cwd = vault`, scoped
allowed-tools, run by the daemon. `CLAUDE.md` defines the *algorithm*; the daemon
defines the *invocation + verification*.

| Op | Trigger | What Claude does | Daemon verifies |
|---|---|---|---|
| **ingest** `<file>` | watcher / CLI | Read source → extract entities/concepts → create/update pages + `[[cross-refs]]` → ensure a `sources/` summary exists → update `index.md` → append `log.md` | source summary exists; index & log changed; no new dangling links |
| **query** `"<q>"` | hermes / CLI | Read `index.md` → open relevant pages → synthesize answer **with citations** (read-only) | returns answer text in the HTTP response (stdout in M1) |
| **save-query** | opt-in, after a query | Persist Q&A as `wiki/queries/<slug>.md` + cross-link + update index/log | query page exists; index/log changed |
| **lint** | on-demand / weekly cron | Find contradictions, orphans, stale claims, dead `[[links]]`, **iCloud conflict-duplicates** (`* 2.md`); report | report produced |

**Defaults:** save-query is **opt-in** (hermes asks "save this?" via Telegram).
lint runs **on demand + an optional weekly cron**, not after every ingest.

## 5. Daemon internals

Long-running Python process. **One write-worker** pulling a **persisted serial
queue**; a separate **read-lane** for queries.

- **Job** = `{id, type: ingest|save-query|lint, payload, status}`. The single
  write-worker runs them one at a time → that *is* the single-writer invariant
  (only one `claude -p` mutating files at a time).
- **Dedupe.** SHA-256 of source content → `processed.json` (or sqlite) in
  `~/.wiki-daemon/<vault-id>/`. iCloud emits duplicate/spurious events; the hash
  makes ingest idempotent and cheap to skip.
- **Crash recovery.** Queue persisted to disk; on startup, re-enqueue `in-flight`
  jobs. Re-running an ingest is safe because the op is **create-or-update-by-
  title**, so a half-finished ingest converges instead of duplicating.
- **Reconcile sweep (the backbone).** On startup **and** on a timer, walk
  `raw/sources/` and diff against `processed.json`. **Never trust FSEvents alone
  in an iCloud folder** — this catches files that arrived while the daemon was
  down or whose events were dropped. The watcher is the fast path; the sweep is
  correctness.
- **Read-lane.** Queries are read-only and run **concurrently** with the
  write-worker, so a Telegram query stays responsive mid-ingest. A subsequent
  *save-query* is submitted as a normal write job.

## 6. iCloud Drive handling (macOS 15.7.3 Sequoia)

Researched and pinned to the **Sequoia** daemon host. Sequoia is post-Sonoma, so
the "dataless files, no `.icloud` stubs" model applies; the tooling below is
verified to exist on Sequoia.

### Mitigation first: pin the vault ("Keep Downloaded")

Sequoia supports **pinning** files/folders so iCloud won't evict them to a
dataless state; pinned items carry the `com.apple.fileprovider.pinned` extended
attribute ([Eclectic Light – Sequoia](https://eclecticlight.co/2024/09/30/how-icloud-has-changed-in-sequoia-pinning-and-more/)).
**One-time setup: pin the whole vault on the home Mac** (Finder ▸ *Keep
Downloaded*). This keeps `raw/` and `wiki/` materialized, so dataless handling
becomes a *safety net* for files that just synced but haven't downloaded yet —
not the hot path. (Finder caps multi-select pinning at 10 items; pin the folder.
Programmatic pinning via the xattr is undocumented/experimental — not relied on.)

### Read path — materialization (the safety net)

Sequoia **does not use `.icloud` stub files**; not-downloaded files are
**"dataless" APFS files** (normal name, full reported size, no data extents) —
*undetectable by filename* ([Eclectic Light – Sonoma](https://eclecticlight.co/2023/10/25/macos-sonoma-has-changed-icloud-drive-radically/),
Apple TN3150).

Before ingesting a raw source, the watcher pipeline must:
1. **Detect dataless** via the `SF_DATALESS` flag in `stat.st_flags`
   (`0x40000000`, readable from Python `os.stat`); cross-check with `ls -l%`
   (shows `%` for dataless and does **not** materialize dataless directories).
2. If dataless, **force download**: `brctl download <path>` (works on Sequoia),
   fallback `fileproviderctl materialize <path>`; poll until the flag clears.
3. **Stability gate:** only enqueue once size + mtime are unchanged for N seconds
   *and* the file is materialized (guards against partial sync states).
4. Ignore `.DS_Store`, dotfiles, non-`.md`.

**Sweep gotcha:** recursive globbing/listing can *accidentally materialize*
dataless directories ([fish-shell #8399](https://github.com/fish-shell/fish-shell/issues/8399)).
The reconcile sweep must use **stat-based, non-materializing** checks (per-entry
`os.stat`, `ls -l%` semantics) and must not blindly `**`-glob the vault.

Full pipeline: `FSEvent or sweep → is .md under raw/sources/? → materialize →
stability gate → SHA → dedupe → enqueue ingest`.

### Write path — direct-in-iCloud with discipline (chosen for v1)

Plain file writes (which `claude -p` does) bypass `NSFileCoordinator`, risking
conflict-duplicates / phantom files ([cabeen.io](https://cabeen.io/blog/posts/2026-01-15-icloud-is-not-a-folder.html),
[fsevents#285](https://github.com/fsevents/fsevents/issues/285)). That danger is
worst under **multi-writer** and **deletes** — neither of which we do. Mitigations
make direct writes acceptable for this low-frequency, single-writer workload:

- **Single writer** — only the home Mac's daemon writes `wiki/`; other devices
  read-only. No write-write conflicts.
- **Atomic temp-then-rename** for every page write.
- **Minimal deletes** — prefer tombstoning/updating over deleting pages.
- **Debounce** between writes; let iCloud settle.
- **lint detects conflict-duplicates** (`page 2.md`) so they're caught, not
  silent.

**Escape hatch (documented, not built):** if conflicts actually appear, move to a
*local source-of-truth + published iCloud read-replica* model (Claude edits a
local `wiki/`; daemon publishes changed files to iCloud via a coordinated
`NSFileCoordinator` helper).

## 7. Channel & clients

One **core service layer** (the queue + ops) behind **one HTTP API**; the CLI is
a thin HTTP client. This serves three clients with one transport.

**HTTP API (FastAPI/uvicorn):**
- **Binds to `127.0.0.1` by default** — hermes/CLI use loopback, no auth needed.
- **Optional LAN bind + bearer token** (off by default) — enabled when the iOS
  app needs to reach the daemon. A different machine = a real network surface, so
  the token is required whenever bound beyond loopback.
- Endpoints map to the ops: `POST /query` (sync, read-lane) · `POST /save-query`
  · `POST /ingest` · `POST /lint` · `GET /health` · `GET /status`.

**Clients:**
- **`wiki` CLI** — thin HTTP client over loopback (`wiki ingest <file>`,
  `wiki query "..."`, `wiki save-query`, `wiki lint`, `wiki status`). For manual
  use, cron (`wiki lint`), and hermes.
- **hermes** — shells out to the `wiki` CLI (same machine):
  ```
  Telegram msg → hermes: "wiki request?" → `wiki query "..."`
              → relay answer → (optional) "save this?" → `wiki save-query ...`
  ```
- **iOS WikiReader app (future)** — talks HTTP to the daemon over the home LAN
  (or a tunnel) with the bearer token, e.g. to query the wiki from mobile. The
  API is **designed for this now**; wiring it is a later milestone (Section 9).

The queue/single-writer model is unchanged regardless of transport: HTTP handlers
submit write jobs to the serial queue or run queries on the read-lane.

"Not limited locally" is satisfied today by **Telegram → hermes**; true
internet-exposed remote access (beyond the home LAN) remains *your* infra choice
(e.g. Tailscale) and is not built into the daemon.

## 8. Language & repo layout

**Python 3.12+.** The daemon is I/O-bound (wall-clock dominated by `claude -p`),
so Rust buys nothing technically; Python wins on iteration speed and trivial
subprocess/file/HTTP handling. All iCloud tricks above work from Python
(`os.stat` flags, `brctl`, PyObjC where needed).

```
wiki-daemon/
├── pyproject.toml
├── README.md
├── docs/superpowers/specs/2026-05-31-wiki-daemon-design.md   ← this file
├── src/wiki_daemon/
│   ├── __main__.py        # entrypoint: `wiki-daemon serve`
│   ├── config.py          # vault path, vault-id, ~/.wiki-daemon paths
│   ├── watcher.py         # FSEvents watcher + reconcile sweep
│   ├── icloud.py          # dataless detection + brctl materialization + stability gate
│   ├── queue.py           # serial write-queue + read-lane + crash recovery
│   ├── state.py           # SHA-256 cache, processed.json/sqlite
│   ├── claude.py          # `claude -p` invocation + per-op verification
│   ├── ops.py             # ingest / query / save-query / lint orchestration (core service layer)
│   ├── api.py             # HTTP API (FastAPI): loopback default, optional LAN+token
│   └── cli.py             # `wiki` CLI: in-process ops in M1; thin HTTP client over loopback from M3
├── prompts/               # op prompt wrappers (thin; real schema is vault/CLAUDE.md)
├── templates/             # vault scaffold: CLAUDE.md, purpose.md, index.md, log.md
└── tests/
```

## 9. Phasing (de-risk ingest first)

The prior `llm_wiki` attempt died at ingest, so **prove ingest quality before
building machinery around it.**

- **M1 — Manual ingest, zero daemon.** `wiki ingest <file>` runs the `ops` core
  **in-process** (no server) → `claude -p` → eyeball the pages. Iterate on
  `CLAUDE.md`/`purpose.md` until real clips produce good, well-linked pages.
  *(Highest-risk-first; no watcher, no HTTP API.)*
- **M2 — Daemon + iCloud watcher.** Autonomous ingest: serial queue, SHA dedupe,
  reconcile sweep, crash recovery, dataless-aware reads, stability gate.
- **M3 — Query + save-query + HTTP API + hermes.** FastAPI on loopback, `wiki`
  CLI as HTTP client, read-lane queries, opt-in save-query; wire hermes/Telegram.
- **M4 — Lint** (on-demand + weekly cron) + polish.
- **M5 (later) — iOS app connectivity.** Enable optional LAN bind + bearer token;
  the iOS WikiReader app queries the daemon over the home network. API is
  designed for this from M3; this milestone is the wiring + auth hardening.

## 10. Explicitly out of scope (YAGNI)

The weight that made `llm_wiki` fragile. Retrieval is `index.md` + grep until
that *demonstrably* fails.

- Embeddings + LanceDB / any vector store.
- Louvain graph communities / relevance scoring (the iOS app already renders the
  `[[wiki-link]]` graph).
- Deep-research / web-search ingestion.
- Human-in-the-loop review queue.
- Multi-format ingestion (PDF/DOCX/PPTX) — clips arrive as Markdown already.
- Browser extension / additional clippers (the iOS app covers capture).
- Multi-project / multi-vault support.
- Internet-exposed remote access beyond the home LAN (use Tailscale or similar;
  Telegram → hermes already covers remote query). The HTTP API itself **is** in
  scope (loopback in v1; optional LAN + token for the iOS app in M5).

## 11. Open items for the plan

- Exact `claude -p` flags: model, `--allowedTools` scoping per op, output format
  for verification.
- `CLAUDE.md` ingest-algorithm wording (the actual prompt engineering) — tuned
  empirically in M1.
- State store: flat `processed.json` vs sqlite (decide by M2 once volume is known).
- Reconcile-sweep interval and stability-gate window N (tune on real iCloud
  latency on the Sequoia host).
- HTTP API details for M3/M5: bearer-token generation/storage, LAN bind config
  (which interface), and whether the iOS app reaches it via LAN or Tailscale.
- Setup checklist item: pin the vault ("Keep Downloaded") on the Sequoia host.
- Validation: each milestone is smoke-tested on the **Intel x86_64 Sequoia host**
  (not just the arm64 dev machine), since iCloud + arch only differ there.
