"""判断候选记忆与相关旧记忆之间的处理动作。"""

from typing import Any, Protocol

from ai_agent_learning.memory.schema import (
    MemoryAction,
    MemoryCandidate,
    MemoryDecision,
    MemoryDecisionBatch,
    StoredMemory,
)


class MemoryDecisionClient(Protocol):
    async def create_memory_decisions(
        self,
        model: str,
        candidates_with_memories: list[
            tuple[MemoryCandidate, list[StoredMemory]]
        ],
    ) -> Any: ...


class MemoryDecisionMaker:
    """调用模型生成决定，并校验决定只能引用已提供的旧记忆。"""

    def __init__(self, client: MemoryDecisionClient) -> None:
        self._client = client

    async def decide_many(
        self,
        model: str,
        candidates_with_memories: list[
            tuple[MemoryCandidate, list[StoredMemory]]
        ],
    ) -> list[MemoryDecision]:
        if not candidates_with_memories:
            return []

        raw_batch = await self._client.create_memory_decisions(
            model,
            candidates_with_memories,
        )
        batch = MemoryDecisionBatch.model_validate(raw_batch)
        decisions_by_index = {
            item.candidate_index: item.decision for item in batch.decisions
        }
        expected_indexes = set(range(len(candidates_with_memories)))
        if len(decisions_by_index) != len(batch.decisions):
            raise ValueError("批量记忆决定包含重复的候选索引")
        if set(decisions_by_index) != expected_indexes:
            raise ValueError("批量记忆决定的候选索引不完整")

        decisions: list[MemoryDecision] = []
        for index, (candidate, old_memories) in enumerate(candidates_with_memories):
            decision = decisions_by_index[index]
            self._validate_decision(candidate, old_memories, decision)
            decisions.append(decision)
        return decisions

    @staticmethod
    def _validate_decision(
        candidate: MemoryCandidate,
        old_memories: list[StoredMemory],
        decision: MemoryDecision,
    ) -> None:
        allowed_ids = {memory.id for memory in old_memories}
        unknown_ids = set(decision.target_ids) - allowed_ids
        if unknown_ids:
            raise ValueError("记忆决定引用了未提供的旧记忆 ID")
        if decision.action is MemoryAction.CONFIRM:
            target = next(
                memory for memory in old_memories if memory.id == decision.target_ids[0]
            )
            if target.status is not candidate.status:
                raise ValueError("CONFIRM 不能更新记忆状态，状态变化必须使用 REPLACE")

    async def decide(
        self,
        model: str,
        candidate: MemoryCandidate,
        old_memories: list[StoredMemory],
    ) -> MemoryDecision:
        if not old_memories:
            return MemoryDecision(
                action=MemoryAction.ADD,
                reason="没有找到相关旧记忆",
            )

        return (
            await self.decide_many(model, [(candidate, old_memories)])
        )[0]
