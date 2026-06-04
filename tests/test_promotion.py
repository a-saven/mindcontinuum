"""Tests for stable-memory promotion + rejection workflow."""

from __future__ import annotations

import pytest

from mindcontinuum.core import MemoryStore


def test_propose_stable_sets_status_and_logs(store: MemoryStore):
    saved = store.save_memory(title="proposable", body="b")
    out = store.propose_stable(saved["id"], reason="confirmed via two sources", actor="mcp")
    assert out["status"] == "proposed_stable"
    events = [e for e in store.list_events() if e["action"] == "memory.propose_stable"]
    assert events and events[0]["payload"]["reason"] == "confirmed via two sources"


def test_propose_stable_refuses_when_already_stable(store: MemoryStore):
    saved = store.save_memory(title="x")
    store.propose_stable(saved["id"], actor="mcp")
    store.promote_to_stable(saved["id"], actor="ui")
    with pytest.raises(ValueError):
        store.propose_stable(saved["id"], actor="mcp")


def test_promote_to_stable_blocked_for_agents(store: MemoryStore):
    saved = store.save_memory(title="x")
    store.propose_stable(saved["id"], actor="mcp")
    with pytest.raises(PermissionError):
        store.promote_to_stable(saved["id"], actor="mcp")
    with pytest.raises(PermissionError):
        store.promote_to_stable(saved["id"], actor="mcp:chatgpt")


def test_promote_to_stable_allowed_for_ui(store: MemoryStore):
    saved = store.save_memory(title="x")
    promoted = store.promote_to_stable(saved["id"], actor="ui")
    assert promoted["status"] == "stable"


def test_reject_proposal_returns_to_processed(store: MemoryStore):
    saved = store.save_memory(title="x")
    store.propose_stable(saved["id"], actor="mcp")
    out = store.reject_proposal(saved["id"], actor="ui")
    assert out["status"] == "processed"


def test_save_memory_with_status_stable_blocked_for_agents(store: MemoryStore):
    with pytest.raises(PermissionError):
        store.save_memory(title="x", status="stable", actor="mcp")


def test_save_memory_with_status_stable_allowed_for_ui(store: MemoryStore):
    saved = store.save_memory(title="x", status="stable", actor="ui")
    assert saved["status"] == "stable"


def test_mcp_update_refuses_to_modify_stable_body(store: MemoryStore):
    saved = store.save_memory(title="x", status="stable", actor="ui")
    with pytest.raises(PermissionError):
        store.update_memory(saved["id"], body="new body", actor="mcp")
    with pytest.raises(PermissionError):
        store.update_memory(saved["id"], title="new title", actor="mcp")


def test_mcp_append_to_stable_is_allowed(store: MemoryStore):
    saved = store.save_memory(title="x", body="initial", status="stable", actor="ui")
    updated = store.append_memory(saved["id"], "more context", actor="mcp")
    assert "initial" in updated["body"]
    assert "more context" in updated["body"]


def test_ui_can_modify_stable_with_actor_ui(store: MemoryStore):
    saved = store.save_memory(title="x", body="b", status="stable", actor="ui")
    updated = store.update_memory(saved["id"], body="rewritten", actor="ui")
    assert updated["body"] == "rewritten"
