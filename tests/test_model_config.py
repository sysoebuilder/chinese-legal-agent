from pathlib import Path

from dotenv import dotenv_values

from ai_agent_learning.model_config import (
    create_model_config,
    save_source_model,
)


def test_first_config_uses_builtin_url_and_creates_source_file(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "ai_agent_learning.model_config.get_data_dir", lambda: tmp_path,
    )

    config = create_model_config("glm", "secret-key", "")
    path = save_source_model(config)
    values = dotenv_values(path)

    assert config.model == "GLM-5.3-flash"
    assert config.base_url == "https://open.bigmodel.cn/api/paas/v4/"
    assert values == {
        "ZAI_API_KEY": "secret-key",
        "GLM_MODEL": "GLM-5.3-flash",
        "GLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4/",
        "QDRANT_URL": "http://127.0.0.1:6333",
    }
