"""手动修改和删除记忆时的数据一致性检查。"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, call

from ai_agent_learning.langchain_agent.tools import delete_memory as delete_module
from ai_agent_learning.langchain_agent.tools import update_memory as update_module
from ai_agent_learning.memory.schema import MemoryCandidate, StoredMemory


def _stored_memory() -> StoredMemory:
    created_at = datetime.now(UTC) - timedelta(days=10)
    candidate = MemoryCandidate(
        content="旧内容",
        type="learning_state",
        key="learning_topic",
        scope="global",
        importance=0.5,
        confidence=0.6,
        temporal_type="persistent",
        status="active",
        evidence="旧依据",
    )
    return StoredMemory(
        **candidate.model_dump(),
        id="123e4567-e89b-12d3-a456-426614174000",
        created_at=created_at,
        updated_at=created_at,
        last_confirmed_at=created_at,
        confirmation_count=4,
    )


def test_update_memory_reembeds_and_replaces_all_business_fields(monkeypatch) -> None:
    original = _stored_memory()
    another = original.model_copy(update={"id": "123e4567-e89b-12d3-a456-426614174001"})
    store = AsyncMock()
    store.get.side_effect = [original, another]
    embedding = AsyncMock()
    embedding.embed.side_effect = [[[0.1] * 512], [[0.2] * 512]]
    monkeypatch.setattr(update_module, "QdrantMemoryStore", lambda: store)
    monkeypatch.setattr(update_module, "GlmEmbeddingClient", lambda: embedding)

    async def scenario() -> None:
        result = await update_module.update_memory_tool.ainvoke(
            {
                "updates": [
                    {
                        "memory_id": original.id,
                        "changes": {
                            "content": "新内容",
                            "type": "project_state",
                            "key": "project_topic",
                            "scope": "project:agent100",
                            "importance": 0.8,
                            "confidence": 0.9,
                            "temporal_type": "permanent",
                            "status": "superseded",
                            "evidence": "新依据",
                        },
                    },
                    {"memory_id": another.id, "changes": {"content": "第二条新内容"}},
                ]
            }
        )
        updated, vector = store.upsert.await_args_list[0].args
        assert vector == [0.1] * 512
        assert updated.id == original.id
        assert updated.created_at == original.created_at
        assert updated.updated_at > original.updated_at
        assert updated.last_confirmed_at == updated.updated_at
        assert updated.confirmation_count == 1
        assert updated.content == "新内容"
        assert updated.type.value == "project_state"
        assert updated.key == "project_topic"
        assert updated.scope == "project:agent100"
        assert updated.importance == 0.8
        assert updated.confidence == 0.9
        assert updated.temporal_type.value == "permanent"
        assert updated.status.value == "superseded"
        assert updated.evidence == "新依据"
        assert [memory["id"] for memory in result["memories"]] == [original.id, another.id]
        assert store.upsert.await_args_list[1].args[1] == [0.2] * 512
        assert embedding.embed.await_args_list == [
            call(["新内容"]), call(["第二条新内容"])
        ]
        store.close.assert_awaited_once()
        embedding.close.assert_awaited_once()

    asyncio.run(scenario())


def test_update_returns_error_for_system_fields(monkeypatch) -> None:
    original = _stored_memory()
    store = AsyncMock()
    store.get.return_value = original
    monkeypatch.setattr(update_module, "QdrantMemoryStore", lambda: store)

    async def scenario() -> None:
        result = await update_module.update_memory_tool.ainvoke(
            {
                "updates": [
                    {
                        "memory_id": original.id,
                        "changes": {"created_at": datetime.now(UTC).isoformat()},
                    }
                ],
            }
        )
        assert '"error"' in result
        store.upsert.assert_not_awaited()

    asyncio.run(scenario())


def test_update_missing_memory_returns_error_and_preserves_completed_results(monkeypatch) -> None:
    original = _stored_memory()
    missing_id = "123e4567-e89b-12d3-a456-426614174001"
    store = AsyncMock()
    store.get.side_effect = [original, None]
    embedding = AsyncMock()
    embedding.embed.return_value = [[0.1] * 512]
    monkeypatch.setattr(update_module, "QdrantMemoryStore", lambda: store)
    monkeypatch.setattr(update_module, "GlmEmbeddingClient", lambda: embedding)

    result = asyncio.run(update_module.update_memory_tool.ainvoke({
        "updates": [
            {"memory_id": original.id, "changes": {"content": "新内容"}},
            {"memory_id": missing_id, "changes": {"content": "不存在"}},
        ]
    }))

    assert [memory["id"] for memory in result["memories"]] == [original.id]
    assert result["error"] == f"记忆不存在：{missing_id}"
    store.upsert.assert_awaited_once()
    store.close.assert_awaited_once()


def test_delete_memory_uses_id_list(monkeypatch) -> None:
    store = AsyncMock()
    store.delete.side_effect = [True, False]
    monkeypatch.setattr(delete_module, "QdrantMemoryStore", lambda: store)
    memory_id = _stored_memory().id
    memory_ids = [memory_id, "123e4567-e89b-12d3-a456-426614174001"]

    result = asyncio.run(delete_module.delete_memory_tool.ainvoke({"memory_ids": memory_ids}))

    assert result == {"deleted_ids": [memory_ids[0]], "missing_ids": [memory_ids[1]]}
    assert store.delete.await_args_list == [call(memory_ids[0]), call(memory_ids[1])]
    store.close.assert_awaited_once()


def test_delete_memory_returns_error_after_partial_success(monkeypatch) -> None:
    store = AsyncMock()
    store.delete.side_effect = [True, RuntimeError("Qdrant unavailable")]
    monkeypatch.setattr(delete_module, "QdrantMemoryStore", lambda: store)
    memory_ids = ["123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174001"]

    result = asyncio.run(delete_module.delete_memory_tool.ainvoke({"memory_ids": memory_ids}))

    assert result == {
        "deleted_ids": [memory_ids[0]],
        "missing_ids": [],
        "error": "Qdrant unavailable",
    }
    store.close.assert_awaited_once()
