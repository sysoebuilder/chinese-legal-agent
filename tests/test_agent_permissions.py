"""权限接口、风险分档与真实 Agent 审批流程；模型和工具均使用离线替身。"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from test_config_api import app_for

from chinese_legal_agent.api.runtime import resolve_permissions
from chinese_legal_agent.config import ConfigStore, PermissionsConfig
from chinese_legal_agent.langchain_agent import runtime
from chinese_legal_agent.langchain_agent.approval import build_interrupt_on
from chinese_legal_agent.langchain_agent.tools.risk import RiskLevel
from chinese_legal_agent.model_config import ModelConfig


@pytest.mark.parametrize("mode", ["restricted", "unrestricted"])
def test_permissions_cover_all_risk_levels_and_mcp_tools(mode):
    tools = [
        SimpleNamespace(name=level.value, metadata={"risk_level": level.value})
        for level in RiskLevel
    ] + [
        SimpleNamespace(name="send_email", metadata={"risk_level": "high"}),
        SimpleNamespace(name="remote__write", metadata={"risk_level": "high"}),
    ]
    approvals = build_interrupt_on(tools, PermissionsConfig(mode=mode))
    assert set(approvals) == (
        {"medium", "high", "critical", "send_email", "remote__write"}
        if mode == "restricted"
        else set()
    )
    assert all(
        item["allowed_decisions"] == ["approve", "reject"]
        for item in approvals.values()
    )
    # 即使无限制也不能放过缺少有效风险元数据的工具。
    with pytest.raises(ValueError, match="risk_level"):
        build_interrupt_on(
            [SimpleNamespace(name="unknown", metadata={})], PermissionsConfig(mode=mode)
        )


def test_permissions_api_authentication_validation_and_persistence(tmp_path):
    app, _ = app_for(tmp_path, token="test-token")
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        assert client.get("/v1/config/permissions").status_code == 401
        assert (
            client.patch(
                "/v1/config/permissions", json={"mode": "restricted"}
            ).status_code
            == 401
        )
        assert client.get("/v1/config/permissions", headers=headers).json() == {
            "mode": "unrestricted"
        }
        original = (tmp_path / "models.json").read_bytes()
        for mode in ("restricted", "unrestricted"):
            response = client.patch(
                "/v1/config/permissions", json={"mode": mode}, headers=headers
            )
            assert response.status_code == 200
            assert response.json() == {"mode": mode}
            assert ConfigStore(tmp_path).load().permissionsconfig.mode == mode
            assert app.state.app_config.permissionsconfig.mode == mode
            assert client.get("/v1/config", headers=headers).json()[
                "permissionsconfig"
            ] == {"mode": mode}
        before = (tmp_path / "permissions.json").read_bytes()
        for body in (
            {"mode": "custom"},
            {"mode": True},
            {"mode": None},
            {"risk_levels": []},
        ):
            assert (
                client.patch(
                    "/v1/config/permissions", json=body, headers=headers
                ).status_code
                == 422
            )
            assert (tmp_path / "permissions.json").read_bytes() == before
        assert (tmp_path / "models.json").read_bytes() == original
    with TestClient(app) as client:
        assert client.get("/v1/config/permissions", headers=headers).json() == {
            "mode": "unrestricted"
        }


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize(
    "mode,decision",
    [("restricted", "approve"), ("restricted", "reject"), ("unrestricted", None)],
)
def test_saved_permissions_control_tool_execution_in_agent(
    tmp_path, monkeypatch, mode, decision
):
    app, store = app_for(tmp_path)
    with TestClient(app) as client:
        assert (
            client.patch("/v1/config/permissions", json={"mode": mode}).status_code
            == 200
        )
        assert (
            client.patch(
                "/v1/config/secrets", json={"keys": {"ZAI_API_KEY": "fake-key"}}
            ).status_code
            == 200
        )
        assert client.post("/v1/runs", json={"message": "send"}).status_code == 202
    frozen = next(iter(store.rows.values()))["model_config"]
    assert frozen["permission_mode"] == mode
    config = ConfigStore(tmp_path).load()
    config.permissionsconfig = resolve_permissions(frozen, config)
    config.mouldesconfig.legal.able = False
    calls = []

    def record_send(message: str) -> str:
        """记录测试调用，不发送真实邮件。"""
        calls.append(message)
        return "recorded"

    tool = StructuredTool.from_function(
        record_send, name="send_email", metadata={"risk_level": "high"}
    )
    monkeypatch.setattr(runtime, "ALL_TOOLS", [tool])
    model = ToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "send_email", "args": {"message": "test"}, "id": "call-1"}
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    graph = runtime.create_new_chat_agent(
        ModelConfig("glm", "test", "fake-key", "https://example.com"),
        model=model,
        app_config=config,
    )
    run_config = {"configurable": {"thread_id": "permissions-test"}}

    async def run():
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="send")]}, run_config
        )
        if mode == "restricted":
            assert calls == []
            assert (
                result["__interrupt__"][0].value["action_requests"][0]["name"]
                == "send_email"
            )
            # 模拟暂停期间修改设置、Worker 重建 Agent 后恢复原运行。
            updated = ConfigStore(tmp_path).update(
                "permissions", {"mode": "unrestricted"}
            )
            assert updated.permissionsconfig.mode == "unrestricted"
            updated.permissionsconfig = resolve_permissions(frozen, updated)
            assert updated.permissionsconfig.mode == "restricted"
            updated.mouldesconfig.legal.able = False
            resumed_graph = runtime.create_new_chat_agent(
                ModelConfig("glm", "test", "fake-key", "https://example.com"),
                model=model,
                app_config=updated,
                checkpointer=graph.checkpointer,
            )
            result = await resumed_graph.ainvoke(
                Command(resume={"decisions": [{"type": decision}]}), run_config
            )
        else:
            assert not result.get("__interrupt__")
        assert result["messages"][-1].content == "done"

    asyncio.run(run())
    assert calls == (["test"] if decision != "reject" else [])
