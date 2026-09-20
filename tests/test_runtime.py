import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_agent_learning import main
from ai_agent_learning.model_config import ModelConfig


@pytest.mark.parametrize("failure_stage", [None, "registration", "body", "cancel"])
def test_runtime_closes_model_connection(failure_stage, monkeypatch):
    async def run():
        model = SimpleNamespace(close=AsyncMock())
        config = ModelConfig("glm", "test-model", "test-key", "https://example.com")
        monkeypatch.setattr(main, "load_active_model", lambda: config)
        monkeypatch.setattr(main, "load_dotenv", lambda *args, **kwargs: None)
        monkeypatch.setattr(main, "GlmModelClient", lambda **kwargs: model)

        if failure_stage == "registration":

            def fail_registration():
                raise RuntimeError("registration failed")

            monkeypatch.setattr(main, "build_builtin_tools", fail_registration)

        async def use_runtime():
            async with main.create_runtime() as (client, registry, _model_name):
                assert client is model
                assert {spec.name for spec in registry.list_specs()} == {
                    "current_time",
                    "send_email",
                    "run_command",
                    "web_search",
                    "search_memory",
                    "search_raws",
                }
                if failure_stage == "body":
                    raise RuntimeError("body failed")
                if failure_stage == "cancel":
                    raise asyncio.CancelledError()

        if failure_stage == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await use_runtime()
        elif failure_stage:
            with pytest.raises(RuntimeError):
                await use_runtime()
        else:
            await use_runtime()
        model.close.assert_awaited_once()

    asyncio.run(run())
