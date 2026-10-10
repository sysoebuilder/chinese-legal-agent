"""聊天模型适配器参数检查。"""

from pydantic import SecretStr

from ai_agent_learning.langchain_agent import runtime
from ai_agent_learning.model_config import ModelConfig


def test_create_langchain_model_uses_deepseek_adapter(monkeypatch):
    captured = {}
    fake_model = object()

    def fake_chat_deepseek(**kwargs):
        captured.update(kwargs)
        return fake_model

    monkeypatch.setattr(runtime, "ChatDeepSeek", fake_chat_deepseek)
    config = ModelConfig(
        provider="deepseek",
        model="deepseek-test",
        api_key="deepseek-key",
        base_url="https://deepseek.test/",
    )

    result = runtime.create_langchain_model(config)

    assert result is fake_model
    assert captured == {
        "model": "deepseek-test",
        "api_key": SecretStr("deepseek-key"),
        "base_url": "https://deepseek.test/",
        "streaming": True,
        "stream_usage": False,
        "timeout": 30.0,
        "max_retries": 2,
        "extra_body": {"thinking": {"type": "enabled"}},
    }
