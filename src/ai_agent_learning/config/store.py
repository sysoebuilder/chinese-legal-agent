"""启动加载、旧配置迁移与按文件原子更新。"""

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

from ai_agent_learning.paths import get_data_dir

from .schema import (
    APPConfig,
    ModelsConfig,
    MouldesConfig,
    PermissionsConfig,
    SecretsConfig,
)

_lock = threading.RLock()
SECTIONS = {
    "models": ("modelconfig", ModelsConfig),
    "mouldes": ("mouldesconfig", MouldesConfig),
    "secrets": ("secretsconfig", SecretsConfig),
    "permissions": ("permissionsconfig", PermissionsConfig),
}


def config_dir() -> Path:
    configured = os.getenv("AGENT_CONFIG_DIR")
    return Path(configured).expanduser().resolve() if configured else get_data_dir()


def _merge(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or config_dir()

    @contextmanager
    def _locked(self):
        # API 和多个 worker 共用文件；锁住读-校验-替换，避免覆盖并发修改。
        with _lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / ".config.lock").open("a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write(self, section: str, value) -> None:
        payload = value.model_dump(mode="json")
        if isinstance(value, SecretsConfig):
            payload = {
                "keys": {
                    name: secret.get_secret_value()
                    for name, secret in value.keys.items()
                }
            }
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.directory / f"{section}.json")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _legacy_config(self) -> APPConfig:
        config = APPConfig()
        # 显式配置目录兼容 Compose 原来的 data 挂载路径。
        legacy_dir = self.directory
        source = dotenv_values(
            legacy_dir / ".env", interpolate=False, encoding="utf-8-sig"
        )
        active = dotenv_values(
            legacy_dir / "active-model.env", interpolate=False, encoding="utf-8-sig"
        )
        for name, value in source.items():
            if value is not None and any(
                marker in name
                for marker in ("KEY", "TOKEN", "AUTH_CODE", "SMTP_USERNAME")
            ):
                config.secretsconfig.keys[name] = SecretStr(value)
        for provider, names in {
            "glm": ("GLM_MODEL", "GLM_BASE_URL", "ZAI_API_KEY"),
            "deepseek": ("DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY"),
        }.items():
            model_name, url_name, _ = names
            entry = config.modelconfig.models[provider]
            model_value = source.get(model_name)
            if model_value:
                entry.model = model_value
            url_value = source.get(url_name)
            if url_value:
                entry.base_url = url_value
        provider = active.get("MODEL_PROVIDER")
        if provider is not None and provider in config.modelconfig.models:
            config.modelconfig.active = provider
            entry = config.modelconfig.models[provider]
            entry.model = active.get("MODEL_NAME") or entry.model
            entry.base_url = active.get("MODEL_BASE_URL") or entry.base_url
            api_key = active.get("MODEL_API_KEY")
            if api_key:
                config.secretsconfig.keys[entry.api_key_ref] = SecretStr(api_key)
        return APPConfig.model_validate(config.model_dump())

    def _load(self) -> APPConfig:
        defaults = (
            self._legacy_config()
            if not any((self.directory / f"{name}.json").exists() for name in SECTIONS)
            else APPConfig()
        )
        values = {}
        # 凭据先写入；首次迁移过程中退出时不会遗失已迁移的 Key。
        for section in ("secrets", "models", "mouldes", "permissions"):
            field, schema = SECTIONS[section]
            path = self.directory / f"{section}.json"
            if path.exists():
                try:
                    values[field] = schema.model_validate_json(
                        path.read_text(encoding="utf-8-sig")
                    )
                except (ValueError, UnicodeError):
                    raise ValueError(f"{section}.json 格式或字段无效") from None
            else:
                values[field] = getattr(defaults, field)
                self._write(section, values[field])
        return APPConfig(**values)

    def load(self) -> APPConfig:
        with self._locked():
            return self._load()

    def update(self, section: str, patch: dict) -> APPConfig:
        if section not in SECTIONS:
            raise ValueError("未知配置分类")
        field, schema = SECTIONS[section]
        with self._locked():
            current = self._load()
            value = getattr(current, field)
            if section == "secrets":
                payload = {
                    "keys": {
                        name: secret.get_secret_value()
                        for name, secret in value.keys.items()
                    }
                }
                if set(patch) - {"keys"} or not isinstance(patch.get("keys", {}), dict):
                    raise ValueError("secrets 只接受 keys 对象")
                for name, secret in patch.get("keys", {}).items():
                    if secret is None:
                        payload["keys"].pop(name, None)
                    elif not isinstance(secret, str):
                        raise ValueError("密钥必须是字符串或 null")
                    else:
                        payload["keys"][name] = secret
            else:
                payload = _merge(value.model_dump(mode="json"), patch)
                if section == "mouldes" and isinstance(patch.get("plugins"), dict):
                    # 连接编辑器提交完整映射，允许移除旧请求头和环境变量。
                    for name, plugin_patch in patch["plugins"].items():
                        if isinstance(plugin_patch, dict):
                            for mapping in ("headers", "env"):
                                if mapping in plugin_patch:
                                    payload["plugins"][name][mapping] = plugin_patch[
                                        mapping
                                    ]
            try:
                updated = schema.model_validate(payload)
            except ValueError:
                raise ValueError(f"{section} 配置字段无效") from None
            self._write(section, updated)
            setattr(current, field, updated)
            return current



def load_app_config() -> APPConfig:
    return ConfigStore().load()
