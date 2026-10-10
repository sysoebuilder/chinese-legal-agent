"""使用 LangChain 模型执行原有长期记忆整理流程。"""

import asyncio
import logging
import time
from contextlib import suppress

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from chinese_legal_agent.langchain_agent.middleware.memory import MemoryTurn
from chinese_legal_agent.memory.prompts import (
    build_memory_decisions_instructions,
    build_memory_instructions,
)
from chinese_legal_agent.memory.schema import (
    MemoryCandidate,
    MemoryCandidateBatch,
    MemoryDecisionBatch,
    StoredMemory,
)
from chinese_legal_agent.memory.service import MemoryService
from chinese_legal_agent.schema import Message, ModelRequest

logger = logging.getLogger(__name__)


class LangChainMemoryClient:
    """用非流式 LangChain 模型生成候选记忆和批量决策。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    async def create_memory_candidates(
        self,
        request: ModelRequest,
    ) -> MemoryCandidateBatch:
        instructions = build_memory_instructions()
        messages = [
            SystemMessage(content=instructions.content),
            *[self._convert_message(message) for message in request.messages],
        ]
        for attempt in range(3):
            attempt_messages = list(messages)
            if attempt:
                attempt_messages.append(
                    SystemMessage(
                        content=(
                            "上一次响应为空。请输出完整 JSON 对象；"
                            '没有候选时输出 {"memories":[]}。'
                        )
                    )
                )
            response = await self._model.ainvoke(
                attempt_messages,
                stream=False,
                response_format={"type": "json_object"},
            )
            content = response.text.strip()
            if content:
                return MemoryCandidateBatch.model_validate_json(content)
        raise ValueError("模型连续三次没有返回候选记忆")

    async def create_memory_decisions(
        self,
        model: str,
        candidates_with_memories: list[
            tuple[MemoryCandidate, list[StoredMemory]]
        ],
    ) -> MemoryDecisionBatch:
        del model
        instructions = build_memory_decisions_instructions(
            candidates_with_memories
        )
        response = await self._model.ainvoke(
            [
                SystemMessage(content=instructions.content),
                HumanMessage(content="请生成整批候选记忆的 JSON 决策。"),
            ],
            stream=False,
            response_format={"type": "json_object"},
        )
        content = response.text.strip()
        if not content:
            raise ValueError("模型没有返回批量记忆决定")
        return MemoryDecisionBatch.model_validate_json(content)

    @staticmethod
    def _convert_message(message: Message) -> HumanMessage | AIMessage | SystemMessage:
        message_types = {
            "user": HumanMessage,
            "assistant": AIMessage,
            "system": SystemMessage,
        }
        return message_types[message.role](content=message.content)


class MemoryWorker:
    """顺序消费记忆任务，与主 Agent 并行运行。"""

    def __init__(
        self,
        queue: asyncio.Queue[MemoryTurn],
        model: BaseChatModel,
        model_name: str,
    ) -> None:
        self._queue = queue
        self._model_name = model_name
        self._service = MemoryService(LangChainMemoryClient(model))
        self._task: asyncio.Task[None] | None = None
        self._processing = False

    @property
    def is_idle(self) -> bool:
        """队列为空且 Worker 没有正在处理的记忆任务。"""
        return self._queue.empty() and not self._processing

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("记忆 Worker 已经启动")
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        task = self._task
        if task is None:
            return
        await self.wait_until_idle()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None
        await self._service.close()

    async def wait_until_idle(self) -> None:
        """等待所有已入队任务处理完成。"""
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            turn = await self._queue.get()
            self._processing = True
            started_at = time.perf_counter()
            try:
                decisions = await self._service.save_memories(
                    ModelRequest(
                        model=self._model_name,
                        messages=[
                            Message(role="user", content=turn.user_content),
                            Message(
                                role="assistant",
                                content=turn.assistant_content,
                            ),
                        ],
                    )
                )
                logger.info(
                    "本轮对话记忆整理完成",
                    extra={
                        "event": "memory.turn.completed",
                        "decision_count": len(decisions),
                        "trace_id": turn.trace_id,
                        "duration_ms": round(
                            (time.perf_counter() - started_at) * 1000,
                            2,
                        ),
                    },
                )
            except Exception:
                logger.exception(
                    "本轮对话记忆整理失败",
                    extra={
                        "event": "memory.turn.failed",
                        "trace_id": turn.trace_id,
                        "duration_ms": round(
                            (time.perf_counter() - started_at) * 1000,
                            2,
                        ),
                    },
                )
            finally:
                self._processing = False
                self._queue.task_done()
