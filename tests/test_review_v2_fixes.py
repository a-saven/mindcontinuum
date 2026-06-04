"""Regression tests for the v0.2.2 review fixes.

Covers:
  * Thread safety: concurrent saves don't corrupt state.
  * search_memory: resolved_mode surfaces the actually-used mode.
  * Pydantic max_length on inputs.
  * Import endpoint Content-Length rejection.
  * Markdown import: H1+body preamble preserved, H1-only preamble dropped.
"""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mindcontinuum.api import MAX_IMPORT_BYTES
from mindcontinuum.config import Settings
from mindcontinuum.core import MAX_TITLE, MemoryStore
from mindcontinuum.server import create_app


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_save_memory_writes_are_all_persisted(tmp_path: Path):
    """A pool of worker threads hammering save_memory should produce a
    consistent end state and not interleave transactions on a single
    connection. With the per-thread-connection refactor this is correct;
    with the previous single-connection design it would non-deterministically
    raise OperationalError or lose rows."""
    store = MemoryStore(db_path=tmp_path / "hammer.sqlite", enable_embeddings=False)
    try:
        N = 60
        WORKERS = 8

        def worker(i: int) -> int:
            return store.save_memory(title=f"row-{i:03d}", body=f"body-{i}")["id"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
            ids = list(ex.map(worker, range(N)))

        assert len(set(ids)) == N, "rowids must be unique"
        # Each save_memory should have written exactly one event_log row.
        items = store.list_recent(limit=N + 10)
        assert len(items) == N
        events = store.list_events(limit=N * 2 + 10)
        create_events = [e for e in events if e["action"] == "memory.create"]
        assert len(create_events) == N
    finally:
        store.close()


def test_concurrent_writes_and_reads_interleave_safely(tmp_path: Path):
    """Mix of writers and readers shouldn't deadlock or produce stale views."""
    store = MemoryStore(db_path=tmp_path / "rw.sqlite", enable_embeddings=False)
    try:
        N = 40

        def writer(i: int) -> int:
            return store.save_memory(title=f"w-{i}", body="x")["id"]

        def reader(_i: int) -> int:
            return len(store.list_recent(limit=200))

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            tasks = []
            for i in range(N):
                tasks.append(ex.submit(writer, i))
                tasks.append(ex.submit(reader, i))
            for t in tasks:
                t.result()  # raises if any failed

        items = store.list_recent(limit=200)
        assert len(items) == N
    finally:
        store.close()


def test_close_clears_all_thread_local_connections(tmp_path: Path):
    """After close(), reopening through the property should produce a
    fresh, working connection rather than a closed one."""
    store = MemoryStore(db_path=tmp_path / "close.sqlite", enable_embeddings=False)
    try:
        store.save_memory(title="before-close")
        store.close()
        # close() is idempotent
        store.close()
        # Subsequent calls reopen transparently (a fresh connection is
        # created on the next access via the property).
        out = store.save_memory(title="after-close")
        assert out["id"] >= 1
    finally:
        store.close()


# ---------------------------------------------------------------------------
# search_memory: resolved_mode
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path, db_path=tmp_path / "rv2.sqlite",
        host="127.0.0.1", port=3793, mcp_mount="/mcp",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_search_auto_resolves_to_concrete_mode(client: TestClient):
    client.post("/api/memory", json={"title": "MindContinuum tunnel"})
    r = client.get("/api/search?q=tunnel&mode=auto")
    body = r.json()
    assert body["mode"] == "auto"
    assert body["resolved_mode"] in ("keyword", "hybrid"), body


def test_search_keyword_passes_through(client: TestClient):
    client.post("/api/memory", json={"title": "alpha keyword test"})
    r = client.get("/api/search?q=alpha&mode=keyword")
    body = r.json()
    assert body["mode"] == "keyword"
    assert body["resolved_mode"] == "keyword"


def test_search_invalid_mode_returns_400(client: TestClient):
    """Tightened from the previous accept-500-or-400 test now that the
    api layer reliably maps ValueError to 400."""
    r = client.get("/api/search?q=x&mode=bogus")
    assert r.status_code == 400
    assert "mode" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Pydantic max_length
# ---------------------------------------------------------------------------


def test_oversized_title_rejected_at_http_layer(client: TestClient):
    huge_title = "x" * (MAX_TITLE + 10)
    r = client.post("/api/memory", json={"title": huge_title})
    assert r.status_code == 422, r.text
    # Should fail fast at validation, never reaching core.
    detail = r.json()["detail"]
    assert any("title" in str(err.get("loc", "")) for err in detail)


def test_oversized_body_rejected_at_http_layer(client: TestClient):
    # Use a body just over MAX_BODY so test stays fast.
    from mindcontinuum.core import MAX_BODY
    r = client.post("/api/memory", json={"title": "ok", "body": "y" * (MAX_BODY + 1)})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Import endpoint size cap
# ---------------------------------------------------------------------------


def test_import_json_rejects_oversize_via_content_length(client: TestClient):
    # Spoof Content-Length without sending the bytes.
    r = client.post(
        "/api/import/json",
        content=b"{}",
        headers={"Content-Type": "application/json",
                 "content-length": str(MAX_IMPORT_BYTES + 1)},
    )
    assert r.status_code == 413, r.text
    assert "limit" in r.json()["detail"].lower()


def test_import_markdown_rejects_oversize_via_content_length(client: TestClient):
    r = client.post(
        "/api/import/markdown",
        content=b"## x",
        headers={"Content-Type": "text/markdown",
                 "content-length": str(MAX_IMPORT_BYTES + 1)},
    )
    assert r.status_code == 413


def test_import_normal_size_works(client: TestClient):
    md = "## ok\n\nbody"
    r = client.post(
        "/api/import/markdown",
        content=md.encode("utf-8"),
        headers={"Content-Type": "text/markdown"},
    )
    assert r.status_code == 200
    assert r.json()["imported"] == 1


# ---------------------------------------------------------------------------
# Markdown import: H1+body preamble
# ---------------------------------------------------------------------------


def test_markdown_import_keeps_h1_preamble_with_body(store: MemoryStore):
    md = """\
# Top topic

Standalone body that lives under H1.

## Sub item

Sub body.
"""
    result = store.import_markdown(md)
    assert result["imported"] == 2, result
    titles = {i["title"] for i in store.list_recent(limit=10)}
    assert "Top topic" in titles
    assert "Sub item" in titles


def test_markdown_import_drops_h1_only_preamble(store: MemoryStore):
    md = """\
# top heading on its own

## Real item

Body
"""
    result = store.import_markdown(md)
    assert result["imported"] == 1, result
    titles = {i["title"] for i in store.list_recent(limit=10)}
    assert "Real item" in titles
    assert "top heading on its own" not in titles


def test_markdown_import_no_h1_just_h2s(store: MemoryStore):
    md = "## first\n\nbody one\n\n## second\n\nbody two\n"
    result = store.import_markdown(md)
    assert result["imported"] == 2
