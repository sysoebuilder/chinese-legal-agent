import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ai_agent_learning.memory import (
    MemoryCandidate,
    MemoryDecisionMaker,
    MemoryStatus,
    MemoryType,
    StoredMemory,
    TemporalType,
)
from ai_agent_learning.model import DeepSeekClient, GlmModelClient
from ai_agent_learning.schema import Message, ModelRequest

REQUEST = ModelRequest(
    model="test-model",
    messages=[Message(role="user", content="我目前正在学习 RAG 记忆库。")],
)


def completion(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ]
    )


@pytest.mark.parametrize("client_type", [GlmModelClient, DeepSeekClient])
def test_clients_create_structured_memories_without_streaming(client_type) -> None:
    payload = {
        "memories": [
            {
                "content": "用户目前正在学习 RAG 记忆库",
                "type": "learning_state",
                "key": "current_learning_topic",
                "scope": "project:agent100",
                "importance": 0.8,
                "confidence": 0.95,
                "temporal_type": "temporary",
                "status": "active",
                "evidence": "我目前正在学习 RAG 记忆库",
            }
        ]
    }

    async def run():
        client = client_type("test-key")
        await client.close()
        create = AsyncMock(return_value=completion(json.dumps(payload)))
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        result = await client.create_memory_candidates(REQUEST)

        memory = result.memories[0]
        assert memory.type is MemoryType.LEARNING_STATE
        assert memory.temporal_type is TemporalType.TEMPORARY
        assert memory.key == "current_learning_topic"
        call = create.await_args.kwargs
        assert call["stream"] is False
        assert call["response_format"] == {"type": "json_object"}
        thinking = "enabled" if client_type is GlmModelClient else "disabled"
        assert call["extra_body"] == {"thinking": {"type": thinking}}
        assert call["messages"][0]["role"] == "system"
        assert call["messages"][1] == {
            "role": "user",
            "content": "我目前正在学习 RAG 记忆库。",
        }

    asyncio.run(run())


def test_memory_response_is_validated() -> None:
    async def run():
        client = DeepSeekClient("test-key")
        await client.close()
        create = AsyncMock(
            return_value=completion(
                json.dumps(
                    {
                        "memories": [
                            {
                                "content": "无效候选",
                                "type": "unknown",
                                "key": "Invalid Key",
                                "scope": "global",
                                "importance": 2,
                                "confidence": 1,
                                "temporal_type": "temporary",
                                "evidence": "原话",
                            }
                        ]
                    }
                )
            )
        )
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with pytest.raises(ValidationError):
            await client.create_memory_candidates(REQUEST)

    asyncio.run(run())


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


@pytest.mark.parametrize("client_type", [GlmModelClient, DeepSeekClient])
def test_clients_create_all_memory_decisions_in_one_request(client_type) -> None:
    first = memory_candidate("项目使用 Qdrant", "project_database")
    second = memory_candidate("项目使用 BM25S", "project_lexical_index")
    old = stored_memory("memory-1", first)
    payload = {
        "decisions": [
            {
                "candidate_index": 0,
                "decision": {
                    "action": "confirm",
                    "target_ids": ["memory-1"],
                    "replacement": None,
                    "reason": "内容一致",
                },
            },
            {
                "candidate_index": 1,
                "decision": {
                    "action": "add",
                    "target_ids": [],
                    "replacement": None,
                    "reason": "没有相关旧记忆",
                },
            },
        ]
    }

    async def run():
        client = client_type("test-key")
        await client.close()
        create = AsyncMock(return_value=completion(json.dumps(payload)))
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        result = await client.create_memory_decisions(
            "test-model", [(first, [old]), (second, [])]
        )

        assert len(result.decisions) == 2
        create.assert_awaited_once()
        prompt = create.await_args.kwargs["messages"][0]["content"]
        assert '"candidate_index": 0' in prompt
        assert '"candidate_index": 1' in prompt
        assert "memory-1" in prompt

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

    async def run():
        decisions = await MemoryDecisionMaker(client).decide_many(
            "test-model", [(first, [old]), (second, [])]
        )

        assert decisions[0].target_ids == ["memory-1"]
        assert decisions[1].action.value == "add"
        assert old.status is MemoryStatus.ACTIVE
        client.create_memory_decisions.assert_awaited_once()

    asyncio.run(run())
