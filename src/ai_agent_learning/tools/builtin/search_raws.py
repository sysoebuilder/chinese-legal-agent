"""两路召回的中国法规检索工具。"""

import asyncio
import os
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from ai_agent_learning.law_index import LawBm25Index
from ai_agent_learning.memory.embedding import GlmEmbeddingClient
from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL
from ai_agent_learning.tools.contracts import RiskLevel, ToolSpec

SPEC = ToolSpec(
    name="search_raws",
    description=(
        "从中国法律法规与规章知识库中检索相关资料。"
        "将问题拆成互不重复的法律争议点；关键词保持中性，案件事实不得预设法律结论。"
        "返回结果可能包含有效、失效、尚未生效或效力状态未知的法规。"
        "必须根据用户问题对应的时间点检查公布日期、生效日期、失效日期和效力状态；"
        "用户未指定时间时，默认按当前日期判断。"
        "时间字段缺失或无法确认效力时，不得将该结果表述为现行有效依据，"
        "应明确说明需要进一步核实。"
        "返回结果也可能包含地方性法规、地方政府规章或仅适用于特定地区的规范，"
        "不得仅因检索到地方规定就假设其适用于用户。"
        "应根据行为发生地、当事人住所地、合同履行地、不动产所在地、"
        "用人单位所在地或争议处理地等案件相关地域判断适用范围。"
        "如果回答必须依赖地方规定，但现有信息不足以确定适用地区，"
        "应先询问用户相关地域信息，不得直接引用某一地区的规定作为结论；"
        "在用户补充信息前，可以说明全国性法律的一般规则，并提示地方规定可能影响最终结论。"
    ),
    risk_level=RiskLevel.LOW,
    input_schema={
        "type": "object",
        "properties": {
            "retrieval_cases": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "issue": {"type": "string", "minLength": 1},
                        "title_keywords": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                        },
                        "content_keywords": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "minLength": 1},
                        },
                        "case_facts": {"type": "string", "minLength": 1},
                    },
                    "required": [
                        "issue",
                        "title_keywords",
                        "content_keywords",
                        "case_facts",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["retrieval_cases"],
        "additionalProperties": False,
    },
    has_side_effects=False,
    supports_idempotent_retry=True,
    manages_own_timeout=False,
)


async def handler(retrieval_cases: list[dict[str, Any]]) -> dict[str, Any]:
    index = await asyncio.to_thread(LawBm25Index)
    embedding = GlmEmbeddingClient(dimensions=1024)
    store = AsyncQdrantClient(
        url=(os.getenv("QDRANT_URL") or DEFAULT_QDRANT_URL).strip(), timeout=60
    )
    try:
        vectors = await embedding.embed(
            [case["case_facts"] for case in retrieval_cases]
        )
        output = []
        for case, vector in zip(retrieval_cases, vectors, strict=True):
            title_ids = await asyncio.to_thread(
                index.title_candidates, case["title_keywords"], 50
            )
            title_rows = (
                await store.retrieve(
                    "npc_laws", ids=title_ids, with_payload=True, with_vectors=False
                )
                if title_ids
                else []
            )
            by_id = {str(row.id): row for row in title_rows}
            unique_ids, seen_titles = [], set()
            for point_id in title_ids:
                row = by_id.get(point_id)
                if row is None:
                    continue
                payload = row.payload or {}
                title = str(payload.get("title") or "").strip()
                if title not in seen_titles:
                    seen_titles.add(title)
                    unique_ids.append(point_id)
            lexical = await asyncio.to_thread(
                index.rerank_content, case["content_keywords"], unique_ids, 10
            )
            semantic = await store.query_points(
                "npc_laws",
                query=vector,
                using="text",
                limit=10,
                score_threshold=0.6,
                with_payload=True,
                search_params=models.SearchParams(exact=False),
            )
            merged: dict[str, dict[str, Any]] = {}
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
            for point in semantic.points:
                point_id = str(point.id)
                item = merged.setdefault(
                    point_id,
                    {
                        "id": point_id,
                        "payload": point.payload,
                        "bm25_score": None,
                        "vector_score": point.score,
                    },
                )
                item["vector_score"] = point.score
            output.append({"issue": case["issue"], "laws": list(merged.values())})
        return {"results": output}
    finally:
        await store.close()
        await embedding.close()
