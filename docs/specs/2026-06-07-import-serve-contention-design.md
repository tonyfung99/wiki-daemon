# import/ingest vs. a running daemon — design

**Status:** approved (brainstorm)
**Date:** 2026-06-07

## Problem

`wiki import` lands a file into `raw/sources/` and then ingests it **in the
caller's process** (`cli.cmd_import` → `ops.ingest`). If `wiki serve` is running
for the same vault, its watcher (`daemon._Handler.on_created`) independently sees
the new file and enqueues its own ingest job. The same source is then ingested
**twice, by two processes**:

- Content-hash dedup (`StateStore`) only protects *sequential* re-ingests. Each
  process loads its own in-memory snapshot of `processed.json`; if both start
  before either calls `mark_processed`, both run `claude`.
- This breaks the **single-writer invariant** (design.md/README: the daemon is
  the sole writer of `wiki/`, the `raw/`→`wiki/` firewall). Two concurrent
  `claude` writers can produce iCloud conflict-duplicates (`page 2.md`) — the
  very thing `wiki lint` cleans up. A Hermes agent running `wiki import` against
  a served vault hits the same race.

The bare race also exists without a daemon: two manual `wiki ingest` commands (or
a double-run `import`) on the same vault can overlap.

## Goal

Make manual `import`/`ingest` safe alongside a running daemon, and safe against
each other, using two layered mechanisms grounded in established CLI practice:

1. **Defer-to-daemon (ownership model).** Like `docker`/`systemctl`: when the
   daemon that owns the vault is alive, the CLI does not write `wiki/` itself —
   it lands the file, enqueues the job, and lets the daemon ingest.
2. **Advisory lock (defense-in-depth).** Like git's `index.lock`: a per-vault
   `flock` around in-process ingest so two manual commands fail fast instead of
   double-writing.

References: git `index.lock` (atomic create + rename, prevents concurrent index
writes); `flock(2)` advisory locks (kernel-released on crash, `-n`/non-blocking
to fail fast); docker/systemctl CLI-defers-to-daemon ownership.

## Decisions

- **Ownership rule:** the running daemon is the single writer of `wiki/`. While
  it is alive, manual `import`/`ingest` must not ingest in-process — they defer.
- **Detection:** `daemon_owns_vault(cfg)` = `status.json` has a `pid` and
  `runtime.is_pid_alive(pid)` (same primitives `wiki status` already uses).
- **Defer = land + enqueue + return 0.** `import` still lands the file;
  both commands then `JobQueue.enqueue` the job (dedupes by payload) and print
  `queued for the running daemon (N pending)`. No in-process ingest.
- **Lock = fail-fast `flock`.** A non-blocking exclusive lock at
  `cfg.state_dir/ingest.lock` (local, off-iCloud) wraps in-process ingest. If it
  cannot be acquired, another manual ingest is running → print
  `another ingest is in progress for this vault` and exit 1. The daemon does NOT
  take this lock (defer-to-daemon already covers its case).
- **Interactive sub-case:** if the daemon is alive, interactive ingest is not
  possible (it would be a second live writer). The CLI defers anyway and prints
  a note that the file is queued and will be ingested headlessly; clarifications
  surface via `wiki review`. If `--interactive` was passed *explicitly*, the
  note is prominent so the downgrade is not silent.

## Non-goals / known limits

- **Cross-host is out of scope.** `flock` is local-only; two machines sharing
  one iCloud vault, each running a daemon, will not coordinate. The daemon is
  meant to run on a single host. Documented limitation; a future vault-level
  owner record (not advisory locks) is the real cross-host fix.
- The daemon does not hold a continuous lock — defer-to-daemon handles its case.
- A small TOCTOU window exists (a daemon starting between the
  `daemon_owns_vault` check and lock acquisition). Backstopped by content-hash
  dedup; documented, not engineered away.
- No change to ingest/verify logic itself, or to the review/options flow.

## Architecture

### `runtime.py`

```python
def daemon_owns_vault(cfg) -> bool:
    """True if a live daemon process is serving this vault (status.json pid +
    is_pid_alive)."""
    pid = StatusFile(cfg.state_dir / "status.json").read().get("pid")
    return is_pid_alive(pid)


@contextmanager
def vault_ingest_lock(cfg):
    """Non-blocking exclusive flock at state_dir/ingest.lock. Raises
    IngestLockBusy if another holder has it. Released on exit or process death."""
    lock_path = cfg.state_dir / "ingest.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise IngestLockBusy(str(lock_path)) from exc
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
```

`IngestLockBusy` is a small exception defined alongside.

### `cli.py` — `cmd_import` / `cmd_ingest`

Shared shape (import lands the file first; ingest receives an existing path):

```
if daemon_owns_vault(cfg):
    JobQueue(cfg.queue_dir).enqueue(Job(type="ingest", payload=str(path)))
    pending = <count pending-*.json>
    # interactive downgrade note when applicable
    print(f"queued for the running daemon ({pending} pending)")
    return 0

try:
    with vault_ingest_lock(cfg):
        result = ingest_interactive(...) if _want_interactive(...) else ingest(...)
except IngestLockBusy:
    print("another ingest is in progress for this vault", file=sys.stderr)
    return 1
# ... existing result handling (skipped / ingested / failed) ...
```

- Enqueue uses the same absolute path form the watcher uses, so
  `JobQueue.enqueue`'s payload dedup applies. Even a duplicate job is harmless:
  the daemon's content-hash dedup skips it on dequeue.
- The interactive note: when `daemon_owns_vault` and interactive was requested,
  print "daemon is serving this vault; your file is queued and will be ingested
  headlessly — answer any clarifications with `wiki review`." Prominent if
  `--interactive` was explicit (`ns.interactive is True`).

### Docs

- `README`: note that with a daemon running, `import`/`ingest` queue the file for
  the daemon rather than ingesting in-process; the bare-race lock is mentioned.
- `skills/wiki/SKILL.md`: add to the capture recipe — "if a daemon serves the
  vault, `import` just queues the file; then poll `wiki review --source <file>`
  (the daemon ingests headlessly)."

## Testing strategy (TDD)

Pure-Python, no network / no `claude`:

- `tests/test_runtime.py`
  - `daemon_owns_vault`: true when `status.json` pid is alive (use `os.getpid()`),
    false when no status / dead pid.
  - `vault_ingest_lock`: a second acquisition while held raises `IngestLockBusy`;
    the lock is released after the `with` block (re-acquire succeeds).
- `tests/test_cli.py`
  - `cmd_import` with `daemon_owns_vault` monkeypatched True: lands the file,
    enqueues a job (assert a `pending-*.json` exists), prints `queued`, and the
    real `ingest`/`ingest_interactive` is NOT called (spy).
  - `cmd_ingest` same defer behavior.
  - no daemon: in-process ingest runs under the lock (existing fake-runner path
    still passes).
  - lock contention: with the lock pre-held, `cmd_ingest` returns 1 and prints
    `another ingest is in progress`.
  - interactive + daemon present: defers with the headless note; explicit
    `--interactive` makes the note prominent.

## Risks

- `flock` semantics differ across platforms/filesystems; the lock lives in the
  local `state_dir` (not iCloud) to stay on a well-behaved local FS. macOS local
  FS supports `fcntl.flock`.
- TOCTOU between detection and lock (documented above; dedup backstops it).
- If `status.json` is stale (daemon crashed without clearing pid), `is_pid_alive`
  returns False for a dead pid, so the CLI correctly treats the vault as
  daemon-free — the existing `wiki status` "stale pid" handling already relies on
  this.
