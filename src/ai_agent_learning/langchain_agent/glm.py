"""保留 GLM 思考片段和工具续调消息的 LangChain 适配器。"""

import json
from collections.abc import AsyncIterator
from typing import Any, Literal, cast

import httpx
from httpx_sse import aconnect_sse
from langchain_community.chat_models.zhipuai import (
    ChatZhipuAI,
    _convert_delta_to_message_chunk,
    _get_jwt_token,
    _truncate_params,
)
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    convert_to_openai_messages,
)
from langchain_core.outputs import ChatGenerationChunk


class ChatGlm(ChatZhipuAI):
    """补全社区适配器遗漏的 GLM reasoning_content 流式转换。"""

    thinking: Literal["enabled", "disabled"] = "enabled"

    @property
    def _default_params(self) -> dict[str, Any]:
        params = super()._default_params
        params["thinking"] = {"type": self.thinking}
        return params

    def _create_message_dicts(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """保留工具调用和 GLM 工具续调所需的原始思考内容。"""
        converted = convert_to_openai_messages(messages)
        message_dicts = cast(list[dict[str, Any]], converted)
        for source, target in zip(messages, message_dicts, strict=True):
            if not isinstance(source, AIMessage):
                continue
            reasoning = source.additional_kwargs.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                target["reasoning_content"] = reasoning

        params = self._default_params
        if stop is not None:
            params["stop"] = stop
        return message_dicts, params

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self.zhipuai_api_key is None:
            raise ValueError("未配置 GLM API Key")
        if self.zhipuai_api_base is None:
            raise ValueError("未配置 GLM API 地址")

        message_dicts, params = self._create_message_dicts(messages, stop)
        payload = {
            **params,
            **kwargs,
            "messages": message_dicts,
            "stream": True,
        }
        _truncate_params(payload)
        headers = {
            "Authorization": _get_jwt_token(self.zhipuai_api_key),
            "Accept": "application/json",
        }

        async with (
            httpx.AsyncClient(headers=headers, timeout=60) as client,
            aconnect_sse(
                client,
                "POST",
                self.zhipuai_api_base,
                json=payload,
            ) as event_source,
        ):
            async for sse in event_source.aiter_sse():
                data = json.loads(sse.data)
                choices = data.get("choices") or []
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta") or {}
                message = _convert_delta_to_message_chunk(
                    delta,
                    AIMessageChunk,
                )
                if isinstance(message, AIMessageChunk):
                    reasoning = delta.get("reasoning_content")
                    if isinstance(reasoning, str):
                        message.additional_kwargs["reasoning_content"] = reasoning

                finish_reason = choice.get("finish_reason")
                generation_info = None
                if finish_reason is not None:
                    generation_info = {
                        "finish_reason": finish_reason,
                        "token_usage": data.get("usage"),
                        "model_name": data.get("model", ""),
                    }

                chunk = ChatGenerationChunk(
                    message=message,
                    generation_info=generation_info,
                )
                if run_manager is not None:
                    await run_manager.on_llm_new_token(chunk.text, chunk=chunk)
                yield chunk

                if finish_reason is not None:
                    break
