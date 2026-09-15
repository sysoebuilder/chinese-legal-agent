"""判断候选记忆与相关旧记忆之间的处理动作。"""

from typing import Any, Protocol

from ai_agent_learning.memory.schema import (
    MemoryAction,
    MemoryCandidate,
    MemoryDecision,
    StoredMemory,
)


class MemoryDecisionClient(Protocol):
    async def create_memory_decision(
        self,
        model: str,
        candidate: MemoryCandidate,
        old_memories: list[StoredMemory],
    ) -> Any: ...


class MemoryDecisionMaker:
    """调用模型生成决定，并校验决定只能引用已提供的旧记忆。"""

    def __init__(self, client: MemoryDecisionClient) -> None:
        self._client = client

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

        raw_decision = await self._client.create_memory_decision(
            model,
            candidate,
            old_memories,
        )
        decision = MemoryDecision.model_validate(raw_decision)
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
        return decision
