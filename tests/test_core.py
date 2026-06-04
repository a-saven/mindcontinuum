"""Tests for the safe core memory operations.

These MUST pass before any tunnel/connector testing per the DoD in the spec.
"""

from __future__ import annotations

import pytest

from mindcontinuum.core import MemoryStore


def test_save_memory_minimal_creates_inbox_item(store: MemoryStore):
    item = store.save_memory(title="Hello", body="World")
    assert item["id"] >= 1
    assert item["title"] == "Hello"
    assert item["body"] == "World"
    assert item["type"] == "note"
    assert item["status"] == "inbox"
    assert item["importance"] == "medium"
    assert item["tags"] == []


def test_save_memory_records_event(store: MemoryStore):
    item = store.save_memory(title="Event check", body="x")
    events = store.list_events()
    actions = [e["action"] for e in events]
    assert "memory.create" in actions
    create_evt = next(e for e in events if e["action"] == "memory.create")
    assert create_evt["target_id"] == item["id"]


def test_save_memory_with_project_creates_and_links(store: MemoryStore):
    item = store.save_memory(title="P1", body="b", project="ACME Launch")
    assert item["project_slug"] == "acme-launch"
    assert item["project_name"] == "ACME Launch"
    again = store.save_memory(title="P2", project="ACME Launch")
    assert again["project_id"] == item["project_id"]


def test_save_memory_normalizes_tags(store: MemoryStore):
    item = store.save_memory(title="T", tags=["Alpha", "alpha", "  ", "Beta"])
    assert item["tags"] == ["alpha", "beta"]


def test_save_memory_rejects_bad_enums(store: MemoryStore):
    with pytest.raises(ValueError):
        store.save_memory(title="x", type="bogus")
    with pytest.raises(ValueError):
        store.save_memory(title="x", importance="extreme")
    with pytest.raises(ValueError):
        store.save_memory(title="x", status="brand-new")
    with pytest.raises(ValueError):
        store.save_memory(title="")


def test_get_memory_returns_full_item(store: MemoryStore):
    saved = store.save_memory(title="g1", body="b1", project="Proj")
    fetched = store.get_memory(saved["id"])
    assert fetched is not None
    assert fetched["id"] == saved["id"]
    assert fetched["project_slug"] == "proj"


def test_get_memory_unknown_returns_none(store: MemoryStore):
    assert store.get_memory(9999) is None


def test_list_recent_orders_by_created_desc(store: MemoryStore):
    a = store.save_memory(title="a")
    b = store.save_memory(title="b")
    c = store.save_memory(title="c")
    items = store.list_recent(limit=10)
    titles = [i["title"] for i in items]
    assert titles[:3] == ["c", "b", "a"]
    assert items[0]["id"] == c["id"]


def test_list_recent_can_filter_status(store: MemoryStore):
    store.save_memory(title="inbox-one")
    arch = store.save_memory(title="to-archive")
    store.archive_memory(arch["id"])
    items = store.list_recent(status="archived")
    assert len(items) == 1
    assert items[0]["title"] == "to-archive"


def test_search_memory_uses_fts5(store: MemoryStore):
    store.save_memory(title="MindContinuum tunnel test works", body="ChatGPT connected.")
    store.save_memory(title="Unrelated note", body="weather is nice")
    out = store.search_memory("tunnel test")
    assert len(out) == 1
    assert "tunnel" in out[0]["title"].lower()


def test_search_memory_filters_by_type_and_project(store: MemoryStore):
    store.save_memory(title="alpha decision", body="x", type="decision", project="P1")
    store.save_memory(title="alpha note", body="x", type="note", project="P1")
    store.save_memory(title="alpha decision in P2", body="x", type="decision", project="P2")
    a = store.search_memory("alpha", type="decision")
    assert len(a) == 2
    b = store.search_memory("alpha", type="decision", project="P1")
    assert len(b) == 1
    assert b[0]["project_slug"] == "p1"


def test_search_memory_handles_punctuation_query(store: MemoryStore):
    store.save_memory(title="hello world", body="content")
    out = store.search_memory("hello, world!")
    assert len(out) == 1


def test_search_memory_empty_query_returns_empty(store: MemoryStore):
    store.save_memory(title="anything")
    assert store.search_memory("") == []
    assert store.search_memory("   ") == []


def test_append_memory_preserves_old_body(store: MemoryStore):
    saved = store.save_memory(title="t", body="line one")
    updated = store.append_memory(saved["id"], "line two")
    assert "line one" in updated["body"]
    assert "line two" in updated["body"]
    assert "append @" in updated["body"]


def test_append_memory_rejects_empty(store: MemoryStore):
    saved = store.save_memory(title="t", body="x")
    with pytest.raises(ValueError):
        store.append_memory(saved["id"], "   ")


def test_append_memory_unknown_id(store: MemoryStore):
    with pytest.raises(LookupError):
        store.append_memory(999, "x")


def test_update_memory_changes_status_and_tags(store: MemoryStore):
    saved = store.save_memory(title="t", body="b")
    updated = store.update_memory(saved["id"], status="stable", tags=["one", "two"])
    assert updated["status"] == "stable"
    assert updated["tags"] == ["one", "two"]


def test_archive_memory_marks_status(store: MemoryStore):
    saved = store.save_memory(title="to-archive")
    archived = store.archive_memory(saved["id"])
    assert archived["status"] == "archived"


def test_record_decision_creates_linked_memory(store: MemoryStore):
    d = store.record_decision(
        project="ACME", decision="Use SQLite for v1",
        rationale="Local-first, FTS5 built-in, zero ops.",
        tradeoffs="Single-writer; no horizontal scale.",
    )
    assert d["decision_id"] >= 1
    assert d["memory_id"] >= 1
    mem = store.get_memory(d["memory_id"])
    assert mem is not None
    assert mem["type"] == "decision"
    assert "Use SQLite" in mem["body"]
    assert "Rationale" in mem["body"]
    assert "Tradeoffs" in mem["body"]
    listing = store.list_decisions(project="ACME")
    assert any(x["id"] == d["decision_id"] for x in listing)


def test_record_decision_requires_decision_text(store: MemoryStore):
    with pytest.raises(ValueError):
        store.record_decision(project="p", decision="   ")


def test_record_task_creates_linked_memory(store: MemoryStore):
    t = store.record_task(
        project="ACME", title="Ship tunnel demo",
        next_action="Validate Cloudflare tunnel on Windows", priority="high",
    )
    assert t["task_id"] >= 1
    assert t["memory_id"] >= 1
    mem = store.get_memory(t["memory_id"])
    assert mem is not None
    assert mem["type"] == "task"
    listing = store.list_tasks(project="ACME")
    assert any(x["id"] == t["task_id"] for x in listing)


def test_record_task_validates_priority(store: MemoryStore):
    with pytest.raises(ValueError):
        store.record_task(project="p", title="x", priority="extreme")


def test_export_all_round_trips_shape(store: MemoryStore):
    store.save_memory(title="exp1", body="b", project="ACME")
    store.record_decision(project="ACME", decision="d")
    store.record_task(project="ACME", title="t")
    payload = store.export_all()
    assert payload["version"] == 1
    assert payload["memory_items"]
    assert payload["projects"]
    assert payload["decisions"]
    assert payload["tasks"]


def test_export_markdown_contains_titles(store: MemoryStore):
    store.save_memory(title="MD title here", body="body text")
    md = store.export_markdown()
    assert "# MindContinuum export" in md
    assert "MD title here" in md
    assert "body text" in md
