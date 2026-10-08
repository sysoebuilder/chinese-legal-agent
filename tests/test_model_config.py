import json
from pathlib import Path

from ai_agent_learning.model_config import (
    create_model_config,
    save_source_model,
)


def test_first_config_separates_model_and_credentials(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("LLM_CLI_CONFIG_DIR", str(tmp_path))

    config = create_model_config("glm", "secret-key", "")
    path = save_source_model(config)
    values = json.loads(path.read_text(encoding="utf-8"))

    assert config.model == "GLM-5.3-flash"
    assert config.base_url == "https://open.bigmodel.cn/api/paas/v4/"
    assert values["models"]["glm"]["model"] == config.model
    assert values["models"]["glm"]["api_key_ref"] == "ZAI_API_KEY"
    assert "secret-key" not in path.read_text(encoding="utf-8")
    assert json.loads((tmp_path / "secrets.json").read_text())["keys"]["ZAI_API_KEY"] == "secret-key"
