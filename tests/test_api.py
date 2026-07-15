import time
from wiki_daemon.api import extract_citations, JobStore, QueryJob
from wiki_daemon.ops import QueryResult


def test_extract_simple_link():
    result = extract_citations("See [[Graph View Notes]] for details.")
    assert result == [{"wikiLink": "Graph View Notes", "title": "Graph View Notes"}]


def test_extract_aliased_link():
    result = extract_citations("See [[Graph View Notes|the notes]] for details.")
    assert result == [{"wikiLink": "Graph View Notes", "title": "the notes"}]


def test_extract_deduplicates():
    result = extract_citations("[[A]] and [[B]] and [[A]] again.")
    assert len(result) == 2
    assert result[0]["wikiLink"] == "A"
    assert result[1]["wikiLink"] == "B"


def test_extract_empty_on_no_links():
    assert extract_citations("No links here.") == []


def test_extract_empty_string():
    assert extract_citations("") == []


def test_extract_malformed_links_ignored():
    result = extract_citations("[[]] and [[ ]] and [[|alias]]")
    assert result == []


def test_extract_multiple_links():
    md = "Check [[Alpha]], [[Beta|B]], and [[Gamma]]."
    result = extract_citations(md)
    assert len(result) == 3
    assert result[0] == {"wikiLink": "Alpha", "title": "Alpha"}
    assert result[1] == {"wikiLink": "Beta", "title": "B"}
    assert result[2] == {"wikiLink": "Gamma", "title": "Gamma"}


def test_jobstore_create_and_get():
    store = JobStore()
    job_id = store.create("What is X?", save=True)
    assert job_id.startswith("qry_")
    job = store.get(job_id)
    assert isinstance(job, QueryJob)
    assert job.question == "What is X?"
    assert job.save is True
    assert job.status == "running"


def test_jobstore_get_unknown_returns_none():
    store = JobStore()
    assert store.get("qry_nonexistent") is None


def test_jobstore_complete_marks_done():
    store = JobStore()
    job_id = store.create("Q?", save=False)
    store.complete(job_id, QueryResult(ok=True, answer="A"))
    job = store.get(job_id)
    assert job.status == "done"
    assert job.result.answer == "A"


def test_jobstore_fail_marks_failed():
    store = JobStore()
    job_id = store.create("Q?", save=False)
    store.complete(job_id, QueryResult(ok=False, kind="auth", reason="401"))
    job = store.get(job_id)
    assert job.status == "failed"


def test_jobstore_expiry():
    store = JobStore(expiry_seconds=0.01)
    job_id = store.create("Q?", save=False)
    time.sleep(0.02)
    assert store.get(job_id) is None


def test_jobstore_evicts_oldest_when_full():
    store = JobStore(max_jobs=2)
    id1 = store.create("Q1", save=False)
    id2 = store.create("Q2", save=False)
    id3 = store.create("Q3", save=False)
    assert store.get(id1) is None  # evicted
    assert store.get(id2) is not None
    assert store.get(id3) is not None


# ===== HTTP server tests =====

import json
import urllib.request
import urllib.error
from wiki_daemon.api import start_api_server
from wiki_daemon.config import Config
from wiki_daemon.scaffold import init_vault


TOKEN = "wk_testtoken1234"


def _api(tmp_path, *, token=TOKEN, query_fn=None):
    """Start a test API server on a random port, return (base_url, server, cfg)."""
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    # Write a config with the token
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'api_token = "{token}"\n', encoding="utf-8")
    server = start_api_server(cfg, config_path=config_path,
                              host="127.0.0.1", port=0,
                              query_fn=query_fn)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    return base, server, cfg


def _get(url, token=TOKEN):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req)


def _post(url, body, token=TOKEN):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(req)


def _post_err(url, body, token=TOKEN):
    """POST expecting an HTTP error; return (status, parsed JSON body)."""
    try:
        _post(url, body, token=token)
        raise AssertionError("expected HTTP error")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# --- health ---

def test_health_no_auth_required(tmp_path):
    base, server, cfg = _api(tmp_path)
    try:
        resp = _get(f"{base}/api/v1/health", token=None)
        data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert data["schemaVersion"] == 1
        assert data["daemonVersion"] == "0.1.0"
        assert data["provider"] == "claude"
        assert data["queryAvailable"] is True
        assert "vaultName" in data
    finally:
        server.shutdown()


# --- auth ---

def test_query_post_no_token_returns_401(tmp_path):
    base, server, _ = _api(tmp_path)
    try:
        code, data = _post_err(f"{base}/api/v1/query",
                               {"question": "Q?"}, token=None)
        assert code == 401
        assert data["error"]["code"] == "unauthorized"
    finally:
        server.shutdown()


def test_query_post_wrong_token_returns_401(tmp_path):
    base, server, _ = _api(tmp_path)
    try:
        code, data = _post_err(f"{base}/api/v1/query",
                               {"question": "Q?"}, token="wk_wrong")
        assert code == 401
    finally:
        server.shutdown()


# --- bad request ---

def test_query_post_empty_question_returns_400(tmp_path):
    base, server, _ = _api(tmp_path)
    try:
        code, data = _post_err(f"{base}/api/v1/query", {"question": ""})
        assert code == 400
        assert data["error"]["code"] == "bad_request"
    finally:
        server.shutdown()


def test_query_post_missing_question_returns_400(tmp_path):
    base, server, _ = _api(tmp_path)
    try:
        code, data = _post_err(f"{base}/api/v1/query", {})
        assert code == 400
    finally:
        server.shutdown()


# --- full job lifecycle ---

def test_query_lifecycle_post_poll_done(tmp_path):
    from wiki_daemon.ops import QueryResult

    def fake_query(cfg, question, *, save):
        return QueryResult(ok=True, answer="The answer about [[Topic A]].",
                           saved=save)

    base, server, _ = _api(tmp_path, query_fn=fake_query)
    try:
        # POST
        resp = _post(f"{base}/api/v1/query",
                     {"question": "What about topic A?", "save": False})
        assert resp.status == 202
        data = json.loads(resp.read())
        job_id = data["jobId"]
        assert data["status"] == "queued"

        # Poll until done (max 5s)
        import time
        for _ in range(50):
            resp = _get(f"{base}/api/v1/query/{job_id}")
            data = json.loads(resp.read())
            if data["status"] == "done":
                break
            time.sleep(0.1)

        assert data["status"] == "done"
        assert data["ok"] is True
        assert data["answerMarkdown"] == "The answer about [[Topic A]]."
        assert data["saved"] is False
        assert data["saveError"] is None
        assert len(data["citations"]) == 1
        assert data["citations"][0]["wikiLink"] == "Topic A"
        assert "startedAt" in data
        assert "completedAt" in data
        assert data["provider"] == "claude"
    finally:
        server.shutdown()


def test_query_save_true_default(tmp_path):
    from wiki_daemon.ops import QueryResult

    seen = {}

    def fake_query(cfg, question, *, save):
        seen["save"] = save
        return QueryResult(ok=True, answer="A", saved=True)

    base, server, _ = _api(tmp_path, query_fn=fake_query)
    try:
        resp = _post(f"{base}/api/v1/query", {"question": "Q?"})
        data = json.loads(resp.read())
        job_id = data["jobId"]
        import time
        for _ in range(50):
            resp = _get(f"{base}/api/v1/query/{job_id}")
            data = json.loads(resp.read())
            if data["status"] == "done":
                break
            time.sleep(0.1)
        assert seen["save"] is True  # default is save=True for API
    finally:
        server.shutdown()


def test_query_save_false_explicit(tmp_path):
    from wiki_daemon.ops import QueryResult

    seen = {}

    def fake_query(cfg, question, *, save):
        seen["save"] = save
        return QueryResult(ok=True, answer="A", saved=False)

    base, server, _ = _api(tmp_path, query_fn=fake_query)
    try:
        _post(f"{base}/api/v1/query", {"question": "Q?", "save": False})
        import time
        time.sleep(0.3)
        assert seen["save"] is False
    finally:
        server.shutdown()


def test_query_save_verification_failure(tmp_path):
    from wiki_daemon.ops import QueryResult

    def fake_query(cfg, question, *, save):
        return QueryResult(ok=True, answer="A", saved=False,
                           reason="no query page records this question")

    base, server, _ = _api(tmp_path, query_fn=fake_query)
    try:
        resp = _post(f"{base}/api/v1/query", {"question": "Q?", "save": True})
        data = json.loads(resp.read())
        job_id = data["jobId"]
        import time
        for _ in range(50):
            resp = _get(f"{base}/api/v1/query/{job_id}")
            data = json.loads(resp.read())
            if data["status"] == "done":
                break
            time.sleep(0.1)
        assert data["ok"] is True
        assert data["saved"] is False
        assert data["saveError"] == "no query page records this question"
        assert data["answerMarkdown"] == "A"
    finally:
        server.shutdown()


# --- provider failure ---

def test_query_provider_failure(tmp_path):
    from wiki_daemon.ops import QueryResult

    def fake_query(cfg, question, *, save):
        return QueryResult(ok=False, kind="auth", reason="claude failed: 401")

    base, server, _ = _api(tmp_path, query_fn=fake_query)
    try:
        resp = _post(f"{base}/api/v1/query", {"question": "Q?"})
        data = json.loads(resp.read())
        job_id = data["jobId"]
        import time
        for _ in range(50):
            resp = _get(f"{base}/api/v1/query/{job_id}")
            data = json.loads(resp.read())
            if data["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert data["status"] == "failed"
        assert data["ok"] is False
        assert data["error"]["code"] == "provider_failed"
        assert data["error"]["details"]["kind"] == "auth"
        assert data["error"]["retryable"] is True
    finally:
        server.shutdown()


# --- running deadline (wedged worker) ---

def test_query_running_past_deadline_returns_failed_timeout(tmp_path):
    """A job stuck 'running' past the deadline is reported failed/timeout."""
    import threading as _t
    from wiki_daemon.ops import QueryResult

    gate = _t.Event()

    def blocking_query(cfg, question, *, save):
        gate.wait(10)
        return QueryResult(ok=True, answer="late answer")

    base, server, _ = _api(tmp_path, query_fn=blocking_query)
    try:
        resp = _post(f"{base}/api/v1/query", {"question": "Q?", "save": False})
        job_id = json.loads(resp.read())["jobId"]

        # While it is still running, it reports "running".
        data = json.loads(_get(f"{base}/api/v1/query/{job_id}").read())
        assert data["status"] == "running"

        # Make it appear to have been running past the deadline by winding
        # the monotonic start time backwards (older than the derived deadline,
        # but still younger than the store expiry so it is not evicted → 404).
        # Default: deadline=720, expiry=900, so 800 sits between them.
        store = server.RequestHandlerClass.job_store
        job = store.get(job_id)
        job.created -= 800.0

        data = json.loads(_get(f"{base}/api/v1/query/{job_id}").read())
        assert data["status"] == "failed"
        assert data["ok"] is False
        assert data["error"]["code"] == "provider_failed"
        assert data["error"]["details"]["kind"] == "timeout"
        assert data["error"]["details"]["provider"] == "claude"
        assert data["error"]["retryable"] is True
        assert "time" in data["error"]["message"].lower()
    finally:
        gate.set()
        server.shutdown()


def test_deadline_and_expiry_derived_from_query_timeout(tmp_path):
    """Deadline and store expiry derive from cfg.query_timeout and stay
    strictly ordered: timeout < deadline < expiry."""
    base, server, cfg = _api(tmp_path)
    try:
        handler = server.RequestHandlerClass
        deadline = handler.running_deadline_seconds
        expiry = handler.job_store._expiry
        assert cfg.query_timeout == 600
        assert deadline == cfg.query_timeout + 120
        assert expiry == cfg.query_timeout + 300
        assert cfg.query_timeout < deadline < expiry
    finally:
        server.shutdown()


# --- not found ---

def test_query_get_unknown_job_returns_404(tmp_path):
    base, server, _ = _api(tmp_path)
    try:
        try:
            _get(f"{base}/api/v1/query/qry_nonexistent")
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
            data = json.loads(e.read())
            assert data["error"]["code"] == "not_found"
    finally:
        server.shutdown()


# --- concurrent queries ---

def test_concurrent_queries(tmp_path):
    import time
    from wiki_daemon.ops import QueryResult

    def slow_query(cfg, question, *, save):
        time.sleep(0.2)
        return QueryResult(ok=True, answer=f"Answer to: {question}")

    base, server, _ = _api(tmp_path, query_fn=slow_query)
    try:
        resp1 = _post(f"{base}/api/v1/query", {"question": "Q1?", "save": False})
        resp2 = _post(f"{base}/api/v1/query", {"question": "Q2?", "save": False})
        id1 = json.loads(resp1.read())["jobId"]
        id2 = json.loads(resp2.read())["jobId"]
        assert id1 != id2
        for _ in range(50):
            d1 = json.loads(_get(f"{base}/api/v1/query/{id1}").read())
            d2 = json.loads(_get(f"{base}/api/v1/query/{id2}").read())
            if d1["status"] == "done" and d2["status"] == "done":
                break
            time.sleep(0.1)
        assert "Q1" in d1["answerMarkdown"]
        assert "Q2" in d2["answerMarkdown"]
    finally:
        server.shutdown()


# --- token not configured ---

def test_query_no_token_configured_returns_401(tmp_path):
    cfg = Config(vault=tmp_path / "v", state_root=tmp_path / "s")
    init_vault(cfg)
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_vault = "/v"\n', encoding="utf-8")
    server = start_api_server(cfg, config_path=config_path,
                              host="127.0.0.1", port=0)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        code, data = _post_err(f"{base}/api/v1/query",
                               {"question": "Q?"}, token="wk_anything")
        assert code == 401
        assert "not configured" in data["error"]["message"].lower()
    finally:
        server.shutdown()
