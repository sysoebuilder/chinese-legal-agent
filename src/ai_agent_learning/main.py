"""不依赖聊天运行时的命令入口。"""

import asyncio

from ai_agent_learning.law_install import install_laws
from ai_agent_learning.model_config import (
    load_model_choices,
    save_active_model,
    save_source_model,
    source_config_path,
)
from ai_agent_learning.ui.terminal import TerminalUI


def run_config() -> None:
    """选择模型配置并保存为当前配置。"""
    ui = TerminalUI()
    try:
        source = source_config_path()
        choices = load_model_choices() if source.is_file() else []
        configured = False
        for choice in choices:
            try:
                choice.validate()
                configured = True
                break
            except ValueError:
                continue
        if configured:
            ui.show_notice(f"候选配置来源：{source}")
            selected = ui.select_model(choices)
        else:
            ui.show_notice("未检测到有效模型配置，开始首次配置。")
            selected = ui.setup_first_model()
        if selected is None:
            return
        if not configured:
            save_source_model(selected)
        path = save_active_model(selected)
    except (OSError, UnicodeError, ValueError):
        ui.show_error("无法读取或保存配置，请检查项目 .env 和数据目录权限。")
        return
    ui.show_notice(f"当前模型：{selected.provider} / {selected.model}")
    ui.show_notice(f"已更新：{path}")


def run_law_install() -> None:
    """执行完整的离线法规库安装流程。"""
    asyncio.run(install_laws())
