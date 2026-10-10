"""初始化 CLI 的首次运行、重跑与失败退出；不连接任何外部服务。"""

import json
import os
import subprocess
import sys


def run_initializer(directory):
    environment = os.environ.copy()
    environment.pop("WINDOWS_MCP_URL", None)
    environment.pop("LAWS_INIT_ENABLED", None)
    environment.pop("RERANKER_INIT_ENABLED", None)
    # 子进程仅使用指定临时目录，不读取项目 data/。
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_agent_learning.initialize",
            "--config-dir",
            str(directory),
        ],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


def test_initialize_creates_config_without_keys_and_preserves_user_edits(tmp_path):
    directory = tmp_path / "config"
    result = run_initializer(directory)
    assert result.returncode == 0, result.stderr
    assert {path.name for path in directory.glob("*.json")} == {
        "models.json",
        "mouldes.json",
        "secrets.json",
        "permissions.json",
    }
    assert json.loads((directory / "secrets.json").read_text()) == {"keys": {}}
    permissions_path = directory / "permissions.json"
    assert json.loads(permissions_path.read_text()) == {"mode": "unrestricted"}
    permissions_path.write_text('{"mode":"restricted"}', encoding="utf-8")
    models_path = directory / "models.json"
    models = json.loads(models_path.read_text())
    models["active"] = "deepseek"
    models["models"]["deepseek"]["model"] = "user-model"
    models_path.write_text(json.dumps(models), encoding="utf-8")
    secrets_path = directory / "secrets.json"
    secrets_path.write_text(
        '{"keys":{"DEEPSEEK_API_KEY":"private-test-key"}}', encoding="utf-8"
    )
    before = {path.name: path.read_bytes() for path in directory.glob("*.json")}
    result = run_initializer(directory)
    assert result.returncode == 0, result.stderr
    assert {path.name: path.read_bytes() for path in directory.glob("*.json")} == before
    assert "private-test-key" not in result.stdout + result.stderr


def test_initialize_fills_missing_files_without_overwriting_existing_config(tmp_path):
    secrets_path = tmp_path / "secrets.json"
    secrets_path.write_text(
        '{"keys":{"ZAI_API_KEY":"keep-test-key"}}', encoding="utf-8"
    )
    before = secrets_path.read_bytes()
    result = run_initializer(tmp_path)
    assert result.returncode == 0, result.stderr
    assert secrets_path.read_bytes() == before
    assert (tmp_path / "models.json").is_file()
    assert (tmp_path / "mouldes.json").is_file()
    assert (tmp_path / "permissions.json").is_file()


def test_initialize_fails_on_invalid_config_without_echoing_or_replacing_it(tmp_path):
    path = tmp_path / "secrets.json"
    original = '{"keys":{"ZAI_API_KEY":"private-test-key"},"unknown":true}'
    path.write_text(original, encoding="utf-8")
    result = run_initializer(tmp_path)
    assert result.returncode == 1
    assert path.read_text() == original
    assert "private-test-key" not in result.stdout + result.stderr
    assert not (tmp_path / "models.json").exists()


def test_initialize_migrates_legacy_model_config(tmp_path):
    (tmp_path / "active-model.env").write_text(
        "MODEL_PROVIDER=deepseek\nMODEL_NAME=legacy-model\n"
        "MODEL_BASE_URL=https://api.deepseek.com\nMODEL_API_KEY=legacy-test-key\n",
        encoding="utf-8",
    )
    result = run_initializer(tmp_path)
    assert result.returncode == 0, result.stderr
    models = json.loads((tmp_path / "models.json").read_text())
    assert models["active"] == "deepseek"
    assert models["models"]["deepseek"]["model"] == "legacy-model"
    secrets = json.loads((tmp_path / "secrets.json").read_text())
    assert secrets["keys"]["DEEPSEEK_API_KEY"] == "legacy-test-key"
    assert "legacy-test-key" not in result.stdout + result.stderr
