"""Local embeddings layer — lazy fastembed loader with safe fallback.

The first read or write that needs an embedding triggers model load (and a
one-time model download on first run). If `fastembed` (or its model file)
isn't available, ``embeddings_available()`` returns False and all callers
should degrade to keyword-only search. Embedding writes are wrapped in
try/except so the main write path NEVER breaks because of embeddings.

Storage: a single ``memory_embeddings`` row per memory_id. Vector is stored
as a normalized float32 blob. Cosine = dot product on normalized vectors.

Default model: BAAI/bge-small-en-v1.5 (384-dim, ~133MB, MIT). Model cache
is pinned to the app data directory so users don't accumulate models in
``~/.cache``.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384
DISABLE_ENV_VAR = "MINDCONTINUUM_DISABLE_EMBEDDINGS"
_TRUTHY = {"1", "true", "yes", "on"}

_MODEL = None
_MODEL_LOCK = Lock()
_AVAILABLE: bool | None = None


def set_cache_dir(path: Path | str) -> None:
    """Pin the fastembed model cache to a folder under the app data dir.

    Has to run BEFORE the model is loaded for the first time.
    """
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(path))


def _disabled_by_env() -> bool:
    return os.environ.get(DISABLE_ENV_VAR, "").strip().lower() in _TRUTHY


def embeddings_available() -> bool:
    """Whether fastembed can be used right now.

    Returns False if either:
      * the user explicitly set MINDCONTINUUM_DISABLE_EMBEDDINGS=1, or
      * the fastembed package cannot be imported.

    The env flag is re-read on every call so tests/operators can toggle it
    at runtime without restarting; the import check is cached after the
    first successful probe.
    """
    if _disabled_by_env():
        return False
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    try:
        import fastembed  # noqa: F401
        _AVAILABLE = True
    except Exception as e:  # pragma: no cover - environment-dependent
        log.info("embeddings unavailable: %s", e)
        _AVAILABLE = False
    return _AVAILABLE


def reset_state_for_tests() -> None:  # pragma: no cover - test helper
    """Reset module-level caches so tests can monkeypatch availability."""
    global _AVAILABLE, _MODEL
    _AVAILABLE = None
    _MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding(DEFAULT_MODEL)
    return _MODEL


def embed_text(text: str) -> list[float]:
    """Return an L2-normalised embedding for the given text.

    Raises if embeddings aren't available or the model fails to load. Callers
    should check ``embeddings_available()`` first or wrap in try/except.
    """
    import numpy as np  # local import; numpy is a transitive of fastembed
    model = _get_model()
    vec = next(model.embed([text or ""]))
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr) or 1.0)
    arr = arr / norm
    return arr.tolist()


def _vec_to_blob(vec: Iterable[float]) -> bytes:
    import numpy as np
    arr = np.asarray(list(vec), dtype=np.float32)
    return arr.tobytes()


def _blob_to_vec(blob: bytes):
    import numpy as np
    return np.frombuffer(blob, dtype=np.float32)


def index_memory(conn: sqlite3.Connection, memory_id: int, text: str) -> bool:
    """Compute + persist a vector for ``memory_id``. Returns True on success.

    Never raises — failures are logged and the function returns False so the
    write path stays unaffected.
    """
    if not embeddings_available():
        return False
    try:
        vec = embed_text(text)
        blob = _vec_to_blob(vec)
        conn.execute(
            "INSERT OR REPLACE INTO memory_embeddings (memory_id, model, dim, vector) "
            "VALUES (?, ?, ?, ?)",
            (int(memory_id), DEFAULT_MODEL, len(vec), blob),
        )
        return True
    except Exception as e:  # pragma: no cover - depends on model availability
        log.warning("index_memory failed for id=%s: %s", memory_id, e)
        return False


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    top_k: int = 20,
    namespace: str = "work",
) -> list[tuple[int, float]]:
    """Return [(memory_id, score)] ranked by cosine similarity descending.

    Empty result if embeddings aren't available or no vectors have been
    indexed yet.
    """
    if not embeddings_available():
        return []
    import numpy as np
    try:
        q = embed_text(query)
    except Exception:  # pragma: no cover
        return []
    rows = conn.execute(
        "SELECT e.memory_id AS memory_id, e.vector AS vector "
        "FROM memory_embeddings e "
        "JOIN memory_items m ON m.id = e.memory_id "
        "WHERE e.model = ? AND m.namespace = ?",
        (DEFAULT_MODEL, namespace),
    ).fetchall()
    if not rows:
        return []
    ids = np.array([int(r["memory_id"]) for r in rows], dtype=np.int64)
    mat = np.vstack([_blob_to_vec(r["vector"]) for r in rows])
    q_arr = np.asarray(q, dtype=np.float32)
    scores = mat @ q_arr  # both normalised → cosine
    order = np.argsort(-scores)[:top_k]
    return [(int(ids[i]), float(scores[i])) for i in order]


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    cache = os.environ.get("FASTEMBED_CACHE_PATH")
    available = embeddings_available()
    disabled_by_env = _disabled_by_env()
    total = conn.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0]
    indexed = conn.execute(
        "SELECT COUNT(*) FROM memory_embeddings WHERE model = ?", (DEFAULT_MODEL,)
    ).fetchone()[0]
    return {
        "available": available,
        "disabled_by_env": disabled_by_env,
        "model": DEFAULT_MODEL if available else None,
        "dim": DEFAULT_DIM if available else None,
        "count_total": int(total),
        "count_indexed": int(indexed),
        "cache_path": cache,
    }


__all__ = [
    "DEFAULT_MODEL", "DEFAULT_DIM",
    "set_cache_dir", "embeddings_available", "embed_text",
    "index_memory", "semantic_search", "status",
    "reset_state_for_tests",
]
