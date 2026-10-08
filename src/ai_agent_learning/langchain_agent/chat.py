"""新版 Agent 的终端聊天入口和流式事件适配。"""

import asyncio
import logging
import os
import time
from typing import Any
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, Interrupt
from openai import APIStatusError

from ai_agent_learning.langchain_agent.checkpoint import has_pending_turn
from ai_agent_learning.langchain_agent.session import (
    create_session_name,
)
from ai_agent_learning.langchain_agent.tools import TOOL_BY_NAME
from ai_agent_learning.ui.terminal import TerminalUI

logger = logging.getLogger(__name__)

RETRYABLE_API_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def _is_nonrecoverable_api_error(error: Exception) -> bool:
    return isinstance(error, APIStatusError) and (
        error.status_code not in RETRYABLE_API_STATUS_CODES
    )


def _new_turn_config(session_id: str, trace_id: str) -> RunnableConfig:
    configurable: dict[str, Any] = {"thread_id": session_id}
    if os.environ.pop("LEGAL_FAULT_DECIDE_ONCE", None) == "1":
        configurable["legal_fault"] = {"node": "decide", "mode": "error"}
    return RunnableConfig(
        configurable=configurable,
        metadata={"trace_id": trace_id},
    )


async def stream_agent_turn(
    agent: Any,
    content: str | None,
    ui: TerminalUI,
    config: RunnableConfig,
    trace_id: str | None = None,
) -> list[AnyMessage]:
    """流式执行一轮 Agent，并返回包含工具往返的完整消息历史。"""
    agent_input: dict[str, list[AnyMessage]] | Command | None = (
        {"messages": [HumanMessage(content=content)]} if content is not None else None
    )
    latest_messages: list[AnyMessage] | None = None

    while True:
        pending_interrupts: list[Interrupt] = []
        async for part in agent.astream(
            agent_input,
            config=config,
            stream_mode=["messages", "values"],
            subgraphs=True,
            version="v2",
        ):
            if part["type"] == "values":
                state_messages = part["data"].get("messages")
                if state_messages is not None:
                    latest_messages = list(state_messages)
                pending_interrupts.extend(part["interrupts"])
                continue

            if part["type"] != "messages":
                continue

            message, metadata = part["data"]
            if isinstance(message, AIMessageChunk):
                for block in message.content_blocks:
                    if block.get("type") != "reasoning":
                        continue
                    reasoning = block.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        ui.reasoning_delta(reasoning)

                text = message.text
                if text and "legal_structured" not in metadata.get("tags", []):
                    ui.text_delta(text)
            elif (
                isinstance(message, AIMessage)
                and metadata.get("langgraph_node") == "refuse"
            ):
                # 拒答是节点生成的固定提示，不经过模型流式调用。
                ui.text_delta(message.text)
            elif isinstance(message, ToolMessage):
                # 模型可能在调用工具前输出文字；先提交这一段，再继续下一轮。
                ui.finish_response()

        ui.finish_response()
        if not pending_interrupts:
            break

        resume_values = {}
        for pending_interrupt in pending_interrupts:
            if pending_interrupt.id in resume_values:
                continue
            resume_values[pending_interrupt.id] = await _review_interrupt(
                pending_interrupt,
                ui,
                config,
                trace_id,
            )
        agent_input = Command(resume=resume_values)

    if latest_messages is None:
        raise RuntimeError("LangChain Agent 没有返回最终状态")
    return latest_messages


async def _resume_pending_turn(
    agent: Any,
    ui: TerminalUI,
    session_id: str,
    *,
    confirm: bool = False,
) -> list[AnyMessage] | None:
    """同一会话有待执行节点时，从 checkpoint 继续，不添加用户消息。"""
    trace_id = uuid4().hex
    turn_config = RunnableConfig(
        configurable={"thread_id": session_id},
        metadata={"trace_id": trace_id},
    )
    snapshot = await agent.aget_state(turn_config)
    if not has_pending_turn(snapshot):
        return None
    if confirm and not await asyncio.to_thread(ui.confirm_resume):
        ui.show_notice("已跳过断点恢复，可以输入新消息继续对话。")
        return None
    ui.show_notice("检测到未完成的对话，正在从断点继续…")
    logger.info(
        "对话轮次从断点继续",
        extra={
            "event": "chat.turn.resumed",
            "session_id": session_id,
            "trace_id": trace_id,
            "pending_nodes": list(snapshot.next),
        },
    )
    ui.begin_turn()
    try:
        return await stream_agent_turn(agent, None, ui, turn_config, trace_id)
    finally:
        ui.close_turn()


async def _review_interrupt(
    interrupt: Interrupt,
    ui: TerminalUI,
    config: RunnableConfig,
    trace_id: str | None,
) -> dict[str, Any]:
    """在终端逐项确认一次 LangChain 人工中断。"""
    request = interrupt.value
    if not isinstance(request, dict):
        raise TypeError("工具确认请求格式错误")
    if request.get("type") == "legal_information_required":
        question = request.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("法律信息补充请求缺少问题")
        answer = await asyncio.to_thread(ui.read_follow_up, question.strip())
        ui.begin_turn()
        return {"answer": answer.strip()}

    actions = request.get("action_requests")
    if not isinstance(actions, list) or not actions:
        raise ValueError("工具确认请求中没有待处理操作")

    decisions: list[dict[str, str]] = []
    session_id = config.get("configurable", {}).get("thread_id")
    for action in actions:
        if not isinstance(action, dict):
            raise TypeError("待确认工具调用格式错误")
        name = action.get("name")
        arguments = action.get("args")
        description = action.get("description")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise TypeError("待确认工具调用缺少名称或参数")
        try:
            tool = TOOL_BY_NAME[name]
        except KeyError:
            raise ValueError(f"待确认工具未注册：{name}") from None

        log_fields = {
            "session_id": session_id,
            "trace_id": trace_id,
            "tool_name": name,
            "tool_call_id": interrupt.id,
        }
        logger.info(
            "工具执行等待用户确认",
            extra={"event": "tool.approval.requested", **log_fields},
        )
        approved = ui.confirm_tool_call(
            tool,
            arguments,
            description if isinstance(description, str) else "模型请求执行该工具。",
        )
        logger.info(
            "用户已处理工具确认请求",
            extra={
                "event": (
                    "tool.approval.approved" if approved else "tool.approval.rejected"
                ),
                "approved": approved,
                **log_fields,
            },
        )
        decisions.append(
            {"type": "approve"}
            if approved
            else {"type": "reject", "message": "用户在终端拒绝执行该工具。"}
        )

    return {"decisions": decisions}


async def run_chat_loop(
    agent, model, config, ui, session_store, session, memory_worker
) -> None:
    """处理会话恢复、用户输入和对话轮次；资源由调用方管理。"""
    ui.show_header(config.model, session.name if session else "")
    resumed_messages = None
    if session is not None:
        try:
            resumed_messages = await _resume_pending_turn(
                agent, ui, session.thread_id, confirm=True
            )
        except Exception as error:
            logger.exception(
                "启动时断点恢复失败",
                extra={
                    "event": "chat.turn.resume_failed",
                    "session_id": session.thread_id,
                    "error_type": type(error).__name__,
                },
            )
            ui.show_error(f"断点恢复失败：{error}")
            return
    if resumed_messages is not None and session is not None:
        await session_store.touch(session)
    while True:
        content = await asyncio.to_thread(ui.read_message)
        if not content:
            ui.show_notice("消息不能为空")
            continue
        if content.lower() in {"exit", "quit"}:
            if not memory_worker.is_idle:
                ui.show_notice("正在等待剩余记忆整理完成…")
                await memory_worker.wait_until_idle()
            logger.info(
                "新版会话正常退出",
                extra={
                    "event": "newchat.completed",
                    "session_id": session.thread_id if session else None,
                },
            )
            return

        if session is None:
            session = await session_store.create_session()
            logger.info(
                "会话已就绪",
                extra={
                    "event": "newchat.session.ready",
                    "session_id": session.thread_id,
                },
            )

        trace_id = uuid4().hex
        started_at = time.perf_counter()
        turn_config = _new_turn_config(session.thread_id, trace_id)
        logger.info(
            "对话轮次开始",
            extra={
                "event": "chat.turn.started",
                "session_id": session.thread_id,
                "trace_id": trace_id,
                "model": config.model,
            },
        )
        ui.begin_turn()
        try:
            messages = await stream_agent_turn(
                agent,
                content,
                ui,
                turn_config,
                trace_id,
            )
        except Exception as error:
            logger.exception(
                "对话轮次失败",
                extra={
                    "event": "chat.turn.failed",
                    "session_id": session.thread_id,
                    "trace_id": trace_id,
                    "model": config.model,
                    "duration_ms": round(
                        (time.perf_counter() - started_at) * 1000,
                        2,
                    ),
                    "error_type": type(error).__name__,
                },
            )
            ui.close_turn()
            ui.show_error(str(error))
            if _is_nonrecoverable_api_error(error):
                ui.show_notice("API 错误需要先修正账号或请求；当前 checkpoint 已保留。")
                return
            try:
                recovered_messages = await _resume_pending_turn(
                    agent, ui, session.thread_id, confirm=True
                )
            except Exception as resume_error:
                logger.exception(
                    "报错后断点恢复失败",
                    extra={
                        "event": "chat.turn.resume_failed",
                        "session_id": session.thread_id,
                        "error_type": type(resume_error).__name__,
                    },
                )
                ui.show_error(f"断点恢复失败：{resume_error}")
                return
            if recovered_messages is None:
                continue
            messages = recovered_messages
        finally:
            ui.close_turn()

        logger.info(
            "对话轮次完成",
            extra={
                "event": "chat.turn.completed",
                "session_id": session.thread_id,
                "trace_id": trace_id,
                "model": config.model,
                "duration_ms": round(
                    (time.perf_counter() - started_at) * 1000,
                    2,
                ),
                "message_count": len(messages),
            },
        )

        if not session.name.strip():
            try:
                session_name = await create_session_name(model, content)
            except Exception as error:  # noqa: BLE001 -- 标题失败不影响对话。
                ui.show_error(f"会话标题生成失败：{error}")
                await session_store.touch(session)
            else:
                await session_store.update_name(session, session_name)
        else:
            await session_store.touch(session)


async def run_new_chat() -> None:
    """兼容原有调用；主流程已迁至 run 模块。"""
    from ai_agent_learning.langchain_agent.run import run

    await run()
