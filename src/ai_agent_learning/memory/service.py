"""编排长期记忆流程，当前只负责生成候选记忆。"""

import json
from typing import Any, Protocol

from ai_agent_learning.memory.decision import (
    MemoryDecisionClient,
    MemoryDecisionMaker,
)
from ai_agent_learning.memory.embedding import GlmEmbeddingClient
from ai_agent_learning.memory.schema import (
    MemoryAction,
    MemoryCandidate,
    MemoryCandidateBatch,
    MemoryDecision,
    MemoryStatus,
    StoredMemory,
    TemporalType,
)
from ai_agent_learning.memory.store import QdrantMemoryStore
from ai_agent_learning.schema import Message, ModelRequest

SIMILARITY_THRESHOLD = 0.7
SIMILARITY_LIMIT = 10


class MemoryCandidateClient(Protocol):
    async def create_memory_candidates(self, request: ModelRequest) -> Any:
        """从模型请求中创建尚未进入记忆库的候选记忆。"""
        ...


class MemoryClient(MemoryCandidateClient, MemoryDecisionClient, Protocol):
    """MemoryService 使用的模型能力。"""


class MemoryService:
    """编排一次对话结束后的长期记忆处理流程。"""

    def __init__(
        self,
        client: MemoryClient,
        *,
        embedding: GlmEmbeddingClient | None = None,
        store: QdrantMemoryStore | None = None,
    ) -> None:
        self._client = client
        self._embedding = embedding
        self._store = store
        self._decision_maker = MemoryDecisionMaker(client)

    async def save_memories(
        self,
        request: ModelRequest,
    ) -> list[MemoryDecision]:
        """提取本次对话记忆，逐条决定并执行数据库操作。"""
        embedding, store = self._ensure_resources()
        await store.initialize()
        batch = await self._create_candidates(request)
        vectors = await embedding.embed(
            [candidate.content for candidate in batch.memories]
        )

        candidates_with_memories: list[
            tuple[MemoryCandidate, list[StoredMemory]]
        ] = []
        for candidate, vector in zip(batch.memories, vectors, strict=True):
            old_memories = await self._find_related_memories(candidate, vector)
            candidates_with_memories.append((candidate, old_memories))

        decisions = await self._decision_maker.decide_many(
            request.model,
            candidates_with_memories,
        )
        for candidate, vector, decision in zip(
            batch.memories, vectors, decisions, strict=True
        ):
            await self._apply_decision(candidate, vector, decision)
        return decisions

    async def close(self) -> None:
        """关闭向量模型客户端和数据库服务连接。"""
        try:
            if self._embedding is not None:
                await self._embedding.close()
        finally:
            if self._store is not None:
                await self._store.close()

    def _ensure_resources(self) -> tuple[GlmEmbeddingClient, QdrantMemoryStore]:
        if self._embedding is None:
            self._embedding = GlmEmbeddingClient()
        if self._store is None:
            self._store = QdrantMemoryStore(vector_size=self._embedding.dimensions)
        return self._embedding, self._store

    async def _create_candidates(self, request: ModelRequest) -> MemoryCandidateBatch:
        """提取候选记忆，并执行格式、依据和时效类型过滤。"""
        content_messages = [
            message for message in request.messages if not self._is_tool_event(message)
        ]
        content_request = ModelRequest(
            model=request.model,
            messages=content_messages,
        )
        raw_batch = await self._client.create_memory_candidates(content_request)
        batch = MemoryCandidateBatch.model_validate(raw_batch)
        evidence_messages = [
            self._normalize(message.content)
            for message in content_messages
            if message.role in {"user", "assistant"}
        ]
        memories = [
            candidate
            for candidate in batch.memories
            if (
                candidate.temporal_type is not TemporalType.TEMPORARY
                or candidate.status is not MemoryStatus.ACTIVE
            )
            and self._has_evidence(candidate, evidence_messages)
        ]
        return MemoryCandidateBatch(memories=memories)

    async def _find_related_memories(
        self,
        candidate: MemoryCandidate,
        vector: list[float],
    ) -> list[StoredMemory]:
        """全局合并相同 key 与语义相似的旧记忆，并按 ID 去重。"""
        _, store = self._ensure_resources()
        key_matches = await store.find_by_key(candidate.key)
        semantic_matches = await store.search(
            vector,
            limit=SIMILARITY_LIMIT,
            score_threshold=SIMILARITY_THRESHOLD,
        )
        memories_by_id = {memory.id: memory for memory in key_matches}
        for result in semantic_matches:
            memories_by_id.setdefault(result.memory.id, result.memory)
        return list(memories_by_id.values())

    async def _apply_decision(
        self,
        candidate: MemoryCandidate,
        vector: list[float],
        decision: MemoryDecision,
    ) -> None:
        """执行决策；REPLACE 成功写入新记忆后永久删除旧记忆。"""
        embedding, store = self._ensure_resources()
        if decision.action is MemoryAction.ADD:
            await store.add(candidate, vector)
            return
        if decision.action is MemoryAction.CONFIRM:
            confirmed = await store.confirm(decision.target_ids[0])
            if confirmed is None:
                raise ValueError("需要确认的旧记忆不存在")
            return
        if decision.action is MemoryAction.REPLACE:
            replacement = decision.replacement
            if replacement is None:
                raise ValueError("REPLACE 决策缺少替换内容")
            replacement_vectors = await embedding.embed([replacement.content])
            replacement_vector = replacement_vectors[0]
            await store.add(replacement, replacement_vector)
            for memory_id in decision.target_ids:
                deleted = await store.delete(memory_id)
                if not deleted:
                    raise ValueError("需要替换的旧记忆不存在")
            return
        if decision.action is not MemoryAction.IGNORE:
            raise ValueError(f"不支持的记忆动作：{decision.action}")

    @classmethod
    def _has_evidence(
        cls,
        candidate: MemoryCandidate,
        evidence_messages: list[str],
    ) -> bool:
        """证据必须出自一条用户或助手消息；语义归属由提取提示词约束。"""
        evidence = cls._normalize(candidate.evidence).strip("\"'“”‘’")
        return bool(evidence) and any(
            evidence in message for message in evidence_messages
        )

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(value.replace("\u200b", "").split()).casefold()

    @staticmethod
    def _is_tool_event(message: Message) -> bool:
        """识别会话中序列化保存的工具调用和工具结果。"""
        if message.role != "assistant" or not message.content.lstrip().startswith("{"):
            return False
        try:
            payload = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(payload, dict) and payload.get("event") in {
            "agent.tool_call",
            "agent.tool_result",
        }
