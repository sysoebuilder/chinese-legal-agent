"""法律检索 MCP Server 的 stdio 与 HTTP 入口。"""

import argparse
from typing import Annotated, Any

from dotenv import load_dotenv
from mcp.server import MCPServer
from pydantic import Field

from ai_agent_learning.mcp_server.search_raws import (
    TOOL_DESCRIPTION,
    RetrievalCase,
    search_laws,
)
from ai_agent_learning.model_config import source_config_path

server = MCPServer("agent100-law-search")


@server.tool(name="search_raws", description=TOOL_DESCRIPTION)
async def search_raws(
    retrieval_cases: Annotated[
        list[RetrievalCase],
        Field(min_length=1, max_length=5, description="本次要并行检索的 1 至 5 个不同法律争议点。"),
    ],
) -> dict[str, Any]:
    """检索最多五个法律争议点。"""
    return await search_laws(retrieval_cases)


def main() -> None:
    """从当前项目配置读取密钥并启动 MCP 服务。"""
    parser = argparse.ArgumentParser(description="agent100 法律检索 MCP Server")
    parser.add_argument("--http", action="store_true", help="通过 Streamable HTTP 提供服务")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP 监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 监听端口，默认 8000")
    args = parser.parse_args()
    if args.http and not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    load_dotenv(source_config_path(), override=False)
    if args.http:
        server.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        server.run(transport="stdio")
