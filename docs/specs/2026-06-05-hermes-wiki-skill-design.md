# Hermes `wiki` skill — design

**Status:** approved (brainstorm)
**Date:** 2026-06-05

## Problem

An autonomous agent (e.g. a Hermes agent on the daemon host) has no
discoverable, invocable way to operate `wiki-daemon`. The repo documents the
`wiki` CLI for humans (README) and documents *developing* the codebase
(`CLAUDE.md`), but nothing tells an agent **how to drive the tool**: install it,
run the ingest → review → query loop, fix headless auth, and respect the
`raw/` → `wiki/` firewall.

## Goal

Ship a Hermes **skill** inside this repo so an agent can operate the wiki
naturally ("clip this", "what do my notes say about X?", "is the daemon
healthy?"). The skill lives with the code and is registered via Hermes
`external_dirs`, so it stays in sync on `git pull` — no copy to drift.

## Decision summary

- **Deliverable is a skill, not a passive `AGENTS.md`.** A skill is
  discoverable (`skills_list`), invocable, self-documents its setup, and can
  declare config + conditional activation. A doc is passive context only.
- **Lives in-repo at `skills/wiki/SKILL.md`**, registered via
  `skills.external_dirs` in `~/.hermes/config.yaml`. Single source of truth,
  always current with the CLI.
- **Vault handling: set-default once, then bare commands.** The skill records
  the vault as wiki's default; subsequent commands omit `--vault` and rely on
  wiki's discovery chain.
- **Content depth: hybrid.** One canonical example per operation; defer exact
  flags to `wiki <cmd> --help`. Resists drift against the CLI and README.

## Non-goals

- No passive `AGENTS.md` (explicitly decided against).
- No full inline flag reference — `--help` is the source of truth for flags.
- Not packaging a separate Claude Code skill. (The `SKILL.md` format is shared,
  so the file is reusable later if wanted; not in scope now.)
- No new CLI features — the skill only wraps the existing `wiki` commands.

## Schema verification (Hermes, official docs)

Confirmed against the Hermes developer/user docs (2026-06):

- Frontmatter required: `name` (must match folder), `description`.
- Optional top-level: `version`, `author`, `license`, `platforms`,
  `metadata`, `required_environment_variables`, `required_credential_files`.
- `metadata.hermes` recognized keys: `tags`, `related_skills`,
  `requires_toolsets`, `requires_tools`, `fallback_for_toolsets`,
  `fallback_for_tools`, `config` (list of `{key, description, default?,
  prompt?}`). **`category` is NOT recognized** — folded into `tags`.
- `external_dirs` is a real `skills.*` config key; paths support `~` and
  `${VAR}`; external dirs are scanned alongside `~/.hermes/skills/`, with local
  skills winning on name conflict.
- Skill config: stored under `skills.config.<key>` in `config.yaml`, injected
  into the skill's context on load.

Sources: Hermes docs — Creating Skills; Skills System (external_dirs);
Configuration.

## Architecture

Three artifacts:

### 1. `skills/wiki/SKILL.md`

**Frontmatter:**

```yaml
---
name: wiki
description: >
  Operate a local LLM-maintained Markdown knowledge base (wiki-daemon) via the
  `wiki` CLI: ingest/import sources, query the wiki, review clarifications, run
  health checks (lint), check daemon status/health. Use when the user wants to
  capture, search, or maintain their personal wiki.
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
```

Rationale: no `default:` on `vault_path` — a hard-coded path that does not exist
on the user's machine is worse than prompting. `requires_toolsets: [terminal]`
keeps the skill out of the prompt unless a shell toolset is active.

**Body sections:**

1. **When to use** — activation cues: capture/clip/import; query/search;
   status/health; review clarifications; lint/repair.
2. **Setup check** —
   - `wiki --version` to confirm install; else `pipx install <repo path>`.
   - Ensure default vault: `wiki init --set-default --vault "<wiki.vault_path>"`
     (idempotent; scaffolds if new, records default either way). After this,
     commands run bare.
3. **Core operations** — one bare-command example each, then "run
   `wiki <cmd> --help` for exact flags":
   - `wiki ingest <file>`
   - `wiki import <file>`
   - `wiki query "<question>"` (+ `--save`)
   - `wiki review` / `wiki review answer <id> "<text>"`
   - `wiki lint` (+ `--deep`, + `--fix --yes`)
   - `wiki status`
   - `wiki doctor`
   - `wiki serve`
4. **Headless agent note** — for an agent with no TTY: pass `--no-interactive`
   on ingest/import (clarifications queue to `wiki/review/` instead of
   blocking); pass `--yes` on `lint --fix`.
5. **Pitfalls** — `wiki` not on PATH (pipx / `which wiki`); `auth: FAILING` in
   `wiki status` → `claude setup-token`; nothing ingesting → check `wiki status`
   queue depth + last error; source must be a `*.md` file under `raw/sources/`.
6. **Verification** — after ingest: `wiki status` (processed count rises) +
   `tail wiki/log.md`.

### 2. README "Hermes Agent Integration" section

Registers the skill and sets the vault config:

```yaml
# ~/.hermes/config.yaml
skills:
  external_dirs:
    - ~/workspace/wiki-daemon/skills    # your clone path
  config:
    wiki:
      vault_path: "/path/to/your/vault"
```

Plus the equivalent `hermes config set skills.config.wiki.vault_path <path>`
one-liner and two example invocations (a query and an import).

### 3. `tests/test_skill.py` (drift guard)

Parse `skills/wiki/SKILL.md` and assert every `wiki <subcommand>` it references
is a real subcommand registered in `wiki_daemon.cli.build_parser()`. This is the
same instinct as `tests/test_cli.py`: keep the documented commands honest as the
CLI evolves. Mechanical, no network, no `claude` calls.

## Vault handling (two mechanisms, bridged)

- Hermes injects `wiki.vault_path` into the skill context on load.
- The skill's one-time setup turns that into wiki's own default via
  `wiki init --set-default --vault "<vault_path>"`, written to
  `~/.config/wiki/config.toml`.
- All later operations run **bare**; wiki's discovery chain
  (config → `$WIKI_VAULT` → upward search from CWD) resolves the vault.

Single value, set once; clean commands thereafter.

## Command accuracy (correcting the initial draft)

`--vault` is a **flag** on every subcommand (`cli.py`: `common.add_argument
("--vault", ...)`, attached via `parents=[common]`), not a positional. All
examples in the skill must use the flag form (or omit it once set-default is
run). Every command in the skill is generated from `cli.py` / `wiki --help`, not
copied from the draft.

## Testing strategy

- `tests/test_skill.py` — drift guard described above; runs under
  `.venv/bin/pytest -q` with the rest of the suite.
- Manual: register `external_dirs`, confirm the skill appears in `skills_list`,
  run a `wiki status` through the agent.

## Risks / open questions

- Hermes skill format could change; the `external_dirs` + `metadata.hermes`
  schema is verified as of 2026-06 but is external and may drift.
- The drift guard only checks subcommand *names*, not flags or prose accuracy.
  Acceptable: flags are deferred to `--help` by design.
