"""容器部署需要的工具平台选择。"""

import importlib.util

import pytest

from chinese_legal_agent.langchain_agent import tools


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_tool_registry_only_exposes_windows_commands_on_windows(monkeypatch, platform):
    spec = importlib.util.spec_from_file_location("container_tools", tools.__file__)
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as patch:
        patch.setattr(tools.os, "name", platform)
        spec.loader.exec_module(module)
    names = {tool.name for tool in module.ALL_TOOLS}
    assert ("run_command" in names) == (platform == "nt")
    assert {"current_time", "send_email", "search_laws", "search_memory"} <= names
    assert module.TOOL_BY_NAME.keys() == names
