"""Compact MCP proxy for PC15 Windows control.

Open Computer Use correctly returns an accessibility tree plus an MCP image
block.  Computer currently serializes that image block into local-model text,
which can overwhelm the model with PNG base64.  This proxy preserves the
upstream session and all action tools while removing image blocks and applying
conservative tree budgets to get_app_state.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server


UPSTREAM_NODE = os.environ.get("MAE_OCU_NODE", r"C:\MAE-Agent\runtime\node.exe")
UPSTREAM_SCRIPT = os.environ.get(
    "MAE_OCU_SCRIPT",
    r"C:\MAE-Agent\npm\node_modules\open-computer-use\bin\open-computer-use",
)
MAX_TEXT_CHARS = int(os.environ.get("MAE_OCU_MAX_TEXT_CHARS", "30000"))

server = Server("mae-windows-control")
upstream: ClientSession | None = None
upstream_tools: list[types.Tool] = []


def compact_content(content: list[types.ContentBlock]) -> list[types.ContentBlock]:
    """Remove screenshots and bound text without changing element indexes."""
    compacted: list[types.ContentBlock] = []
    for block in content:
        if isinstance(block, types.ImageContent):
            continue
        if isinstance(block, types.TextContent):
            value = block.text
            if len(value) > MAX_TEXT_CHARS:
                value = (
                    value[:MAX_TEXT_CHARS]
                    + "\n\n[Accessibility output truncated by MAE Windows control proxy.]"
                )
            compacted.append(types.TextContent(type="text", text=value))
        else:
            compacted.append(block)
    if not compacted:
        compacted.append(
            types.TextContent(
                type="text",
                text="Windows action completed. Screenshot omitted from local-model context.",
            )
        )
    return compacted


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return upstream_tools


@server.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    if upstream is None:
        raise RuntimeError("Windows control upstream is not initialized")

    clean_args = dict(arguments or {})
    if name == "get_app_state":
        clean_args.setdefault("text_limit", 500)
        clean_args.setdefault("max_tree_nodes", 300)
        clean_args.setdefault("max_tree_depth", 24)

    result = await upstream.call_tool(name, clean_args)
    return types.CallToolResult(
        content=compact_content(list(result.content)),
        isError=result.isError,
        structuredContent=result.structuredContent,
    )


async def main() -> None:
    global upstream, upstream_tools

    params = StdioServerParameters(
        command=UPSTREAM_NODE,
        args=[UPSTREAM_SCRIPT, "mcp"],
    )
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        upstream = await stack.enter_async_context(ClientSession(read, write))
        await upstream.initialize()
        tools_result = await upstream.list_tools()
        upstream_tools = list(tools_result.tools)

        local_read, local_write = await stack.enter_async_context(stdio_server())
        await server.run(
            local_read,
            local_write,
            InitializationOptions(
                server_name="mae-windows-control",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
                instructions=(
                    "Control Windows applications on PC15. Call get_app_state once "
                    "per turn before acting. Results are accessibility-first and omit "
                    "encoded screenshots so local models can reason reliably."
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
