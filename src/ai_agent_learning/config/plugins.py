"""根据 able 创建 MCP 连接配置；禁用的服务不会启动或发现工具。"""

import os

from mcp import StdioServerParameters

from .schema import APPConfig


def configured_mcp_servers(config: APPConfig):
    from ai_agent_learning.mcp_client import resolve_headers

    local = {}
    cloud = {}
    for name, plugin in config.mouldesconfig.plugins.items():
        if not plugin.able or plugin.kind == "builtin":
            continue
        # 旧配置可能仍启用宿主终端；当前 Agent 不接入它。
        if name == "windows_terminal":
            continue
        if plugin.kind == "http":
            cloud[name] = {
                "url": plugin.url,
                "headers": plugin.headers,
                "read_timeout_seconds": plugin.read_timeout_seconds,
                "trust_env": plugin.trust_env,
            }
        else:
            # stdio 的 env 也支持引用 secrets.json，保留宿主 PATH 等变量。
            local[name] = StdioServerParameters(
                command=plugin.command,
                args=plugin.args,
                cwd=plugin.cwd,
                env={
                    **os.environ,
                    **resolve_headers(plugin.env, secrets=config.secretsconfig),
                },
            )
    return local, cloud
