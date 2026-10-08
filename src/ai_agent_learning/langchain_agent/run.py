"""聊天主流程：统一管理资源，创建 Agent，再进入对话循环。"""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool

from ai_agent_learning.config import APPConfig, load_app_config, use_app_config
from ai_agent_learning.config.plugins import configured_mcp_servers
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
from ai_agent_learning.mcp_client import connect_servers
from ai_agent_learning.model_config import ModelConfig
from ai_agent_learning.qdrant_server import QdrantServer
from ai_agent_learning.ui.terminal import TerminalUI

logger = logging.getLogger(__name__)


@dataclass
class ChatResources:
    config: ModelConfig
    model: Any
    agent: Any
    ui: TerminalUI
    session_store: PostgresSessionStore
    session: Any
    memory_worker: MemoryWorker
    app_config: APPConfig


@asynccontextmanager
async def open_chat_agent():
    """对话期间保持资源可用，退出时关闭数据库连接和后台资源。"""
    database_url = get_database_url()
    await asyncio.to_thread(migrate_database, database_url)
    await asyncio.to_thread(configure_logging, database_url)
    try:
        logger.info("新版会话启动", extra={"event": "newchat.started"})
        app_config = load_app_config()
        config = app_config.active_model()
        async with AsyncExitStack() as stack:
            stack.enter_context(use_app_config(app_config))
            server = QdrantServer()
            stack.push_async_callback(server.close)
            await server.start()
            session_store = await stack.enter_async_context(open_session_store(database_url))
            checkpointer = await stack.enter_async_context(open_checkpointer(database_url))
            ui = TerminalUI()
            mcp_tools: list[StructuredTool] = []
            local_servers, cloud_servers = configured_mcp_servers(app_config)
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
                mcp_tools=mcp_tools, app_config=app_config,
            )
            yield ChatResources(config, model, agent, ui, session_store, session, memory_worker, app_config)
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
