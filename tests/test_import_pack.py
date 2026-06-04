"""Tests for JSON/Markdown import and project context pack."""

from __future__ import annotations

import json

from mindcontinuum.core import MemoryStore


def test_import_json_roundtrips(store: MemoryStore):
    store.save_memory(title="alpha", body="body a", project="ProjOne", tags=["one"])
    store.record_decision(project="ProjOne", decision="Pick SQLite", rationale="ergonomic")
    store.record_task(project="ProjOne", title="document mcp endpoint")
    payload = store.export_all()

    # New store, import the same payload
    other = MemoryStore(db_path=store.db_path.parent / "import-target.sqlite",
                        enable_embeddings=False)
    try:
        result = other.import_json(payload)
        assert result["imported"] >= 1
        assert result["errors"] == 0
        items = other.list_recent(limit=100)
        titles = [i["title"] for i in items]
        assert "alpha" in titles
    finally:
        other.close()


def test_import_json_idempotent(store: MemoryStore):
    store.save_memory(title="dup-check", body="b")
    payload = store.export_all()
    first = store.import_json(payload)
    second = store.import_json(payload)
    # everything skipped on second pass
    assert second["imported"] == 0
    assert second["skipped"] >= first["imported"]


def test_import_json_rejects_wrong_version(store: MemoryStore):
    import pytest
    with pytest.raises(ValueError):
        store.import_json({"version": 99})


def test_import_markdown_basic(store: MemoryStore):
    md = """
# top heading is ignored as title fallback only

## First imported item
> Type: research
> Project: ImportTest
> Tags: alpha, beta
> Summary line goes here

Body content for item one.

## Second imported item

Just a body and a title.
""".strip()
    result = store.import_markdown(md)
    assert result["imported"] == 2
    items = store.list_recent(limit=100)
    titles = [i["title"] for i in items]
    assert "First imported item" in titles
    assert "Second imported item" in titles
    first = next(i for i in items if i["title"] == "First imported item")
    assert first["type"] == "research"
    assert first["summary"] == "Summary line goes here"
    assert first["project_slug"] == "importtest"
    assert "alpha" in (first["tags"] or [])


def test_import_markdown_dedup_within_day(store: MemoryStore):
    md = "## Same title\n\nbody"
    store.import_markdown(md)
    second = store.import_markdown(md)
    assert second["skipped"] >= 1


def test_get_project_context_pack_shape(store: MemoryStore):
    store.record_decision(project="PackTest", decision="Use SQLite", rationale="local-first",
                          status="accepted", actor="ui")
    store.record_task(project="PackTest", title="Ship pack endpoint", priority="high")
    store.save_memory(title="Pack pinned fact", body="b", project="PackTest", status="pinned", actor="ui")
    store.save_memory(title="Pack risk", body="might break", type="risk", project="PackTest",
                      importance="high")
    pack = store.get_project_context_pack("PackTest")
    assert pack["project"]["slug"] == "packtest"
    assert pack["decisions"]
    assert pack["tasks"]
    assert pack["stable_items"]
    assert pack["risks"]
    assert "generated_at" in pack


def test_render_project_pack_markdown_includes_sections(store: MemoryStore):
    store.record_decision(project="PackTest2", decision="Local-first MCP",
                          status="accepted", actor="ui")
    store.record_task(project="PackTest2", title="Wire tunnel")
    pack = store.get_project_context_pack("PackTest2")
    md = store.render_project_pack_markdown(pack)
    assert "# Project: PackTest2" in md
    assert "## Accepted Decisions" in md
    assert "## Open Tasks" in md
    assert "## Active Risks" in md
    assert "## Stable Context" in md
    assert "Local-first MCP" in md


def test_get_project_context_pack_unknown_raises(store: MemoryStore):
    import pytest
    with pytest.raises(LookupError):
        store.get_project_context_pack("nope")
