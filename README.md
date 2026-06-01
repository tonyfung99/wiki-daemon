# wiki-daemon

The Mac-side brain for a personal, LLM-maintained knowledge base in the spirit of
Karpathy's [LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

It watches an iCloud Drive vault for raw clips (written by the iOS
[WikiReader](https://github.com/tonyfung99/WikiReader) app), ingests them into an
interconnected Markdown wiki via headless `claude -p`, and serves queries — locally
via a `wiki` CLI / HTTP API and, through `hermes-agent`, from Telegram.

**Status:** M1+M2 (autonomous ingest) implemented and merged. `wiki init`,
`wiki ingest`, `wiki status`, `wiki doctor`, and `wiki-daemon serve` work.

- Design spec: [`docs/superpowers/specs/2026-05-31-wiki-daemon-design.md`](docs/superpowers/specs/2026-05-31-wiki-daemon-design.md)
- Ingest plan: [`docs/superpowers/plans/2026-05-31-wiki-daemon-ingest.md`](docs/superpowers/plans/2026-05-31-wiki-daemon-ingest.md)
- Validate on the Intel host: [`docs/RUNBOOK-intel-host-validation.md`](docs/RUNBOOK-intel-host-validation.md)
  (run `wiki doctor --vault <path>` to check iCloud + tooling).
