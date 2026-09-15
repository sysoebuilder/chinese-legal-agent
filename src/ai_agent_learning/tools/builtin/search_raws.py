from typing import Any

from ai_agent_learning.memory.embedding import GlmEmbeddingClient
from ai_agent_learning.memory.schema import MemorySearchResult
from ai_agent_learning.memory.store import QdrantMemoryStore
from ai_agent_learning.tools.contracts import RiskLevel, ToolSpec

SPEC = ToolSpec(
    name="search_raws",

    description=(
        "从中国法律法规与规章知识库中检索相关法律条文。"
        "将用户问题拆分为一个或多个相互独立的法律事实争议点进行检索。"
        "每个 retrieval_case 应描述一个独立问题，不能只是对同一问题进行重复改写。"
        "case_facts 必须保持客观，只描述已知事实，不得预设违法、合法、"
        "应当赔偿、合同无效等法律结论。"
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
                        "title_keywords": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "description": (
                                "用于法规标题检索的关键词。"
                                "不知道相关法规时可以为空，不得编造法规名称。"
                            )
                        },

                        "content_keywords": {
                            "type": "array",
                            "items": {
                                "type": "string"
                            },
                            "minItems": 1,
                            "description": (
                                "用于正文 BM25/关键词检索的中性法律术语、"
                                "行为、对象或条件。"
                            )
                        },

                        "case_facts": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "围绕当前争议点整理出的客观案件事实，"
                                "用于正文语义向量检索。"
                                "不得加入未经法律资料支持的结论。"
                            )
                        }
                    },

                    "required": [
                        "issue",
                        "title_keywords",
                        "content_keywords",
                        "case_facts"
                    ],

                    "additionalProperties": False
                }
            }
        },

        "required": ["retrieval_cases"],
        "additionalProperties": False
    },

    has_side_effects=False,
    supports_idempotent_retry=True,
    manages_own_timeout=False
)

