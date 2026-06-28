# Design: HTTP API for WikiReader / external clients

**Date:** 2026-06-23
**Status:** Draft

## Problem

WikiReader started as an Obsidian-like iOS Markdown reader. The product direction
is now to make it the mobile query interface for an LLM-maintained wiki vault.
wiki-daemon already owns the query operation (`ops.query`), but there is no HTTP
surface for external clients. Hermes and Telegram are existing control surfaces
but should not sit between WikiReader and the daemon for app queries.

Target architecture:

```
WikiReader iOS app
  -> wiki-daemon HTTP API (inside `wiki serve`)
      -> ops.query(cfg, question, save=...)
      -> configured provider CLI: claude / gemini / codex

Hermes agent
  -> wiki-daemon CLI or HTTP API
```

## Decisions (from brainstorming)

- **Job-based async** — `POST /api/v1/query` returns a `jobId` immediately; the
  iOS app polls `GET /api/v1/query/{jobId}` for the result. Provider calls take
  15-60s; a synchronous HTTP request that long is fragile on mobile (iOS
  background task limits, network switches).
- **`save=true` by default** — app queries should compound into the wiki. The CLI
  keeps `save=false` as its default for exploratory use.
- **Citation extraction is daemon-side, link resolution is app-side** — the daemon
  parses `[[wiki-links]]` from the answer and returns structured citation objects
  (`wikiLink`, `title`). File-path resolution (matching link text to `.md` files)
  belongs in the iOS app, which already has local vault access and needs wiki-link
  navigation for its reader.
- **Runs inside `wiki serve`** — the HTTP server starts as a daemon thread
  alongside the existing watcher/ingest loop. One process to manage.
- **Always-on HTTP server** — `wiki serve` always starts the HTTP server.
  `/api/v1/health` works without a token. Authenticated endpoints check the
  token from config.toml on each request, so `wiki token generate` works at any
  time without restarting the daemon. `--no-api` disables the server entirely.
- **Unbounded concurrency** — each query runs `ops.query` in its own thread. No
  artificial cap; the provider subprocess is the bottleneck and the daemon is
  single-user.
- **stdlib `http.server`** — no new dependencies. The server is simple enough
  (three endpoints, JSON in/out) that a framework adds nothing.

## Config additions

In `~/.config/wiki/config.toml`:

```toml
api_token = "wk_a1b2c3..."     # generated via `wiki token generate`
api_port = 7880                 # default
api_bind = "0.0.0.0"            # default; listens on all interfaces
```

These become fields on `Config` (with defaults for port/bind, `None` for token).

The server always starts (unless `--no-api`). Without a token configured,
`/health` works but authenticated endpoints return 401. This lets the user start
the daemon first, generate a token later via `wiki token generate`, and have the
API become functional immediately — no daemon restart needed.

## Token management CLI

```
wiki token generate    — generate a random token, save to config.toml, print it
wiki token show        — print the current token (for pasting into the iOS app)
wiki token rotate      — generate a new token, replace the old one
```

Token format: `wk_` prefix + 32 hex chars (128-bit random). Stored in
`config.toml` under `api_token`.

This gives Hermes a scriptable flow: `wiki token show` to read the token and
relay it to the user/app. If none exists, `wiki token generate` first.

## Authentication

- Bearer token: `Authorization: Bearer <token>`.
- Token is read from `config.toml` on each request (no restart needed after
  `wiki token generate`). The file is small; per-request TOML parse is
  negligible for a single-user daemon.
- Constant-time comparison via `hmac.compare_digest`.
- `GET /api/v1/health` does **not** require auth (so the app can distinguish
  "can't reach daemon" from "wrong token").
- All other endpoints require auth. If no token is configured yet, authenticated
  endpoints return `401` with `"message": "API token not configured"`. If a
  token is configured but the request's token doesn't match, return `401` with
  `"message": "Invalid token"`.
- Never log the token value.

## Endpoints

### `GET /api/v1/health`

No auth required. Lets the app verify connection and show setup state.

Response:

```json
{
  "schemaVersion": 1,
  "status": "ok",
  "daemonVersion": "0.1.0",
  "vaultName": "Personal Wiki",
  "queryAvailable": true,
  "provider": "claude"
}
```

- `vaultName` = basename of the vault path (e.g. `"MyWiki"`).
- `queryAvailable` = true when the API is ready to accept queries (provider
  configured, auth not in a failing state).
- `provider` = resolved `Config.provider`.

### `POST /api/v1/query`

Auth required. Starts a query job.

Request:

```json
{
  "question": "What did I decide about the graph view?",
  "save": true
}
```

- `question`: required, non-empty string.
- `save`: optional, default `true`.

Response (HTTP 202):

```json
{
  "schemaVersion": 1,
  "jobId": "qry_20260623_153012_a1b2",
  "status": "queued"
}
```

The job immediately transitions to `running` when its worker thread starts
(effectively instant — there is no queue; each POST spawns a thread).

### `GET /api/v1/query/{jobId}`

Auth required. Polls job status.

While running:

```json
{
  "schemaVersion": 1,
  "jobId": "qry_20260623_153012_a1b2",
  "status": "running"
}
```

When done (HTTP 200):

```json
{
  "schemaVersion": 1,
  "jobId": "qry_20260623_153012_a1b2",
  "status": "done",
  "ok": true,
  "answerMarkdown": "# Short Answer\n\nThe graph view should...",
  "saved": true,
  "saveError": null,
  "citations": [
    {
      "wikiLink": "Graph View Notes",
      "title": "Graph View Notes"
    }
  ],
  "provider": "claude",
  "startedAt": "2026-06-23T15:30:12Z",
  "completedAt": "2026-06-23T15:30:37Z"
}
```

Response mapping from `QueryResult`:

| `QueryResult` field | API field | Notes |
|---|---|---|
| `ok` | `ok` | |
| `answer` | `answerMarkdown` | Exact provider stdout, no stripping |
| `saved` | `saved` | |
| `reason` (save fail) | `saveError` | Only when `ok=True` but `saved=False` |
| `kind` (provider fail) | error `details.kind` | When `ok=False` |

Save behavior:

- `save=false`: provider runs read-only. `saved` is always `false`, `saveError`
  is always `null`.
- `save=true`: uses existing save-query behavior. If the provider returns an
  answer but save verification fails, `ok=true`, `saved=false`,
  `saveError="no query page records this question"`. The answer is still useful.

On failure (HTTP 200 with `ok=false`, or 5xx for daemon bugs):

```json
{
  "schemaVersion": 1,
  "jobId": "qry_20260623_153012_a1b2",
  "status": "failed",
  "ok": false,
  "error": {
    "code": "provider_failed",
    "message": "Provider failed while generating the answer.",
    "retryable": true,
    "details": {
      "kind": "auth",
      "provider": "claude"
    }
  }
}
```

Unknown `jobId` returns HTTP 404.

## Citation extraction

Parse `[[wiki-links]]` from `answerMarkdown` using regex:

- `[[Page Name]]` → `{"wikiLink": "Page Name", "title": "Page Name"}`
- `[[Page Name|Display Alias]]` → `{"wikiLink": "Page Name", "title": "Display Alias"}`

No file-path resolution. The iOS app resolves links against its local vault copy.
Extraction failure (malformed links, regex edge cases) never fails the query —
`citations` returns an empty list.

Deduplication: if the same `wikiLink` appears multiple times in the answer,
return it once.

## Job storage

- In-memory `dict[str, QueryJob]`, keyed by `jobId`.
- `jobId` format: `qry_YYYYMMDD_HHMMSS_<4-hex>` (timestamp + random suffix).
- Jobs expire after 10 minutes. Expired jobs are cleaned on access (lazy eviction).
- Max 50 stored results; oldest evicted when the cap is hit.
- No persistence — daemon restart clears all jobs. The iOS app handles this
  gracefully (job-not-found → re-ask).

## Concurrency

- The HTTP server runs in a daemon thread (`threading.Thread(daemon=True)`).
- Each query POST spawns a worker thread that calls `ops.query()`.
- No cap on concurrent queries. Each `ops.query` call spawns an independent
  provider subprocess with no shared mutable state. Read-only queries are safe
  concurrent with daemon ingest (reads are lock-free). Save-queries write to
  `wiki/queries/` (append-only, distinct files per query).
- Job dict access is protected by a `threading.Lock`.

## Error model

All errors use JSON:

```json
{
  "schemaVersion": 1,
  "error": {
    "code": "query_failed",
    "message": "Provider failed while generating the answer.",
    "retryable": true,
    "details": {
      "kind": "auth",
      "provider": "claude"
    }
  }
}
```

Error codes:

| Code | HTTP | When |
|---|---|---|
| `unauthorized` | 401 | Missing/invalid bearer token |
| `bad_request` | 400 | Missing/empty question |
| `not_found` | 404 | Unknown jobId |
| `query_timeout` | 504 | Worker exceeded 120s |
| `provider_failed` | — | Returned inside job result (`ok=false`) |
| `internal_error` | 500 | Unexpected daemon exception |

Provider failures are **not** HTTP errors — they are returned inside the job
result (status `failed`, `ok=false`) so the app gets the structured error via
the normal polling flow.

## Integration with `wiki serve`

`daemon.serve()` gains an API server:

1. Read `api_port`, `api_bind` from config (token is read per-request).
2. If `--no-api`: skip entirely. Otherwise: start the HTTP server in a daemon
   thread.
3. Log the bind address on startup: `api: listening on 0.0.0.0:7880`.
4. The server holds a reference to `cfg` (for health/query) and the config file
   path (for per-request token reads).
5. On daemon shutdown (SIGINT/SIGTERM), the HTTP server stops with the process
   (daemon thread).

The existing watcher/ingest loop is untouched.

## New module: `src/wiki_daemon/api.py`

Single module containing:

- `QueryJob` dataclass (status, result, timestamps, thread reference)
- `JobStore` class (dict + lock + expiry/eviction)
- `extract_citations(markdown: str) -> list[dict]`
- `ApiHandler(http.server.BaseHTTPRequestHandler)` — routing, auth, request
  parsing, response writing
- `start_api_server(cfg, config_path, host, port) -> HTTPServer` — creates and
  starts the server in a daemon thread; returns the server instance for shutdown.
  `config_path` is used to read the token per-request.

## CLI changes

### `wiki serve` flags

- `--no-api`: disable the HTTP API server entirely. By default the server
  always starts (health works immediately; authenticated endpoints become
  available once a token is generated).
- `--api-port <port>`: override `api_port` (default 7880).
- `--api-bind <addr>`: override `api_bind` (default `0.0.0.0`).

### `wiki token` subcommand

- `wiki token generate` — generate and save a token, print it.
- `wiki token show` — print the current token.
- `wiki token rotate` — generate a new token, replace, print it.

All read/write `~/.config/wiki/config.toml`.

## Config changes

`Config` gains three optional fields:

```python
api_token: str | None = None
api_port: int = 7880
api_bind: str = "0.0.0.0"
```

`_config()` in `cli.py` reads these from the TOML config file and passes them
through. The `serve` command's `--api-port` / `--api-bind` flags override the
config values.

## Testing

### Unit tests (fake query function)

1. `GET /api/v1/health` returns daemon version, vault name, provider.
2. `POST /api/v1/query` without auth returns 401.
3. `POST /api/v1/query` with empty question returns 400.
4. Full job lifecycle: POST (202) → poll running → poll done. Fake query
   function returns a canned `QueryResult(ok=True, answer="...")`.
5. `save=false` → `saved` is false in result.
6. `save=true` with successful save → `saved=true`.
7. `save=true` with save verification failure → `ok=true`, `saved=false`,
   `saveError` set, answer still present.
8. Provider failure → job status `failed`, structured error with `kind`.
9. Citation extraction: `[[Page]]` and `[[Page|Alias]]` parsed correctly.
   Duplicates deduplicated. Malformed links don't crash.
10. Unknown jobId returns 404.
11. Job expiration: expired job returns 404.
12. `wiki token generate/show/rotate` CLI tests.
13. Concurrent queries: two POSTs, both complete independently.

### E2E tests (real LLM)

Marked `@pytest.mark.e2e` (skipped by default, run with `pytest -m e2e`):

1. Start the API server against a real test vault with a configured provider.
2. POST a simple question, poll until done, verify the answer is non-empty
   Markdown.
3. POST with `save=true`, verify `saved=true` and a `wiki/queries/*.md` page
   exists.

## Out of scope

- Streaming (SSE or WebSocket for incremental answer delivery).
- Page list/read endpoints (`GET /api/v1/pages` etc.) — the iOS app has local
  vault access.
- Multi-user auth, device-specific tokens, OAuth.
- Public internet deployment, HTTPS termination (Tailscale/WireGuard handles
  this).
- Remote ingest via the API.
- Structured citations from the LLM prompt (the LLM writes `[[wiki-links]]` in
  prose; the API extracts them).
