from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from incidentgraph_agent.config import Settings
from incidentgraph_agent.database import FalkorStore
from incidentgraph_agent.errors import IncidentGraphError
from incidentgraph_agent.graph_tools import GraphToolRegistry
from incidentgraph_agent.repository import IncidentRepository


def create_server(registry: GraphToolRegistry) -> Server:
    """Create the local MCP server with explicit strict input JSON Schemas."""
    catalog = registry.list_tools()
    schemas = {item["name"]: item["input_schema"] for item in catalog}
    tools = [
        types.Tool(
            name=item["name"],
            description=item["description"],
            inputSchema=item["input_schema"],
        )
        for item in catalog
    ]

    async def list_tools(_context: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            result = registry.invoke(params.name, params.arguments or {})
        except IncidentGraphError as exc:
            return types.CallToolResult(
                content=[types.TextContent(text=f"{exc.code}: {exc}")],
                isError=True,
            )
        return types.CallToolResult(
            content=[
                types.TextContent(
                    text=json.dumps(result, ensure_ascii=False, separators=(",", ":"))
                )
            ],
            structuredContent=result,
            isError=False,
        )

    return Server(
        name="incidentgraph-agent",
        version="0.1.0",
        instructions=(
            "Read-only synthetic incident investigation tools backed by FalkorDB. "
            "Results are bounded graph evidence and do not prove causality."
        ),
        get_tool_input_schema=lambda name: schemas.get(name),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def _run_stdio(server: Server) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def serve_stdio() -> None:
    settings = Settings.from_env()
    with FalkorStore(settings) as store:
        server = create_server(GraphToolRegistry(IncidentRepository(store)))
        asyncio.run(_run_stdio(server))


def main() -> int:
    try:
        serve_stdio()
        return 0
    except IncidentGraphError as exc:
        print(
            json.dumps({"error": {"code": exc.code, "message": str(exc)}}),
            file=sys.stderr,
        )
        return 2
    except Exception:
        print("The local IncidentGraph MCP server failed to start.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
