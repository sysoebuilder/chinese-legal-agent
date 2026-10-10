"""Windows 终端 MCP：同一个文件提供后台启动、前台服务与停止操作。"""

import argparse
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
import psutil
import uvicorn
from dotenv import dotenv_values, load_dotenv, set_key
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse, PlainTextResponse

from ai_agent_learning.langchain_agent.tools.run_command import (
    TOOL_DESCRIPTION,
    handler,
)
from ai_agent_learning.model_config import source_config_path

server = MCPServer("agent100-windows-terminal")


@server.tool(
    name="run_command",
    description="在 Windows 宿主机上执行命令，working_directory 必须使用 Windows 绝对路径。"
    + TOOL_DESCRIPTION,
    annotations=ToolAnnotations(
        read_only_hint=False, destructive_hint=True, idempotent_hint=False,
    ),
)
async def run_command(
    command: Annotated[str, Field(min_length=1, description="PowerShell 命令")],
    working_directory: Annotated[str, Field(min_length=1, description="Windows 上现有目录的绝对路径")],
    timeout_seconds: Annotated[int, Field(ge=1, le=300)] = 30,
) -> dict[str, Any]:
    return await handler(command, working_directory, timeout_seconds)


class BearerAuth:
    """HTTP 认证覆盖初始化、工具发现及调用，lifespan 请求原样转发。"""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if not hmac.compare_digest(headers.get(b"authorization", b""), self.expected):
                response = PlainTextResponse(
                    "需要有效的 Bearer token", status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(token: str):
    if not token.strip():
        raise ValueError("请配置 WINDOWS_MCP_TOKEN，终端服务不允许无认证启动")
    app = server.streamable_http_app(
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://host.docker.internal:*"],
        ),
    )
    app.add_middleware(BearerAuth, token=token)
    async def health(request):
        return JSONResponse({"service": "agent100-windows-terminal"})

    app.add_route("/healthz", health, methods=["GET"])
    return app


def configure_local_service(port: int) -> str:
    """只配置终端 MCP 的两个键，保留数据库和模型配置。"""
    config_path = source_config_path()
    values = dotenv_values(config_path)
    token = os.getenv("WINDOWS_MCP_TOKEN", "").strip() or (values.get("WINDOWS_MCP_TOKEN") or "").strip()
    token = token or secrets.token_urlsafe(32)
    set_key(str(config_path), "WINDOWS_MCP_TOKEN", token)
    if not (values.get("WINDOWS_MCP_URL") or "").strip():
        set_key(str(config_path), "WINDOWS_MCP_URL", f"http://host.docker.internal:{port}/mcp")
    return token


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE = "ai_agent_learning.mcp_terminal"
STARTUP_TIMEOUT_SECONDS = 60


def emit(kind: str, message: str) -> None:
    print(json.dumps({"type": kind, "message": message}, ensure_ascii=False), flush=True)


@contextmanager
def lifecycle_lock(directory: Path):
    import msvcrt

    with (directory / "lifecycle.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def owned_process(state: Path, port: int | None = None) -> psutil.Process | None:
    if not state.exists():
        return None
    record = json.loads(state.read_text(encoding="utf-8"))
    try:
        process = psutil.Process(record["pid"])
        if process.create_time() != record["created_at"]:
            return None
        # 同时识别合并前已运行的进程，方便升级后安全停止或复用。
        if process.cmdline()[1:] not in [
            ["-m", MODULE, "serve", "--port", str(record["port"])],
            ["-m", MODULE, "--port", str(record["port"])],
        ]:
            return None
        if Path(process.cwd()).resolve() != PROJECT_ROOT:
            return None
        if port is not None and port != record["port"]:
            raise RuntimeError("MCP 端口配置已改变，请先停止旧后台服务再启动")
        return process
    except psutil.NoSuchProcess:
        return None


def stop_process(process: psutil.Process) -> None:
    # 只清理已验证归属的进程树，避免 PID 重用导致误杀其他程序。
    processes = [*reversed(process.children(recursive=True)), process]
    for item in processes:
        try:
            item.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=5)
    for item in alive:
        try:
            item.kill()
        except psutil.NoSuchProcess:
            pass


def ready(port: int, token: str) -> bool:
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            response = client.get(
                f"http://127.0.0.1:{port}/healthz",
                headers={"Authorization": f"Bearer {token}"},
            )
        return response.status_code == 200 and response.json() == {"service": "agent100-windows-terminal"}
    except (httpx.HTTPError, ValueError):
        return False


def up(state: Path, port: int) -> None:
    process = owned_process(state, port)
    if process is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                raise RuntimeError(f"端口 {port} 已被占用，请先停止手动启动的 MCP 服务或其他占用进程")
        except OSError:
            pass
    token = configure_local_service(port)
    url = (dotenv_values(source_config_path()).get("WINDOWS_MCP_URL") or "").strip()
    if url != f"http://host.docker.internal:{port}/mcp":
        raise RuntimeError("WINDOWS_MCP_URL 与 后台服务的端口不一致，请检查 data/.env 和 compose.yaml")
    if process is not None:
        if not ready(port, token):
            raise RuntimeError("已有 MCP 进程未通过认证就绪检查，请检查日志后重新启动")
        emit("info", "Windows 终端 MCP 已运行")
        return

    log_path = state.parent / "terminal.log"
    # pythonw 的启动器及实际解释器均使用 GUI 子系统，不分配终端控制台。
    background_python = Path(sys.executable).with_name("pythonw.exe")
    if not background_python.is_file():
        raise RuntimeError("未找到 pythonw.exe，请检查本机 Python 或 .venv 安装")
    environment = dict(
        os.environ, WINDOWS_MCP_TOKEN=token, PYTHONUNBUFFERED="1", PYTHONFAULTHANDLER="1",
    )
    with log_path.open("ab") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 Windows 终端 MCP，解释器：{background_python}，端口：{port}\n".encode("utf-8"))
        log.flush()
        child = subprocess.Popen(
            [str(background_python), "-m", MODULE, "serve", "--port", str(port)],
            cwd=PROJECT_ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            # 脱离启动器的 Job，避免 Compose/终端退出时连带回收常驻服务。
            creationflags=(
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_BREAKAWAY_FROM_JOB
            ),
        )
    process = psutil.Process(child.pid)
    state.write_text(json.dumps({"pid": child.pid, "created_at": process.create_time(), "port": port}), encoding="utf-8")
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"Windows 终端 MCP 启动失败（退出码 {child.returncode}），日志位于 {log_path}")
            if ready(port, token):
                emit("info", f"Windows 终端 MCP 已就绪：127.0.0.1:{port}")
                return
            time.sleep(0.2)
        raise RuntimeError(f"等待 Windows 终端 MCP 就绪超时（{STARTUP_TIMEOUT_SECONDS} 秒，进程 {child.pid} 仍在运行），日志位于 {log_path}")
    except BaseException:
        if child.poll() is None:
            stop_process(process)
        state.unlink(missing_ok=True)
        raise


def down(state: Path, port: int) -> None:
    process = owned_process(state)
    if process is not None:
        stop_process(process)
    state.unlink(missing_ok=True)
    emit("info", "Windows 终端 MCP 已停止")


def serve(port: int, *, configure: bool = False) -> None:
    """当前进程持续运行 HTTP 服务；后台 start 的子进程也走这个入口。"""
    if configure:
        os.environ["WINDOWS_MCP_TOKEN"] = configure_local_service(port)
    load_dotenv(source_config_path(), override=False)
    app = create_app(os.getenv("WINDOWS_MCP_TOKEN", "").strip())
    uvicorn.run(app, host="127.0.0.1", port=port)


def manage(action: str, port: int, project_name: str, service: str) -> None:
    """手动启动与 Compose 共用同一套进程管理与状态文件。"""
    if not all(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name) for name in [project_name, service]):
        raise ValueError("不支持的项目或服务名称")
    os.chdir(PROJECT_ROOT)
    os.environ["AGENT_DATA_DIR"] = str(PROJECT_ROOT / "data")
    directory = PROJECT_ROOT / "data" / "compose-mcp"
    directory.mkdir(parents=True, exist_ok=True)
    state = directory / f"{project_name}-{service}.json"
    with lifecycle_lock(directory):
        (up if action == "start" else down)(state, port)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", nargs="?", choices=["start", "stop", "serve", "compose"], default="start",
        help="默认 start 在后台启动；serve 前台调试；stop 停止；compose 供 Compose 调用",
    )
    parser.add_argument("action", nargs="?", choices=["up", "down", "stop", "metadata"])
    parser.add_argument("service", nargs="?", default="windows_terminal")
    parser.add_argument("--project-name", default="agent100")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--configure", action="store_true", help="serve 模式首次启动时配置 token 与 worker 地址")
    args = parser.parse_intermixed_args()
    if args.command != "compose" and (args.action is not None or args.service != "windows_terminal"):
        parser.error("start/stop/serve 不需要 action 或 service 参数")
    if args.command == "compose" and args.action is None:
        parser.error("compose 需要指定 up/down/stop/metadata")
    if args.command == "compose" and args.action == "metadata":
        parameters = [{"name": "port", "description": "Windows 本机 MCP 端口", "type": "integer", "default": "8765"}]
        print(json.dumps({"description": __doc__, **{action: {"parameters": parameters} for action in ["up", "down", "stop"]}}))
        return
    if os.name != "nt":
        parser.error("Windows 终端 MCP 必须在 Windows 本机运行")
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须在 1 到 65535 之间")
    try:
        if args.command == "serve":
            serve(args.port, configure=args.configure)
        else:
            action = args.command
            if args.command == "compose":
                action = "start" if args.action == "up" else "stop"
            manage(action, args.port, args.project_name, args.service)
    except (OSError, RuntimeError, ValueError, psutil.Error) as error:
        emit("error", str(error))
        sys.exit(1)


if __name__ == "__main__":
    main()
