"""Tests for the FastAPI REST surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mindcontinuum.config import Settings
from mindcontinuum.server import create_app


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_status_includes_mcp_url(client: TestClient, settings: Settings):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["server_name"] == "MindContinuum"
    assert data["mcp_url_local"].endswith(settings.mcp_mount)
    assert "type" in data["enums"]


def test_index_renders_html(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "MindContinuum" in r.text


def test_create_search_get_archive_cycle(client: TestClient):
    payload = {
        "title": "ChatGPT bridge online",
        "body": "Saved from a Streamable HTTP MCP call.",
        "type": "research",
        "project": "Bridge Spike",
        "tags": ["spike", "tunnel"],
        "importance": "high",
    }
    r = client.post("/api/memory", json=payload)
    assert r.status_code == 200
    created = r.json()
    memory_id = created["id"]
    assert created["project_slug"] == "bridge-spike"

    r = client.get(f"/api/memory/{memory_id}")
    assert r.status_code == 200

    r = client.get("/api/search", params={"q": "bridge"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert any(item["id"] == memory_id for item in body["results"])

    r = client.post(f"/api/memory/{memory_id}/archive")
    assert r.status_code == 200
    assert r.json()["status"] == "archived"


def test_validation_rejects_empty_title(client: TestClient):
    r = client.post("/api/memory", json={"title": "", "body": "x"})
    assert r.status_code == 422 or r.status_code == 400


def test_decisions_and_tasks_endpoints(client: TestClient):
    r = client.post("/api/decisions", json={
        "project": "Bridge Spike",
        "decision": "Use Cloudflare tunnel for Windows demo",
        "rationale": "No account-bound tunnel-client; well-tested on Windows.",
    })
    assert r.status_code == 200
    r = client.post("/api/tasks", json={
        "project": "Bridge Spike",
        "title": "Document Windows firewall step",
        "next_action": "Add screenshot to docs/WINDOWS_SETUP.md",
        "priority": "high",
    })
    assert r.status_code == 200
    r = client.get("/api/decisions")
    assert any(d["decision"].startswith("Use Cloudflare") for d in r.json()["decisions"])
    r = client.get("/api/tasks")
    assert any(t["title"].startswith("Document Windows") for t in r.json()["tasks"])


def test_export_json_and_markdown(client: TestClient):
    client.post("/api/memory", json={"title": "Export me", "body": "content"})
    r = client.get("/api/export/json")
    assert r.status_code == 200
    assert "memory_items" in r.text
    r = client.get("/api/export/markdown")
    assert r.status_code == 200
    assert "# MindContinuum export" in r.text


def test_mcp_endpoint_is_mounted(client: TestClient):
    """The MCP Streamable HTTP endpoint should be served at /mcp.

    A bare GET without the MCP handshake headers will be rejected by the
    server, but it should not 404 — anything other than 404/405 confirms
    the mount is wired.
    """
    r = client.get("/mcp")
    assert r.status_code not in (404, 405), f"MCP not mounted: {r.status_code} {r.text[:200]}"


def test_events_log_records_writes(client: TestClient):
    client.post("/api/memory", json={"title": "Log me", "body": "x"})
    r = client.get("/api/events?limit=10")
    actions = [e["action"] for e in r.json()["events"]]
    assert "memory.create" in actions
