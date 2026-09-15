"""原生 Qdrant 进程的运行期管理，不依赖 Docker。"""

import asyncio
import os
import subprocess

import httpx

from ai_agent_learning.paths import find_project_root
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
            root = find_project_root()
            executable = root / "Qdrant" / "qdrant.exe"
            config = root / "qdrant.yaml"
            if not executable.is_file() or not config.is_file():
                raise FileNotFoundError(f"需要 Qdrant 程序 {executable} 和配置 {config}")
            log_dir = root / "data"
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "qdrant-server.log").open("ab") as log:
                self.process = subprocess.Popen(  # noqa: ASYNC220 -- 只创建进程，等待在异步循环中完成。
                    [str(executable), "--config-path", str(config)],
                    cwd=root,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            deadline = asyncio.get_running_loop().time() + 60
            while not await ready():
                if self.process.poll() is not None:
                    raise RuntimeError(f"Qdrant 启动失败，请检查 {log_dir / 'qdrant-server.log'}")
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
