import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chinese_legal_agent.memory.store import QdrantMemoryStore
from chinese_legal_agent.memory import (
    MemoryCandidate,
    MemoryDecisionMaker,
    MemoryStatus,
    StoredMemory,
)


def memory_candidate(content: str, key: str) -> MemoryCandidate:
    return MemoryCandidate(
        content=content,
        type="project_state",
        key=key,
        scope="project:agent100",
        importance=0.8,
        confidence=0.9,
        temporal_type="persistent",
        status="active",
        evidence=content,
    )


def stored_memory(memory_id: str, candidate: MemoryCandidate) -> StoredMemory:
    now = datetime.now(UTC)
    return StoredMemory(
        **candidate.model_dump(),
        id=memory_id,
        created_at=now,
        updated_at=now,
        last_confirmed_at=now,
    )


def test_memory_search_uses_exact_vector_query() -> None:
    store = QdrantMemoryStore(vector_size=1)
    store.initialize = AsyncMock()
    store._client.query_points = AsyncMock(return_value=SimpleNamespace(points=[]))

    async def run() -> None:
        try:
            assert await store.search([1.0], limit=50, score_threshold=0.6) == []
            kwargs = store._client.query_points.await_args.kwargs
            assert kwargs["search_params"].exact is True
            assert kwargs["limit"] == 50
            assert kwargs["score_threshold"] == 0.6
        finally:
            await store.close()

    asyncio.run(run())


def test_decision_maker_validates_and_orders_batch_results() -> None:
    first = memory_candidate("项目使用 Qdrant", "project_database")
    second = memory_candidate("项目使用 BM25S", "project_lexical_index")
    old = stored_memory("memory-1", first)
    client = SimpleNamespace(
        create_memory_decisions=AsyncMock(
            return_value={
                "decisions": [
                    {
                        "candidate_index": 1,
                        "decision": {
                            "action": "add",
                            "reason": "没有相关旧记忆",
                        },
                    },
                    {
                        "candidate_index": 0,
                        "decision": {
                            "action": "confirm",
                            "target_ids": ["memory-1"],
                            "reason": "内容一致",
                        },
                    },
                ]
            }
        )
    )

    async def run() -> None:
        decisions = await MemoryDecisionMaker(client).decide_many(
            "test-model", [(first, [old]), (second, [])]
        )

        assert decisions[0].target_ids == ["memory-1"]
        assert decisions[1].action.value == "add"
        assert old.status is MemoryStatus.ACTIVE
        client.create_memory_decisions.assert_awaited_once()

    asyncio.run(run())
