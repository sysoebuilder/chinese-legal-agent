"""本机 MCP 的生命周期、端口冲突和进程归属保护。"""

import json
import os
import socket
import subprocess
import sys

import psutil
import pytest
from dotenv import dotenv_values

from ai_agent_learning import mcp_terminal as terminal


def test_terminal_import_does_not_load_unrelated_retrieval_dependencies():
    script = """
import json, sys
import ai_agent_learning.mcp_terminal
unrelated = ['ai_agent_learning.memory', 'ai_agent_learning.law_index',
             'ai_agent_learning.reranker', 'qdrant_client', 'bm25s']
print(json.dumps([name for name in unrelated if name in sys.modules]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        check=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert json.loads(result.stdout) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows 后台进程启动")
def test_background_allows_service_to_become_ready_after_twenty_seconds(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("LLM_CLI_DATA_DIR", str(data))
    monkeypatch.setenv("WINDOWS_MCP_TOKEN", "slow-start-test-token")
    monkeypatch.setattr(terminal, "PROJECT_ROOT", tmp_path)
    clock = [0.0]
    monkeypatch.setattr(terminal.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(terminal.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(terminal, "ready", lambda port, token: clock[0] >= 25)
    current = psutil.Process()

    class Child:
        pid = current.pid

        def poll(self):
            return None

    monkeypatch.setattr(terminal.subprocess, "Popen", lambda *args, **kwargs: Child())
    # 模拟子进程不会创建真实服务；断言失败时也不能清理当前测试进程。
    monkeypatch.setattr(terminal, "stop_process", lambda process: None)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    terminal.up(data / "service.json", port)
    assert clock[0] >= 25


@pytest.mark.skipif(os.name != "nt", reason="本机终端服务只支持 Windows")
def test_background_up_is_idempotent_and_down_cleans_process(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    config = data / ".env"
    config.write_text("CHECKPOINT_DATABASE_URL=postgresql://keep:unchanged@localhost/test\n", encoding="utf-8")
    monkeypatch.setenv("LLM_CLI_DATA_DIR", str(data))
    monkeypatch.setenv("WINDOWS_MCP_TOKEN", "lifecycle-test-token")
    monkeypatch.setattr(terminal, "PROJECT_ROOT", tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    state = data / "service.json"
    try:
        terminal.up(state, port)
        first = state.read_text()
        terminal.up(state, port)
        assert state.read_text() == first
        assert terminal.owned_process(state, port) is not None
        assert terminal.ready(port, "lifecycle-test-token")
        assert not terminal.ready(port, "wrong-token")
        values = dotenv_values(config)
        assert values["CHECKPOINT_DATABASE_URL"] == "postgresql://keep:unchanged@localhost/test"
        assert values["WINDOWS_MCP_URL"] == f"http://host.docker.internal:{port}/mcp"
    finally:
        # down 根据状态文件中的端口停止，兼容 Compose 没有传入 up options。
        terminal.down(state, 8765)
    assert not state.exists()
    assert not terminal.ready(port, "lifecycle-test-token")
    terminal.down(state, port)


def test_background_rejects_busy_port_before_configuration(tmp_path):
    state = tmp_path / "service.json"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(RuntimeError, match="已被占用"):
            terminal.up(state, listener.getsockname()[1])
    assert not state.exists()


def test_background_does_not_claim_reused_or_unrelated_pid(tmp_path):
    state = tmp_path / "service.json"
    current = psutil.Process()
    state.write_text(json.dumps({"pid": current.pid, "created_at": current.create_time() - 1, "port": 8765}))
    assert terminal.owned_process(state, 8765) is None
    state.write_text(json.dumps({"pid": current.pid, "created_at": current.create_time(), "port": 8765}))
    assert terminal.owned_process(state, 8765) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows 后台进程生命周期")
def test_mcp_survives_start_command_exiting(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("LLM_CLI_DATA_DIR", str(data))
    monkeypatch.setenv("WINDOWS_MCP_TOKEN", "launcher-test-token")
    monkeypatch.setattr(terminal, "PROJECT_ROOT", tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    state = data / "compose-mcp" / "agent100-windows_terminal.json"
    script = """
import sys
from pathlib import Path
from ai_agent_learning import mcp_terminal as terminal
root, port = Path(sys.argv[1]), int(sys.argv[2])
terminal.PROJECT_ROOT = root
sys.argv = ['mcp_terminal', '--port', str(port)]
terminal.main()
"""
    try:
        # 默认命令在后台启动，确认启动命令退出后服务仍可调用。
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), str(port)],
            capture_output=True, timeout=60, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB,
        )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        process = terminal.owned_process(state, port)
        assert process is not None
        # 检查启动器和实际解释器，避免只隐藏外层后实际服务仍创建控制台。
        console_probe = """
import ctypes, json, sys
from ctypes import wintypes
api = ctypes.WinDLL('kernel32', use_last_error=True)
api.AttachConsole.argtypes = [wintypes.DWORD]
api.AttachConsole.restype = wintypes.BOOL
api.FreeConsole()
ctypes.set_last_error(0)
attached = bool(api.AttachConsole(int(sys.argv[1])))
error = ctypes.get_last_error() if not attached else 0
if attached:
    api.FreeConsole()
print(json.dumps({'attached': attached, 'error': error}))
"""
        for item in [process, *process.children(recursive=True)]:
            probe = subprocess.run(
                [sys.executable, "-c", console_probe, str(item.pid)],
                capture_output=True, text=True, check=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # ERROR_INVALID_HANDLE 表示目标进程没有控制台。
            assert json.loads(probe.stdout) == {"attached": False, "error": 6}
        assert terminal.ready(port, "launcher-test-token")
    finally:
        monkeypatch.setattr(sys, "argv", ["mcp_terminal", "stop", "--port", str(port)])
        terminal.main()
    assert not state.exists()
    assert not terminal.ready(port, "launcher-test-token")


def test_compose_metadata_remains_available_from_unified_entry(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [
        "mcp_terminal", "compose", "--project-name", "test-project", "metadata", "windows_terminal",
    ])
    terminal.main()
    metadata = json.loads(capsys.readouterr().out)
    assert {"up", "down", "stop"} <= metadata.keys()
    assert metadata["up"]["parameters"][0]["name"] == "port"
