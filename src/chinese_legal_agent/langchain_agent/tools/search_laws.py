"""search_laws 工具实现与 LangChain 注册。"""

import asyncio
import os
from typing import Annotated, Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import AsyncQdrantClient, models

from chinese_legal_agent.langchain_agent.tools.risk import RiskLevel
from chinese_legal_agent.law_index import LawBm25Index
from chinese_legal_agent.memory.embedding import GlmEmbeddingClient
from chinese_legal_agent.qdrant_config import DEFAULT_QDRANT_URL
from chinese_legal_agent.reranker import load_reranker, rerank_laws

TOOL_DESCRIPTION = (
    "从中国法律法规与规章知识库中检索相关资料。将问题拆成互不重复的法律争议点；关键词保持中性，案件事实不得预设法律结论。返回结果可能包含有效、失"
    "效、尚未生效或效力状态未知的法规。必须根据用户问题对应的时间点检查公布日期、生效日期、失效日期和效力状态；用户未指定时间时，默认按当前日期判"
    "断。时间字段缺失或无法确认效力时，不得将该结果表述为现行有效依据，应明确说明需要进一步核实。返回结果也可能包含地方性法规、地方政府规章或仅适"
    "用于特定地区的规范，不得仅因检索到地方规定就假设其适用于用户。应根据行为发生地、当事人住所地、合同履行地、不动产所在地、用人单位所在地或争议"
    "处理地等案件相关地域判断适用范围。如果回答必须依赖地方规定，但现有信息不足以确定适用地区，应先询问用户相关地域信息，不得直接引用某一地区的规"
    "定作为结论；在用户补充信息前，可以说明全国性法律的一般规则，并提示地方规定可能影响最终结论。"
)
TOOL_METADATA = {
    "risk_level": RiskLevel.LOW.value,
    "has_side_effects": False,
    "supports_idempotent_retry": True,
    "manages_own_timeout": False,
}


async def handler(retrieval_cases: list[dict[str, Any]]) -> dict[str, Any]:
    index = await asyncio.to_thread(LawBm25Index)
    embedding = GlmEmbeddingClient(dimensions=1024)
    store = AsyncQdrantClient(
        url=(os.getenv("QDRANT_URL") or DEFAULT_QDRANT_URL).strip(), timeout=60
    )
    try:
        vector_cases = [case for case in retrieval_cases if not case["title_keywords"]]
        vectors = (
            await embedding.embed([case["case_facts"] for case in vector_cases])
            if vector_cases
            else []
        )
        vector_iter = iter(vectors)
        cases_with_vectors = [
            (case, next(vector_iter) if not case["title_keywords"] else None)
            for case in retrieval_cases
        ]
        await asyncio.to_thread(load_reranker)

        async def search_case(
            case: dict[str, Any], vector: list[float] | None
        ) -> dict[str, Any]:
            merged: dict[str, dict[str, Any]] = {}
            if vector is None:
                title_ids = await asyncio.to_thread(
                    index.title_candidates, case["title_keywords"]
                )
                lexical = await asyncio.to_thread(
                    index.rerank_content, case["content_keywords"], title_ids, 50
                )
                lexical_rows = (
                    await store.retrieve(
                        "npc_laws",
                        ids=[point_id for point_id, _ in lexical],
                        with_payload=True,
                        with_vectors=False,
                    )
                    if lexical
                    else []
                )
                lexical_payload = {str(row.id): row.payload for row in lexical_rows}
                for point_id, score in lexical:
                    merged[point_id] = {
                        "id": point_id,
                        "payload": lexical_payload.get(point_id),
                        "bm25_score": score,
                        "vector_score": None,
                    }
            else:
                semantic = await store.query_points(
                    "npc_laws",
                    query=vector,
                    using="text",
                    limit=50,
                    score_threshold=0.6,
                    with_payload=True,
                    search_params=models.SearchParams(exact=True),
                )
                for point in semantic.points:
                    point_id = str(point.id)
                    merged[point_id] = {
                        "id": point_id,
                        "payload": point.payload,
                        "bm25_score": None,
                        "vector_score": point.score,
                    }
            laws = await asyncio.to_thread(
                rerank_laws, case["case_facts"], list(merged.values())
            )
            return {"issue": case["issue"], "laws": laws}

        output = await asyncio.gather(
            *(search_case(case, vector) for case, vector in cases_with_vectors)
        )
        return {"results": output}
    finally:
        await store.close()
        await embedding.close()


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: str = Field(
        min_length=1, description="该条检索对应的独立法律争议点，简短概括。"
    )
    title_keywords: list[Annotated[str, Field(min_length=1)]] = Field(
        description="可能适用的法规名称或标题关键词，用于 BM25S 标题召回；不确定法规名称时可填空数组。"
    )
    content_keywords: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=1,
        description="法条正文中的中性法律概念、行为或条件关键词，用于筛选标题召回结果；至少一项。",
    )
    case_facts: str = Field(
        min_length=1,
        description="与该争议点有关的客观案情和待解决问题；无标题关键词时用于向量检索，所有情况都用于最终重排。不得预设法律结论。",
    )


class SearchLawsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieval_cases: list[RetrievalCase] = Field(
        min_length=1,
        max_length=5,
        description="本次要并行检索的 1 至 5 个不同法律争议点。",
    )


async def search_laws(retrieval_cases: list[RetrievalCase]) -> dict[str, Any]:
    """把 Pydantic 参数转换回旧 handler 接受的字典结构。"""
    return await handler([item.model_dump() for item in retrieval_cases])


search_laws_tool = StructuredTool.from_function(
    coroutine=search_laws,
    name="search_laws",
    description=TOOL_DESCRIPTION,
    args_schema=SearchLawsInput,
    metadata=TOOL_METADATA,
)
