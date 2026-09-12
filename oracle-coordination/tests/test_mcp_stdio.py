import sys
from pathlib import Path
import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_mcp_stdio_process_registration(ecosystem, tmp_path):
    oracle, _ = ecosystem
    config = tmp_path / "bridge.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "principal": "alice",
                "project_id": "backend",
                "repository": str(tmp_path),
                "state_file": str(tmp_path / "bridge.db"),
                "transport": "local",
                "server_db": oracle.store.path,
            }
        )
    )
    params = StdioServerParameters(
        command=str(Path(sys.executable).parent / "oracle-bridge"), args=["--config", str(config), "mcp"]
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert len(tools.tools) == 14
            response = await client.call_tool(
                "oracle_register",
                {
                    "registration": {
                        "project_id": "backend",
                        "repository": "backend",
                        "human_id": "alice",
                        "machine_id": "stdio",
                        "agent_type": "generic-mcp",
                        "agent_session_id": "stdio-client",
                        "interests": ["User.id"],
                    }
                },
            )
            assert not response.isError
            assert "registered" in str(response.content)
            checkpoint = await client.call_tool("oracle_checkpoint", {})
            assert not checkpoint.isError
