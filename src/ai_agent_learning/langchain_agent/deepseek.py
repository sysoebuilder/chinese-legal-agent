"""Keep DeepSeek reasoning chunks when streaming JSON responses."""

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.messages import AIMessageChunk, BaseMessage, BaseMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_deepseek import ChatDeepSeek


class ChatDeepSeekWithJsonReasoning(ChatDeepSeek):
    """Use the raw completion stream for JSON mode so reasoning stays visible."""

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if "response_format" not in kwargs:
            async for generation_chunk in super()._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            ):
                yield generation_chunk
            return

        kwargs["stream"] = True
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        response = await self.async_client.create(**payload)
        default_chunk_class: type[BaseMessageChunk] = AIMessageChunk
        async with response:
            async for chunk in response:
                chunk_data = chunk if isinstance(chunk, dict) else chunk.model_dump()
                generation_chunk = self._convert_chunk_to_generation_chunk(
                    chunk_data, default_chunk_class, {}
                )
                if generation_chunk is None:
                    continue
                default_chunk_class = type(generation_chunk.message)
                if run_manager is not None:
                    await run_manager.on_llm_new_token(
                        generation_chunk.text,
                        chunk=generation_chunk,
                    )
                yield generation_chunk
