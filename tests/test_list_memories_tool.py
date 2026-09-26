"""分页记忆工具的离线测试。"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from qdrant_client import models

from ai_agent_learning.langchain_agent.tools import ALL_TOOLS, TOOL_BY_NAME
from ai_agent_learning.langchain_agent.tools import list_memories as builtin
from ai_agent_learning.langchain_agent.tools.list_memories import list_memories_tool
from ai_agent_learning.memory.schema import MemoryCandidate, StoredMemory
from ai_agent_learning.memory.store import QdrantMemoryStore


def _memory() -> StoredMemory:
    now = datetime.now(UTC)
    candidate = MemoryCandidate(
        content="正在学习 MCP",
        type="learning_state",
        key="learning_topic",
        scope="global",
        importance=0.7,
        confidence=0.9,
        temporal_type="persistent",
        status="active",
        evidence="正在学习 MCP",
    )
    return StoredMemory(
        **candidate.model_dump(),
        id="123e4567-e89b-12d3-a456-426614174000",
        created_at=now,
        updated_at=now,
        last_confirmed_at=now,
    )


def test_store_scrolls_without_initializing_or_writing() -> None:
    async def scenario() -> None:
        store = QdrantMemoryStore()
        client = AsyncMock()
        memory = _memory()
        client.scroll.return_value = (
            [models.Record(id=memory.id, payload=memory.model_dump(mode="json"))],
            memory.id,
        )
        store._client = client

        records, cursor = await store.list_memories(limit=1, status=memory.status)

        assert records == [memory]
        assert cursor == memory.id
        assert client.scroll.call_args.kwargs["with_vectors"] is False
        assert client.scroll.call_args.kwargs["scroll_filter"] is not None
        client.scroll.return_value = ([], None)
        next_records, next_cursor = await store.list_memories(
            limit=1,
            cursor=cursor,
            status=memory.status,
        )
        assert next_records == []
        assert next_cursor is None
        assert client.scroll.call_args.kwargs["offset"] == memory.id
        client.collection_exists.assert_not_called()
        client.create_collection.assert_not_called()
        client.upsert.assert_not_called()

    asyncio.run(scenario())


def test_langchain_tool_is_registered_and_returns_page(monkeypatch) -> None:
    memory = _memory()
    fake_store = AsyncMock()
    fake_store.list_memories.return_value = ([memory], None)
    monkeypatch.setattr(builtin, "QdrantMemoryStore", lambda: fake_store)

    assert list_memories_tool in ALL_TOOLS
    assert TOOL_BY_NAME["list_memories"].metadata["has_side_effects"] is False
    result = asyncio.run(list_memories_tool.ainvoke({"limit": 5}))

    assert result["memories"][0]["id"] == memory.id
    assert result["next_cursor"] is None
    fake_store.close.assert_awaited_once()
