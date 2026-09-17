import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv

from ai_agent_learning.agent.context import ContextManager
from ai_agent_learning.agent.loop import AgentLoop
from ai_agent_learning.agent.policy import AgentLimits, AgentPolicy
from ai_agent_learning.agent.state import AgentRunResult, StopReason
from ai_agent_learning.context import save_context, select_context
from ai_agent_learning.law_install import install_laws
from ai_agent_learning.logging_config import configure_logging
from ai_agent_learning.memory.service import MemoryService
from ai_agent_learning.model import DeepSeekClient, GlmModelClient
from ai_agent_learning.model_config import (
    load_active_model,
    load_model_choices,
    save_active_model,
    save_source_model,
    source_config_path,
)
from ai_agent_learning.planning.executor import PlanExecutor
from ai_agent_learning.planning.planner import Planner
from ai_agent_learning.planning.renderer import (
    show_step_status,
    show_task_plan,
)
from ai_agent_learning.planning.state import (
    PlanExecution,
    PlanStatus,
    StepExecution,
)
from ai_agent_learning.qdrant_server import QdrantServer
from ai_agent_learning.schema import Message, ModelRequest
from ai_agent_learning.session import create_session_name, save_session, select_session
from ai_agent_learning.tools.catalog import build_builtin_tools
from ai_agent_learning.tools.contracts import RiskLevel, ToolCall, ToolResult
from ai_agent_learning.tools.executor import ToolExecutor
from ai_agent_learning.tools.registry import ToolRegistry
from ai_agent_learning.ui.terminal import TerminalUI

CONTEXT_LIMIT = 128_000
KEEP_RECENT = 6


def tool_call_to_message(call: ToolCall) -> Message:
    return Message(
        role="assistant",
        content=json.dumps(
            {
                "event": "agent.tool_call",
                "call_id": call.id,
                "name": call.name,
                "arguments": call.arguments,
            },
            ensure_ascii=False,
        ),
    )


def tool_result_to_message(result: ToolResult) -> Message:
    return Message(
        role="assistant",
        content=json.dumps(
            {
                "event": "agent.tool_result",
                "call_id": result.call_id,
                "success": result.success,
                "data": result.data,
                "error": result.error,
                "metadata": result.metadata,
            },
            ensure_ascii=False,
        ),
    )


def agent_run_result_to_messages(
    result: AgentRunResult,
) -> list[Message]:
    messages: list[Message] = []

    for step in result.state.steps:
        if step.message is not None:
            messages.append(step.message)

        messages.extend(tool_call_to_message(call) for call in step.tool_calls)
        messages.extend(
            tool_result_to_message(tool_result) for tool_result in step.tool_results
        )

    return messages


def run_config() -> None:
    ui = TerminalUI()
    try:
        source = source_config_path()
        choices = load_model_choices() if source.is_file() else []
        configured = False
        for choice in choices:
            try:
                choice.validate()
                configured = True
                break
            except ValueError:
                continue
        if configured:
            ui.show_notice(f"候选配置来源：{source}")
            selected = ui.select_model(choices)
        else:
            ui.show_notice("未检测到有效模型配置，开始首次配置。")
            selected = ui.setup_first_model()
        if selected is None:
            return
        if not configured:
            save_source_model(selected)
        path = save_active_model(selected)
    except (OSError, UnicodeError, ValueError):
        ui.show_error("无法读取或保存配置，请检查项目 .env 和数据目录权限。")
        return
    ui.show_notice(f"当前模型：{selected.provider} / {selected.model}")
    ui.show_notice(f"已更新：{path}")


def run_law_install() -> None:
    """执行完整的离线法规库安装流程。"""
    asyncio.run(install_laws())


@asynccontextmanager
async def create_runtime() -> AsyncIterator[
    tuple[GlmModelClient | DeepSeekClient, ToolRegistry, str]
]:
    """选择当前模型，并管理客户端和本次启动的 Qdrant 服务。"""
    config = load_active_model()
    client_types = {"glm": GlmModelClient, "deepseek": DeepSeekClient}
    client = client_types[config.provider](
        api_key=config.api_key,
        base_url=config.base_url,
    )
    server = QdrantServer()
    try:
        # .env 为搜索、邮件等工具提供凭据；模型的四个字段始终只取自 config。
        load_dotenv(source_config_path(), override=False)
        await server.start()
        registry = ToolRegistry(tools=build_builtin_tools())
        yield client, registry, config.model
    finally:
        try:
            await client.close()
        finally:
            await server.close()


async def run_chat() -> None:
    configure_logging()
    async with create_runtime() as (client, tool_registry, reasoning_id):
        ui = TerminalUI()
        tool_executor = ToolExecutor(
            registry=tool_registry,
            request_approval=ui.confirm_tool_call,
            confirmable_risks=frozenset({RiskLevel.HIGH}),
        )

        context_manager = ContextManager(
            client=client,
            tokenizer_model=reasoning_id,
            context_limit=CONTEXT_LIMIT,
            keep_recent=KEEP_RECENT,
        )

        agent_policy = AgentPolicy(limits=AgentLimits())

        agent_loop = AgentLoop(
            model_client=client,
            registry=tool_registry,
            executor=tool_executor,
            context_manager=context_manager,
            policy=agent_policy,
        )

        session = select_session(ui=ui)
        memory_start_index = len(session.messages)
        context = select_context(session.session_id)
        context_manager.initialize_token_estimate(context.messages)
        memory_service = MemoryService(client)

        ui.show_header(reasoning_id, session.name)

        while True:
            content = ui.read_message()
            if not content:
                ui.show_notice("消息不能为空")
                continue

            if content in {"exit", "quit"}:
                break

            if not session.name.strip():
                session.name = create_session_name(
                    content,
                    session_id=session.session_id,
                )

            session.messages.append(
                Message(
                    role="user",
                    content=content,
                ),
            )

            context.messages.append(
                Message(
                    role="user",
                    content=content,
                ),
            )

            request = ModelRequest(
                messages=context.messages,
                model=reasoning_id,
            )
            ui.begin_turn()
            try:
                agent_result = await agent_loop.run(
                    request,
                    on_text_delta=ui.text_delta,
                    on_reasoning_delta=ui.reasoning_delta,
                    on_response_end=ui.finish_response,
                )
            except Exception as error:  # noqa: BLE001 -- 单轮失败后恢复终端并允许继续输入。
                ui.close_turn()
                ui.show_error(str(error))
                continue
            finally:
                ui.close_turn()
            if agent_result.state.stop_reason is not StopReason.FINAL_RESPONSE:
                if agent_result.state.stop_reason is StopReason.MAX_STEPS:
                    ui.show_error("Agent 已超过最大步骤数")
                    continue
                if agent_result.state.stop_reason is StopReason.TOOL_FAILURE_LIMIT:
                    ui.show_error("Agent 已超过最大工具调用次数")
                    continue
                if agent_result.state.stop_reason is StopReason.INVALID_MODEL_RESPONSE:
                    ui.show_error("Agent 返回无效模型响应")
                    continue
                if agent_result.state.stop_reason is StopReason.REPEATED_TOOL_CALL:
                    ui.show_error("Agent 重复调用工具")
                    continue
                ui.show_error("Agent 已停止，未知原因")

            run_messages = agent_run_result_to_messages(agent_result)
            session.messages.extend(run_messages)
            context.messages[:] = request.messages
            context.messages.extend(run_messages)

            if agent_result.response is None or agent_result.response.message is None:
                raise RuntimeError("Agent 没有返回最终文本")

        try:
            if session.messages:
                save_session(session)
                save_context(context)

            new_messages = session.messages[memory_start_index:]
            if new_messages:
                ui.show_notice("正在整理本次对话记忆…")
                try:
                    decisions = await memory_service.save_memories(
                        ModelRequest(messages=new_messages, model=reasoning_id)
                    )
                    ui.show_notice(f"本次对话记忆处理完成：{len(decisions)} 条")
                except Exception as error:  # noqa: BLE001 -- 记忆失败不影响会话保存。
                    ui.show_error(f"记忆保存失败：{error}")
        finally:
            await memory_service.close()


async def run_taskplan(goal: str | None = None) -> None:
    configure_logging()
    async with create_runtime() as (client, tool_registry, reasoning_id):
        context_manager = ContextManager(
            client=client,
            tokenizer_model=reasoning_id,
            context_limit=CONTEXT_LIMIT,
            keep_recent=KEEP_RECENT,
        )
        planner = Planner(
            model_client=client,
            registry=tool_registry,
        )
        plan_executor = PlanExecutor(
            model_client=client,
            model=reasoning_id,
            tool_executor=ToolExecutor(registry=tool_registry),
            on_step_status=show_step_status,
        )

        session = select_session()
        context = select_context(session.session_id)
        context_manager.initialize_token_estimate(context.messages)

        if not goal:
            goal = input("请输入任务目标: ").strip()
            if not goal:
                print("任务目标不能为空")
                return

        if goal in {"exit", "quit"}:
            return

        if not session.name.strip():
            session.name = create_session_name(
                goal,
                session_id=session.session_id,
            )

        user_message = Message(
            role="user",
            content=goal,
        )
        session.messages.append(user_message)
        context.messages.append(user_message)

        request = ModelRequest(
            messages=context.messages,
            model=reasoning_id,
        )
        request = await context_manager.compact_if_needed(
            request=request,
            tool_history=(),
        )
        context.messages[:] = request.messages

        plan = await planner.create_plan(request)
        plan_message = Message(
            role="assistant",
            content=plan.model_dump_json(),
        )
        session.messages.append(plan_message)
        context.messages.append(plan_message)

        execution = PlanExecution(
            plan=plan,
            steps=[StepExecution(step_id=step.step_id) for step in plan.steps],
        )

        show_task_plan(plan)

        approval = input("是否执行该计划？[y/N]: ").strip().lower()

        if approval not in {"y", "yes"}:
            execution.status = PlanStatus.CANCELLED
            print("计划已取消，未执行任何步骤。")
        else:
            execution.status = PlanStatus.RUNNING
            print("\n开始执行计划：")
            execution = await plan_executor.execute(execution)

            if execution.status is PlanStatus.COMPLETED:
                print("\n计划执行完成。")
            else:
                print("\n计划执行失败。")

        if session.messages:
            save_session(session)
            save_context(context)


if __name__ == "__main__":
    from ai_agent_learning.cli import main

    main()
