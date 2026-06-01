# Runbook — Intel host validation (Tasks 13 & 17)

These two steps must run on the **Intel x86_64 home Mac (macOS 15.7.3 Sequoia)**
against the **real iCloud Drive vault** — they can't be validated on the arm64
dev machine or in unit tests (which mock iCloud).

## Prerequisites (one-time)

- Signed into iCloud; the vault is a plain folder under
  `~/Library/Mobile Documents/com~apple~CloudDocs/<your-vault>`.
- Repo cloned and installed:
  ```bash
  cd ~/workspace/wiki-daemon
  python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
  ```
- `claude` CLI installed and logged in (`claude --version`).
- Set a shell var for convenience:
  ```bash
  VAULT="$HOME/Library/Mobile Documents/com~apple~CloudDocs/<your-vault>"
  ```

---

## Task 13 — iCloud handling

1. **Scaffold** (skip if the vault already has `CLAUDE.md`):
   ```bash
   .venv/bin/wiki init --vault "$VAULT"
   ```
2. **Pin the vault** so iCloud keeps it downloaded: in Finder, right-click the
   vault folder ▸ **Keep Downloaded**.
3. **Run the doctor:**
   ```bash
   .venv/bin/wiki doctor --vault "$VAULT"
   ```
   Expect `tool:*`, `vault:*` (incl. `vault:icloud` PASS, `vault:pinned` PASS).
   The `icloud:roundtrip` check is best-effort — if it WARNs that the probe
   "didn't evict (not uploaded yet)", do step 4 to confirm the round-trip
   against a file iCloud has already synced.
4. **Confirm the real round-trip** on a synced file: in Finder, right-click any
   `.md` already in the vault ▸ **Remove Download** (evicts it to dataless),
   then:
   ```bash
   .venv/bin/wiki doctor --vault "$VAULT" --probe "$VAULT/<that-file>.md"
   ```
   Expect `icloud:probe [PASS] dataless detected → materialized`.

**Task 13 passes when:** `wiki doctor` reports no `FAIL`, and the `--probe`
round-trip is PASS.

---

## Task 17 — autonomous ingest end-to-end

Use a short reconcile interval so you don't wait 5 minutes:

1. **Start the daemon** (foreground; logs to the terminal):
   ```bash
   .venv/bin/wiki-daemon serve --vault "$VAULT" --reconcile-interval 30
   ```
2. **Clip from the iPhone:** share a tweet via the **WikiReader** share
   extension. It writes a `.md` into `raw/sources/`; iCloud syncs it to the Mac.
   (No iPhone handy? Simulate by dropping a `.md` with frontmatter into
   `"$VAULT/raw/sources/"`.)
3. **Confirm autonomous ingest** (second terminal), within ~30s:
   ```bash
   .venv/bin/wiki status --vault "$VAULT"        # processed count went up
   ls "$VAULT/wiki/entities" "$VAULT/wiki/concepts" "$VAULT/wiki/sources"
   tail "$VAULT/wiki/log.md"                       # new "## [date] ingest | ..." line
   ```
4. **Crash recovery:** drop another `.md` into `raw/sources/`, immediately
   `Ctrl-C` the daemon, then restart it:
   ```bash
   .venv/bin/wiki-daemon serve --vault "$VAULT" --reconcile-interval 30
   ```
   The startup reconcile sweep should enqueue the un-ingested file and process
   it. Confirm via `wiki status` / `wiki/log.md`.

**Task 17 passes when:** a clip becomes wiki pages without manual intervention,
and a file dropped during a crash is still ingested after restart.

---

## Troubleshooting

- **Nothing ingests:** check the daemon terminal for errors; confirm the file
  reached `raw/sources/` on the Mac (`ls`), and that it isn't still dataless
  (`wiki doctor --probe`).
- **`claude` errors / hangs:** run the prompt manually once to confirm auth:
  `claude -p "hi" --dangerously-skip-permissions` in the vault dir.
- **Conflict duplicates** (`page 2.md`): expected to be rare (single writer);
  if they appear, that's the signal to adopt the spec's staging escape-hatch.
