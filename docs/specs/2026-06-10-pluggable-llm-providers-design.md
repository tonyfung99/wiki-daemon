# Pluggable LLM providers (agentic CLIs) — design

**Status:** requirement shaped (brainstorm); not yet approved for implementation
**Date:** 2026-06-10

## Problem

wiki-daemon is hard-wired to Claude Code. It shells out to
`claude -p "<prompt>" --allowed-tools … --dangerously-skip-permissions`, letting
Claude **edit the vault's files with its own agent tools**; the daemon then
verifies the file side-effects. The seam is `claude.py` (`run_claude`,
`run_claude_interactive`, `classify_failure`) plus `health.probe_auth`,
`config.claude_bin`, and the `setup-token` preflight.

The motivation is **cost / quota**: when the Claude subscription's quota or auth
is exhausted, ingest stops. Other agentic CLIs bring their own (often free or
already-paid) quota — Gemini CLI has a generous free tier; Codex runs on a
ChatGPT plan. Letting the operator choose the provider unblocks cost/quota and
avoids single-vendor lock-in.

## Key insight

wiki-daemon drives an **agentic CLI that edits files**, not a text LLM API.
Claude Code, Gemini CLI, and Codex CLI all share the same shape — *run headless
in the vault dir, read a project-instructions file, edit files, exit* — so they
slot behind one abstraction. Raw LLM APIs return text, not file edits, and would
require building a tool-execution loop (re-implementing the agent); that is out
of scope here (phase 3).

## Scope (phase 1)

- A **Provider abstraction** over the per-CLI differences.
- Three providers: **claude, gemini, codex**.
- **Provider selection** via config (one active provider per run).
- **Single canonical brain** `AGENTS.md` with `CLAUDE.md`/`GEMINI.md` symlinks,
  kept healthy by `doctor`.
- `doctor`/`status`/auth surfacing generalized to the active provider.

### Deferred
- **Phase 2 — failover:** an ordered provider list; on `auth`/`quota`/
  `unavailable` failure (already classified + backed-off by the daemon), fall
  back to the next provider.
- **Phase 3 — raw-API provider:** an `api` provider kind with a tool-execution
  harness, for per-token cheap models.
- **Non-goals now:** OpenCode and other CLIs (trivial to add once the
  abstraction exists), per-operation provider routing, multiple providers active
  in one vault.

## Provider mapping (verified)

| | Claude Code | Gemini CLI | Codex CLI |
|---|---|---|---|
| headless run | `claude -p <prompt>` | `gemini -p <prompt>` | `codex exec <prompt>` |
| auto-approve writes | `--dangerously-skip-permissions` | `--yolo` | `--sandbox workspace-write --ask-for-approval never` |
| read-only mode | `--allowed-tools Read Glob Grep` | (omit `--yolo`; can read, cannot write headlessly) | `--sandbox read-only` |
| reads brain file | `CLAUDE.md` | `GEMINI.md` | `AGENTS.md` |
| auth | OAuth / `claude setup-token` | `GOOGLE_API_KEY` or Google login (free tier) | OpenAI login / `OPENAI_API_KEY` |

## Architecture

### `agent.py` (generalize `claude.py`)

```python
@dataclass(frozen=True)
class Provider:
    name: str            # "claude" | "gemini" | "codex"
    bin: str             # default executable name
    brain_filename: str  # the file THIS CLI reads: CLAUDE.md / GEMINI.md / AGENTS.md
    auth_hint: str       # remediation text, e.g. "run `claude setup-token`"
    def headless_cmd(self, prompt: str, *, write: bool) -> list[str]
    def interactive_cmd(self, prompt: str, *, write: bool) -> list[str]
    def classify_failure(self, result) -> str   # auth | quota | unavailable | error

PROVIDERS = {"claude": …, "gemini": …, "codex": …}
def get_provider(cfg) -> Provider
def run_agent(provider, prompt, cwd, *, write, timeout, runner) -> AgentResult
def run_agent_interactive(provider, prompt, cwd, *, write, runner) -> int
```

- `write` replaces the current "allowed_tools" distinction: write ops (ingest,
  apply, lint-repair, save-query) auto-approve edits; read-only ops (query,
  lint-scan) run without write approval. Each provider maps `write` to its own
  flags (table above). The Claude provider keeps the `--allowed-tools` lists
  internally (read set vs write set).
- `run_agent` keeps the existing `AgentResult(ok, returncode, stdout, stderr)`
  shape (renamed from `ClaudeResult`); `ops.py` is unchanged except it resolves
  a provider once and passes `write=…`.
- `classify_failure` gains a `quota` bucket (rate/limit/exhausted signs) so
  phase-2 failover and clearer messaging are possible; existing `auth`/
  `unavailable` keep working.

**Read-only enforcement note:** Claude and Codex can *enforce* read-only via
flags (`--allowed-tools` / `--sandbox read-only`). Gemini relies on omitting
`--yolo` (it cannot write without approval headlessly) plus the prompt's
"READ-ONLY: do not modify files." This is a best-effort guarantee on Gemini —
acceptable, documented.

### Brain file: one canonical `AGENTS.md` + symlinks

- **Canonical:** `AGENTS.md` (vendor-neutral standard; Codex reads it natively)
  carries the maintainer instructions **and the version stamp**
  (`<!-- wiki-template: vN -->`).
- **`CLAUDE.md` → symlink → `AGENTS.md`**, **`GEMINI.md` → symlink → `AGENTS.md`**.
  Each CLI reads its own filename; the filesystem resolves all to one brain, so
  every provider gets identical instructions → consistent reasoning, and
  switching providers needs no content changes.
- **`scaffold`/`init`:** write `AGENTS.md` from the bundled template, then create
  the two symlinks. Add `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` handling to the
  idempotent scaffold (never clobber a real edited file).
- **`prompts.py` neutralized:** replace literal "CLAUDE.md" references with
  "your project instructions" — each agent auto-loads its brain file, so prompts
  are filename-agnostic.

### `doctor` — brain health + symlink self-heal (iCloud safety)

iCloud Drive is unreliable with symlinks (it can drop/break them). Since the
agents run on the daemon Mac, the symlinks only need to resolve locally there —
but iCloud's sync engine may still mangle them. So `doctor` owns repair:

- Generalize `check_claude_md` → `check_brain`:
  - `AGENTS.md` present and version-current (existing stamp logic).
  - `CLAUDE.md` and `GEMINI.md` exist and are symlinks resolving to `AGENTS.md`.
  - WARN naming the problem (stale `AGENTS.md`, or a missing/broken symlink).
- `doctor --fix`:
  - stale `AGENTS.md` content → backup + overwrite (existing behavior, on
    `AGENTS.md`).
  - missing/broken `CLAUDE.md`/`GEMINI.md` symlink → (re)create it pointing at
    `AGENTS.md`.
- Migration: an existing vault has a **real** `CLAUDE.md` (not a symlink).
  `doctor --fix` migrates: write its content to `AGENTS.md` (stamped), replace
  `CLAUDE.md` with a symlink, create the `GEMINI.md` symlink. Non-destructive
  (the old `CLAUDE.md` content becomes `AGENTS.md`).

### Selection config

- `Config.provider: str = "claude"`.
- Resolution order mirrors vault discovery: `--provider <name>` flag →
  `WIKI_PROVIDER` env → `provider` in `~/.config/wiki/config.toml` → default
  `claude`. Unknown name → clear error listing valid providers.
- `--provider` added to the shared `common` argparse parent (every command).

### Surfacing

- `health.probe_auth(cfg)` uses the resolved provider's `headless_cmd` +
  `classify_failure` + `auth_hint`.
- `doctor`: `tool:<provider>` (binary present) and `tool:<provider>-auth`
  (probe). The brain check is provider-agnostic (all read the same AGENTS.md).
- `status`: show the active provider; auth-failure messages use the provider's
  `auth_hint` (not the hard-coded `claude setup-token`).
- `serve` preflight `setup-token` becomes provider-specific guidance via
  `auth_hint`; the interactive `claude setup-token` launcher stays Claude-only
  (other providers print their hint).

## Testing strategy (phase 1, TDD; no real CLI calls)

- `tests/test_agent.py`: each provider builds the right headless/interactive
  command for `write=True/False` (assert the flag mapping in the table);
  `classify_failure` buckets auth/quota/unavailable; `get_provider` resolves from
  config/env/flag and errors on unknown.
- `tests/test_scaffold.py`: `init` writes `AGENTS.md` + `CLAUDE.md`/`GEMINI.md`
  symlinks resolving to it.
- `tests/test_doctor.py`: `check_brain` PASS when AGENTS.md current + symlinks
  intact; WARN on stale AGENTS.md, on a missing symlink, on a real (non-symlink)
  CLAUDE.md (legacy); `--fix` migrates a legacy vault and recreates a deleted
  symlink.
- `tests/test_ops.py` / `test_health.py`: ops resolve a provider and pass
  `write` correctly; probe_auth uses the provider (injected fake runner).
- `tests/test_cli.py`: `--provider` parses; bad provider errors.
- Real (manual / optional CI): ingest one source through Gemini CLI and Codex to
  confirm the end-to-end agent path, gated on those CLIs + auth being present.

## Risks / open items

- **Read-only enforcement on Gemini** is prompt-level, not flag-enforced
  (documented above).
- **iCloud symlink durability** — mitigated by `doctor` self-heal, but a vault
  could run with a broken symlink between `doctor` runs; the daemon could also
  verify/repair the active provider's symlink at startup (cheap) — to decide in
  the plan.
- **Per-CLI flag drift** — these CLIs evolve quickly; the flag mapping is
  centralized in `agent.py` so updates are one place. Pin nothing on exact
  versions; classify failures by output text, as today.
- **`run_agent` rename** touches `ops.py`/`health.py` imports — mechanical, kept
  behind the same result shape to minimize churn.
