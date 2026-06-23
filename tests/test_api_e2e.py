"""E2E tests for the HTTP API — calls a real LLM provider.

Run with: .venv/bin/pytest -m e2e --no-header -v
Requires: a real vault at WIKI_VAULT (or --vault), a working provider CLI.
"""
import json
import os
import time
import urllib.request

import pytest

from wiki_daemon.api import start_api_server
from wiki_daemon.config import Config
from wiki_daemon.scaffold import init_vault

pytestmark = pytest.mark.e2e

TOKEN = "wk_e2etest"


def _vault_path():
    return os.environ.get("WIKI_VAULT")


@pytest.fixture
def e2e_server():
    vault = _vault_path()
    if not vault:
        pytest.skip("WIKI_VAULT not set")
    cfg = Config(vault=vault)
    if not (cfg.vault / "wiki" / "index.md").exists():
        pytest.skip("vault not initialized")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path
        config_path = Path(td) / "config.toml"
        config_path.write_text(f'api_token = "{TOKEN}"\n', encoding="utf-8")
        server = start_api_server(cfg, config_path=config_path,
                                  host="127.0.0.1", port=0)
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        yield base, server, cfg
        server.shutdown()


def _post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    return json.loads(urllib.request.urlopen(req).read())


def _get(url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    return json.loads(urllib.request.urlopen(req).read())


def _poll(base, job_id, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = _get(f"{base}/api/v1/query/{job_id}")
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(2)
    raise TimeoutError(f"job {job_id} did not complete in {timeout}s")


def test_e2e_health(e2e_server):
    base, _, _ = e2e_server
    req = urllib.request.Request(f"{base}/api/v1/health")
    data = json.loads(urllib.request.urlopen(req).read())
    assert data["status"] == "ok"
    assert data["queryAvailable"] is True


def test_e2e_query_readonly(e2e_server):
    base, _, _ = e2e_server
    resp = _post(f"{base}/api/v1/query",
                 {"question": "What topics does this wiki cover?", "save": False})
    job_id = resp["jobId"]
    data = _poll(base, job_id)
    assert data["status"] == "done"
    assert data["ok"] is True
    assert len(data["answerMarkdown"]) > 0
    assert data["saved"] is False
