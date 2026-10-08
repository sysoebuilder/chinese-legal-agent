"""构建 worker 的 Agent 资源，不创建 TerminalUI 或读取 input()。"""

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager

import httpx

from ai_agent_learning.api.errors import WindowsTerminalUnavailable
from ai_agent_learning.config import APPConfig, load_app_config, use_app_config
from ai_agent_learning.config.plugins import configured_mcp_servers
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
from ai_agent_learning.model_config import ModelConfig
from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL


def resolve_model(public_config: dict[str, str], app_config: APPConfig | None = None) -> ModelConfig:
    """冻结提交时的模型参数，执行时从 secrets 读取 Key。"""
    app_config = app_config or load_app_config()
    key_ref = public_config.get("api_key_ref")
    if not key_ref:
        # 兼容旧队列中仅包含 provider/model/base_url 的运行。
        entry = next((entry for entry in app_config.modelconfig.models.values() if entry.provider == public_config["provider"]), None)
        key_ref = entry.api_key_ref if entry else ""
    config = ModelConfig(
        public_config["provider"], public_config["model"],
        app_config.secretsconfig.get(key_ref), public_config["base_url"],
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
    app_config = load_app_config()
    config = resolve_model(public_config, app_config)

    async with AsyncExitStack() as stack:
        stack.enter_context(use_app_config(app_config))
        saver = await stack.enter_async_context(open_checkpointer(database_url))
        tools = []
        local_servers, cloud_servers = configured_mcp_servers(app_config)
        if local_servers or cloud_servers:
            connections = await stack.enter_async_context(
                connect_servers(
                    local_servers=local_servers,
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
            app_config=app_config,
        )
        yield agent
