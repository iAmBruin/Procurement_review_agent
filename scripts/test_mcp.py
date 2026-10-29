"""MCP Server 冒烟测试：通过 stdio 客户端调用 5 个工具。"""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

SERVER_CMD = [sys.executable, "-m", "backend.mcp_server.server", "--transport", "stdio"]


async def main():
    params = StdioServerParameters(command=SERVER_CMD[0], args=SERVER_CMD[1:], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"工具数量: {len(tools.tools)}")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:40]}")

            print("\n调用 get_purchase_case(po_id=1):")
            r = await session.call_tool("get_purchase_case", {"po_id": 1})
            print(r.content[0].text[:300])

            print("\n调用 run_match_check(po_id=1):")
            r = await session.call_tool("run_match_check", {"po_id": 1})
            print(r.content[0].text[:400])

            print("\n调用 search_contract_rules(exception_type=QTY_EXCEEDED):")
            r = await session.call_tool(
                "search_contract_rules", {"exception_type": "QTY_EXCEEDED"}
            )
            print(r.content[0].text[:400])

            print("\n调用 list_open_exceptions():")
            r = await session.call_tool("list_open_exceptions", {})
            print(r.content[0].text[:200])


if __name__ == "__main__":
    asyncio.run(main())