"""ASGI entrypoint and CLI for MindContinuum."""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import embeddings as _emb
from .api import build_router
from .config import Settings
from .core import MemoryStore
from .mcp_tools import build_server


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    # Pin fastembed's model cache to our app data dir so models don't bloat
    # the user profile cache and stay portable with the SQLite store.
    _emb.set_cache_dir(settings.data_dir / "models")
    store = MemoryStore(db_path=settings.db_path)
    mcp_server = build_server(store, name=settings.server_name)
    mcp_asgi = mcp_server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_asgi.router.lifespan_context(app):
            yield
        store.close()

    app = FastAPI(
        title="MindContinuum",
        version="0.1.0",
        description="Local-first long-term memory for AI conversations.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.mcp_server = mcp_server

    app.include_router(build_router())

    static_dir = Path(resources.files("mindcontinuum").joinpath("static"))
    templates_dir = Path(resources.files("mindcontinuum").joinpath("templates"))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    index_html = (templates_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return HTMLResponse(index_html)

    app.mount(settings.mcp_mount, mcp_asgi)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mindcontinuum", description="Run the MindContinuum local server.")
    parser.add_argument("--host", default=None, help="Host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind (default 3780)")
    parser.add_argument("--data-dir", default=None, help="Directory to store the SQLite database")
    parser.add_argument("--reload", action="store_true", help="Reload on file changes (dev)")
    parser.add_argument(
        "--no-embeddings", action="store_true",
        help="Disable local embeddings (semantic + hybrid search). Equivalent to "
             "setting MINDCONTINUUM_DISABLE_EMBEDDINGS=1. Useful on low-memory "
             "machines or to skip the bge-small download on first run.",
    )
    args = parser.parse_args(argv)

    if args.data_dir:
        os.environ["MINDCONTINUUM_DATA_DIR"] = args.data_dir
    if args.host:
        os.environ["MINDCONTINUUM_HOST"] = args.host
    if args.port:
        os.environ["MINDCONTINUUM_PORT"] = str(args.port)
    if args.no_embeddings:
        os.environ["MINDCONTINUUM_DISABLE_EMBEDDINGS"] = "1"

    settings = Settings.from_env()

    import uvicorn

    print("┏━ MindContinuum")
    print(f"┃  UI/API   http://{settings.host}:{settings.port}/")
    print(f"┃  MCP HTTP http://{settings.host}:{settings.port}{settings.mcp_mount}")
    print(f"┃  DB       {settings.db_path}")
    print("┗━")

    uvicorn.run(
        "mindcontinuum.server:create_app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        factory=True,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
