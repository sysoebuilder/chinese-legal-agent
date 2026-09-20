"""每轮 Agent 完成后的记忆消息采集中间件。"""

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class MemoryTurn(BaseModel):
    """一次完整对话中可供后续记忆整理使用的正文。"""

    user_content: str
    assistant_content: str
    trace_id: str | None = None


class MemorySaveMiddleware(AgentMiddleware):
    """采集本轮用户输入和最终回答，不在中间件中执行慢操作。"""

    def __init__(self, queue: asyncio.Queue[MemoryTurn]) -> None:
        super().__init__()
        self.queue = queue

    async def aafter_agent(
        self,
        state: AgentState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        messages = state["messages"]
        user_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage)
            ),
            None,
        )
        if user_index is None:
            return None

        user_content = messages[user_index].text.strip()
        final_answer = next(
            (
                message.text.strip()
                for message in reversed(messages[user_index + 1 :])
                if isinstance(message, AIMessage) and message.text.strip()
            ),
            "",
        )
        if not user_content or not final_answer:
            return None

        del runtime
        config = get_config()
        metadata = config.get("metadata") or {}
        memory_turn = MemoryTurn(
            user_content=user_content,
            assistant_content=final_answer,
            trace_id=metadata.get("trace_id"),
        )
        self.queue.put_nowait(memory_turn)
        logger.info(
            "本轮对话已加入记忆整理队列",
            extra={
                "event": "memory.turn.queued",
                "queue_size": self.queue.qsize(),
                "trace_id": memory_turn.trace_id,
            },
        )
        return None
