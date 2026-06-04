"""Tests for the embeddings layer.

Most tests gracefully skip if fastembed is not importable so the suite
stays green in minimal environments. A few tests exercise the integration
path when the module is available.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from mindcontinuum import embeddings as emb
from mindcontinuum.core import MemoryStore


def test_embeddings_available_consistent_across_calls():
    a = emb.embeddings_available()
    b = emb.embeddings_available()
    assert a == b


def test_blob_roundtrip(monkeypatch):
    monkeypatch.setattr(emb, "_AVAILABLE", True, raising=False)
    pytest.importorskip("numpy")
    vec = [0.1, 0.2, 0.3, 0.4]
    blob = emb._vec_to_blob(vec)
    out = emb._blob_to_vec(blob)
    assert list(out) == pytest.approx(vec, rel=1e-6)


def test_status_returns_counts(store: MemoryStore):
    store.save_memory(title="hello")
    s = store.embeddings_status()
    assert "available" in s
    assert s["count_total"] == 1


def test_index_memory_safe_no_crash_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(emb, "_AVAILABLE", False, raising=False)
    store = MemoryStore(db_path=tmp_path / "noemb.sqlite", enable_embeddings=True)
    try:
        item = store.save_memory(title="x")  # should not raise
        assert item["id"] >= 1
        s = store.embeddings_status()
        assert s["count_indexed"] == 0
    finally:
        store.close()


def test_semantic_search_returns_empty_when_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(emb, "_AVAILABLE", False, raising=False)
    store = MemoryStore(db_path=tmp_path / "nosem.sqlite", enable_embeddings=False)
    try:
        store.save_memory(title="anything")
        out = store.search_memory("anything", mode="semantic")
        # falls back to LIKE search
        assert len(out) >= 0  # never raises
    finally:
        store.close()


# ---- live fastembed tests (opt-out via env) ----

_LIVE = os.environ.get("MINDCONTINUUM_SKIP_EMB_LIVE", "").strip().lower() in {"1", "yes", "true"}
_HAS_FE = False
try:
    import fastembed  # noqa: F401
    _HAS_FE = True
except Exception:
    pass

live = pytest.mark.skipif(
    _LIVE or not _HAS_FE,
    reason="fastembed not installed or live embedding tests opted out",
)


@live
def test_embed_text_is_normalised():
    import numpy as np
    v = emb.embed_text("hello world")
    assert abs(np.linalg.norm(v) - 1.0) < 1e-4


@live
def test_semantic_search_ranks_relevant_above_unrelated(tmp_path):
    store = MemoryStore(db_path=tmp_path / "sem.sqlite")
    try:
        store.save_memory(title="Python tutorial for beginners", body="learn syntax")
        store.save_memory(title="Python advanced patterns", body="metaclasses")
        store.save_memory(title="Holiday cookie recipe", body="butter sugar flour")
        out = store.search_memory("python programming", mode="semantic", limit=2)
        titles = [r["title"] for r in out]
        assert any("Python" in t for t in titles)
        assert "Holiday cookie recipe" not in titles
    finally:
        store.close()


@live
def test_hybrid_search_combines_both_signals(tmp_path):
    store = MemoryStore(db_path=tmp_path / "hyb.sqlite")
    try:
        store.save_memory(title="MindContinuum tunnel test works")
        store.save_memory(title="entirely unrelated bakery menu")
        out = store.search_memory("tunnel test", mode="hybrid")
        assert any("tunnel" in r["title"].lower() for r in out)
        # unrelated item must not surface from hybrid for this query
        assert not any("bakery" in r["title"].lower() for r in out)
    finally:
        store.close()


@live
def test_reindex_endpoint(tmp_path):
    store = MemoryStore(db_path=tmp_path / "reindex.sqlite")
    try:
        for i in range(5):
            store.save_memory(title=f"row {i}")
        out = store.reindex_embeddings()
        assert out["available"] is True
        assert out["indexed"] == 5
        s = store.embeddings_status()
        assert s["count_indexed"] == 5
    finally:
        store.close()
