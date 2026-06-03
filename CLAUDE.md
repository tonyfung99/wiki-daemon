# CLAUDE.md — repo guidance for AI agents

`wiki-daemon` is the Mac-side daemon that watches a plain-Markdown vault and
uses headless `claude -p` to maintain an LLM-owned wiki. See `README.md` for the
user-facing overview and `docs/design.md` for rationale.

## Docs layout & conventions

Design specs and implementation plans are **generic project artifacts** — keep
them in tool-neutral paths. Do **not** write them under a skill-named directory
(e.g. `docs/superpowers/...`); those paths leak tooling into a public repo.

- **Specs** → `docs/specs/YYYY-MM-DD-<topic>-design.md`
- **Plans** → `docs/plans/YYYY-MM-DD-<feature>.md`

This overrides any skill's default location (the brainstorming and writing-plans
skills honor a project's stated spec/plan paths). When a skill would write to
`docs/superpowers/specs` or `docs/superpowers/plans`, use `docs/specs` and
`docs/plans` instead. Likewise, keep skill/tool names out of the prose of specs
and plans where a neutral phrasing works — the documents should read as
project-generic to an outside reader.

## Workflow defaults

- When executing an implementation plan, **always use subagent-driven development**
  (`superpowers:subagent-driven-development`) by default — don't ask which
  execution mode to use. Fresh subagent per task + spec/code-quality review.

## Working in this repo

- Python 3.12; the venv is at `.venv`. Run tests with `.venv/bin/pytest -q`.
- One console script `wiki`: manual commands (`init`/`ingest`/`import`/`status`/
  `doctor`) plus the daemon via `wiki serve`. (`python -m wiki_daemon` also works.)
- The daemon is the single writer of `wiki/`; the `raw/` → `wiki/` boundary is a
  firewall (watcher only watches `raw/`; `claude` only writes `wiki/`).
