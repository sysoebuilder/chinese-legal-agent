"""构建 worker 的 Agent 资源，不创建 TerminalUI 或读取 input()。"""

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager

import httpx

from ai_agent_learning.api.errors import WindowsTerminalUnavailable
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
from ai_agent_learning.mcp_client import connect_servers
from ai_agent_learning.model_config import (
    ModelConfig,
    load_active_model,
    load_model_choices,
)
from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL


def resolve_model(public_config: dict[str, str]) -> ModelConfig:
    """冻结提交时的模型参数，执行时在本机读取 Key，Key 不进入队列或事件。"""
    try:
        active = load_active_model()
    except SystemExit:
        active = None
    candidates = ([active] if active else []) + load_model_choices()
    source = next(
        (
            c
            for c in candidates
            if c.provider == public_config["provider"] and c.api_key
        ),
        None,
    )
    if source is None:
        raise ValueError("运行所需模型凭据缺失，请在终端配置模型")
    config = ModelConfig(
        public_config["provider"],
        public_config["model"],
        source.api_key,
        public_config["base_url"],
    )
    config.validate()
    return config


@asynccontextmanager
async def open_worker_agent(database_url: str, public_config: dict[str, str]):
    # worker 之间共享的 Qdrant 应作为独立服务运行，不能在每个任务退出时关闭。
    qdrant_url = os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        response = await client.get(f"{qdrant_url}/collections")
        response.raise_for_status()
    config = resolve_model(public_config)
    # 复用终端入口的 MCP 配置，不执行终端交互。
    from ai_agent_learning.langchain_agent.run import (
        LOCAL_MCP_SERVERS,
        configured_cloud_servers,
    )

    async with AsyncExitStack() as stack:
        saver = await stack.enter_async_context(open_checkpointer(database_url))
        tools = []
        cloud_servers = configured_cloud_servers()
        if LOCAL_MCP_SERVERS or cloud_servers:
            connections = await stack.enter_async_context(
                connect_servers(
                    local_servers=LOCAL_MCP_SERVERS,
                    cloud_servers=cloud_servers,
                )
            )
            if "windows_terminal" in connections.errors:
                raise WindowsTerminalUnavailable()
            tools = connections.tools
        model = create_langchain_model(config)
        memory_queue: asyncio.Queue[MemoryTurn] = asyncio.Queue()
        memory_worker = MemoryWorker(
            memory_queue, create_langchain_model(config), config.model
        )
        stack.push_async_callback(memory_worker.close)
        memory_worker.start()
        agent = create_new_chat_agent(
            config,
            model=model,
            checkpointer=saver,
            memory_middleware=MemorySaveMiddleware(memory_queue),
            mcp_tools=tools,
        )
        yield agent
