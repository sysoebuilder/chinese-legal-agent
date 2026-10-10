"""下载发布的法律数据和官方重排模型，不导入聊天或 CUDA 运行时。"""

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx

from chinese_legal_agent.paths import get_data_dir
from chinese_legal_agent.qdrant_config import DEFAULT_QDRANT_URL
from chinese_legal_agent.reranker import MODEL_PATH

LAW_MANIFEST_URL = (
    "https://github.com/sysoebuilder/chinese-legal-agent/releases/download/"
    "laws-20261007/laws-manifest.json"
)
LAW_MANIFEST_SHA256 = "ad420a0b838c0434c49c42fe680ac068ad1d4dd6132f9d905d02f89c262a3956"
MODEL_REPO = "BAAI/bge-reranker-v2-m3"
MODEL_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MODEL_FILES = (
    "config.json",
    "model.safetensors",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "tokenizer.json",
)
INDEX_FILES = (
    "data.csc.index.npy",
    "indices.csc.index.npy",
    "indptr.csc.index.npy",
    "params.index.json",
    "vocab.index.json",
)
BLOCK_SIZE = 1024 * 1024


class ResourceInitializationError(ValueError):
    """可以显示给用户的固定错误说明，不包含外部请求或凭据。"""


def report(message: str) -> None:
    print(message, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * BLOCK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def matches(path: Path, checksum: str, size: int | None = None) -> bool:
    return (
        path.is_file()
        and (size is None or path.stat().st_size == size)
        and sha256(path) == checksum
    )


@contextmanager
def resource_lock(data_dir: Path):
    """多次 compose run 共用卷时串行初始化，避免并发恢复或替换索引。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".initialize-resources.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def download(
    client: httpx.Client,
    url: str,
    destination: Path,
    checksum: str,
    size: int | None = None,
) -> Path:
    """校验缓存和最终文件；断线时保留 partial，下次按 Range 续传。"""
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ResourceInitializationError("下载文件的 SHA-256 配置无效")
    if matches(destination, checksum, size):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    for attempt in range(3):
        try:
            offset = partial.stat().st_size if partial.is_file() else 0
            if size is not None and offset >= size:
                if offset == size and matches(partial, checksum, size):
                    partial.replace(destination)
                    return destination
                partial.unlink()
                offset = 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            report(f"下载 {destination.name}（已缓存 {offset // BLOCK_SIZE} MiB）")
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416:
                    partial.unlink(missing_ok=True)
                response.raise_for_status()
                append = offset > 0 and response.status_code == 206
                if append and not response.headers.get("content-range", "").startswith(
                    f"bytes {offset}-"
                ):
                    partial.unlink(missing_ok=True)
                    raise ResourceInitializationError("下载服务器返回的续传范围不正确")
                if not append:
                    offset = 0
                last_report = time.monotonic()
                with partial.open("ab" if append else "wb") as output:
                    for block in response.iter_bytes(BLOCK_SIZE):
                        output.write(block)
                        offset += len(block)
                        if size is not None and offset > size:
                            raise ResourceInitializationError(
                                "下载文件超过清单中的大小"
                            )
                        if time.monotonic() - last_report >= 5:
                            report(f"{destination.name}：{offset // BLOCK_SIZE} MiB")
                            last_report = time.monotonic()
            if not matches(partial, checksum, size):
                partial.unlink(missing_ok=True)
                raise ResourceInitializationError("下载文件大小或 SHA-256 校验失败")
            partial.replace(destination)
            return destination
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                raise
            report(f"下载重试 {attempt + 1}/2：{destination.name}")
            time.sleep(attempt + 1)
    raise RuntimeError("下载未完成")


def load_manifest(client: httpx.Client, cache: Path) -> dict:
    url = os.getenv("LAWS_MANIFEST_URL", LAW_MANIFEST_URL)
    checksum = os.getenv("LAWS_MANIFEST_SHA256", LAW_MANIFEST_SHA256)
    path = download(client, url, cache / "laws-manifest.json", checksum)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (
        manifest.get("format_version") != 1
        or manifest.get("collection") != "npc_laws"
        or not isinstance(manifest.get("count"), int)
        or manifest["count"] <= 0
    ):
        raise ResourceInitializationError("法律发布清单的格式或集合无效")
    snapshot = manifest["snapshot"]
    if snapshot["filename"] != "npc_laws.snapshot" or snapshot["encoding"] not in {
        "gzip",
        "gzip-split",
    }:
        raise ResourceInitializationError("法律快照格式不支持")
    assets = [*snapshot["parts"], manifest["bm25"]]
    names = set()
    for asset in assets:
        name = asset["name"]
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            or name in {".", ".."}
            or name in names
            or not isinstance(asset["bytes"], int)
            or asset["bytes"] <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
            or not asset["url"].startswith("https://")
        ):
            raise ResourceInitializationError("法律发布清单的附件无效")
        names.add(name)
    if (
        not snapshot["parts"]
        or sum(a["bytes"] for a in snapshot["parts"]) != snapshot["compressed_bytes"]
    ):
        raise ResourceInitializationError("法律快照分卷大小不一致")
    return manifest


def collection_count(client: httpx.Client) -> int | None:
    response = client.get("collections/npc_laws")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    vectors = response.json()["result"]["config"]["params"]["vectors"]
    if (
        set(vectors) != {"text"}
        or vectors["text"]["size"] != 1024
        or vectors["text"]["distance"] != "Cosine"
    ):
        raise ResourceInitializationError("现有 npc_laws 集合的向量配置不匹配")
    response = client.post("collections/npc_laws/points/count", json={"exact": True})
    response.raise_for_status()
    return response.json()["result"]["count"]


def index_ready(path: Path, count: int) -> bool:
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
        ids = json.loads((path / "ids.json").read_text(encoding="utf-8"))
        return (
            manifest.get("collection") == "npc_laws"
            and manifest.get("count") == count
            and isinstance(ids, list)
            and len(ids) == count
            and all(
                (path / kind / name).is_file()
                and (path / kind / name).stat().st_size > 0
                for kind in ("title", "content")
                for name in INDEX_FILES
            )
        )
    except (OSError, ValueError):
        return False


class PartReader(io.RawIOBase):
    """将有序分卷作为一个流交给 gzip，避免另外生成整份压缩包。"""

    def __init__(self, parts: list[Path]):
        super().__init__()
        self.parts = iter(parts)
        self.current = None

    def readable(self):
        return True

    def readinto(self, buffer):
        while True:
            if self.current is None:
                path = next(self.parts, None)
                if path is None:
                    return 0
                self.current = path.open("rb")
            length = self.current.readinto(buffer)
            if length:
                return length
            self.current.close()
            self.current = None

    def close(self):
        if self.current is not None:
            self.current.close()
        super().close()


def prepare_snapshot(client: httpx.Client, manifest: dict, cache: Path) -> Path:
    snapshot = manifest["snapshot"]
    destination = cache / snapshot["filename"]
    if matches(destination, snapshot["sha256"], snapshot["bytes"]):
        return destination
    parts = []
    combined = hashlib.sha256()
    for asset in snapshot["parts"]:
        path = download(
            client, asset["url"], cache / asset["name"], asset["sha256"], asset["bytes"]
        )
        parts.append(path)
        with path.open("rb") as source:
            for block in iter(lambda: source.read(8 * BLOCK_SIZE), b""):
                combined.update(block)
    if combined.hexdigest() != snapshot["compressed_sha256"]:
        raise ResourceInitializationError("法律快照分卷拼接校验失败")
    report("正在拼接并解压法律快照…")
    partial = destination.with_name(destination.name + ".partial")
    digest = hashlib.sha256()
    size = 0
    with (
        io.BufferedReader(PartReader(parts)) as joined,
        gzip.GzipFile(fileobj=joined) as source,
        partial.open("wb") as output,
    ):
        for block in iter(lambda: source.read(8 * BLOCK_SIZE), b""):
            size += len(block)
            if size > snapshot["bytes"]:
                raise ResourceInitializationError("解压后的法律快照超过清单大小")
            digest.update(block)
            output.write(block)
    if size != snapshot["bytes"] or digest.hexdigest() != snapshot["sha256"]:
        raise ResourceInitializationError("解压后的法律快照校验失败")
    partial.replace(destination)
    return destination


def extract_index(archive_path: Path, staging: Path, count: int) -> Path:
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts or "\\" in member.name:
                raise ResourceInitializationError("BM25 归档包含无效路径")
            # 旧安装状态不是运行索引，发布包中的这两个文件不恢复。
            if member.name in {"laws-install.json", "laws-installed.json"}:
                continue
            if (
                not relative.parts
                or relative.parts[0] != "law-bm25"
                or not (member.isdir() or member.isfile())
            ):
                raise ResourceInitializationError("BM25 归档包含不支持的文件")
            target = staging / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    index = staging / "law-bm25"
    if not index_ready(index, count):
        raise ResourceInitializationError("恢复的 BM25 索引不完整或法规数量不一致")
    return index


def initialize_laws(data_dir: Path | None = None) -> None:
    data_dir = data_dir or get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cache = data_dir / "law-initialize"
    qdrant_url = os.getenv("QDRANT_URL", DEFAULT_QDRANT_URL).rstrip("/") + "/"
    with httpx.Client(
        follow_redirects=True, timeout=httpx.Timeout(120, connect=20)
    ) as downloader:
        manifest = load_manifest(downloader, cache)
        with httpx.Client(base_url=qdrant_url, timeout=1800, trust_env=False) as qdrant:
            count = collection_count(qdrant)
            if count not in {None, 0, manifest["count"]}:
                raise ResourceInitializationError(
                    "现有法律集合数量与发布包不一致，初始化不会覆盖非空集合"
                )
            target = data_dir / "law-bm25"
            ready = index_ready(target, manifest["count"])
            if count == manifest["count"] and ready:
                report(f"法律库及 BM25 索引已完整，跳过：{count} 条")
                return
            with tempfile.TemporaryDirectory(
                prefix=".law-initialize-", dir=data_dir
            ) as directory:
                staging = Path(directory)
                index = None
                if not ready:
                    asset = manifest["bm25"]
                    archive = download(
                        downloader,
                        asset["url"],
                        cache / asset["name"],
                        asset["sha256"],
                        asset["bytes"],
                    )
                    index = extract_index(archive, staging, manifest["count"])
                if count != manifest["count"]:
                    snapshot = prepare_snapshot(downloader, manifest, cache)
                    # 下载期间再次确认，不覆盖被其他进程写入的非空集合。
                    count = collection_count(qdrant)
                    if count not in {None, 0}:
                        raise ResourceInitializationError(
                            "下载期间法律集合发生变化，请重新运行初始化"
                        )
                    report("正在向 Qdrant 恢复法律快照…")
                    with snapshot.open("rb") as source:
                        response = qdrant.post(
                            "collections/npc_laws/snapshots/upload",
                            params={
                                "priority": "snapshot",
                                "wait": "true",
                                "checksum": manifest["snapshot"]["sha256"],
                            },
                            files={
                                "snapshot": (
                                    snapshot.name,
                                    source,
                                    "application/octet-stream",
                                )
                            },
                        )
                    response.raise_for_status()
                    if (
                        not response.json().get("result")
                        or collection_count(qdrant) != manifest["count"]
                    ):
                        raise ResourceInitializationError(
                            "法律快照恢复后法规数量不一致"
                        )
                if index is not None:
                    backup = staging / "previous-index"
                    if target.exists():
                        target.rename(backup)
                    try:
                        index.rename(target)
                    except OSError:
                        if backup.exists():
                            backup.rename(target)
                        raise
            report(f"法律库及 BM25 索引初始化完成：{manifest['count']} 条")


def model_file_ready(directory: Path, name: str) -> bool:
    try:
        path = directory / name
        if not path.is_file() or path.stat().st_size == 0:
            return False
        if name.endswith(".json"):
            return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
        if name != "model.safetensors":
            return True
        # Safetensors 头部记录张量结束位置，检测截断权重，不加载模型或 CUDA。
        weights = directory / "model.safetensors"
        with weights.open("rb") as source:
            header_size = int.from_bytes(source.read(8), "little")
            if not 0 < header_size <= 16 * BLOCK_SIZE:
                return False
            header = json.loads(source.read(header_size))
        tensors = [value for name, value in header.items() if name != "__metadata__"]
        return (
            bool(tensors)
            and max(tensor["data_offsets"][1] for tensor in tensors)
            == weights.stat().st_size - 8 - header_size
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def model_ready(directory: Path) -> bool:
    return all(model_file_ready(directory, name) for name in MODEL_FILES)


def initialize_reranker(directory: Path | None = None) -> None:
    directory = directory or MODEL_PATH
    if model_ready(directory):
        report(f"重排模型已完整，跳过：{directory}")
        return
    from huggingface_hub import hf_hub_download

    report(f"正在从 Hugging Face 下载重排模型：{MODEL_REPO}")
    for name in MODEL_FILES:
        if not model_file_ready(directory, name):
            report(f"下载模型文件：{name}")
            hf_hub_download(
                repo_id=MODEL_REPO,
                revision=MODEL_REVISION,
                filename=name,
                local_dir=directory,
                force_download=(directory / name).exists(),
            )
    if not model_ready(directory):
        raise ResourceInitializationError("下载后的重排模型不完整，请重新运行初始化")
    report(f"重排模型初始化完成：{directory}")
