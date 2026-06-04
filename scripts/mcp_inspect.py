"""Tiny local MCP inspector for the bridge spike.

Validates the Streamable HTTP loop without ChatGPT: lists tools, calls
save_memory, then calls search_memory and prints the round-trip result.

Run against a running server:

    python scripts/mcp_inspect.py                # http://127.0.0.1:3780/mcp
    python scripts/mcp_inspect.py --url https://your-tunnel.example.com/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def run(url: str) -> int:
    try:
        from mcp.client.streamable_http import streamablehttp_client
        from mcp.client.session import ClientSession
    except ImportError as e:
        print(f"missing mcp client: {e}", file=sys.stderr)
        return 1

    async with streamablehttp_client(url) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"[connected] {init.serverInfo.name} v{init.serverInfo.version}")
            tools = await session.list_tools()
            print(f"[tools] {len(tools.tools)}: {', '.join(t.name for t in tools.tools)}")

            ping = await session.call_tool("ping", {})
            print(f"[ping] {ping.structuredContent or ping.content}")

            saved = await session.call_tool("save_memory", {
                "title": "MindContinuum tunnel test works",
                "body": "Round-trip from local MCP inspector.",
                "type": "research",
                "project": "Bridge Spike",
                "tags": ["spike", "tunnel"],
            })
            saved_data = saved.structuredContent or {}
            print(f"[save_memory] -> {json.dumps(saved_data, indent=2)[:400]}")

            found = await session.call_tool("search_memory", {"query": "tunnel test"})
            found_data = found.structuredContent or {}
            print(f"[search_memory] -> {json.dumps(found_data, indent=2)[:600]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3780/mcp")
    args = parser.parse_args()
    return asyncio.run(run(args.url))


if __name__ == "__main__":
    sys.exit(main())
