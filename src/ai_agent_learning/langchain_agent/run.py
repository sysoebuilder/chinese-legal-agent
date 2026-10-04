"""聊天主流程：统一管理资源，创建 Agent，再进入对话循环。"""

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from mcp import StdioServerParameters

from ai_agent_learning.langchain_agent.chat import run_chat_loop
from ai_agent_learning.langchain_agent.memory import MemoryWorker
from ai_agent_learning.langchain_agent.middleware.memory import MemorySaveMiddleware, MemoryTurn
from ai_agent_learning.langchain_agent.runtime import create_langchain_model, create_new_chat_agent
from ai_agent_learning.langchain_agent.session import SqliteSessionStore, select_sqlite_session
from ai_agent_learning.logging_config import configure_logging, shutdown_logging
from ai_agent_learning.model_config import ModelConfig, load_active_model, source_config_path
from ai_agent_learning.mcp_client import CloudServerConfig, connect_servers
from ai_agent_learning.paths import get_data_dir
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


@dataclass
class ChatResources:
    config: ModelConfig
    model: Any
    agent: Any
    ui: TerminalUI
    session_store: SqliteSessionStore
    session: Any
    memory_worker: MemoryWorker


@asynccontextmanager
async def open_chat_agent():
    """对话期间保持资源可用；退出时依次关闭记忆任务、SQLite 和 Qdrant。"""
    checkpoint_path = get_data_dir() / "langgraph-checkpoints.sqlite"
    configure_logging(checkpoint_path)
    try:
        logger.info("新版会话启动", extra={"event": "newchat.started"})
        config = load_active_model()
        load_dotenv(source_config_path(), override=False)
        async with AsyncExitStack() as stack:
            server = QdrantServer()
            stack.push_async_callback(server.close)
            await server.start()
            checkpointer = await stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(checkpoint_path))
            )
            ui = TerminalUI()
            mcp_tools: list[StructuredTool] = []
            if LOCAL_MCP_SERVERS or CLOUD_MCP_SERVERS:
                connections = await stack.enter_async_context(connect_servers(
                    local_servers=LOCAL_MCP_SERVERS, cloud_servers=CLOUD_MCP_SERVERS,
                ))
                mcp_tools = connections.tools
                for name, error in connections.errors.items():
                    ui.show_error(f"MCP Server {name} 不可用：{error}")
            session_store = SqliteSessionStore(checkpointer.conn)
            session = await select_sqlite_session(session_store, ui)
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
        shutdown_logging()


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
