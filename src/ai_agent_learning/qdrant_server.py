"""原生 Qdrant 进程的运行期管理，不依赖 Docker。"""

import asyncio
import os
import subprocess
from pathlib import Path

import httpx

from ai_agent_learning.paths import get_data_dir
from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL


class QdrantServer:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    async def start(self) -> None:
        url = os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).strip().rstrip("/")
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:

            async def ready() -> bool:
                try:
                    response = await client.get(f"{url}/collections")
                    return response.status_code == 200 and isinstance(
                        response.json().get("result", {}).get("collections"), list
                    )
                except (httpx.HTTPError, ValueError, AttributeError):
                    return False

            if await ready():
                return
            if url != DEFAULT_QDRANT_URL:
                raise RuntimeError(f"Qdrant 服务不可用：{url}；仅自动启动默认本机服务")
            executable = Path(__file__).parent / "runtime" / "qdrant.exe"
            if not executable.is_file():
                raise FileNotFoundError(f"安装包中缺少 Qdrant 服务端：{executable}")
            data_dir = get_data_dir()
            storage_dir = data_dir / "qdrant-server"
            config = data_dir / "qdrant.yaml"
            config_content = (
                "storage:\n"
                f'  storage_path: "{storage_dir.as_posix()}"\n\n'
                "service:\n"
                "  host: 127.0.0.1\n"
                "  http_port: 6333\n"
                "  grpc_port: null\n\n"
                "log_level: INFO\n"
            )
            if (
                not config.is_file()
                or config.read_text(encoding="utf-8") != config_content
            ):
                config.write_text(config_content, encoding="utf-8")
            with (data_dir / "qdrant-server.log").open("ab") as log:
                self.process = subprocess.Popen(  # noqa: ASYNC220 -- 只创建进程，等待在异步循环中完成。
                    [str(executable), "--config-path", str(config)],
                    cwd=data_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            deadline = asyncio.get_running_loop().time() + 60
            while not await ready():
                if self.process.poll() is not None:
                    raise RuntimeError(
                        f"Qdrant 启动失败，请检查 {data_dir / 'qdrant-server.log'}"
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("Qdrant 未在 60 秒内就绪")
                await asyncio.sleep(0.5)

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        finally:
            self.process = None
