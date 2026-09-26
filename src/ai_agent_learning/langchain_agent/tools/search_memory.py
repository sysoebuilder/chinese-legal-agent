"""search_memory 工具实现与 LangChain 注册。"""

import asyncio
from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field

from ai_agent_learning.langchain_agent.tools.risk import RiskLevel
from ai_agent_learning.reranker import rerank_memories
from ai_agent_learning.memory.embedding import GlmEmbeddingClient
from ai_agent_learning.memory.store import QdrantMemoryStore

TOOL_DESCRIPTION = (
    "按需检索与当前对话有关的用户长期记忆。在回答需要依赖以往记忆时调用。queries 应描述需要查找的信息，并结合当前对话消除‘之前’‘这个’"
    "等模糊指代；不得猜测用户未表达过的事实。不同的信息需求可拆成最多 10 条查询；各查询并发检索前 50 条并分别重排前 5 条，最后按记忆 ID 去重。"
)
TOOL_METADATA = {
    "risk_level": RiskLevel.LOW.value,
    "has_side_effects": False,
    "supports_idempotent_retry": False,
    "manages_own_timeout": False,
}


KEY_LIMIT = 10


VECTOR_LIMIT = 50


SCORE_THRESHOLD = 0.6


async def handler(
    queries: list[str],
    key: str | None = None,
) -> dict[str, Any]:
    """批量向量化；各查询并发检索并重排前五条，最后按 ID 去重。"""
    normalized_queries = list(dict.fromkeys(query.strip() for query in queries))
    normalized_queries = [query for query in normalized_queries if query]
    if not normalized_queries:
        raise ValueError("至少需要一条非空记忆查询")
    if len(normalized_queries) > 10:
        raise ValueError("记忆查询最多 10 条")

    normalized_key = key.strip() if key else None
    embedding = GlmEmbeddingClient()
    store = QdrantMemoryStore(vector_size=embedding.dimensions)
    try:
        await store.initialize()
        key_matches = []
        if normalized_key:
            key_matches = await store.find_by_key(normalized_key, limit=KEY_LIMIT)
        vectors = await embedding.embed(normalized_queries)

        async def search_query(query: str, vector: list[float]) -> list[dict[str, Any]]:
            vector_matches = await store.search(
                vector,
                limit=VECTOR_LIMIT,
                score_threshold=SCORE_THRESHOLD,
            )
            candidates: dict[str, dict[str, Any]] = {
                memory.id: {"memory": memory.model_dump(mode="json"), "score": None}
                for memory in key_matches
            }
            for result in vector_matches:
                item = candidates.setdefault(
                    result.memory.id,
                    {
                        "memory": result.memory.model_dump(mode="json"),
                        "score": result.score,
                    },
                )
                previous_score = item["score"]
                if previous_score is None or result.score > previous_score:
                    item["score"] = result.score
            return await asyncio.to_thread(rerank_memories, query, list(candidates.values()))

        ranked_by_query = await asyncio.gather(
            *(search_query(query, vector) for query, vector in zip(normalized_queries, vectors, strict=True))
        )
        unique: dict[str, dict[str, Any]] = {}
        for ranked in ranked_by_query:
            for item in ranked:
                memory_id = item["memory"]["id"]
                previous = unique.get(memory_id)
                if previous is None or (
                    item["rerank_score"], item["score"] or 0.0
                ) > (
                    previous["rerank_score"], previous["score"] or 0.0
                ):
                    unique[memory_id] = item
        return {"memories": list(unique.values())}
    finally:
        try:
            await store.close()
        finally:
            await embedding.close()


class SearchMemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[
        Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    ] = Field(
        min_length=1,
        max_length=10,
        description="用于语义检索长期记忆的查询语句，填写 1 至 10 条不同角度的查询；每条查询并发检索前 50 条、重排前 5 条，最后按记忆 ID 去重。",
    )
    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", description="可选的记忆键名过滤条件；省略时搜索全部键。")


search_memory_tool = StructuredTool.from_function(
    coroutine=handler,
    name="search_memory",
    description=TOOL_DESCRIPTION,
    args_schema=SearchMemoryInput,
    metadata=TOOL_METADATA,
)
