"""无终端输入的 Agent 执行，保留现有 LangGraph checkpoint。"""

import asyncio
import time
from collections.abc import Iterator
from contextlib import aclosing
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command

from ai_agent_learning.api.store import RunNotFound


class RunCancelled(asyncio.CancelledError):
    """协作取消，跳过节点内部的普通错误重试。"""


class CancellationGuard(AsyncCallbackHandler):
    raise_error = True

    def __init__(self, store, run_id: str, generation: int, interval: float = 1.0):
        self.store, self.run_id, self.generation = store, run_id, generation
        self.interval = interval
        self.last_check = float("-inf")

    async def check(self, *, force: bool = False) -> None:
        if not force and time.monotonic() - self.last_check < self.interval:
            return
        row = await asyncio.to_thread(self.store.get, self.run_id)
        self.last_check = time.monotonic()
        if row["cancel_requested"] or row["generation"] != self.generation:
            raise RunCancelled()

    async def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        await self.check(force=True)

    async def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        await self.check(force=True)

    async def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        await self.check(force=True)


def pending_interrupts(snapshot) -> list[dict[str, Any]]:
    return [{"id": item.id, "value": item.value} for item in snapshot.interrupts]


def checkpoint_started(snapshot, row: dict[str, Any]) -> bool:
    return (snapshot.metadata or {}).get("run_id") == row["run_id"] or any(
        message.id == f"run:{row['run_id']}:user"
        for message in snapshot.values.get("messages", [])
    )


async def discard_cancelled_checkpoint(
    agent, store, snapshot, row
) -> list[ToolMessage]:
    """在会话锁内结束已取消运行的旧任务，保留消息与已保存的工具结果。"""
    owner_id = (snapshot.metadata or {}).get("run_id")
    if not owner_id:
        owner_id = next(
            (
                message.id[4:-5]
                for message in reversed(snapshot.values.get("messages", []))
                if isinstance(message, HumanMessage)
                and message.id
                and message.id.startswith("run:")
                and message.id.endswith(":user")
            ),
            None,
        )
    if not owner_id or owner_id == row["run_id"]:
        raise RuntimeError("无法确认会话待执行断点的运行状态，请检查恢复状态或新建会话")
    try:
        owner = await asyncio.to_thread(store.get, owner_id)
    except RunNotFound:
        raise RuntimeError(
            "无法确认会话待执行断点的运行状态，请检查恢复状态或新建会话"
        ) from None
    if owner["thread_id"] != row["thread_id"] or owner["status"] != "cancelled":
        raise RuntimeError("无法确认会话待执行断点的运行状态，请检查恢复状态或新建会话")

    # 使用旧 checkpoint 的配置，避免把新运行标记为已经开始。
    # END 只清除待执行任务，不删除历史，也不执行旧断点或批准工具。
    cleared_config = await agent.aupdate_state(snapshot.config, None, as_node=END)
    cleared = await agent.aget_state(cleared_config)
    if cleared.next or cleared.interrupts:
        raise RuntimeError("已取消运行的待执行节点尚未清理完成")
    return cancelled_tool_results(cleared)


def cancelled_tool_results(snapshot) -> list[ToolMessage]:
    """补齐停止时未保存的工具结果，使下一轮模型输入保持完整。"""
    messages = snapshot.values.get("messages", [])
    answered = {
        message.tool_call_id for message in messages if isinstance(message, ToolMessage)
    }
    missing = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["id"] in answered:
                continue
            missing.append(
                ToolMessage(
                    content="此工具调用随运行停止，结果未保存；不能假定操作已完成。",
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                    id=f"cancelled-tool:{call['id']}",
                )
            )
            answered.add(call["id"])
    return missing


def final_answer(snapshot) -> str:
    for message in reversed(snapshot.values.get("messages", [])):
        if isinstance(message, HumanMessage):
            break
        if (
            isinstance(message, AIMessage)
            and message.text.strip()
            and not message.tool_calls
        ):
            return message.text
    raise RuntimeError("Agent 没有返回最终回答")


def message_events(
    message, metadata: dict[str, Any], generation: int, seen_tools: set[str]
) -> Iterator[tuple[str, dict[str, Any]]]:
    """将一条模型或工具消息转换为事件，按原消息顺序产出。"""
    common = {
        "message_id": message.id,
        "node": metadata.get("langgraph_node"),
        "generation": generation,
    }
    if isinstance(message, AIMessageChunk):
        for block in message.content_blocks:
            if block.get("type") != "reasoning":
                continue
            reasoning = block.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                yield "reasoning.delta", {**common, "text": reasoning}
        if message.text and "legal_structured" not in metadata.get("tags", []):
            yield "answer.delta", {**common, "text": message.text}
        return

    if isinstance(message, ToolMessage):
        if message.tool_call_id in seen_tools:
            return
        seen_tools.add(message.tool_call_id)
        yield (
            "tool.completed",
            {
                **common,
                "tool_name": message.name,
                "tool_call_id": message.tool_call_id,
            },
        )
        return

    if isinstance(message, AIMessage) and metadata.get("langgraph_node") == "refuse":
        yield "answer.delta", {**common, "text": message.text}


async def save_result(store, row: dict[str, Any], snapshot) -> None:
    """保存图结束后的中断或最终回答。"""
    run_id, generation = row["run_id"], row["generation"]
    interrupts = pending_interrupts(snapshot)
    if interrupts:
        await asyncio.to_thread(store.interrupted, run_id, generation, interrupts)
        return
    if snapshot.next:
        raise RuntimeError("Agent 执行结束但仍有未完成节点")
    await asyncio.to_thread(
        store.finish,
        run_id,
        generation,
        "succeeded",
        {"answer": final_answer(snapshot)},
    )


async def execute_agent(
    agent, store, row: dict[str, Any], event_bus, *, cancel_check_interval: float = 1.0
) -> None:
    run_id, generation = row["run_id"], row["generation"]
    guard = CancellationGuard(store, run_id, generation, cancel_check_interval)
    config = RunnableConfig(
        configurable={"thread_id": row["thread_id"]},
        metadata={"run_id": run_id, "trace_id": run_id, "generation": generation},
        callbacks=[guard],
    )
    user_message_id = f"run:{run_id}:user"
    await guard.check()
    snapshot = await agent.aget_state(config)
    # metadata 仍能识别当前运行，即使摘要中间件已经删除了最初的用户消息。
    started = checkpoint_started(snapshot, row)
    interrupts = pending_interrupts(snapshot)
    responses = row["resume_values"]
    if (
        started
        and interrupts
        and (not responses or set(responses) != {item["id"] for item in interrupts})
    ):
        # 崩溃可能发生在 checkpoint 已保存中断、run 状态尚未更新之间。
        await asyncio.to_thread(store.interrupted, run_id, generation, interrupts)
        return
    if started and not interrupts and not snapshot.next:
        # 图已完成但 worker 未来得及标记 succeeded，不重复调用模型。
        await save_result(store, row, snapshot)
        return
    stopped_tool_results = []
    if not started and (snapshot.next or cancelled_tool_results(snapshot)):
        # 清理节点后、提交新消息前退出，也必须在重试时补齐工具结果。
        stopped_tool_results = await discard_cancelled_checkpoint(
            agent, store, snapshot, row
        )

    agent_input = None
    if not started:
        agent_input = {
            "messages": [
                *stopped_tool_results,
                HumanMessage(content=row["message"], id=user_message_id),
            ]
        }
    elif interrupts:
        agent_input = Command(resume=responses)

    seen_tools: set[str] = set()
    async with aclosing(
        agent.astream(
            agent_input,
            config=config,
            stream_mode=["messages", "values"],
            subgraphs=True,
            version="v2",
        )
    ) as parts:
        async for part in parts:
            await guard.check()
            if part["type"] != "messages":
                continue
            message, metadata = part["data"]
            for event, data in message_events(
                message, metadata, generation, seen_tools
            ):
                await asyncio.to_thread(event_bus.publish, run_id, event, data)

    await guard.check(force=True)
    snapshot = await agent.aget_state(config)
    await save_result(store, row, snapshot)
