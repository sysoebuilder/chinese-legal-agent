"""使用 Qdrant 服务端持久化、查询和管理长期记忆。"""

import asyncio
import math
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Self
from uuid import uuid4

from qdrant_client import AsyncQdrantClient, models

from chinese_legal_agent.memory.schema import (
    MemoryCandidate,
    MemorySearchResult,
    MemoryStatus,
    MemoryType,
    StoredMemory,
)
from chinese_legal_agent.qdrant_config import DEFAULT_QDRANT_URL

DEFAULT_COLLECTION_NAME = "memories"
DEFAULT_VECTOR_SIZE = 512
# 服务端允许的最低阈值（KB）；很小的候选集仍可能采用扫描。
FULL_SCAN_THRESHOLD_KB = 10


class QdrantMemoryStore:
    """单用户长期记忆的服务端向量存储。"""

    def __init__(
        self,
        url: str | None = None,
        *,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        vector_size: int = DEFAULT_VECTOR_SIZE,
    ) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size 必须大于 0")
        if not collection_name.strip():
            raise ValueError("collection_name 不能为空")

        server_url = (url or os.getenv("QDRANT_URL") or DEFAULT_QDRANT_URL).strip()
        if not server_url:
            raise ValueError("QDRANT_URL 不能为空")
        self._client = AsyncQdrantClient(url=server_url, timeout=30)
        self._collection_name = collection_name
        self._vector_size = vector_size
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    @property
    def collection_name(self) -> str:
        return self._collection_name

    @property
    def vector_size(self) -> int:
        return self._vector_size

    async def initialize(self) -> None:
        """创建集合；集合已存在时校验向量配置。"""
        if self._initialized:
            return

        async with self._initialization_lock:
            if self._initialized:
                return
            if await self._client.collection_exists(self._collection_name):
                info = await self._client.get_collection(self._collection_name)
                payload_schema = info.payload_schema
                vector_config = info.config.params.vectors
                if not isinstance(vector_config, models.VectorParams):
                    raise ValueError("记忆集合必须使用单一向量配置")
                if vector_config.size != self._vector_size:
                    raise ValueError(
                        "记忆集合向量维度不一致："
                        f"数据库为 {vector_config.size}，配置为 {self._vector_size}"
                    )
                if vector_config.distance != models.Distance.COSINE:
                    raise ValueError("记忆集合必须使用 Cosine 距离")
                hnsw = info.config.hnsw_config
                optimizer = info.config.optimizer_config
                if (
                    hnsw.m != 16
                    or hnsw.ef_construct != 128
                    or hnsw.full_scan_threshold != FULL_SCAN_THRESHOLD_KB
                    or optimizer.indexing_threshold != 1
                ):
                    await self._client.update_collection(
                        collection_name=self._collection_name,
                        hnsw_config=models.HnswConfigDiff(
                            m=16,
                            ef_construct=128,
                            full_scan_threshold=FULL_SCAN_THRESHOLD_KB,
                        ),
                        optimizers_config=models.OptimizersConfigDiff(
                            indexing_threshold=1
                        ),
                    )
            else:
                payload_schema = {}
                await self._client.create_collection(
                    collection_name=self._collection_name,
                    vectors_config=models.VectorParams(
                        size=self._vector_size,
                        distance=models.Distance.COSINE,
                    ),
                    hnsw_config=models.HnswConfigDiff(
                        m=16,
                        ef_construct=128,
                        full_scan_threshold=FULL_SCAN_THRESHOLD_KB,
                    ),
                    optimizers_config=models.OptimizersConfigDiff(indexing_threshold=1),
                )
            for field_name in ("key", "status"):
                existing = payload_schema.get(field_name)
                if existing is not None:
                    if existing.data_type != models.PayloadSchemaType.KEYWORD:
                        raise ValueError(f"{field_name} 必须使用 KEYWORD payload 索引")
                    continue
                await self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name=field_name,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=True,
                )
            self._initialized = True

    async def add(
        self,
        candidate: MemoryCandidate,
        vector: Sequence[float],
        *,
        memory_id: str | None = None,
    ) -> StoredMemory:
        """把经过决策的候选记忆作为正式记忆写入数据库。"""
        now = datetime.now(UTC)
        memory = StoredMemory(
            **candidate.model_dump(),
            id=memory_id or str(uuid4()),
            created_at=now,
            updated_at=now,
            last_confirmed_at=now,
        )
        await self.upsert(memory, vector)
        return memory

    async def upsert(
        self,
        memory: StoredMemory,
        vector: Sequence[float],
    ) -> None:
        """新增或完整覆盖一条正式记忆及其 content 向量。"""
        await self.initialize()
        normalized_vector = self._validate_vector(vector)
        await self._client.upsert(
            collection_name=self._collection_name,
            points=[
                models.PointStruct(
                    id=memory.id,
                    vector=normalized_vector,
                    payload=memory.model_dump(mode="json"),
                )
            ],
            wait=True,
        )

    async def get(self, memory_id: str) -> StoredMemory | None:
        """按 ID 读取一条正式记忆。"""
        await self.initialize()
        records = await self._client.retrieve(
            collection_name=self._collection_name,
            ids=[memory_id],
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            return None
        return self._memory_from_payload(records[0].payload)

    async def search(
        self,
        vector: Sequence[float],
        *,
        limit: int = 5,
        score_threshold: float | None = None,
        scope: str | None = None,
        memory_type: MemoryType | None = None,
        status: MemoryStatus = MemoryStatus.ACTIVE,
    ) -> list[MemorySearchResult]:
        """按 content 向量检索相似的正式记忆。"""
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        await self.initialize()
        response = await self._client.query_points(
            collection_name=self._collection_name,
            query=self._validate_vector(vector),
            search_params=models.SearchParams(exact=True),
            query_filter=self._build_filter(
                scope=scope,
                memory_type=memory_type,
                status=status,
            ),
            limit=limit,
            score_threshold=score_threshold,
            with_payload=True,
            with_vectors=False,
        )
        return [
            MemorySearchResult(
                memory=self._memory_from_payload(point.payload),
                score=point.score,
            )
            for point in response.points
        ]

    async def find_by_key(
        self,
        key: str,
        *,
        status: MemoryStatus = MemoryStatus.ACTIVE,
        limit: int = 10,
    ) -> list[StoredMemory]:
        """按结构化 key 精确查找记忆，用于更新和去重。"""
        if not key.strip():
            raise ValueError("key 不能为空")
        if limit <= 0:
            raise ValueError("limit 必须大于 0")
        await self.initialize()
        key = key.strip()
        records, _ = await self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=models.Filter(
                must=[
                    self._match_condition("key", key),
                    self._match_condition("status", status.value),
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [self._memory_from_payload(record.payload) for record in records]

    async def list_memories(
        self,
        *,
        limit: int = 20,
        cursor: str | None = None,
        status: MemoryStatus | None = None,
    ) -> tuple[list[StoredMemory], str | None]:
        """只读分页浏览现有记忆；不创建或更新 Qdrant 集合。"""
        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1 到 50 之间")
        if cursor is not None and not cursor.strip():
            raise ValueError("cursor 不能为空字符串")

        scroll_filter = None
        if status is not None:
            scroll_filter = models.Filter(
                must=[self._match_condition("status", status.value)]
            )
        offset: int | str | None = cursor
        if cursor is not None and cursor.isdecimal():
            offset = int(cursor)
        records, next_offset = await self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=scroll_filter,
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        return (
            [self._memory_from_payload(record.payload) for record in records],
            str(next_offset) if next_offset is not None else None,
        )

    async def set_status(
        self,
        memory_id: str,
        status: MemoryStatus,
    ) -> StoredMemory | None:
        """更新记忆状态，并同步更新时间。"""
        memory = await self.get(memory_id)
        if memory is None:
            return None
        now = datetime.now(UTC)
        await self._client.set_payload(
            collection_name=self._collection_name,
            points=[memory_id],
            payload={"status": status.value, "updated_at": now.isoformat()},
            wait=True,
        )
        return memory.model_copy(update={"status": status, "updated_at": now})

    async def confirm(self, memory_id: str) -> StoredMemory | None:
        """记录一次重复确认，不修改正文和向量。"""
        memory = await self.get(memory_id)
        if memory is None:
            return None
        now = datetime.now(UTC)
        confirmation_count = memory.confirmation_count + 1
        await self._client.set_payload(
            collection_name=self._collection_name,
            points=[memory_id],
            payload={
                "updated_at": now.isoformat(),
                "last_confirmed_at": now.isoformat(),
                "confirmation_count": confirmation_count,
            },
            wait=True,
        )
        return memory.model_copy(
            update={
                "updated_at": now,
                "last_confirmed_at": now,
                "confirmation_count": confirmation_count,
            }
        )

    async def delete(self, memory_id: str) -> bool:
        """永久删除一条记忆；返回该记忆原先是否存在。"""
        if await self.get(memory_id) is None:
            return False
        result = await self._client.delete(
            collection_name=self._collection_name,
            points_selector=models.PointIdsList(points=[memory_id]),
            wait=True,
        )
        if result.status is not models.UpdateStatus.COMPLETED:
            raise RuntimeError(f"记忆删除未确认完成：{result.status.value}")
        return True

    async def count(
        self,
        *,
        status: MemoryStatus | None = None,
    ) -> int:
        """统计记忆数量，可按状态过滤。"""
        await self.initialize()
        count_filter = None
        if status is not None:
            count_filter = models.Filter(
                must=[self._match_condition("status", status.value)]
            )
        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=count_filter,
            exact=True,
        )
        return result.count

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def _validate_vector(self, vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        if len(values) != self._vector_size:
            raise ValueError(
                f"向量维度必须为 {self._vector_size}，实际为 {len(values)}"
            )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("向量不能包含 NaN 或无穷值")
        return values

    @staticmethod
    def _match_condition(key: str, value: str) -> models.FieldCondition:
        return models.FieldCondition(
            key=key,
            match=models.MatchValue(value=value),
        )

    @classmethod
    def _build_filter(
        cls,
        *,
        scope: str | None,
        memory_type: MemoryType | None,
        status: MemoryStatus,
    ) -> models.Filter:
        conditions: list[models.Condition] = [
            cls._match_condition("status", status.value)
        ]
        if memory_type is not None:
            conditions.append(cls._match_condition("type", memory_type.value))
        return models.Filter(must=conditions)

    @staticmethod
    def _memory_from_payload(payload: dict[str, Any] | None) -> StoredMemory:
        if payload is None:
            raise ValueError("Qdrant 记忆记录缺少 payload")
        return StoredMemory.model_validate(payload)
