"""为一次运行绑定配置快照，避免修改进程环境或串用不同任务的 Key。"""

import os
from contextlib import contextmanager
from contextvars import ContextVar

from .schema import APPConfig

_current: ContextVar[APPConfig | None] = ContextVar("app_config", default=None)


@contextmanager
def use_app_config(config: APPConfig):
    token = _current.set(config)
    try:
        yield config
    finally:
        _current.reset(token)


def get_secret(name: str) -> str:
    config = _current.get()
    return (
        config.secretsconfig.get(name)
        if config is not None
        else os.getenv(name, "").strip()
    )
