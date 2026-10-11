"""模型适配结构、参数校验及部署配置路径。"""

from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


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
    from chinese_legal_agent.config import config_dir

    return config_dir() / ".env"
