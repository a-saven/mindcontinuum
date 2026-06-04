"""Runtime configuration. Local-first, env-overridable, no remote dependencies."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_data_dir() -> Path:
    env = os.environ.get("MINDCONTINUUM_DATA_DIR")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "MindContinuum"
    return Path.home() / ".mindcontinuum"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    host: str
    port: int
    mcp_mount: str
    server_name: str = "MindContinuum"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _default_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = Path(os.environ.get("MINDCONTINUUM_DB", data_dir / "mindcontinuum.sqlite"))
        return cls(
            data_dir=data_dir,
            db_path=db_path,
            host=os.environ.get("MINDCONTINUUM_HOST", "127.0.0.1"),
            port=int(os.environ.get("MINDCONTINUUM_PORT", "3780")),
            mcp_mount=os.environ.get("MINDCONTINUUM_MCP_MOUNT", "/mcp"),
        )
