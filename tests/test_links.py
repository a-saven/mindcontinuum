"""Tests for memory_links: contradictions, related links, supersede, merge."""

from __future__ import annotations

import pytest

from mindcontinuum.core import MemoryStore


def test_mark_contradiction_creates_link_no_status_change(store: MemoryStore):
    a = store.save_memory(title="Sky is blue")
    b = store.save_memory(title="Sky is green at sunrise")
    out = store.mark_contradiction(b["id"], a["id"], note="time-of-day caveat", actor="mcp")
    assert out["link_type"] == "contradicts"
    refreshed_a = store.get_memory(a["id"])
    refreshed_b = store.get_memory(b["id"])
    # neither status changes
    assert refreshed_a["status"] == "inbox"
    assert refreshed_b["status"] == "inbox"
    # but get_memory surfaces it on both sides
    assert any(l["link_type"] == "contradicts" for l in refreshed_a["links"])
    assert any(l["link_type"] == "contradicts" for l in refreshed_b["links"])
    assert refreshed_a["contradictions"]


def test_mark_contradiction_idempotent(store: MemoryStore):
    a = store.save_memory(title="claim a")
    b = store.save_memory(title="claim b")
    store.mark_contradiction(b["id"], a["id"], actor="mcp")
    store.mark_contradiction(b["id"], a["id"], actor="mcp")
    refreshed = store.get_memory(a["id"])
    contradicts = [l for l in refreshed["links"] if l["link_type"] == "contradicts"]
    assert len(contradicts) == 1


def test_link_memories_allowed_types(store: MemoryStore):
    a = store.save_memory(title="A")
    b = store.save_memory(title="B")
    for lt in ("related", "supersedes", "derived_from"):
        out = store.link_memories(a["id"], b["id"], lt, actor="mcp")
        assert out["link_type"] == lt


def test_link_memories_rejects_contradicts_and_duplicate_of(store: MemoryStore):
    a = store.save_memory(title="A")
    b = store.save_memory(title="B")
    with pytest.raises(ValueError):
        store.link_memories(a["id"], b["id"], "contradicts", actor="mcp")
    with pytest.raises(ValueError):
        store.link_memories(a["id"], b["id"], "duplicate_of", actor="mcp")


def test_link_memories_idempotent(store: MemoryStore):
    a = store.save_memory(title="A")
    b = store.save_memory(title="B")
    store.link_memories(a["id"], b["id"], "related", actor="mcp")
    store.link_memories(a["id"], b["id"], "related", actor="mcp")
    refreshed = store.get_memory(a["id"])
    related = [l for l in refreshed["links"] if l["link_type"] == "related"]
    assert len(related) == 1


def test_supersede_marks_old_stale_and_links(store: MemoryStore):
    old = store.save_memory(title="old fact", status="stable", actor="ui")
    new = store.save_memory(title="new fact")
    out = store.supersede(old["id"], new["id"], actor="ui")
    assert out["old_status"] == "stale"
    refreshed = store.get_memory(old["id"])
    assert refreshed["status"] == "stale"
    assert any(l["link_type"] == "supersedes" for l in refreshed["links"])


def test_supersede_blocked_for_agents(store: MemoryStore):
    old = store.save_memory(title="o")
    new = store.save_memory(title="n")
    with pytest.raises(PermissionError):
        store.supersede(old["id"], new["id"], actor="mcp")


def test_merge_into_archives_source(store: MemoryStore):
    a = store.save_memory(title="duplicate of B")
    b = store.save_memory(title="canonical B")
    out = store.merge_into(a["id"], b["id"], actor="ui")
    assert out["source_status"] == "archived"
    refreshed = store.get_memory(a["id"])
    assert refreshed["status"] == "archived"
    assert any(l["link_type"] == "duplicate_of" for l in refreshed["links"])


def test_merge_into_blocked_for_agents(store: MemoryStore):
    a = store.save_memory(title="a")
    b = store.save_memory(title="b")
    with pytest.raises(PermissionError):
        store.merge_into(a["id"], b["id"], actor="mcp")


def test_suggest_duplicates_returns_high_overlap(store: MemoryStore):
    base = store.save_memory(
        title="Cloudflare tunnel setup", project="Bridge", tags=["cloudflare", "tunnel"],
    )
    similar = store.save_memory(
        title="Cloudflare tunnel setup notes", project="Bridge", tags=["cloudflare"],
    )
    unrelated = store.save_memory(title="Holiday menu ideas", tags=["food"])
    cands = store.suggest_duplicates(base["id"])
    ids = [c["id"] for c in cands]
    assert similar["id"] in ids
    assert unrelated["id"] not in ids


def test_suggest_duplicates_returns_empty_for_unique_item(store: MemoryStore):
    a = store.save_memory(title="very unique snowflake title 1234")
    out = store.suggest_duplicates(a["id"])
    assert out == []
