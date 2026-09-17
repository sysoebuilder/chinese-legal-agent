import json
from collections.abc import Callable
from typing import Any, Literal, cast

import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageParam

from ai_agent_learning.memory.prompts import (
    build_memory_decisions_instructions,
    build_memory_instructions,
)
from ai_agent_learning.memory.schema import (
    MemoryCandidate,
    MemoryCandidateBatch,
    MemoryDecision,
    MemoryDecisionBatch,
    StoredMemory,
)
from ai_agent_learning.planning.schema import TaskPlan
from ai_agent_learning.retry import retry_async
from ai_agent_learning.schema import Message, ModelRequest, ModelResponse
from ai_agent_learning.tools.contracts import ToolCall, ToolResult, ToolSpec


class GlmModelClient:
    """通过智谱的 OpenAI 兼容接口统一使用流式生成。

    文档：https://docs.bigmodel.cn/cn/guide/capabilities/thinking
    工具续调：https://docs.bigmodel.cn/cn/guide/capabilities/thinking-mode
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://open.bigmodel.cn/api/paas/v4/",
        *,
        thinking: Literal["enabled", "disabled"] = "enabled",
    ) -> None:
        if thinking not in ("enabled", "disabled"):
            raise ValueError("thinking 必须是 enabled 或 disabled")
        self._thinking = thinking
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._http_client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=5.0),
        )

    @staticmethod
    def _build_api_tools(
        tools: list[ToolSpec],
    ) -> list[dict[str, Any]]:
        api_tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in tools
        ]
        return api_tools

    @staticmethod
    def _build_messages(
        messages: list[Message],
        tool_history: list[ToolResult | ToolCall] | None = None,
    ) -> list[ChatCompletionMessageParam]:
        api_messages: list[dict[str, Any]] = [
            {"role": message.role, "content": message.content} for message in messages
        ]
        emitted_calls: set[str] = set()
        pending_assistant: dict[str, Any] | None = None
        for item in tool_history or []:
            if isinstance(item, ToolCall):
                if item.id in emitted_calls:
                    continue
                if item.assistant_message is not None:
                    # 同一轮的多个工具共用原始消息；保留参数字符串和思考内容。
                    api_messages.append(item.assistant_message)
                    emitted_calls.update(
                        call["id"] for call in item.assistant_message["tool_calls"]
                    )
                    pending_assistant = None
                else:
                    if pending_assistant is None:
                        pending_assistant = {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [],
                        }
                        api_messages.append(pending_assistant)
                    pending_assistant["tool_calls"].append(
                        {
                            "id": item.id,
                            "type": "function",
                            "function": {
                                "name": item.name,
                                "arguments": json.dumps(
                                    item.arguments, ensure_ascii=False
                                ),
                            },
                        }
                    )
                    emitted_calls.add(item.id)
            else:
                pending_assistant = None
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": item.call_id,
                        "content": json.dumps(
                            {
                                "success": item.success,
                                "data": item.data,
                                "error": item.error,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
        return cast(list[ChatCompletionMessageParam], api_messages)

    async def create_response_stream(
        self,
        request: ModelRequest,
        tools: list[ToolSpec],
        tool_history: list[ToolResult | ToolCall] | None = None,
        *,
        on_text_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        json_output: bool = False,
    ) -> ModelResponse:
        """接收流式响应，实时通知正文片段，完整接收后返回可执行的工具调用。"""
        api_tools = self._build_api_tools(tools)
        tool_options: dict[str, Any] = {}
        if api_tools:
            tool_options = {"tools": api_tools, "tool_choice": "auto"}
        if json_output:
            tool_options["response_format"] = {"type": "json_object"}
        stream = await self._client.chat.completions.create(
            model=request.model,
            messages=self._build_messages(request.messages, tool_history),
            extra_body={"thinking": {"type": self._thinking}},
            stream=True,
            **tool_options,
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        # 正常结束、网络异常或调用方取消时，都关闭本次响应流。
        async with stream:
            async for chunk in stream:
                for choice in chunk.choices:
                    if choice.index != 0:
                        continue
                    delta = choice.delta
                    if delta.content is not None:
                        content_parts.append(delta.content)
                        if delta.content and on_text_delta is not None:
                            on_text_delta(delta.content)
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning is not None:
                        reasoning_parts.append(reasoning)
                        if reasoning and on_reasoning_delta is not None:
                            on_reasoning_delta(reasoning)
                    for part in delta.tool_calls or []:
                        call = calls.setdefault(
                            part.index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if part.id:
                            call["id"] = part.id
                        if part.type:
                            call["type"] = part.type
                        if part.function is not None:
                            if part.function.name:
                                call["function"]["name"] = part.function.name
                            if part.function.arguments is not None:
                                call["function"]["arguments"] += part.function.arguments
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason

        if finish_reason not in {"stop", "tool_calls"}:
            raise ValueError(
                f"GLM 流式响应未正常完成：{finish_reason or '缺少结束标记'}"
            )
        ordered_calls = [calls[index] for index in sorted(calls)]
        for call in ordered_calls:
            if not call["id"] or not call["function"]["name"]:
                raise ValueError("GLM 流式工具调用缺少 id 或函数名")
        output: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) if content_parts else None,
            "tool_calls": ordered_calls,
        }
        if reasoning_parts:
            output["reasoning_content"] = "".join(reasoning_parts)
        return self._parse_response(ChatCompletionMessage.model_validate(output))

    @staticmethod
    def _parse_response(output: ChatCompletionMessage) -> ModelResponse:
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": output.content,
            "tool_calls": [
                call.model_dump(exclude_none=True) for call in output.tool_calls or []
            ],
        }
        reasoning = getattr(output, "reasoning_content", None)
        if reasoning is not None:
            assistant_message["reasoning_content"] = reasoning
        tool_calls: list[ToolCall] = []
        for call in output.tool_calls or []:
            if call.type != "function":
                continue
            arguments = json.loads(call.function.arguments)
            if not isinstance(arguments, dict):
                raise TypeError("工具调用参数必须是 JSON 对象")
            tool_calls.append(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments,
                    assistant_message=assistant_message,
                )
            )
        content = (output.content or "").strip()
        return ModelResponse(
            message=Message(role="assistant", content=content) if content else None,
            tool_calls=tool_calls,
        )

    async def create_task_plan(
        self,
        request: ModelRequest,
        tools: list[ToolSpec],
    ) -> TaskPlan:
        # GLM 的 JSON 模式不接受 Responses API 的 text_format。
        # 仅提供工具说明，不注册可执行工具，避免规划阶段触发调用。
        instructions = Message(
            role="system",
            content=(
                "请生成任务计划，只输出符合以下 JSON Schema 的 JSON 对象，"
                "不要输出 Markdown，不要执行工具。\n"
                + json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False)
                + "\n可用于计划的工具定义：\n"
                + json.dumps(
                    self._build_api_tools(tools),
                    ensure_ascii=False,
                )
            ),
        )
        response = await self.create_response_stream(
            request=ModelRequest(
                model=request.model,
                messages=[instructions, *request.messages],
            ),
            tools=[],
            json_output=True,
        )
        if response.message is None or response.tool_calls:
            raise ValueError("GLM 没有返回任务计划")
        return TaskPlan.model_validate_json(response.message.content)

    async def create_memory_candidates(
        self,
        request: ModelRequest,
    ) -> MemoryCandidateBatch:
        instructions = build_memory_instructions()
        for attempt in range(3):
            messages = [instructions, *request.messages]
            if attempt:
                messages.append(
                    Message(
                        role="system",
                        content=(
                            "上一次候选记忆响应为空。请立即输出完整 JSON 对象；"
                            "第一个字符必须是 {，没有候选记忆时输出 "
                            '{"memories":[]}。'
                        ),
                    )
                )
            completion = await self._client.chat.completions.create(
                model=request.model,
                messages=cast(
                    list[ChatCompletionMessageParam],
                    [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                ),
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "enabled"}},
                stream=False,
            )
            if not completion.choices:
                continue
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError(
                    "候选记忆响应未正常完成："
                    f"{choice.finish_reason or '缺少结束标记'}"
                )
            content = (choice.message.content or "").strip()
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
        instructions = build_memory_decisions_instructions(candidates_with_memories)
        completion = await self._client.chat.completions.create(
            model=model,
            messages=cast(
                list[ChatCompletionMessageParam],
                [
                    {"role": instructions.role, "content": instructions.content},
                    {
                        "role": "user",
                        "content": "请根据以上规则生成整批候选记忆的 JSON 决策。",
                    },
                ],
            ),
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "enabled"}},
            stream=False,
        )
        if not completion.choices:
            raise ValueError("模型没有返回批量记忆决定")
        choice = completion.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError(
                "批量记忆决定响应未正常完成："
                f"{choice.finish_reason or '缺少结束标记'}"
            )
        content = (choice.message.content or "").strip()
        if not content:
            raise ValueError("模型没有返回批量记忆决定")
        return MemoryDecisionBatch.model_validate_json(content)

    async def create_memory_decision(
        self,
        model: str,
        candidate: MemoryCandidate,
        old_memories: list[StoredMemory],
    ) -> MemoryDecision:
        batch = await self.create_memory_decisions(
            model, [(candidate, old_memories)]
        )
        if len(batch.decisions) != 1 or batch.decisions[0].candidate_index != 0:
            raise ValueError("单条记忆决定响应的候选索引无效")
        return batch.decisions[0].decision

    async def tokenization(self, messages: list[Message], model: str) -> int:
        async def create_request() -> httpx.Response:
            response = await self._http_client.post(
                "tokenizer",
                json={
                    "model": model,
                    "messages": self._build_messages(messages),
                },
            )
            response.raise_for_status()
            return response

        response = await retry_async(create_request, max_attempts=3)
        return int(response.json()["usage"]["total_tokens"])

    async def compact_context(self, messages: list[Message], model: str) -> Message:
        instructions = Message(
            role="system",
            content=(
                "你的任务是压缩随后提供的历史对话。"
                "请保留重要事实、用户偏好、已有结论、"
                "未完成任务和必要细节。"
                "不要回答历史问题，只输出摘要。"
            ),
        )
        response = await self.create_response_stream(
            request=ModelRequest(model=model, messages=[instructions, *messages]),
            tools=[],
        )
        if response.message is None or response.tool_calls:
            raise ValueError("GLM 没有返回历史对话摘要")
        return Message(
            role="system",
            content=f"历史对话摘要: {response.message.content}",
        )

    async def close(self) -> None:
        try:
            await self._http_client.aclose()
        finally:
            await self._client.close()


class DeepSeekClient:
    """DeepSeek 的 OpenAI 兼容客户端，仅使用流式生成。

    模型名称由 ModelRequest.model 指定，例如 deepseek-v4-flash。
    默认关闭思考模式；启用思考后，工具续调应保留原始 assistant_message。
    tokenization 通过最小流式生成请求读取实际输入用量，会产生 API 费用。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        *,
        thinking: Literal["enabled", "disabled"] = "enabled",
    ) -> None:
        if thinking not in ("enabled", "disabled"):
            raise ValueError("thinking 必须是 enabled 或 disabled")
        self._thinking = thinking
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    # 两家提供商使用相同的 Chat Completions 消息和工具结构。
    _build_api_tools = staticmethod(GlmModelClient._build_api_tools)
    _build_messages = staticmethod(GlmModelClient._build_messages)
    _parse_response = staticmethod(GlmModelClient._parse_response)

    async def create_response_stream(
        self,
        request: ModelRequest,
        tools: list[ToolSpec],
        tool_history: list[ToolResult | ToolCall] | None = None,
        *,
        on_text_delta: Callable[[str], None] | None = None,
        on_reasoning_delta: Callable[[str], None] | None = None,
        json_output: bool = False,
    ) -> ModelResponse:
        """接收流式响应，实时通知正文片段，完整接收后返回可执行的工具调用。"""
        api_tools = self._build_api_tools(tools)
        tool_options: dict[str, Any] = {}
        if api_tools:
            tool_options = {"tools": api_tools, "tool_choice": "auto"}
        if json_output:
            tool_options["response_format"] = {"type": "json_object"}
        stream = await self._client.chat.completions.create(
            model=request.model,
            messages=self._build_messages(request.messages, tool_history),
            extra_body={"thinking": {"type": self._thinking}},
            stream=True,
            **tool_options,
        )
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        # 正常结束、网络异常或调用方取消时，都关闭本次响应流。
        async with stream:
            async for chunk in stream:
                for choice in chunk.choices:
                    if choice.index != 0:
                        continue
                    delta = choice.delta
                    if delta.content is not None:
                        content_parts.append(delta.content)
                        if delta.content and on_text_delta is not None:
                            on_text_delta(delta.content)
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning is not None:
                        reasoning_parts.append(reasoning)
                        if reasoning and on_reasoning_delta is not None:
                            on_reasoning_delta(reasoning)
                    for part in delta.tool_calls or []:
                        call = calls.setdefault(
                            part.index,
                            {
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            },
                        )
                        if part.id:
                            call["id"] = part.id
                        if part.type:
                            call["type"] = part.type
                        if part.function is not None:
                            if part.function.name:
                                call["function"]["name"] = part.function.name
                            if part.function.arguments is not None:
                                call["function"]["arguments"] += part.function.arguments
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason

        if finish_reason not in {"stop", "tool_calls"}:
            raise ValueError(
                f"DeepSeek 流式响应未正常完成：{finish_reason or '缺少结束标记'}"
            )
        ordered_calls = [calls[index] for index in sorted(calls)]
        for call in ordered_calls:
            if not call["id"] or not call["function"]["name"]:
                raise ValueError("DeepSeek 流式工具调用缺少 id 或函数名")
        output: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) if content_parts else None,
            "tool_calls": ordered_calls,
        }
        if reasoning_parts:
            output["reasoning_content"] = "".join(reasoning_parts)
        return self._parse_response(ChatCompletionMessage.model_validate(output))

    async def create_task_plan(
        self,
        request: ModelRequest,
        tools: list[ToolSpec],
    ) -> TaskPlan:
        # 仅提供工具说明，不注册可执行工具，避免规划阶段触发调用。
        instructions = Message(
            role="system",
            content=(
                "请生成任务计划，只输出符合以下 JSON Schema 的 JSON 对象，"
                "不要输出 Markdown，不要执行工具。\n"
                + json.dumps(TaskPlan.model_json_schema(), ensure_ascii=False)
                + "\n可用于计划的工具定义：\n"
                + json.dumps(
                    self._build_api_tools(tools),
                    ensure_ascii=False,
                )
            ),
        )
        response = await self.create_response_stream(
            request=ModelRequest(
                model=request.model,
                messages=[instructions, *request.messages],
            ),
            tools=[],
            json_output=True,
        )
        if response.message is None or response.tool_calls:
            raise ValueError("DeepSeek 没有返回任务计划")
        return TaskPlan.model_validate_json(response.message.content)

    async def create_memory_candidates(
        self,
        request: ModelRequest,
    ) -> MemoryCandidateBatch:
        instructions = build_memory_instructions()
        for attempt in range(3):
            messages = [instructions, *request.messages]
            if attempt:
                messages.append(
                    Message(
                        role="system",
                        content=(
                            "上一次候选记忆响应为空。请立即输出完整 JSON 对象；"
                            "第一个字符必须是 {，没有候选记忆时输出 "
                            '{"memories":[]}。'
                        ),
                    )
                )
            completion = await self._client.chat.completions.create(
                model=request.model,
                messages=cast(
                    list[ChatCompletionMessageParam],
                    [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                ),
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "disabled"}},
                stream=False,
            )
            if not completion.choices:
                continue
            choice = completion.choices[0]
            if choice.finish_reason != "stop":
                raise ValueError(
                    "候选记忆响应未正常完成："
                    f"{choice.finish_reason or '缺少结束标记'}"
                )
            content = (choice.message.content or "").strip()
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
        instructions = build_memory_decisions_instructions(candidates_with_memories)
        completion = await self._client.chat.completions.create(
            model=model,
            messages=cast(
                list[ChatCompletionMessageParam],
                [{"role": instructions.role, "content": instructions.content}],
            ),
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "disabled"}},
            stream=False,
        )
        if not completion.choices:
            raise ValueError("模型没有返回批量记忆决定")
        choice = completion.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError(
                "批量记忆决定响应未正常完成："
                f"{choice.finish_reason or '缺少结束标记'}"
            )
        content = (choice.message.content or "").strip()
        if not content:
            raise ValueError("模型没有返回批量记忆决定")
        return MemoryDecisionBatch.model_validate_json(content)

    async def create_memory_decision(
        self,
        model: str,
        candidate: MemoryCandidate,
        old_memories: list[StoredMemory],
    ) -> MemoryDecision:
        batch = await self.create_memory_decisions(
            model, [(candidate, old_memories)]
        )
        if len(batch.decisions) != 1 or batch.decisions[0].candidate_index != 0:
            raise ValueError("单条记忆决定响应的候选索引无效")
        return batch.decisions[0].decision

    async def tokenization(self, messages: list[Message], model: str) -> int:
        """用最小流式请求获取输入 Token 数，不包含生成的 Token。

        DeepSeek 没有公开独立的 tokenizer API；此方法会发起计费请求。
        关闭思考且最多生成 1 Token，只读取 usage.prompt_tokens。
        """
        if not model.strip():
            raise ValueError("model 不能为空")
        if not messages:
            return 0

        stream = await self._client.chat.completions.create(
            model=model,
            messages=self._build_messages(messages),
            stream=True,
            stream_options={"include_usage": True},
            max_tokens=1,
            extra_body={"thinking": {"type": "disabled"}},
        )
        input_tokens: int | None = None
        async with stream:
            async for chunk in stream:
                # 用量通常出现在 choices 为空的最后一个 chunk 中。
                if chunk.usage is not None:
                    input_tokens = chunk.usage.prompt_tokens
        if (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or input_tokens < 0
        ):
            raise ValueError("DeepSeek 未返回有效的输入 Token 用量")
        return input_tokens

    async def compact_context(self, messages: list[Message], model: str) -> Message:
        instructions = Message(
            role="system",
            content=(
                "你的任务是压缩随后提供的历史对话。"
                "请保留重要事实、用户偏好、已有结论、"
                "未完成任务和必要细节。"
                "不要回答历史问题，只输出摘要。"
            ),
        )
        response = await self.create_response_stream(
            request=ModelRequest(model=model, messages=[instructions, *messages]),
            tools=[],
        )
        if response.message is None or response.tool_calls:
            raise ValueError("DeepSeek 没有返回历史对话摘要")
        return Message(
            role="system",
            content=f"历史对话摘要: {response.message.content}",
        )

    async def close(self) -> None:
        await self._client.close()
