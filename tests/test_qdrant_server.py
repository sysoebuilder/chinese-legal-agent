import httpx
import pytest

from ai_agent_learning.qdrant_server import QdrantServer


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [True, False])
async def test_qdrant_check_never_creates_a_native_database(monkeypatch, tmp_path, available):
    original_client = httpx.AsyncClient

    def respond(request):
        assert str(request.url) == "http://qdrant:6333/collections"
        if available:
            return httpx.Response(200, json={"result": {"collections": []}})
        raise httpx.ConnectError("offline", request=request)

    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(respond)
    ))
    server = QdrantServer()
    if available:
        await server.start()
        await server.close()
    else:
        with pytest.raises(RuntimeError, match="docker compose up"):
            await server.start()
    assert list(tmp_path.iterdir()) == []


def test_configuration_directory_is_independent_from_database_data(monkeypatch, tmp_path):
    from ai_agent_learning.config import ConfigStore
    from ai_agent_learning.model_config import source_config_path

    configured = tmp_path / "config"
    monkeypatch.setenv("AGENT_CONFIG_DIR", str(configured))
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / "database-data"))
    store = ConfigStore()
    store.update("secrets", {"keys": {"DEEPSEEK_API_KEY": "test-key"}})
    config = store.update("models", {"active": "deepseek"})
    assert config.active_model().provider == "deepseek"
    assert config.active_model().api_key == "test-key"
    assert (configured / "models.json").exists()
    assert source_config_path() == configured / ".env"
    assert not (tmp_path / "database-data").exists()
