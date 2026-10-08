"""所有会话统一使用 PostgreSQL 检查点。"""

import asyncio
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
)
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from ai_agent_learning.database import validate_database_url


def has_pending_turn(snapshot: Any) -> bool:
    """CLI 和 API 共用的断点判定；恢复时不追加用户消息。"""
    return bool(snapshot.next)


class ThreadedPostgresSaver(PostgresSaver):
    """在线程中执行官方同步实现，兼容 Windows 的异步子进程事件循环。"""

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        def read() -> list[CheckpointTuple]:
            return list(self.list(config, filter=filter, before=before, limit=limit))

        for checkpoint in await asyncio.to_thread(read):
            yield checkpoint

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self.put,
            config,
            checkpoint,
            metadata,
            new_versions,
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def aget_delta_channel_history(
        self,
        *,
        config: RunnableConfig,
        channels: Sequence[str],
    ) -> Mapping[str, DeltaChannelHistory]:
        return await asyncio.to_thread(
            self.get_delta_channel_history,
            config=config,
            channels=channels,
        )


@asynccontextmanager
async def open_checkpointer(
    database_url: str,
) -> AsyncIterator[BaseCheckpointSaver]:
    """缺少或无效的 PostgreSQL 配置直接报错，不创建本地替代数据库。"""
    validate_database_url(database_url)

    if sys.platform == "win32":
        # 保留 ProactorEventLoop，避免破坏 PowerShell 和 stdio MCP 子进程。
        manager = ThreadedPostgresSaver.from_conn_string(database_url)
        saver = await asyncio.to_thread(manager.__enter__)
        try:
            await asyncio.to_thread(saver.setup)
            yield saver
        finally:
            await asyncio.to_thread(manager.__exit__, None, None, None)
    else:
        async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
            await saver.setup()
            yield saver
