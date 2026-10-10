"""配置迁移、持久化、运行隔离与插件路由的离线测试。"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from ai_agent_learning.api.runtime import resolve_model
from ai_agent_learning.config import APPConfig, ConfigStore, get_secret, use_app_config
from ai_agent_learning.config.plugins import configured_mcp_servers
from ai_agent_learning.langchain_agent import runtime
from ai_agent_learning.langchain_agent.legal.state import LegalAgentState
from ai_agent_learning.mcp_client import resolve_headers


def test_load_migrates_legacy_and_separates_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("WINDOWS_MCP_URL", raising=False)
    (tmp_path / ".env").write_text(
        "ZAI_API_KEY='source-key'\nQQ_SMTP_AUTH_CODE='smtp-key'\nWINDOWS_MCP_URL='http://host.docker.internal:8765/mcp'\n",
        encoding="utf-8",
    )
    (tmp_path / "active-model.env").write_text(
        "MODEL_PROVIDER='deepseek'\nMODEL_NAME='my-model'\nMODEL_API_KEY='active-key'\nMODEL_BASE_URL='https://example.com'\n",
        encoding="utf-8",
    )
    store = ConfigStore(tmp_path)
    config = store.load()
    assert config.active_model().model == "my-model"
    assert config.active_model().api_key == "active-key"
    assert config.secretsconfig.get("QQ_SMTP_AUTH_CODE") == "smtp-key"
    assert config.mouldesconfig.plugins["windows_terminal"].able
    assert (
        config.mouldesconfig.plugins["windows_terminal"].url
        == "http://host.docker.internal:8765/mcp"
    )
    assert {path.name for path in tmp_path.glob("*.json")} == {
        "models.json",
        "mouldes.json",
        "secrets.json",
        "permissions.json",
    }
    assert "active-key" not in (tmp_path / "models.json").read_text()
    assert "active-key" not in repr(config)
    assert "source-key" not in json.dumps(config.public_view())
    # JSON 是迁移后的唯一配置源，不会被旧快照覆盖。
    store.update("models", {"models": {"deepseek": {"model": "edited-model"}}})
    assert ConfigStore(tmp_path).load().active_model().model == "edited-model"


def test_patch_validation_and_concurrent_updates_preserve_other_fields(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    before = (tmp_path / "mouldes.json").read_bytes()
    with pytest.raises(ValueError):
        store.update("mouldes", {"plugins": {"web_search": {"able": "false"}}})
    assert (tmp_path / "mouldes.json").read_bytes() == before
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                ConfigStore(tmp_path).update,
                "mouldes",
                {"plugins": {name: {"able": False}}},
            )
            for name in ("web_search", "send_email")
        ]
        for future in futures:
            future.result()
    config = store.load()
    assert not config.mouldesconfig.plugins["web_search"].able
    assert not config.mouldesconfig.plugins["send_email"].able
    assert config.mouldesconfig.plugins["current_time"].able
    with pytest.raises(ValueError):
        store.update("models", {"active": "missing"})


def test_secrets_context_and_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOM_KEY", "environment")
    store = ConfigStore(tmp_path)
    config = store.update("secrets", {"keys": {"CUSTOM_KEY": "file-key"}})
    assert config.public_view()["secretsconfig"] == {"keys": {"CUSTOM_KEY": True}}
    with use_app_config(config):
        assert get_secret("CUSTOM_KEY") == "file-key"
        assert resolve_headers({"Authorization": "Bearer ${CUSTOM_KEY}"}) == {
            "Authorization": "Bearer file-key"
        }
        assert asyncio.run(asyncio.to_thread(get_secret, "CUSTOM_KEY")) == "file-key"
    assert get_secret("CUSTOM_KEY") == "environment"
    config = store.update("secrets", {"keys": {"CUSTOM_KEY": None}})
    assert "CUSTOM_KEY" not in config.secretsconfig.keys
    assert config.secretsconfig.get("CUSTOM_KEY") == "environment"


def test_corrupt_file_is_not_silently_replaced(tmp_path):
    store = ConfigStore(tmp_path)
    store.load()
    target = tmp_path / "models.json"
    target.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="models.json"):
        store.load()
    assert target.read_text() == "{broken"


def test_mcp_config_excludes_disabled_and_resolves_stdio_secrets(monkeypatch):
    from ai_agent_learning.config import plugins
    from ai_agent_learning.config.schema import PluginConfig

    config = APPConfig()
    config.mouldesconfig.plugins = {
        "disabled": PluginConfig(
            kind="http", able=False, url="https://disabled.example"
        ),
        "remote": PluginConfig(kind="http", url="https://remote.example"),
        "local": PluginConfig(
            kind="stdio",
            command="python",
            args=["server.py"],
            env={"TOKEN": "${CUSTOM_KEY}"},
        ),
        "windows_terminal": PluginConfig(
            kind="http", url="http://host.docker.internal:8765/mcp"
        ),
    }
    config.secretsconfig = config.secretsconfig.model_validate(
        {"keys": {"CUSTOM_KEY": "stdio-key"}}
    )
    # 不修改整个 os 模块，避免影响 pathlib 和测试运行器。
    monkeypatch.setattr(
        plugins, "os", SimpleNamespace(name="nt", environ={"PATH": "test-path"})
    )
    local, cloud = configured_mcp_servers(config)
    assert set(cloud) == {"remote"}
    assert local["local"].env == {"PATH": "test-path", "TOKEN": "stdio-key"}


@pytest.mark.parametrize("enabled", [False, True])
def test_graph_legal_switch_and_plugin_tool_filter(monkeypatch, enabled):
    calls = []
    captured = {}

    def fake_general(**kwargs):
        captured.update(kwargs)
        graph = StateGraph(LegalAgentState)

        def answer(state):
            calls.append("general")
            return {"messages": [AIMessage(content="general answer")]}

        graph.add_node("answer", answer)
        graph.add_edge(START, "answer")
        graph.add_edge("answer", END)
        return graph.compile()

    def fake_legal(model):
        graph = StateGraph(LegalAgentState)

        def classify(state):
            calls.append("legal")
            return {"active_case_id": None}

        graph.add_node("classify", classify)
        graph.add_edge(START, "classify")
        graph.add_edge("classify", END)
        return graph.compile()

    monkeypatch.setattr(runtime, "create_agent", fake_general)
    monkeypatch.setattr(runtime, "create_legal_graph", fake_legal)
    monkeypatch.setattr(runtime, "build_context_middleware", lambda _: object())
    app = APPConfig()
    app.mouldesconfig.legal.able = enabled
    app.mouldesconfig.plugins["web_search"].able = False
    app.mouldesconfig.plugins["github"].able = False
    from ai_agent_learning.model_config import ModelConfig

    graph = runtime.create_new_chat_agent(
        ModelConfig("glm", "test", "key", "https://example.com"),
        model=object(),
        app_config=app,
        mcp_tools=[
            SimpleNamespace(
                name="github__disabled_tool", metadata={"risk_level": "low"}
            ),
            SimpleNamespace(
                name="cloudflare_docs__search", metadata={"risk_level": "low"}
            ),
        ],
    )
    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="hello")], "active_case_id": "old-case"},
            {"configurable": {"thread_id": "test"}},
        )
    )
    assert calls == (["legal", "general"] if enabled else ["general"])
    assert result["active_case_id"] is None
    assert "check_legal_enabled" in graph.get_graph().nodes
    names = {tool.name for tool in captured["tools"]}
    assert "web_search" not in names and "github__disabled_tool" not in names
    assert {"current_time", "cloudflare_docs__search"} <= names
    assert "web_search" not in captured["system_prompt"]


def test_worker_resolves_frozen_key_reference(tmp_path):
    store = ConfigStore(tmp_path)
    config = store.update(
        "secrets", {"keys": {"CUSTOM_KEY": "frozen-key", "ZAI_API_KEY": "other-key"}}
    )
    resolved = resolve_model(
        {
            "provider": "glm",
            "model": "frozen",
            "base_url": "https://frozen.example",
            "api_key_ref": "CUSTOM_KEY",
        },
        config,
    )
    assert resolved.model == "frozen"
    assert resolved.api_key == "frozen-key"
