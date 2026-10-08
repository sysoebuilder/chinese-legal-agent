"""从共享 checkpoint 读取对话，过滤工具消息并按完整轮次分页。"""

from collections.abc import Iterable, Iterator, Sequence
from uuid import UUID

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver

from ai_agent_learning.api.schemas import HistoryMessage, HistoryPage


def checkpoint_messages(
    saver: PostgresSaver,
    thread_id: str,
) -> Iterator[list[BaseMessage]]:
    """从新到旧读取根图快照；上下文压缩前的消息仍在旧快照里。"""
    config: RunnableConfig = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""}
    }
    before: RunnableConfig | None = None
    while True:
        checkpoints = list(saver.list(config, before=before, limit=20))
        if not checkpoints:
            return
        for checkpoint in checkpoints:
            yield checkpoint.checkpoint["channel_values"].get("messages", [])
        before = checkpoints[-1].config


def message_turns(messages: Sequence[BaseMessage]) -> list[list[HistoryMessage]]:
    turns: list[list[HistoryMessage]] = []
    current = None
    for message in messages:
        if isinstance(message, HumanMessage):
            current = None
            # 上下文总结是内部 HumanMessage，不是真实用户输入。
            if message.additional_kwargs.get("lc_source") == "summarization":
                continue
            if message.id is None:
                raise ValueError("会话消息缺少编号")
            current = [HistoryMessage(id=message.id, role="user", content=message.text)]
            turns.append(current)
        elif (
            current is not None
            and isinstance(message, AIMessage)
            and not message.tool_calls
            and message.text.strip()
        ):
            if message.id is None:
                raise ValueError("会话消息缺少编号")
            # 一轮中只保留最后一条普通回答，不返回工具执行过程。
            current[1:] = [
                HistoryMessage(
                    id=message.id,
                    role="assistant",
                    content=message.text,
                )
            ]
    return turns


def history_page(
    thread_id: str,
    snapshots: Iterable[Sequence[BaseMessage]],
    limit: int,
    before: str | None,
) -> HistoryPage:
    seen: set[str] = set()
    selected: list[list[HistoryMessage]] = []
    started = before is None
    for messages in snapshots:
        for turn in reversed(message_turns(messages)):
            user_id = turn[0].id
            if user_id in seen:
                continue
            seen.add(user_id)
            if not started:
                started = user_id == before
                continue
            selected.append(turn)
            if len(selected) > limit:
                return make_page(thread_id, selected, limit)
    if not started:
        raise ValueError("before 不是当前会话的有效用户消息编号")
    return make_page(thread_id, selected, limit)


def make_page(
    thread_id: str,
    turns: list[list[HistoryMessage]],
    limit: int,
) -> HistoryPage:
    has_more = len(turns) > limit
    page = turns[:limit]
    return HistoryPage(
        thread_id=UUID(thread_id),
        messages=[message for turn in reversed(page) for message in turn],
        has_more=has_more,
        next_before=page[-1][0].id if has_more else None,
    )
