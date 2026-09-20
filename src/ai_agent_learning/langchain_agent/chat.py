"""新版 Agent 的终端聊天入口和流式事件适配。"""

import asyncio
import logging
import time
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command, Interrupt

from ai_agent_learning.langchain_agent.memory import MemoryWorker
from ai_agent_learning.langchain_agent.middleware.memory import (
    MemorySaveMiddleware,
    MemoryTurn,
)
from ai_agent_learning.langchain_agent.runtime import (
    create_langchain_model,
    create_new_chat_agent,
)
from ai_agent_learning.langchain_agent.session import (
    SqliteSessionStore,
    create_session_name,
    select_sqlite_session,
)
from ai_agent_learning.langchain_agent.tools import TOOL_SPECS
from ai_agent_learning.logging_config import configure_logging
from ai_agent_learning.model_config import load_active_model, source_config_path
from ai_agent_learning.paths import get_data_dir
from ai_agent_learning.qdrant_server import QdrantServer
from ai_agent_learning.tools.contracts import ToolCall
from ai_agent_learning.ui.terminal import TerminalUI

logger = logging.getLogger(__name__)


async def stream_agent_turn(
    agent: Any,
    content: str,
    ui: TerminalUI,
    config: RunnableConfig,
    trace_id: str | None = None,
) -> list[AnyMessage]:
    """流式执行一轮 Agent，并返回包含工具往返的完整消息历史。"""
    agent_input: dict[str, list[AnyMessage]] | Command = {
        "messages": [HumanMessage(content=content)]
    }
    latest_messages: list[AnyMessage] | None = None

    while True:
        pending_interrupts: list[Interrupt] = []
        async for part in agent.astream(
            agent_input,
            config=config,
            stream_mode=["messages", "values"],
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

            message, _metadata = part["data"]
            if isinstance(message, AIMessageChunk):
                for block in message.content_blocks:
                    if block.get("type") != "reasoning":
                        continue
                    reasoning = block.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        ui.reasoning_delta(reasoning)

                text = message.text
                if text:
                    ui.text_delta(text)
            elif isinstance(message, ToolMessage):
                # 模型可能在调用工具前输出文字；先提交这一段，再继续下一轮。
                ui.finish_response()

        ui.finish_response()
        if not pending_interrupts:
            break

        resume_values = {
            interrupt.id: _review_interrupt(interrupt, ui, config, trace_id)
            for interrupt in pending_interrupts
        }
        agent_input = Command(resume=resume_values)

    if latest_messages is None:
        raise RuntimeError("LangChain Agent 没有返回最终状态")
    return latest_messages


def _review_interrupt(
    interrupt: Interrupt,
    ui: TerminalUI,
    config: RunnableConfig,
    trace_id: str | None,
) -> dict[str, Any]:
    """在终端逐项确认一次 LangChain 人工中断。"""
    request = interrupt.value
    if not isinstance(request, dict):
        raise TypeError("工具确认请求格式错误")
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
            spec = TOOL_SPECS[name]
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
            spec,
            ToolCall(id=interrupt.id, name=name, arguments=arguments),
            description if isinstance(description, str) else "模型请求执行该工具。",
        )
        logger.info(
            "用户已处理工具确认请求",
            extra={
                "event": (
                    "tool.approval.approved"
                    if approved
                    else "tool.approval.rejected"
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


async def run_new_chat() -> None:
    """运行与旧 chat 隔离的 LangChain/LangGraph 多轮会话。"""
    configure_logging()
    logger.info("新版会话启动", extra={"event": "newchat.started"})
    config = load_active_model()
    load_dotenv(source_config_path(), override=False)
    server = QdrantServer()
    try:
        await server.start()
        ui = TerminalUI()
        checkpoint_path = get_data_dir() / "langgraph-checkpoints.sqlite"
        async with AsyncSqliteSaver.from_conn_string(
            str(checkpoint_path)
        ) as checkpointer:
            session_store = SqliteSessionStore(checkpointer.conn)
            session = await select_sqlite_session(session_store, ui)
            logger.info(
                "会话已就绪",
                extra={
                    "event": "newchat.session.ready",
                    "session_id": session.thread_id,
                },
            )
            model = create_langchain_model(config)
            memory_model = create_langchain_model(config)
            memory_queue: asyncio.Queue[MemoryTurn] = asyncio.Queue()
            memory_middleware = MemorySaveMiddleware(memory_queue)
            memory_worker = MemoryWorker(
                memory_queue,
                memory_model,
                config.model,
            )
            memory_worker.start()
            agent = create_new_chat_agent(
                config,
                checkpointer=checkpointer,
                model=model,
                memory_middleware=memory_middleware,
            )
            try:
                ui.show_header(config.model, session.name)
                while True:
                    content = await asyncio.to_thread(ui.read_message)
                    if not content:
                        ui.show_notice("消息不能为空")
                        continue
                    if content in {"exit", "quit"}:
                        if not memory_worker.is_idle:
                            ui.show_notice("正在等待剩余记忆整理完成…")
                            await memory_worker.wait_until_idle()
                        logger.info(
                            "新版会话正常退出",
                            extra={
                                "event": "newchat.completed",
                                "session_id": session.thread_id,
                            },
                        )
                        return

                    trace_id = uuid4().hex
                    started_at = time.perf_counter()
                    turn_config = RunnableConfig(
                        configurable={"thread_id": session.thread_id},
                        metadata={"trace_id": trace_id},
                    )
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
                    except Exception as error:  # noqa: BLE001 -- 单轮失败后允许继续输入。
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
                        continue
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
            finally:
                await memory_worker.close()
    finally:
        await server.close()
        logger.info("新版会话资源已关闭", extra={"event": "newchat.closed"})
