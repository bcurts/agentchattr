"""Anonymous Parallel Search MCP client for job research tools."""

import asyncio
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


ENDPOINT = "https://search.parallel.ai/mcp"
USER_AGENT = "agentchattr (+https://github.com/bcurts/agentchattr)"
TIMEOUT_SECONDS = 20


async def call_tool(name: str, arguments: dict) -> str:
    """Run one bounded MCP request without reading or forwarding credentials.

    The caller supplies the job's conversation ID. Connection lifetime must not
    determine that ID: related calls can use different MCP connections.
    """
    async with asyncio.timeout(TIMEOUT_SECONDS):
        async with streamablehttp_client(
            ENDPOINT,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_SECONDS,
            sse_read_timeout=TIMEOUT_SECONDS,
        ) as (read, write, _):
            async with ClientSession(
                read, write,
                read_timeout_seconds=timedelta(seconds=TIMEOUT_SECONDS),
            ) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
    # Raise application errors after leaving the SDK task groups, so they are
    # not wrapped in an ExceptionGroup that hides the useful provider message.
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.isError:
        raise RuntimeError(f"Parallel Search MCP: {text}")
    if not text:
        raise RuntimeError("Parallel Search MCP returned no text.")
    return text
