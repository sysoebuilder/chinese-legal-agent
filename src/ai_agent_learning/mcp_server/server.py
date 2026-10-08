"""法律检索 MCP Server 的 stdio 与 HTTP 入口。"""

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from dotenv import load_dotenv
from mcp.server import MCPServer
from pydantic import Field

from ai_agent_learning.config import load_app_config, use_app_config
from ai_agent_learning.mcp_server.search_laws import (
    TOOL_DESCRIPTION,
    RetrievalCase,
)
from ai_agent_learning.mcp_server.search_laws import (
    search_laws as retrieve_laws,
)
from ai_agent_learning.model_config import source_config_path
from ai_agent_learning.qdrant_server import QdrantServer


@asynccontextmanager
async def lifespan(app: MCPServer) -> AsyncIterator[None]:
    """复用已运行的 Qdrant；必要时启动本机服务，并清理自建进程。"""
    load_dotenv(source_config_path(), override=False)
    await asyncio.to_thread(load_app_config)
    qdrant = QdrantServer()
    try:
        await qdrant.start()
        yield
    finally:
        await qdrant.close()


server = MCPServer("agent100-law-search", lifespan=lifespan)


@server.tool(name="search_laws", description=TOOL_DESCRIPTION)
async def search_laws(
    retrieval_cases: Annotated[
        list[RetrievalCase],
        Field(min_length=1, max_length=5, description="本次要并行检索的 1 至 5 个不同法律争议点。"),
    ],
) -> dict[str, Any]:
    """检索最多五个法律争议点。"""
    try:
        config = await asyncio.to_thread(load_app_config)
        with use_app_config(config):
            return await retrieve_laws(retrieval_cases)
    except Exception:
        logging.getLogger(__name__).exception("search_laws 检索失败")
        raise


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
