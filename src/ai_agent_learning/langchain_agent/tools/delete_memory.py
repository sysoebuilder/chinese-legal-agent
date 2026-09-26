"""按 ID 列表永久删除长期记忆。"""

import json
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.langchain_agent.tools.risk import RiskLevel
from ai_agent_learning.memory.store import QdrantMemoryStore


class DeleteMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_ids: list[str] = Field(min_length=1)


async def handler(memory_ids: list[str]) -> dict[str, Any]:
    """逐个复用单条删除方法，并报告不存在的 ID。"""
    deleted_ids: list[str] = []
    missing_ids: list[str] = []
    errors: list[str] = []
    store: QdrantMemoryStore | None = None
    try:
        if any(not memory_id.strip() for memory_id in memory_ids):
            raise ValueError("memory_ids 不能包含空 ID")
        store = QdrantMemoryStore()
        for memory_id in memory_ids:
            if await store.delete(memory_id):
                deleted_ids.append(memory_id)
            else:
                missing_ids.append(memory_id)
    except Exception as error:  # noqa: BLE001 -- 工具错误应返回 Agent，而非中断会话。
        errors.append(str(error) or type(error).__name__)
    finally:
        if store is not None:
            try:
                await store.close()
            except Exception as error:  # noqa: BLE001 -- 关闭失败也要返回给 Agent。
                errors.append(f"关闭记忆连接失败：{error}")
    result: dict[str, Any] = {"deleted_ids": deleted_ids, "missing_ids": missing_ids}
    if errors:
        result["error"] = "；".join(errors)
    return result


delete_memory_tool = StructuredTool.from_function(
    coroutine=handler,
    name="delete_memory",
    description=(
        "按 ID 列表逐条永久删除长期记忆，并报告不存在的 ID。"
        "仅在用户明确要求删除指定记忆时调用；执行前应展示目标记忆。"
        "中途失败会返回 error，已删除的记忆不可撤销。"
    ),
    args_schema=DeleteMemoryInput,
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
