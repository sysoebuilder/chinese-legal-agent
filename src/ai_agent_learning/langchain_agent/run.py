"""聊天主流程：统一管理资源，创建 Agent，再进入对话循环。"""

import asyncio
import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool
from mcp import StdioServerParameters

from ai_agent_learning.database import get_database_url, migrate_database
from ai_agent_learning.langchain_agent.chat import run_chat_loop
from ai_agent_learning.langchain_agent.checkpoint import open_checkpointer
from ai_agent_learning.langchain_agent.memory import MemoryWorker
from ai_agent_learning.langchain_agent.middleware.memory import (
    MemorySaveMiddleware,
    MemoryTurn,
)
from ai_agent_learning.langchain_agent.runtime import (
    create_langchain_model,
    create_new_chat_agent,
)
from ai_agent_learning.langchain_agent.session import (
    PostgresSessionStore,
    open_session_store,
    select_session,
)
from ai_agent_learning.logging_config import configure_logging, shutdown_logging
from ai_agent_learning.mcp_client import CloudServerConfig, connect_servers
from ai_agent_learning.model_config import ModelConfig, load_active_model
from ai_agent_learning.qdrant_server import QdrantServer
from ai_agent_learning.ui.terminal import TerminalUI

logger = logging.getLogger(__name__)

# 在这里配置需要接入的 MCP Server；空配置时使用现有内置工具。
LOCAL_MCP_SERVERS: dict[str, StdioServerParameters] = {}


CLOUD_MCP_SERVERS: dict[str, CloudServerConfig] = {
    "github": {
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
    },
    "cloudflare_docs":{
        "url": "https://docs.mcp.cloudflare.com/mcp",
    },
}


def configured_cloud_servers() -> dict[str, CloudServerConfig]:
    """按运行时环境加入 Windows 宿主机的 HTTP MCP，不修改公共配置。"""
    servers = dict(CLOUD_MCP_SERVERS)
    url = os.getenv("WINDOWS_MCP_URL", "").strip()
    if url:
        servers["windows_terminal"] = {
            "url": url,
            "headers": {"Authorization": "Bearer ${WINDOWS_MCP_TOKEN}"},
            "read_timeout_seconds": 330,
            "trust_env": False,
        }
    return servers


@dataclass
class ChatResources:
    config: ModelConfig
    model: Any
    agent: Any
    ui: TerminalUI
    session_store: PostgresSessionStore
    session: Any
    memory_worker: MemoryWorker


@asynccontextmanager
async def open_chat_agent():
    """对话期间保持资源可用，退出时关闭数据库连接和后台资源。"""
    database_url = get_database_url()
    await asyncio.to_thread(migrate_database, database_url)
    await asyncio.to_thread(configure_logging, database_url)
    try:
        logger.info("新版会话启动", extra={"event": "newchat.started"})
        config = load_active_model()
        async with AsyncExitStack() as stack:
            server = QdrantServer()
            stack.push_async_callback(server.close)
            await server.start()
            session_store = await stack.enter_async_context(open_session_store(database_url))
            checkpointer = await stack.enter_async_context(open_checkpointer(database_url))
            ui = TerminalUI()
            mcp_tools: list[StructuredTool] = []
            cloud_servers = configured_cloud_servers()
            local_servers = dict(LOCAL_MCP_SERVERS)
            if os.name == "nt":
                # Windows 已注册内置 run_command，避免再连接同一终端的 MCP。
                cloud_servers.pop("windows_terminal", None)
                local_servers.pop("windows_terminal", None)
            if local_servers or cloud_servers:
                connections = await stack.enter_async_context(connect_servers(
                    local_servers=local_servers, cloud_servers=cloud_servers,
                ))
                mcp_tools = connections.tools
                for name, error in connections.errors.items():
                    ui.show_error(f"MCP Server {name} 不可用：{error}")
            session = await select_session(session_store, ui)
            if session is not None:
                logger.info("会话已就绪", extra={
                    "event": "newchat.session.ready", "session_id": session.thread_id,
                })
            model = create_langchain_model(config)
            memory_queue: asyncio.Queue[MemoryTurn] = asyncio.Queue()
            memory_middleware = MemorySaveMiddleware(memory_queue)
            memory_worker = MemoryWorker(
                memory_queue, create_langchain_model(config), config.model,
            )
            stack.push_async_callback(memory_worker.close)
            memory_worker.start()
            agent = create_new_chat_agent(
                config, checkpointer=checkpointer, model=model,
                memory_middleware=memory_middleware,
                mcp_tools=mcp_tools,
            )
            yield ChatResources(config, model, agent, ui, session_store, session, memory_worker)
    finally:
        logger.info("新版会话资源已关闭", extra={"event": "newchat.closed"})
        await asyncio.to_thread(shutdown_logging)


async def run() -> None:
    """新的聊天主入口。"""
    async with open_chat_agent() as resources:
        await run_chat_loop(
            agent=resources.agent, model=resources.model, config=resources.config,
            ui=resources.ui, session_store=resources.session_store,
            session=resources.session, memory_worker=resources.memory_worker,
        )


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()
