"""逐条修改长期记忆并重新生成内容向量。"""

import json
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from chinese_legal_agent.langchain_agent.tools.risk import RiskLevel
from chinese_legal_agent.memory.embedding import GlmEmbeddingClient
from chinese_legal_agent.memory.schema import (
    MemoryCandidate,
    MemoryStatus,
    MemoryType,
    StoredMemory,
    TemporalType,
)
from chinese_legal_agent.memory.store import QdrantMemoryStore

EDITABLE_FIELDS = set(MemoryCandidate.model_fields)


class MemoryChanges(BaseModel):
    """可修改的全部业务字段；未提供的字段保持原值。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    content: str | None = Field(default=None, min_length=1, description="新的记忆内容；省略时保持原内容。")
    type: MemoryType | None = Field(default=None, description="新的记忆类型；省略时保持原类型。")
    key: str | None = Field(default=None, min_length=1, pattern=r"^[a-z][a-z0-9_]*$", description="新的记忆键名，只能使用小写字母、数字和下划线，且以字母开头。")
    scope: str | None = Field(default=None, min_length=1, description="新的适用范围；省略时保持原范围。")
    importance: float | None = Field(default=None, ge=0, le=1, description="新的重要程度，0 至 1。")
    confidence: float | None = Field(default=None, ge=0, le=1, description="新的可信程度，0 至 1。")
    temporal_type: TemporalType | None = Field(default=None, description="新的时间属性；省略时保持原值。")
    status: MemoryStatus | None = Field(default=None, description="新的记忆状态；省略时保持原状态。")
    evidence: str | None = Field(default=None, min_length=1, description="支持这条记忆的依据；省略时保持原依据。")


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str = Field(min_length=1, description="要修改的现有记忆 ID，使用已读取记录中的真实 ID。")
    changes: MemoryChanges = Field(description="要修改的业务字段；至少填写一项，未填写的字段保持原值。")


class UpdateMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[MemoryUpdate] = Field(min_length=1, description="逐条修改记忆的请求列表，至少一条。")


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
