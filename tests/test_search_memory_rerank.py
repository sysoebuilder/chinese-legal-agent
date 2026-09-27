"""验证多查询记忆检索的并发、去重和重排。"""

import asyncio
import threading
from types import SimpleNamespace

from ai_agent_learning.langchain_agent.tools import search_memory as builtin


class FakeMemory:
    def __init__(self, number: int) -> None:
        self.id = str(number)
        self.content = f"memory {number}"

    def model_dump(self, *, mode: str) -> dict[str, str]:
        assert mode == "json"
        return {"id": self.id, "content": self.content}


def test_search_memory_concurrent_queries_and_per_query_top_five(monkeypatch) -> None:
    memories = [FakeMemory(index) for index in range(6)]

    class FakeEmbedding:
        dimensions = 512

        async def embed(self, queries):
            assert queries == ["first", "second"]
            return [[1.0], [2.0]]

        async def close(self):
            pass

    class FakeStore:
        def __init__(self, *, vector_size):
            assert vector_size == 512
            self.active = 0
            self.peak = 0
            self.both_started = asyncio.Event()

        async def initialize(self):
            pass

        async def find_by_key(self, key, *, limit):
            assert (key, limit) == ("topic", 10)
            return [memories[0]]

        async def search(self, vector, *, limit, score_threshold):
            assert (limit, score_threshold) == (50, 0.6)
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 2:
                self.both_started.set()
            await asyncio.wait_for(self.both_started.wait(), timeout=1)
            self.active -= 1
            numbers = (1, 2, 3) if vector == [1.0] else (3, 4, 5)
            return [
                SimpleNamespace(memory=memories[number], score=0.9 if number == 3 and vector == [2.0] else 0.7)
                for number in numbers
            ]

        async def close(self):
            pass

    store = FakeStore(vector_size=512)
    rerank_barrier = threading.Barrier(2)
    reranked_queries: list[str] = []
    loaded = threading.Event()

    def fake_load_reranker():
        loaded.set()

    def fake_rerank(query, candidates):
        assert loaded.is_set()
        rerank_barrier.wait(timeout=3)
        reranked_queries.append(query)
        assert {item["memory"]["id"] for item in candidates} == (
            {"0", "1", "2", "3"} if query == "first" else {"0", "3", "4", "5"}
        )
        ranked = [
            dict(item, rerank_score=100.0 if item["memory"]["id"] == "0" else float(item["memory"]["id"]))
            for item in candidates
        ]
        return sorted(ranked, key=lambda item: item["rerank_score"], reverse=True)[:5]

    monkeypatch.setattr(builtin, "GlmEmbeddingClient", FakeEmbedding)
    monkeypatch.setattr(builtin, "QdrantMemoryStore", lambda **kwargs: store)
    monkeypatch.setattr(builtin, "load_reranker", fake_load_reranker)
    monkeypatch.setattr(builtin, "rerank_memories", fake_rerank)

    result = asyncio.run(builtin.handler([" first ", "second", "first"], key="topic"))

    assert store.peak == 2
    assert set(reranked_queries) == {"first", "second"}
    assert [item["memory"]["id"] for item in result["memories"]] == ["0", "3", "2", "1", "5", "4"]
    assert result["memories"][1]["score"] == 0.9
    assert result["memories"][0]["score"] is None
    assert all("rerank_score" in item for item in result["memories"])
    assert builtin.SearchMemoryInput.model_json_schema()["properties"]["queries"]["maxItems"] == 10


def test_ten_queries_return_each_query_top_five(monkeypatch) -> None:
    class FakeEmbedding:
        dimensions = 1

        async def embed(self, queries):
            assert len(queries) == 10
            return [[float(index)] for index in range(10)]

        async def close(self):
            pass

    class FakeStore:
        async def initialize(self):
            pass

        async def search(self, vector, *, limit, score_threshold):
            assert (limit, score_threshold) == (50, 0.6)
            offset = int(vector[0]) * 50
            return [
                SimpleNamespace(memory=FakeMemory(offset + index), score=0.9)
                for index in range(50)
            ]

        async def close(self):
            pass

    reranked: list[str] = []

    def fake_rerank(query, candidates):
        reranked.append(query)
        assert len(candidates) == 50
        return [dict(item, rerank_score=float(index)) for index, item in enumerate(candidates[:5])]

    monkeypatch.setattr(builtin, "GlmEmbeddingClient", FakeEmbedding)
    monkeypatch.setattr(builtin, "QdrantMemoryStore", lambda **kwargs: FakeStore())
    monkeypatch.setattr(builtin, "load_reranker", lambda: None)
    monkeypatch.setattr(builtin, "rerank_memories", fake_rerank)

    result = asyncio.run(builtin.handler([f"query {index}" for index in range(10)]))

    assert len(reranked) == 10
    assert len(result["memories"]) == 50
    assert len({item["memory"]["id"] for item in result["memories"]}) == 50
