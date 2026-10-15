"""API 和 Celery 共用配置，不在日志中输出连接凭据。"""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dotenv import load_dotenv

from chinese_legal_agent.model_config import source_config_path


@dataclass(frozen=True)
class ApiSettings:
    redis_url: str = field(default="redis://127.0.0.1:6379/0", repr=False)
    token: str = field(default="", repr=False)
    heartbeat_interval: float = 15.0
    cancel_check_interval: float = 1.0
    dispatch_interval: float = 0.2
    visibility_timeout: int = 3600

    @classmethod
    def from_env(cls) -> "ApiSettings":
        load_dotenv(source_config_path(), override=False)
        redis_url = os.getenv("AGENT_REDIS_URL", "redis://127.0.0.1:6379/0").strip()
        parsed = urlsplit(redis_url)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("AGENT_REDIS_URL 必须是 Redis 地址")
        timeout = int(os.getenv("AGENT_VISIBILITY_TIMEOUT", "3600"))
        if timeout < 60:
            raise ValueError("AGENT_VISIBILITY_TIMEOUT 至少为 60 秒")
        return cls(
            redis_url=redis_url,
            token=os.getenv("AGENT_API_TOKEN", "").strip(),
            visibility_timeout=timeout,
        )
