"""配置 API 的鉴权、校验、持久化和运行生效测试。"""

import json

from fastapi.testclient import TestClient
from test_run_api import FakePublisher, FakeStore

from chinese_legal_agent.api.app import create_app
from chinese_legal_agent.api.settings import ApiSettings
from chinese_legal_agent.config import ConfigStore


def app_for(tmp_path, token=""):
    store = FakeStore()
    app = create_app(
        store=store,
        publisher=FakePublisher(),
        event_bus=store.event_bus,
        config_store=ConfigStore(tmp_path),
        settings=ApiSettings(token=token, dispatch_interval=0.01),
    )
    return app, store


def test_configuration_roundtrip_and_model_used_for_next_run(tmp_path):
    app, store = app_for(tmp_path)
    with TestClient(app) as client:
        assert set(client.get("/v1/config").json()) == {
            "modelconfig",
            "mouldesconfig",
            "secretsconfig",
            "permissionsconfig",
        }
        response = client.patch(
            "/v1/config/secrets", json={"keys": {"CUSTOM_MODEL_KEY": "private-api-key"}}
        )
        assert response.status_code == 200
        assert response.json() == {"keys": {"CUSTOM_MODEL_KEY": True}}
        response = client.patch(
            "/v1/config/models",
            json={
                "active": "custom",
                "models": {
                    "custom": {
                        "provider": "deepseek",
                        "model": "custom-model",
                        "base_url": "https://example.com",
                        "api_key_ref": "CUSTOM_MODEL_KEY",
                    }
                },
            },
        )
        assert response.status_code == 200
        response = client.patch(
            "/v1/config/mouldes",
            json={"legal": {"able": False}, "plugins": {"web_search": {"able": False}}},
        )
        assert response.status_code == 200
        assert response.json()["legal"] == {"able": False}
        assert app.state.app_config.mouldesconfig.legal.able is False
        assert "private-api-key" not in client.get("/v1/config").text
        run = client.post("/v1/runs", json={"message": "hello"})
        assert run.status_code == 202
        assert run.json()["model"] == "custom-model"
        assert "private-api-key" not in json.dumps(store.rows, default=str)
        assert (
            next(iter(store.rows.values()))["model_config"]["api_key_ref"]
            == "CUSTOM_MODEL_KEY"
        )
    reloaded = ConfigStore(tmp_path).load()
    assert not reloaded.mouldesconfig.legal.able
    assert reloaded.active_model().api_key == "private-api-key"


def test_config_endpoints_require_existing_api_authorization(tmp_path):
    app, _ = app_for(tmp_path, token="test-token")
    with TestClient(app) as client:
        assert client.get("/v1/config").status_code == 401
        assert (
            client.patch(
                "/v1/config/secrets", json={"keys": {"K": "secret"}}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/v1/config/secrets", headers={"Authorization": "Bearer test-token"}
            ).status_code
            == 200
        )


def test_invalid_patch_does_not_write_or_echo_secrets(tmp_path):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        before = (tmp_path / "secrets.json").read_bytes()
        response = client.patch(
            "/v1/config/secrets", json={"keys": {"INVALID-KEY": "private-key"}}
        )
        assert response.status_code == 422
        assert "private-key" not in response.text
        assert (tmp_path / "secrets.json").read_bytes() == before
        assert (
            client.patch(
                "/v1/config/mouldes", json={"legal": {"able": "false"}}
            ).status_code
            == 422
        )
        assert (
            client.patch("/v1/config/models", json={"active": "missing"}).status_code
            == 422
        )
        assert client.get("/v1/config/unknown").status_code == 422


def test_plugin_connection_maps_can_remove_values_and_secrets_can_disable_or_delete(
    tmp_path,
):
    app, _ = app_for(tmp_path)
    with TestClient(app) as client:
        connection = {
            "kind": "http",
            "able": True,
            "url": "https://example.com/mcp",
            "headers": {
                "Authorization": "Bearer ${CUSTOM_TOKEN}",
                "X-Old": "remove-me",
            },
            "read_timeout_seconds": 90,
            "trust_env": False,
        }
        assert (
            client.patch(
                "/v1/config/mouldes", json={"plugins": {"custom": connection}}
            ).status_code
            == 200
        )
        response = client.patch(
            "/v1/config/mouldes",
            json={
                "plugins": {
                    "custom": {"headers": {"Authorization": "Bearer ${CUSTOM_TOKEN}"}}
                }
            },
        )
        assert response.status_code == 200
        custom = response.json()["plugins"]["custom"]
        assert custom["headers"] == {"Authorization": "Bearer ${CUSTOM_TOKEN}"}
        assert custom["read_timeout_seconds"] == 90
        response = client.patch(
            "/v1/config/mouldes",
            json={
                "plugins": {
                    "custom": {
                        "kind": "stdio",
                        "url": None,
                        "headers": {},
                        "command": "python",
                        "args": ["-m", "my_server"],
                        "cwd": "/app",
                        "env": {"CUSTOM_TOKEN": "${CUSTOM_TOKEN}", "OLD": "remove-me"},
                    }
                }
            },
        )
        assert response.status_code == 200
        response = client.patch(
            "/v1/config/mouldes", json={"plugins": {"custom": {"env": {}}}}
        )
        assert response.json()["plugins"]["custom"]["env"] == {}
        assert response.json()["plugins"]["custom"]["headers"] == {}
        assert (
            client.patch(
                "/v1/config/secrets",
                json={"keys": {"CUSTOM_TOKEN": "test-private-token"}},
            ).json()["keys"]["CUSTOM_TOKEN"]
            is True
        )
        assert (
            client.patch(
                "/v1/config/secrets", json={"keys": {"CUSTOM_TOKEN": ""}}
            ).json()["keys"]["CUSTOM_TOKEN"]
            is False
        )
        assert (
            "CUSTOM_TOKEN"
            not in client.patch(
                "/v1/config/secrets", json={"keys": {"CUSTOM_TOKEN": None}}
            ).json()["keys"]
        )
        assert "test-private-token" not in client.get("/v1/config").text
