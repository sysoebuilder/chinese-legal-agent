"""备份 Compose 数据，不需要安装额外 Python 包。

运行：python scripts/backup-data.py
备份期间暂停 API/worker 写入，完成后恢复原先运行的服务。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKER = shutil.which("docker") or str(
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs/DockerDesktop/resources/bin/docker.exe"
)


def run(*args, output=None):
    if output is not None:
        with output.open("wb") as handle:
            subprocess.run([DOCKER, *args], cwd=ROOT, stdout=handle, check=True)
        return ""
    return subprocess.check_output([DOCKER, *args], cwd=ROOT, text=True).strip()


def inspect_service(service):
    container = run("compose", "ps", "-a", "-q", service)
    return json.loads(run("inspect", container))[0]


def cleanup(*commands):
    """尝试所有恢复操作；已有失败时保留原始异常。"""
    original_error = sys.exception()
    failures = []
    for command in commands:
        try:
            run(*command)
        except (OSError, subprocess.CalledProcessError) as exc:
            failures.append(exc)
            print(f"清理或恢复服务失败：{' '.join(command)}；请检查 Docker 状态。", file=sys.stderr)
    if failures and original_error is None:
        raise ExceptionGroup("备份结束后的清理或服务恢复失败", failures)


def archive(service, target, destination, members=(), compressed=False):
    container = inspect_service(service)
    mount = next(item for item in container["Mounts"] if item["Destination"] == target)
    source = mount["Name"] if mount["Type"] == "volume" else mount["Source"]
    helper = run(
        "create", "--network", "none", "--memory", "256m", "--cpus", "1", "--mount",
        f"type={mount['Type']},source={source},target=/source,readonly",
        "--entrypoint", "tar", "postgres:17-alpine",
        "-czf" if compressed else "-cf", f"/tmp/{destination.name}",
        "-C", "/source", *(members or (".",)),
    )
    try:
        run("start", "-a", helper)
        run("cp", f"{helper}:/tmp/{destination.name}", str(destination))
    finally:
        cleanup(("rm", "-f", helper))


def request(path, method="GET"):
    url = "http://127.0.0.1:6333" + path
    with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=600) as response:
        return json.load(response)["result"]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def backup():
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")
    destination = ROOT / "data" / "backups" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    print(f"备份目录：{destination}", flush=True)
    running = set(run("compose", "ps", "--status", "running", "--services").splitlines())
    writers = sorted(running & {"frontend", "api", "worker"})
    redis_stopped = False
    try:
        if writers:
            run("compose", "stop", *writers)
        print("备份 PostgreSQL…", flush=True)
        run("compose", "exec", "-T", "postgres", "pg_dump", "-Fc", "-U", "agent100", "-d", "agent100",
            output=destination / "postgres.dump")
        counts = {}
        for entry in request("/collections")["collections"]:
            collection = entry["name"]
            print(f"备份 Qdrant 集合：{collection}…", flush=True)
            quoted = urllib.parse.quote(collection, safe="")
            counts[collection] = request(f"/collections/{quoted}")["points_count"]
            snapshot = request(f"/collections/{quoted}/snapshots?wait=true", "POST")["name"]
            snapshot_path = f"/collections/{quoted}/snapshots/{urllib.parse.quote(snapshot, safe='')}"
            try:
                with (
                    urllib.request.urlopen("http://127.0.0.1:6333" + snapshot_path, timeout=600) as response,
                    (destination / f"{collection}.snapshot").open("wb") as output,
                ):
                    shutil.copyfileobj(response, output, 4 * 1024 * 1024)
            finally:
                # 下载中断也清理本次生成的临时快照，避免占满 Docker 数据盘。
                original_error = sys.exception()
                try:
                    request(snapshot_path, "DELETE")
                except Exception:
                    print(f"临时快照清理失败：{collection}/{snapshot}；请检查 Qdrant。", file=sys.stderr)
                    if original_error is None:
                        raise
        print("备份 BM25 索引和安装状态…", flush=True)
        archive("api", "/app/data", destination / "law-bm25.tar.gz",
                ("law-bm25", "laws-install.json", "laws-installed.json"), compressed=True)
        print("备份法律离线资源…", flush=True)
        archive("api", "/app/laws", destination / "law-assets.tar",
                ("raw", "npc-law-vectors.zip", "npc-law-vectors.zip.sha256"))
        if "redis" in running:
            run("compose", "exec", "-T", "redis", "redis-cli", "SAVE")
            run("compose", "stop", "redis")
            redis_stopped = True
        archive("redis", "/data", destination / "redis-data.tar.gz", compressed=True)
        print("校验备份文件…", flush=True)
        manifest = {"format_version": 1, "created_at": datetime.now().astimezone().isoformat(),
                    "qdrant_counts": counts,
                    "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)}
                              for p in destination.iterdir() if p.is_file()}}
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"备份完成：{destination}", flush=True)
        return destination
    finally:
        commands = []
        if redis_stopped:
            commands.append(("compose", "start", "redis"))
        if writers:
            commands.append(("compose", "start", *writers))
        cleanup(*commands)


if __name__ == "__main__":
    backup()
