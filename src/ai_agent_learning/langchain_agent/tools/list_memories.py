"""list_memories 工具实现与 LangChain 注册。"""

from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.langchain_agent.tools.risk import RiskLevel
from ai_agent_learning.memory.schema import MemoryStatus
from ai_agent_learning.memory.store import QdrantMemoryStore

TOOL_DESCRIPTION = "分页浏览用户的长期记忆。仅在用户明确要求查看或审查记忆库时调用；返回完整记忆记录和下一页 cursor。默认包含所有状态。"
TOOL_METADATA = {
    "risk_level": RiskLevel.LOW.value,
    "has_side_effects": False,
    "supports_idempotent_retry": False,
    "manages_own_timeout": False,
}


async def handler(
    limit: int = 20,
    cursor: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """读取一页记忆，不调用向量化模型。"""
    parsed_status = MemoryStatus(status) if status is not None else None
    store = QdrantMemoryStore()
    try:
        memories, next_cursor = await store.list_memories(
            limit=limit,
            cursor=cursor,
            status=parsed_status,
        )
        return {
            "memories": [memory.model_dump(mode="json") for memory in memories],
            "next_cursor": next_cursor,
        }
    finally:
        await store.close()


class ListMemoriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1)
    status: Literal["active", "superseded", "expired"] | None = None


list_memories_tool = StructuredTool.from_function(
    coroutine=handler,
    name="list_memories",
    description=TOOL_DESCRIPTION,
    args_schema=ListMemoriesInput,
    metadata=TOOL_METADATA,
)
