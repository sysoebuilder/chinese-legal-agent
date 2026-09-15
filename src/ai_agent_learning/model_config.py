"""候选配置来自数据目录 .env；运行时只使用 active-model.env 中的快照。"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

from ai_agent_learning.paths import get_data_dir
from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL

PROVIDER_DEFAULTS = {
    "glm": ("GLM-5.3-flash", "https://open.bigmodel.cn/api/paas/v4/"),
    "deepseek": ("deepseek-v4-flash", "https://api.deepseek.com"),
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
    return get_data_dir() / ".env"


def active_config_path() -> Path:
    return get_data_dir() / "active-model.env"


def create_model_config(provider: str, api_key: str, model: str = "") -> ModelConfig:
    try:
        default_model, base_url = PROVIDER_DEFAULTS[provider]
    except KeyError as error:
        raise ValueError("模型提供商只支持 glm 或 deepseek") from error
    return ModelConfig(provider, model.strip() or default_model, api_key.strip(), base_url)


def save_source_model(config: ModelConfig) -> Path:
    """保存首次配置，同时写入固定的本机 Qdrant 服务地址。"""
    config.validate()
    target = source_config_path()
    names = {
        "glm": ("ZAI_API_KEY", "GLM_MODEL", "GLM_BASE_URL"),
        "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"),
    }
    key_name, model_name, url_name = names[config.provider]
    for name, value in (
        (key_name, config.api_key),
        (model_name, config.model),
        (url_name, config.base_url),
        ("QDRANT_URL", DEFAULT_QDRANT_URL),
    ):
        set_key(str(target), name, value, quote_mode="always")
    return target


def load_model_choices() -> list[ModelConfig]:
    # 不修改环境变量，不从其他文件或默认模型补齐用户未配置的字段。
    values = dotenv_values(source_config_path(), interpolate=False, encoding="utf-8-sig")

    def value(name: str) -> str:
        return (values.get(name) or "").strip()

    return [
        ModelConfig("glm", value("GLM_MODEL"), value("ZAI_API_KEY"), value("GLM_BASE_URL")),
        ModelConfig("deepseek", value("DEEPSEEK_MODEL"), value("DEEPSEEK_API_KEY"), value("DEEPSEEK_BASE_URL")),
    ]


def save_active_model(config: ModelConfig) -> Path:
    config.validate()
    target = active_config_path()
    # 先完整写入临时文件，再替换当前配置，避免留下部分更新的模型/Key。
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".env", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        for name, value in config.as_env().items():
            set_key(str(temporary), name, value, quote_mode="always")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def load_active_model() -> ModelConfig:
    try:
        target = active_config_path()
        with target.open(encoding="utf-8-sig") as handle:
            values = dotenv_values(stream=handle, interpolate=False)
        config = ModelConfig(
            provider=(values.get("MODEL_PROVIDER") or "").strip(),
            model=(values.get("MODEL_NAME") or "").strip(),
            api_key=(values.get("MODEL_API_KEY") or "").strip(),
            base_url=(values.get("MODEL_BASE_URL") or "").strip(),
        )
        config.validate()
        return config
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit("当前模型配置缺失或无效，请运行 agent100 config 选择模型。") from error
