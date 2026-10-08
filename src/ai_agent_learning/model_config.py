"""模型适配结构及兼容入口；持久化统一交给 config 模块。"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ai_agent_learning.paths import get_data_dir

PROVIDER_DEFAULTS = {
    "glm": ("GLM-5.3-flash", "https://open.bigmodel.cn/api/paas/v4/"),
    "deepseek": ("deepseek-flash", "https://api.deepseek.com"),
}


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str

    def as_env(self) -> dict[str, str]:
        return {
            "MODEL_PROVIDER": self.provider,
            "MODEL_NAME": self.model,
            "MODEL_API_KEY": self.api_key,
            "MODEL_BASE_URL": self.base_url,
        }

    def validate(self) -> None:
        # 模型名称由用户自由输入，不在本地校验名称或可用性。
        missing = [
            name for name, value in self.as_env().items()
            if name != "MODEL_NAME" and not value.strip()
        ]
        if missing:
            raise ValueError("缺少配置：" + ", ".join(missing))
        if self.provider not in {"glm", "deepseek"}:
            raise ValueError("MODEL_PROVIDER 只支持 glm 或 deepseek")
        url = urlsplit(self.base_url)
        if url.scheme not in {"https", "http"} or not url.hostname:
            raise ValueError("MODEL_BASE_URL 必须是完整的 HTTP(S) 地址")


def source_config_path() -> Path:
    return _config_dir() / ".env"


def active_config_path() -> Path:
    return _config_dir() / "active-model.env"


def _config_dir() -> Path:
    configured = os.getenv("LLM_CLI_CONFIG_DIR")
    directory = Path(configured).expanduser().resolve() if configured else get_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def create_model_config(provider: str, api_key: str, model: str = "") -> ModelConfig:
    try:
        default_model, base_url = PROVIDER_DEFAULTS[provider]
    except KeyError as error:
        raise ValueError("模型提供商只支持 glm 或 deepseek") from error
    return ModelConfig(provider, model.strip() or default_model, api_key.strip(), base_url)


def save_source_model(config: ModelConfig) -> Path:
    """保存模型和对应凭据，分别写入 models.json 与 secrets.json。"""
    from ai_agent_learning.config import ConfigStore

    return ConfigStore().save_model(config, activate=False)


def load_model_choices() -> list[ModelConfig]:
    from ai_agent_learning.config import load_app_config

    app = load_app_config()
    return [
        ModelConfig(entry.provider, entry.model, app.secretsconfig.get(entry.api_key_ref), entry.base_url)
        for entry in app.modelconfig.models.values()
    ]


def save_active_model(config: ModelConfig) -> Path:
    from ai_agent_learning.config import ConfigStore

    return ConfigStore().save_model(config)


def load_active_model() -> ModelConfig:
    from ai_agent_learning.config import load_app_config

    try:
        return load_app_config().active_model()
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit("当前模型配置缺失或无效，请运行 agent100 config 选择模型。") from error
