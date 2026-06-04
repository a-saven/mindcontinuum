"""End-to-end REST tests for the new features."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mindcontinuum.config import Settings
from mindcontinuum.server import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path, db_path=tmp_path / "adv.sqlite",
        host="127.0.0.1", port=3792, mcp_mount="/mcp",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_propose_promote_reject_via_rest(client: TestClient):
    r = client.post("/api/memory", json={"title": "to promote"})
    memory_id = r.json()["id"]
    r = client.post(f"/api/memory/{memory_id}/propose_stable", json={"reason": "verified"})
    assert r.status_code == 200
    assert r.json()["status"] == "proposed_stable"
    r = client.post(f"/api/memory/{memory_id}/promote_stable")
    assert r.status_code == 200
    assert r.json()["status"] == "stable"


def test_reject_proposal_via_rest(client: TestClient):
    r = client.post("/api/memory", json={"title": "to reject"})
    memory_id = r.json()["id"]
    client.post(f"/api/memory/{memory_id}/propose_stable", json={"reason": None})
    r = client.post(f"/api/memory/{memory_id}/reject_proposal")
    assert r.status_code == 200
    assert r.json()["status"] == "processed"


def test_links_and_contradictions_via_rest(client: TestClient):
    a = client.post("/api/memory", json={"title": "A"}).json()
    b = client.post("/api/memory", json={"title": "B"}).json()
    r = client.post(f"/api/memory/{a['id']}/links", json={"to_id": b["id"], "link_type": "related"})
    assert r.status_code == 200
    r = client.post(f"/api/memory/{a['id']}/contradicts", json={"existing_id": b["id"], "note": "n"})
    assert r.status_code == 200
    enriched = client.get(f"/api/memory/{a['id']}").json()
    types = {l["link_type"] for l in enriched["links"]}
    assert {"related", "contradicts"}.issubset(types)
    assert len(enriched["contradictions"]) == 1


def test_duplicates_and_merge_via_rest(client: TestClient):
    a = client.post("/api/memory", json={
        "title": "Cloudflare tunnel notes", "project": "Bridge", "tags": ["cloudflare"],
    }).json()
    b = client.post("/api/memory", json={
        "title": "Cloudflare tunnel notes extra", "project": "Bridge", "tags": ["cloudflare"],
    }).json()
    r = client.get(f"/api/memory/{a['id']}/duplicates")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()["candidates"]]
    assert b["id"] in ids
    r = client.post(f"/api/memory/{a['id']}/merge_into/{b['id']}")
    assert r.status_code == 200
    refreshed = client.get(f"/api/memory/{a['id']}").json()
    assert refreshed["status"] == "archived"


def test_supersede_via_rest(client: TestClient):
    old = client.post("/api/memory", json={"title": "old", "status": "stable"}).json()
    new = client.post("/api/memory", json={"title": "new"}).json()
    r = client.post(f"/api/memory/{old['id']}/supersede_by/{new['id']}")
    assert r.status_code == 200
    refreshed = client.get(f"/api/memory/{old['id']}").json()
    assert refreshed["status"] == "stale"


def test_import_json_via_rest(client: TestClient):
    client.post("/api/memory", json={"title": "export-target"})
    export = client.get("/api/export/json").json()
    r = client.post("/api/import/json", json=export)
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] >= 0
    assert "errors" in body


def test_import_markdown_via_rest(client: TestClient):
    md = "## Imported via REST\n\n> Type: idea\n> Tags: rest, import\n\nbody here"
    r = client.post(
        "/api/import/markdown",
        content=md.encode("utf-8"),
        headers={"Content-Type": "text/markdown"},
    )
    assert r.status_code == 200
    listing = client.get("/api/memory").json()
    titles = [i["title"] for i in listing["items"]]
    assert "Imported via REST" in titles


def test_project_pack_endpoint_json_and_md(client: TestClient):
    client.post("/api/decisions", json={
        "project": "RestPack", "decision": "Use Cloudflare", "rationale": "works on Windows",
    })
    client.post("/api/tasks", json={
        "project": "RestPack", "title": "Ship tunnel docs", "priority": "high",
    })
    r = client.get("/api/projects/restpack/pack")
    assert r.status_code == 200
    pack = r.json()
    assert pack["project"]["slug"] == "restpack"
    assert pack["tasks"]
    r = client.get("/api/projects/restpack/pack.md")
    assert r.status_code == 200
    assert "# Project: RestPack" in r.text


def test_embeddings_status_endpoint(client: TestClient):
    r = client.get("/api/embeddings/status")
    assert r.status_code == 200
    assert "available" in r.json()


def test_search_modes_accepted(client: TestClient):
    client.post("/api/memory", json={"title": "MindContinuum tunnel test works"})
    for mode in ("keyword", "auto"):
        r = client.get(f"/api/search?q=tunnel&mode={mode}")
        assert r.status_code == 200
        assert r.json()["mode"] == mode


def test_search_invalid_mode_400(client: TestClient):
    r = client.get("/api/search?q=x&mode=nope")
    assert r.status_code == 400
    assert "mode" in r.json()["detail"].lower()


def test_status_endpoint_exposes_new_fields(client: TestClient):
    s = client.get("/api/status").json()
    assert "personal_mcp_enabled" in s
    assert "embeddings" in s
    assert "namespace" in s["enums"]
    assert "link_type" in s["enums"]
