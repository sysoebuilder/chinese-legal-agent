"""负责将候选记忆和检索查询转换为向量。"""

import math
from collections.abc import Sequence

from openai import AsyncOpenAI

from ai_agent_learning.config.context import get_secret

DEFAULT_EMBEDDING_MODEL = "embedding-3"
DEFAULT_EMBEDDING_DIMENSIONS = 512


class GlmEmbeddingClient:
    """通过智谱 Embedding-3 将文本转换为固定维度的向量。"""

    def __init__(
        self,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS,
    ) -> None:
        api_key = get_secret("ZAI_API_KEY")
        if not api_key:
            raise ValueError("缺少 ZAI_API_KEY 配置")
        if not model.strip():
            raise ValueError("model 不能为空")
        if dimensions not in {256, 512, 1024, 2048}:
            raise ValueError("Embedding-3 维度只能是 256、512、1024 或 2048")

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """一次性转换全部文本，并按输入顺序返回向量。"""
        normalized_texts = [
            self._normalize_text(text, index) for index, text in enumerate(texts)
        ]
        if not normalized_texts:
            return []

        response = await self._client.embeddings.create(
            model=self._model,
            input=normalized_texts,
            dimensions=self._dimensions,
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(normalized_texts))):
            raise ValueError("向量模型返回的索引与输入文本不一致")
        return [self._validate_vector(item.embedding) for item in ordered]

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _normalize_text(text: str, index: int) -> str:
        if not isinstance(text, str):
            raise TypeError(f"第 {index + 1} 条向量化输入必须是字符串")
        normalized = text.strip()
        if not normalized:
            raise ValueError(f"第 {index + 1} 条向量化输入不能为空")
        return normalized

    def _validate_vector(self, vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        if len(values) != self._dimensions:
            raise ValueError(
                f"向量模型返回的维度不一致：期望 {self._dimensions}，实际 {len(values)}"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("向量模型返回了 NaN 或无穷值")
        return values
