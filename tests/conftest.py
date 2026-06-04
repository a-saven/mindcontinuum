"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from mindcontinuum.config import Settings
from mindcontinuum.core import MemoryStore


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "mc.sqlite"


@pytest.fixture
def store(tmp_db: Path) -> MemoryStore:
    s = MemoryStore(db_path=tmp_db)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db = tmp_path / "mc.sqlite"
    return Settings(
        data_dir=tmp_path,
        db_path=db,
        host="127.0.0.1",
        port=3789,
        mcp_mount="/mcp",
    )
