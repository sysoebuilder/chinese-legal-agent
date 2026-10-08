"""检查外部 Qdrant 服务；数据库由 Docker Compose 管理。"""

import os

import httpx

from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL


class QdrantServer:
    async def start(self) -> None:
        url = (os.getenv("QDRANT_URL") or DEFAULT_QDRANT_URL).strip().rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(f"{url}/collections")
                response.raise_for_status()
                collections = response.json().get("result", {}).get("collections")
                if not isinstance(collections, list):
                    raise TypeError("响应中缺少集合列表")
        except (httpx.HTTPError, ValueError, AttributeError, TypeError) as error:
            raise RuntimeError(
                f"Qdrant 服务不可用：{url}；请先运行 docker compose up -d --wait qdrant"
            ) from error

    async def close(self) -> None:
        # 共享数据库由 Compose 管理，聊天退出时不停止数据库。
        pass
