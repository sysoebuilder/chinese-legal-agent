"""配置文件的结构和校验；able 沿用项目配置约定。"""

import os
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_validator,
    model_validator,
)


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelEntry(ConfigModel):
    provider: Literal["glm", "deepseek"]
    model: str
    base_url: str
    api_key_ref: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("base_url 必须是无认证信息的 HTTP(S) 地址")
        return value


def default_models() -> dict[str, ModelEntry]:
    return {
        "glm": ModelEntry(
            provider="glm",
            model="GLM-5.3-flash",
            base_url="https://open.bigmodel.cn/api/paas/v4/",
            api_key_ref="ZAI_API_KEY",
        ),
        "deepseek": ModelEntry(
            provider="deepseek",
            model="deepseek-flash",
            base_url="https://api.deepseek.com",
            api_key_ref="DEEPSEEK_API_KEY",
        ),
    }


class ModelsConfig(ConfigModel):
    active: str = "glm"
    models: dict[str, ModelEntry] = Field(default_factory=default_models)

    @model_validator(mode="after")
    def active_exists(self):
        if self.active not in self.models:
            raise ValueError("active 必须对应 models 中的模型名称")
        return self


class PluginSwitch(ConfigModel):
    able: StrictBool = True


class PluginConfig(PluginSwitch):
    kind: Literal["builtin", "http", "stdio"] = "builtin"
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    read_timeout_seconds: float = Field(default=60, gt=0)
    trust_env: StrictBool = True

    @model_validator(mode="after")
    def connection_valid(self):
        if self.kind == "http" and self.url:
            ModelEntry.validate_url(self.url)
        # 未启用的 HTTP MCP 可等配置地址后再启用。
        if self.kind == "http" and not self.url and self.able:
            raise ValueError("HTTP 插件需要 url")
        if self.kind == "stdio" and (not self.command or not self.command.strip()):
            raise ValueError("stdio 插件需要 command")
        if self.kind == "builtin" and (
            self.url or self.command or self.headers or self.env
        ):
            raise ValueError("内置插件不接受连接参数")
        return self


BUILTIN_PLUGINS = {
    "current_time",
    "web_search",
    "search_memory",
    "list_memories",
    "send_email",
    "run_command",
    "update_memory",
    "delete_memory",
}


def default_plugins() -> dict[str, PluginConfig]:
    plugins = {name: PluginConfig() for name in sorted(BUILTIN_PLUGINS)}
    plugins.update(
        {
            "github": PluginConfig(
                kind="http",
                url="https://api.githubcopilot.com/mcp/",
                headers={"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
            ),
            "cloudflare_docs": PluginConfig(
                kind="http", url="https://docs.mcp.cloudflare.com/mcp"
            ),
        }
    )
    return plugins


class MouldesConfig(ConfigModel):
    legal: PluginSwitch = Field(default_factory=PluginSwitch)
    plugins: dict[str, PluginConfig] = Field(default_factory=default_plugins)

    @model_validator(mode="after")
    def valid_plugin_names(self):
        for name, plugin in self.plugins.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or "__" in name:
                raise ValueError("插件名称仅允许字母、数字、单下划线、连字符")
            if plugin.kind == "builtin" and name not in BUILTIN_PLUGINS:
                raise ValueError("未知的内置插件")
        return self

    def tool_enabled(self, name: str) -> bool:
        plugin_name, separator, _ = name.partition("__")
        if plugin_name == "windows_terminal":
            return False
        plugin = self.plugins.get(plugin_name)
        if plugin is None or not plugin.able:
            return False
        return plugin.kind != "builtin" if separator else plugin.kind == "builtin"


class SecretsConfig(ConfigModel):
    keys: dict[str, SecretStr] = Field(default_factory=dict, repr=False)

    @field_validator("keys")
    @classmethod
    def valid_keys(cls, keys):
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in keys):
            raise ValueError("密钥名称必须是合法的环境变量名称")
        return keys

    def get(self, name: str) -> str:
        if name in self.keys:
            return self.keys[name].get_secret_value().strip()
        return os.getenv(name, "").strip()


class PermissionsConfig(ConfigModel):
    # 沿用原来的空审批风险集合；用户可切换到限制模式。
    mode: Literal["restricted", "unrestricted"] = "unrestricted"


class APPConfig(ConfigModel):
    modelconfig: ModelsConfig = Field(default_factory=ModelsConfig)
    mouldesconfig: MouldesConfig = Field(default_factory=MouldesConfig)
    secretsconfig: SecretsConfig = Field(default_factory=SecretsConfig, repr=False)
    permissionsconfig: PermissionsConfig = Field(default_factory=PermissionsConfig)

    def active_model(self):
        from ai_agent_learning.model_config import ModelConfig

        entry = self.modelconfig.models[self.modelconfig.active]
        config = ModelConfig(
            entry.provider,
            entry.model,
            self.secretsconfig.get(entry.api_key_ref),
            entry.base_url,
        )
        config.validate()
        return config

    def public_view(self) -> dict:
        return {
            "modelconfig": self.modelconfig.model_dump(mode="json"),
            "mouldesconfig": self.mouldesconfig.model_dump(mode="json"),
            "permissionsconfig": self.permissionsconfig.model_dump(mode="json"),
            "secretsconfig": {
                "keys": {
                    name: bool(value.get_secret_value().strip())
                    for name, value in self.secretsconfig.keys.items()
                }
            },
        }
