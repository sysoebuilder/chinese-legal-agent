"""完整离线法规库的校验、入库和 BM25S 索引安装。"""

import asyncio
import hashlib
import json
import shutil
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import bm25s
import pyarrow as pa
import pyarrow.parquet as pq
from qdrant_client import QdrantClient, models

from ai_agent_learning.law_index import INDEX_DIR, tokenize
from ai_agent_learning.paths import find_project_root, get_data_dir
from ai_agent_learning.qdrant_config import DEFAULT_QDRANT_URL
from ai_agent_learning.qdrant_server import QdrantServer

COLLECTION = "npc_laws"
VECTOR_NAME = "text"
DIMENSIONS = 1024


def _dump(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assets_dir() -> Path:
    packaged = Path(__file__).resolve().parent / "laws"
    if packaged.is_dir():
        return packaged
    development = find_project_root() / "laws"
    if development.is_dir():
        return development
    raise FileNotFoundError("安装包中缺少离线法规数据")


def _prepare_payload_database(raw_dir: Path, database: Path, expected: int) -> None:
    database.unlink(missing_ok=True)
    seen: dict[str, set[str]] = {}
    inserted = 0
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute(
            "CREATE TABLE laws (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        for source in sorted(raw_dir.glob("*.parquet")):
            for batch in pq.ParquetFile(source).iter_batches(batch_size=512):
                values = []
                for row in batch.to_pylist():
                    source_id = str(row.get("id") or "").strip()
                    if not source_id:
                        raise ValueError(f"原始法规缺少 id：{source}")
                    fingerprint = hashlib.sha256(
                        json.dumps(row, ensure_ascii=False, sort_keys=True).encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    variants = seen.setdefault(source_id, set())
                    if fingerprint in variants:
                        continue
                    identity = (
                        source_id
                        if not variants
                        else f"{source_id}:variant:{fingerprint}"
                    )
                    variants.add(fingerprint)
                    point_id = str(uuid5(NAMESPACE_URL, f"npc-law:{identity}"))
                    payload = {**row, "source": "npc"}
                    values.append(
                        (
                            point_id,
                            json.dumps(
                                payload, ensure_ascii=False, separators=(",", ":")
                            ),
                        )
                    )
                connection.executemany("INSERT INTO laws VALUES (?, ?)", values)
                inserted += len(values)
                if inserted and inserted % 50_000 < len(values):
                    print(f"已整理原始法规 {inserted}/{expected}", flush=True)
        connection.commit()
        actual = connection.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    if actual != expected:
        database.unlink(missing_ok=True)
        raise ValueError(f"原始法规与向量数量不一致：原始 {actual}，向量 {expected}")


def _validate_collection(client: QdrantClient) -> None:
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            COLLECTION,
            vectors_config={
                VECTOR_NAME: models.VectorParams(
                    size=DIMENSIONS, distance=models.Distance.COSINE
                )
            },
        )
        return
    config = client.get_collection(COLLECTION).config.params.vectors
    if (
        not isinstance(config, dict)
        or set(config) != {VECTOR_NAME}
        or config[VECTOR_NAME].size != DIMENSIONS
        or config[VECTOR_NAME].distance != models.Distance.COSINE
    ):
        raise ValueError("现有 npc_laws 集合不是 text 1024 维 Cosine 配置")


def _import_shard(
    client: QdrantClient, payloads: sqlite3.Connection, content: bytes
) -> int:
    table = pq.read_table(pa.BufferReader(content), columns=["point_id", "text_vector"])
    ids = table.column("point_id").to_pylist()
    vectors = table.column("text_vector").to_pylist()
    payload_by_id: dict[str, dict] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = payloads.execute(
            f"SELECT id, payload FROM laws WHERE id IN ({placeholders})", chunk
        )
        payload_by_id.update(
            (point_id, json.loads(payload)) for point_id, payload in rows
        )
    missing = [point_id for point_id in ids if point_id not in payload_by_id]
    if missing:
        raise ValueError(f"向量找不到对应原始法规：{missing[0]}")
    for start in range(0, len(ids), 64):
        client.upsert(
            COLLECTION,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={VECTOR_NAME: vector},
                    payload=payload_by_id[point_id],
                )
                for point_id, vector in zip(
                    ids[start : start + 64], vectors[start : start + 64], strict=True
                )
            ],
            wait=True,
        )
    return len(ids)


def _build_indexes(client: QdrantClient, expected: int) -> None:
    destination = get_data_dir() / INDEX_DIR
    temporary = destination.with_name(f"{INDEX_DIR}.building")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    rows, offset = [], None
    while True:
        batch, offset = client.scroll(
            COLLECTION, limit=1000, offset=offset, with_payload=True, with_vectors=False
        )
        rows.extend(
            (
                str(row.id),
                str((row.payload or {}).get("title") or ""),
                str((row.payload or {}).get("text") or ""),
            )
            for row in batch
        )
        if offset is None:
            break
    if len(rows) != expected:
        raise RuntimeError(f"建立索引时读取数量不一致：{len(rows)}/{expected}")
    ids = [row[0] for row in rows]
    (temporary / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    for name, field in (("title", 1), ("content", 2)):
        retriever = bm25s.BM25()
        retriever.index([tokenize(row[field]) for row in rows], show_progress=True)
        retriever.save(temporary / name, show_progress=False)
        print(f"{name} BM25S 索引完成：{expected}", flush=True)
    (temporary / "manifest.json").write_text(
        json.dumps({"collection": COLLECTION, "count": expected}, indent=2),
        encoding="utf-8",
    )
    if destination.exists():
        shutil.rmtree(destination)
    temporary.replace(destination)


def _install_sync() -> None:
    assets = _assets_dir()
    archive_path = assets / "npc-law-vectors.zip"
    checksum_path = assets / "npc-law-vectors.zip.sha256"
    raw_dir = assets / "raw" / "npc"
    if (
        not archive_path.is_file()
        or not checksum_path.is_file()
        or not raw_dir.is_dir()
    ):
        raise FileNotFoundError("离线法规原始数据、向量压缩包或校验文件缺失")
    data_dir = get_data_dir()
    state_path = data_dir / "laws-install.json"
    payload_database = data_dir / "laws-install-payloads.sqlite3"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.is_file()
        else {"format_version": 1, "imported_shards": [], "raw_prepared": False}
    )
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        expected = int(manifest["count"])
        if (
            manifest.get("dimensions") != DIMENSIONS
            or manifest.get("vector_name") != VECTOR_NAME
        ):
            raise ValueError("向量安装包配置不匹配")
        expected_hash = checksum_path.read_text(encoding="ascii").split()[0]
        if state.get("archive_sha256") != expected_hash:
            print("正在校验法规向量压缩包…", flush=True)
            actual_hash = _sha256_file(archive_path)
            if actual_hash != expected_hash:
                raise ValueError("法规向量压缩包 SHA-256 校验失败")
            state = {
                "format_version": 1,
                "archive_sha256": expected_hash,
                "imported_shards": [],
                "raw_prepared": False,
            }
            _dump(state_path, state)
        with closing(QdrantClient(url=DEFAULT_QDRANT_URL, timeout=120)) as client:
            _validate_collection(client)
            current = client.count(COLLECTION, exact=True).count
            index_manifest = data_dir / INDEX_DIR / "manifest.json"
            if current == expected and index_manifest.is_file():
                index_state = json.loads(index_manifest.read_text(encoding="utf-8"))
                if index_state.get("count") == expected:
                    _dump(
                        data_dir / "laws-installed.json",
                        {
                            "format_version": 1,
                            "count": expected,
                            "archive_sha256": expected_hash,
                        },
                    )
                    print(f"法规库已经完整安装：{expected} 条")
                    return
            imported = set(state.get("imported_shards", []))
            imported_count = sum(
                int(shard["count"])
                for shard in manifest["shards"]
                if shard["name"] in imported
            )
            if current < imported_count:
                imported.clear()
                state["imported_shards"] = []
            if not state.get("raw_prepared") or not payload_database.is_file():
                print("正在整理原始法规与稳定 ID…", flush=True)
                _prepare_payload_database(raw_dir, payload_database, expected)
                state["raw_prepared"] = True
                _dump(state_path, state)
            with closing(sqlite3.connect(payload_database)) as payloads:
                for index, shard in enumerate(manifest["shards"], 1):
                    name = shard["name"]
                    if name in imported:
                        continue
                    content = archive.read(name)
                    if _sha256_bytes(content) != shard["sha256"]:
                        raise ValueError(f"向量分片校验失败：{name}")
                    count = _import_shard(client, payloads, content)
                    if count != shard["count"]:
                        raise ValueError(f"向量分片数量错误：{name}")
                    state["imported_shards"].append(name)
                    _dump(state_path, state)
                    print(
                        f"[{index}/{len(manifest['shards'])}] 已入库 {name}", flush=True
                    )
            actual = client.count(COLLECTION, exact=True).count
            if actual != expected:
                raise RuntimeError(f"法规入库数量错误：{actual}/{expected}")
            print("正在建立标题和正文 BM25S 索引…", flush=True)
            _build_indexes(client, expected)
    payload_database.unlink(missing_ok=True)
    _dump(
        data_dir / "laws-installed.json",
        {"format_version": 1, "count": expected, "archive_sha256": expected_hash},
    )
    print(f"法规库安装完成：{expected} 条", flush=True)


async def install_laws() -> None:
    server = QdrantServer()
    try:
        await server.start()
        await asyncio.to_thread(_install_sync)
    finally:
        await server.close()
