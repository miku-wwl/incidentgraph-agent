from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import Client, StdioServerParameters


def test_stdio_mcp_discovery_and_calls_query_real_falkordb(store) -> None:
    child_env = dict(os.environ)
    child_env.update(
        {
            "FALKORDB_HOST": store.settings.host,
            "FALKORDB_HOST_PORT": str(store.settings.port),
            "FALKORDB_PASSWORD": store.settings.password,
            "INCIDENTGRAPH_GRAPH_NAME": store.settings.graph_name,
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "incidentgraph_agent.mcp_server"],
        env=child_env,
    )

    async def verify() -> None:
        async with Client(parameters, read_timeout_seconds=25) as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {
                "get_service_dependencies",
                "calculate_blast_radius",
                "correlate_incident_evidence",
                "investigate_incident",
            }
            assert (
                tools["get_service_dependencies"].input_schema["additionalProperties"]
                is False
            )
            assert "seed" not in tools and "write_cypher" not in tools

            result = await client.call_tool(
                "get_service_dependencies",
                {"service_id": "checkout-api", "max_depth": 2},
                read_timeout_seconds=25,
            )
            assert result.is_error is False
            payload = result.structured_content
            if payload is None:
                payload = json.loads(result.content[0].text)
            assert payload["evidence_complete"] is True
            assert len(payload["paths"]) == 5
            assert (
                "path:checkout-api>payment-service>payment-db"
                in payload["evidence_refs"]
            )

            extra = await client.call_tool(
                "get_service_dependencies",
                {"service_id": "checkout-api", "cypher": "MATCH (n) RETURN n"},
                read_timeout_seconds=25,
            )
            assert extra.is_error is True

            missing = await client.call_tool(
                "get_service_dependencies",
                {"service_id": "missing-service"},
                read_timeout_seconds=25,
            )
            assert missing.is_error is True
            error_text = " ".join(
                item.text for item in missing.content if getattr(item, "text", None)
            )
            assert "not_found" in error_text

    asyncio.run(verify())
