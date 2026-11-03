import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import SAMPLE


@pytest.fixture(scope="module")
def client():
    from app.api import app

    with TestClient(app) as c:
        yield c


def test_query_before_ingest_is_409(client):
    assert client.post("/api/query", json={"question": "What is CNS 01h?"}).status_code == 409


def test_full_flow(client):
    for f in sorted(SAMPLE.glob("*.md")):
        with f.open("rb") as fh:
            r = client.post("/api/documents", files={"file": (f.name, fh, "text/markdown")})
        assert r.status_code == 200, r.text
        assert r.json()["chunks"] > 0

    docs = client.get("/api/documents").json()
    assert {d["doc_id"] for d in docs} >= {"nvme-admin-commands", "nvme-log-pages"}

    health = client.get("/api/health").json()
    assert health["llm"] == "extractive" and health["chunks"] > 50

    r = client.post("/api/query", json={"question": "What is the opcode of the Identify command?"})
    body = r.json()
    assert r.status_code == 200 and body["citations"] and "06h" in body["answer"]

    with client.stream("POST", "/api/query/stream", json={"question": "Which CNS returns the Identify Controller data structure?"}) as s:
        events = [json.loads(line[6:]) for line in s.iter_lines() if line.startswith("data: ")]
    assert events[0]["type"] == "contexts" and events[-1]["type"] == "done"

    r = client.post("/api/testcases", json={"feature": "Firmware Commit", "count": 2})
    assert r.status_code == 200 and r.json()["test_cases"]

    assert client.delete("/api/documents/nvme-log-pages").status_code == 200
    assert client.delete("/api/documents/nvme-log-pages").status_code == 404


def test_rejects_bad_upload(client):
    r = client.post("/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415


def test_validation(client):
    assert client.post("/api/query", json={"question": "a"}).status_code == 422
