"""容器部署需要的工具平台选择与法规服务地址。"""

import importlib.util
import json
import zipfile

import pytest

from ai_agent_learning import law_install
from ai_agent_learning.langchain_agent import tools


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


def test_law_installer_connects_to_configured_qdrant(monkeypatch, tmp_path):
    assets = tmp_path / "laws"
    (assets / "raw" / "npc").mkdir(parents=True)
    archive = assets / "npc-law-vectors.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(
            "manifest.json",
            json.dumps({"count": 0, "dimensions": 1024, "vector_name": "text"}),
        )
    checksum = law_install._sha256_file(archive)
    (assets / "npc-law-vectors.zip.sha256").write_text(checksum, encoding="ascii")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "laws-install.json").write_text(
        json.dumps({"archive_sha256": checksum}), encoding="utf-8"
    )
    monkeypatch.setattr(law_install, "_assets_dir", lambda: assets)
    monkeypatch.setattr(law_install, "get_data_dir", lambda: data_dir)
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")

    class ConnectionChecked(Exception):
        pass

    def connect(*, url, timeout):
        assert url == "http://qdrant:6333"
        assert timeout == 120
        raise ConnectionChecked()

    monkeypatch.setattr(law_install, "QdrantClient", connect)
    with pytest.raises(ConnectionChecked):
        law_install._install_sync()
