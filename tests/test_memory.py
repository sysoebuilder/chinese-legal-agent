import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from ai_agent_learning.memory import MemoryType, TemporalType
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
