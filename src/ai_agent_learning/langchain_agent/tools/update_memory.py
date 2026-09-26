"""逐条修改长期记忆并重新生成内容向量。"""

import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.langchain_agent.tools.risk import RiskLevel
from ai_agent_learning.memory.embedding import GlmEmbeddingClient
from ai_agent_learning.memory.schema import (
    MemoryCandidate,
    MemoryStatus,
    MemoryType,
    StoredMemory,
    TemporalType,
)
from ai_agent_learning.memory.store import QdrantMemoryStore

EDITABLE_FIELDS = set(MemoryCandidate.model_fields)


class MemoryChanges(BaseModel):
    """可修改的全部业务字段；未提供的字段保持原值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str | None = Field(default=None, min_length=1)
    type: MemoryType | None = None
    key: str | None = Field(default=None, min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    scope: str | None = Field(default=None, min_length=1)
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    temporal_type: TemporalType | None = None
    status: MemoryStatus | None = None
    evidence: str | None = Field(default=None, min_length=1)


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1)
    changes: MemoryChanges


class UpdateMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[MemoryUpdate] = Field(min_length=1)


async def handler(
    updates: list[dict[str, Any]],
) -> dict[str, Any]:
    """逐条更新；失败时把错误和已完成的结果交还 Agent。"""
    memories: list[dict[str, Any]] = []
    errors: list[str] = []
    store: QdrantMemoryStore | None = None
    embedding: GlmEmbeddingClient | None = None
    try:
        items = [MemoryUpdate.model_validate(item) for item in updates]
        memory_ids = [item.memory_id for item in items]
        patches = [item.changes.model_dump(exclude_unset=True) for item in items]
        if any(not patch for patch in patches):
            raise ValueError("每条记忆至少提供一个需要修改的字段")
        if any(value is None for patch in patches for value in patch.values()):
            raise ValueError("记忆业务字段不能设为 null")

        store = QdrantMemoryStore()
        for memory_id, patch in zip(memory_ids, patches, strict=True):
            previous = await store.get(memory_id)
            if previous is None:
                raise ValueError(f"记忆不存在：{memory_id}")
            candidate_data = previous.model_dump(include=EDITABLE_FIELDS)
            candidate = MemoryCandidate.model_validate({**candidate_data, **patch})
            if embedding is None:
                embedding = GlmEmbeddingClient()
            vectors = await embedding.embed([candidate.content])
            now = datetime.now(UTC)
            updated = StoredMemory.model_validate(
                {
                    **candidate.model_dump(),
                    "id": previous.id,
                    "created_at": previous.created_at,
                    "updated_at": now,
                    "last_confirmed_at": now,
                    "confirmation_count": 1,
                }
            )
            await store.upsert(updated, vectors[0])
            memories.append(updated.model_dump(mode="json"))
    except Exception as error:  # noqa: BLE001 -- 工具错误应返回 Agent，而非中断会话。
        errors.append(str(error) or type(error).__name__)
    finally:
        if store is not None:
            try:
                await store.close()
            except Exception as error:  # noqa: BLE001 -- 关闭失败也要返回给 Agent。
                errors.append(f"关闭记忆连接失败：{error}")
        if embedding is not None:
            try:
                await embedding.close()
            except Exception as error:  # noqa: BLE001 -- 关闭失败也要返回给 Agent。
                errors.append(f"关闭向量化连接失败：{error}")
    result: dict[str, Any] = {"memories": memories}
    if errors:
        result["error"] = "；".join(errors)
    return result


update_memory_tool = StructuredTool.from_function(
    coroutine=handler,
    name="update_memory",
    description=(
        "按 updates 列表逐条修改已有长期记忆的业务字段，未提供的字段保持原值；"
        "每条修改都会重新向量化内容。仅在用户明确要求修改时调用。"
        "调用前应向用户展示所有目标记忆和修改内容；中途失败会返回 error，"
        "此前已完成的修改不会撤销。"
    ),
    args_schema=UpdateMemoryInput,
    handle_validation_error=lambda error: json.dumps(
        {"error": str(error)}, ensure_ascii=False
    ),
    metadata={
        "risk_level": RiskLevel.HIGH.value,
        "has_side_effects": True,
        "supports_idempotent_retry": False,
        "manages_own_timeout": False,
    },
)
