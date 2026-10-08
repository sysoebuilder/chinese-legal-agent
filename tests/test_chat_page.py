"""后端不再提供前端页面或资源；业务接口仍需认证。"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from ai_agent_learning.api.app import create_app
from ai_agent_learning.api.settings import ApiSettings


def test_backend_only_serves_api_and_preserves_authentication():
    app = create_app(
        store=SimpleNamespace(pending_dispatches=list),
        publisher=SimpleNamespace(publish=lambda *_: None),
        settings=ApiSettings(token="test-token", dispatch_interval=0.01),
    )
    with TestClient(app) as client:
        assert client.get("/").status_code == 404
        assert client.get("/assets/chat.js").status_code == 404
        assert not any(
            getattr(route, "path", "").startswith("/assets") for route in app.routes
        )
        assert client.get("/v1/sessions").status_code == 401
        assert "/" not in client.get("/openapi.json").json()["paths"]
        assert client.get("/assets/../app.py").status_code == 404
