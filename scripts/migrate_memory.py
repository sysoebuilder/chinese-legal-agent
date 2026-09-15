"""将旧内嵌 memories 集合迁至本机 Qdrant 服务，不重新向量化、不删除旧库。

在项目根目录执行：python scripts/migrate_memory.py
需要 Docker 引擎可用；脚本自动启动 Qdrant，等待就绪，迁移后关闭自己启动的容器。
首次启动可能拉取镜像；已有运行中的服务只复用，不关闭。容器和存储目录不会删除。
迁移时停止 agent100，避免同时读写旧库和目标集合。
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import time
from contextlib import closing, contextmanager
from pathlib import Path

import httpx
from qdrant_client import QdrantClient, models

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "agent100-qdrant"
MANAGED_LABEL = "agent100.qdrant-managed"


def ready(url):
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f"{url.rstrip('/')}/collections")
            return response.status_code == 200 and isinstance(
                response.json().get("result", {}).get("collections"), list
            )
    except (httpx.HTTPError, ValueError, AttributeError):
        return False


@contextmanager
def managed_qdrant(args):
    if ready(args.url):
        print("复用已运行的 Qdrant，迁移后不关闭该服务。", flush=True)
        yield
        return
    if args.url.rstrip("/") != "http://127.0.0.1:6333":
        raise ValueError("只能自动启动 http://127.0.0.1:6333；其他地址需已有可用服务")
    docker = shutil.which("docker")
    if docker is None:
        local_app_data = Path(
            os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        )
        for desktop_docker in (
            local_app_data
            / "Programs"
            / "DockerDesktop"
            / "resources"
            / "bin"
            / "docker.exe",
            Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe"),
        ):
            if desktop_docker.is_file():
                docker = str(desktop_docker)
                break
    if docker is None:
        raise RuntimeError(
            "未找到 Docker。请先安装 Docker Desktop 并使 Docker 引擎可用；无需手动启停 Qdrant。"
        )

    def run(*command, check=True):
        result = subprocess.run(
            [docker, *command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check and result.returncode:
            raise RuntimeError(
                f"Docker {' '.join(command[:2])} 失败：{result.stderr.strip()}"
            )
        return result

    run("info", "--format", "{{.ServerVersion}}")
    storage = args.server_storage.resolve()
    legacy = args.source.resolve()
    # 服务端不能挂载旧内嵌库目录，且不能与旧库存在父子覆盖关系。
    if storage == legacy or storage in legacy.parents or legacy in storage.parents:
        raise ValueError("服务端存储目录必须与旧内嵌数据库目录完全隔离")
    inspected = run("container", "inspect", CONTAINER, check=False)
    started = False
    try:
        if inspected.returncode == 0:
            container = json.loads(inspected.stdout)[0]
            labels = container.get("Config", {}).get("Labels") or {}
            mounts = container.get("Mounts", [])
            ports = container.get("HostConfig", {}).get("PortBindings", {}) or {}
            bindings = ports.get("6333/tcp") or []
            matching_mount = any(
                m.get("Destination") == "/qdrant/storage"
                and str(m.get("Source", "")).replace("\\", "/").casefold()
                == str(storage).replace("\\", "/").casefold()
                for m in mounts
            )
            if (
                labels.get(MANAGED_LABEL) != "true"
                or not matching_mount
                or len(bindings) != 1
                or bindings[0].get("HostIp") != "127.0.0.1"
                or bindings[0].get("HostPort") != "6333"
            ):
                raise RuntimeError(
                    f"已有同名容器 {CONTAINER} 配置不匹配；拒绝修改或关闭，请先核查。"
                )
            if container.get("State", {}).get("Running"):
                print("已有托管容器正在启动；等待就绪，但不接管关闭。", flush=True)
            else:
                run("start", CONTAINER)
                started = True
        else:
            storage.mkdir(parents=True, exist_ok=True)
            print(f"启动 Qdrant，存储目录 {storage}；首次可能下载镜像。", flush=True)
            run(
                "run",
                "-d",
                "--name",
                CONTAINER,
                "--label",
                f"{MANAGED_LABEL}=true",
                "-p",
                "127.0.0.1:6333:6333",
                "--mount",
                f"type=bind,source={storage},target=/qdrant/storage",
                args.image,
            )
            started = True
        deadline = time.monotonic() + args.startup_timeout
        while not ready(args.url):
            if time.monotonic() >= deadline:
                logs = run("logs", "--tail", "20", CONTAINER, check=False)
                raise RuntimeError(
                    f"Qdrant 未在指定时间内就绪：\n{logs.stdout}\n{logs.stderr}"
                )
            time.sleep(1)
        print("Qdrant 已就绪，开始迁移。", flush=True)
        yield
    finally:
        if started:
            stopped = run("stop", "--time", "30", CONTAINER, check=False)
            if stopped.returncode:
                print(
                    f"警告：自动关闭失败，请核查容器 {CONTAINER}：{stopped.stderr}",
                    flush=True,
                )
            else:
                print("已关闭本次启动的 Qdrant；容器及数据保留。", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "data" / "memory" / "qdrant"
    )
    parser.add_argument("--url", default="http://127.0.0.1:6333")
    parser.add_argument(
        "--server-storage", type=Path, default=ROOT / "data" / "qdrant-server"
    )
    parser.add_argument("--image", default="qdrant/qdrant:latest")
    parser.add_argument("--startup-timeout", type=int, default=60)
    args = parser.parse_args()
    if not (args.source / "meta.json").is_file():
        raise FileNotFoundError(f"旧数据库不存在：{args.source}")
    if args.startup_timeout <= 0:
        raise ValueError("startup-timeout 必须大于 0")
    collection = "memories"
    with (
        closing(QdrantClient(path=str(args.source.resolve()))) as source,
        managed_qdrant(args),
        closing(QdrantClient(url=args.url, timeout=60)) as target,
    ):
        config = source.get_collection(collection).config.params.vectors
        if target.collection_exists(collection):
            current = target.get_collection(collection).config.params.vectors
            if (
                not isinstance(current, models.VectorParams)
                or not isinstance(config, models.VectorParams)
                or current.size != config.size
                or current.distance != config.distance
            ):
                raise ValueError("目标集合向量配置与旧库不一致，拒绝迁移")
        else:
            target.create_collection(collection, vectors_config=config)
        offset = None
        moved = 0
        while True:
            records, offset = source.scroll(
                collection,
                limit=128,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            if records:
                target.upsert(
                    collection,
                    points=[
                        models.PointStruct(
                            id=row.id, vector=row.vector, payload=row.payload
                        )
                        for row in records
                    ],
                    wait=True,
                )
                copied = target.retrieve(
                    collection,
                    ids=[row.id for row in records],
                    with_payload=True,
                    with_vectors=True,
                )
                by_id = {row.id: row for row in copied}
                for row in records:
                    actual = by_id.get(row.id)
                    # 服务端会对 Cosine 向量归一化；旧内嵌库持久化的向量可能非单位长度。
                    # 比较归一化后的期望值，不把等价的 Cosine 向量误判为迁移损坏。
                    expected_vector = row.vector
                    if (
                        isinstance(expected_vector, list)
                        and config.distance == models.Distance.COSINE
                    ):
                        norm = math.hypot(*expected_vector)
                        if norm > 0:
                            expected_vector = [
                                value / norm for value in expected_vector
                            ]
                    if (
                        actual is None
                        or actual.payload != row.payload
                        or not isinstance(row.vector, list)
                        or not isinstance(actual.vector, list)
                        or len(actual.vector) != len(expected_vector)
                        or not all(
                            math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)
                            for a, b in zip(actual.vector, expected_vector, strict=True)
                        )
                    ):
                        raise ValueError(f"迁移验证失败：{row.id}")
                moved += len(records)
                print(f"已迁移并验证 {moved} 条", flush=True)
            if offset is None:
                break
        print(
            f"迁移完成：旧库 {source.count(collection, exact=True).count} 条，"
            f"目标 {target.count(collection, exact=True).count} 条；旧库保留。"
        )


if __name__ == "__main__":
    main()
