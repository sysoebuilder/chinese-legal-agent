"""NPC 法规异步 Batch 向量化：仅正文 text 生成 1024 维命名向量。

标题及其他字段只保存为 payload，不提交到向量化接口。

项目根目录执行（prepare 不联网，submit 会产生 API 费用）：
  python laws/scripts/batch_embed.py prepare --limit 100
  python laws/scripts/batch_embed.py submit
  python laws/scripts/batch_embed.py status
  python laws/scripts/batch_embed.py collect

默认原始数据 laws/raw/npc；任务/断点 data/rag/batch；入库使用本机 Qdrant 服务端。
collect 只查询一次任务状态，未完成时稍后重新运行；不会长时间等待。
不要为同一数据重复 prepare/submit：不同工作目录意味着新的付费任务。
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
from contextlib import ExitStack, closing, nullcontext
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
import pyarrow.parquet as pq
from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

from ai_agent_learning.qdrant_server import QdrantServer

ROOT = Path(__file__).resolve().parents[2]
DIMENSIONS = {"text": 1024}
MODEL = "embedding-3"
ENDPOINT = "/v4/embeddings"
BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
TERMINAL = {"completed", "failed", "expired", "cancelled"}


def dump(path: Path, value: dict) -> None:
    """同目录原子替换断点，避免中断导致 JSON 损坏。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def state_path(args) -> Path:
    return args.work_dir.resolve() / "manifest.json"


def load_state(args) -> dict:
    state = json.loads(state_path(args).read_text(encoding="utf-8"))
    if (
        state.get("model") != MODEL
        or state.get("dimensions") != DIMENSIONS
        or state.get("format_version") != 4
    ):
        raise ValueError("任务模型、维度或格式不匹配；请勿使用旧版任务文件")
    return state


def prepare(args) -> None:
    if not 1 <= args.requests_per_file <= 10000:
        raise ValueError("每个 Batch 文件请求上限必须为 1～10000")
    if args.limit is not None and args.limit <= 0:
        raise ValueError("limit 必须大于 0")
    files = sorted(args.source_dir.resolve().glob("*.parquet"))
    if not files:
        raise FileNotFoundError("源目录中没有 Parquet 文件")
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    if any(work.iterdir()):
        raise ValueError("工作目录非空；请续跑 submit/collect，或使用新的 --work-dir")
    state = {
        "format_version": 4,
        "model": MODEL,
        "dimensions": DIMENSIONS,
        "collection": args.collection,
        "database": str(args.database.resolve()),
        "database_url": args.database_url,
        "count": 0,
        "shards": [],
    }
    # 每条法规仅提交一个正文向量化请求。
    # 单文件最多 100MB；使用 90MB 保留余量。
    requests, records = [], []
    size = 0
    seen = {}
    existing = set()
    if args.skip_existing:
        with closing(QdrantClient(url=args.database_url, timeout=60)) as store:
            if store.collection_exists(args.collection):
                offset = None
                while True:
                    rows, offset = store.scroll(
                        args.collection,
                        limit=1000,
                        offset=offset,
                        with_payload=False,
                        with_vectors=False,
                    )
                    existing.update(str(row.id) for row in rows)
                    if offset is None:
                        break
        print(f"跳过已存在的 {len(existing)} 条")
    state["skipped_existing"] = 0
    state["skipped_identical"] = 0
    state["collision_variants"] = 0

    def flush():
        nonlocal requests, records, size
        if not requests:
            return
        name = f"part-{len(state['shards']):05d}"
        request_path = work / f"{name}.requests.jsonl"
        request_path.write_text("".join(requests), encoding="utf-8")
        (work / f"{name}.records.jsonl").write_text("".join(records), encoding="utf-8")
        state["shards"].append(
            {
                "name": name,
                "count": len(records),
                "request_count": len(requests),
                "sha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
            }
        )
        state["count"] += len(records)
        requests, records, size = [], [], 0

    for source in files:
        for batch in pq.ParquetFile(source).iter_batches(batch_size=256):
            for row in batch.to_pylist():
                source_id = str(row.get("id") or "").strip()
                if not source_id:
                    raise ValueError("法规 id 缺失")
                fingerprint = hashlib.sha256(
                    json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest()
                variants = seen.setdefault(source_id, set())
                if fingerprint in variants:
                    state["skipped_identical"] += 1
                    continue
                identity = (
                    source_id if not variants else f"{source_id}:variant:{fingerprint}"
                )
                if variants:
                    state["collision_variants"] += 1
                variants.add(fingerprint)
                text = str(row.get("text") or "").strip()
                if not text:
                    raise ValueError(f"正文为空，不能生成向量：{source_id}")
                point_id = str(uuid5(NAMESPACE_URL, f"npc-law:{identity}"))
                if point_id in existing:
                    state["skipped_existing"] += 1
                    continue
                row_requests = [
                    line(
                        {
                            "custom_id": f"{point_id}:{name}",
                            "method": "POST",
                            "url": ENDPOINT,
                            "body": {
                                "model": MODEL,
                                "dimensions": DIMENSIONS[name],
                                "input": value,
                            },
                        }
                    )
                    for name, value in (("text", text),)
                ]
                length = sum(len(request.encode("utf-8")) for request in row_requests)
                if length > 90_000_000:
                    raise ValueError(f"单条请求过大：{source_id}")
                if (
                    len(requests) + 1 > args.requests_per_file
                    or size + length > 90_000_000
                ):
                    flush()
                payload = {**row, "source": "npc"}
                requests.extend(row_requests)
                records.append(line({"id": point_id, "payload": payload}))
                size += length
                if (
                    args.limit is not None
                    and state["count"] + len(records) >= args.limit
                ):
                    flush()
                    dump(state_path(args), state)
                    print(
                        f"准备完成：{state['count']} 条，{len(state['shards'])} 个文件"
                    )
                    return
    flush()
    dump(state_path(args), state)
    print(f"准备完成：{state['count']} 条，{len(state['shards'])} 个文件")


def api_client() -> httpx.Client:
    load_dotenv(ROOT / "data" / ".env")
    key = os.getenv("ZAI_API_KEY", "").strip()
    if not key:
        raise ValueError("缺少 ZAI_API_KEY 配置")
    # 不自动重试创建操作：响应丢失时重复 POST 可能造成重复付费。
    return httpx.Client(
        base_url=BASE_URL, headers={"Authorization": f"Bearer {key}"}, timeout=120
    )


def submit(args) -> None:
    state = load_state(args)
    with api_client() as client:
        for shard in state["shards"]:
            if shard.get("batch_id"):
                print(f"跳过已提交：{shard['batch_id']}")
                continue
            if shard.get("submitting"):
                raise RuntimeError(
                    f"{shard['name']} 上次提交结果不确定。请在平台核查任务，"
                    "将 batch_id 填入 manifest.json 后续跑；勿直接重复提交。"
                )
            path = args.work_dir / f"{shard['name']}.requests.jsonl"
            if hashlib.sha256(path.read_bytes()).hexdigest() != shard["sha256"]:
                raise ValueError("请求文件已修改，拒绝提交")
            if not shard.get("input_file_id"):
                with path.open("rb") as file:
                    response = client.post(
                        "files",
                        data={"purpose": "batch"},
                        files={"file": (path.name, file, "application/jsonl")},
                    )
                response.raise_for_status()
                shard["input_file_id"] = response.json()["id"]
                dump(state_path(args), state)
            shard["submitting"] = True
            dump(state_path(args), state)
            response = client.post(
                "batches",
                json={
                    "input_file_id": shard["input_file_id"],
                    "endpoint": ENDPOINT,
                    "auto_delete_input_file": False,
                },
            )
            if 400 <= response.status_code < 500:
                shard["submitting"] = False
                dump(state_path(args), state)
            response.raise_for_status()
            shard["batch_id"] = response.json()["id"]
            shard["submitting"] = False
            dump(state_path(args), state)
            print(f"已提交：{shard['name']} → {shard['batch_id']}")


def download(client, file_id, path):
    if path.exists():
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    with client.stream("GET", f"files/{file_id}/content") as response:
        response.raise_for_status()
        with temporary.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)
    temporary.replace(path)


def import_results(state, shard, work, external_store=None) -> None:
    """稳定 UUID upsert：中断后重跑不会新增重复 point。"""
    if state["collection"] == "memories":
        raise ValueError("禁止写入长期记忆集合")
    output = work / f"{shard['name']}.output.jsonl"
    if not output.exists():
        return
    with (work / f"{shard['name']}.records.jsonl").open(encoding="utf-8") as file:
        records = {row["id"]: row["payload"] for row in map(json.loads, file)}
    succeeded = set()
    db = Path(state["database"])
    if db == (ROOT / "data" / "memory" / "qdrant").resolve():
        raise ValueError("禁止写入长期记忆数据库")
    with (
        nullcontext(external_store)
        if external_store is not None
        else closing(
            QdrantClient(
                url=state.get("database_url", "http://127.0.0.1:6333"), timeout=60
            )
        )
    ) as store:
        collection = state["collection"]
        if store.collection_exists(collection):
            config = store.get_collection(collection).config.params.vectors
            if (
                not isinstance(config, dict)
                or set(config) != set(DIMENSIONS)
                or any(
                    v.size != DIMENSIONS[name] or v.distance != models.Distance.COSINE
                    for name, v in config.items()
                )
            ):
                raise ValueError(
                    "集合必须仅含 text 1024 维 Cosine 命名向量；不会自动删除旧集合"
                )
        else:
            store.create_collection(
                collection,
                vectors_config={
                    name: models.VectorParams(
                        size=DIMENSIONS[name], distance=models.Distance.COSINE
                    )
                    for name in DIMENSIONS
                },
            )
        points = []
        pending = {}
        seen_results = set()
        with output.open(encoding="utf-8") as file:
            for result in map(json.loads, file):
                response = result.get("response") or {}
                if response.get("status_code") != 200 or result.get("error"):
                    continue
                custom_id = result["custom_id"]
                point_id, name = custom_id.rsplit(":", 1)
                if (
                    point_id not in records
                    or name not in DIMENSIONS
                    or custom_id in seen_results
                ):
                    raise ValueError(f"未知或重复的结果 ID：{custom_id}")
                seen_results.add(custom_id)
                data = response["body"]["data"]
                data = sorted(data, key=lambda item: item["index"])
                if len(data) != 1 or data[0]["index"] != 0:
                    raise ValueError("每个请求必须返回一个 index=0 的向量")
                values = [float(x) for x in data[0]["embedding"]]
                if len(values) != DIMENSIONS[name] or not all(
                    math.isfinite(x) for x in values
                ):
                    raise ValueError(f"{name} 返回向量不符合 {DIMENSIONS[name]} 维配置")
                vector = pending.setdefault(point_id, {})
                vector[name] = values
                if set(vector) != set(DIMENSIONS):
                    continue
                pending.pop(point_id)
                points.append(
                    models.PointStruct(
                        id=point_id, vector=vector, payload=records[point_id]
                    )
                )
                succeeded.add(point_id)
                if len(points) >= 64:
                    store.upsert(collection, points=points, wait=True)
                    points = []
        if points:
            store.upsert(collection, points=points, wait=True)
    missing = sorted(set(records) - succeeded)
    dump(work / f"{shard['name']}.missing.json", {"ids": missing})
    shard["imported_count"] = len(succeeded)
    shard["imported"] = True
    print(
        f"{shard['name']}：入库 {len(succeeded)} 条，未成功 {len(missing)} 条（详见 missing.json / errors.jsonl）"
    )


def collect(args) -> None:
    state = load_state(args)
    with api_client() as client, ExitStack() as stack:
        store = None
        for shard in state["shards"]:
            if shard.get("imported") or not shard.get("batch_id"):
                continue
            response = client.get(f"batches/{shard['batch_id']}")
            response.raise_for_status()
            batch = response.json()
            shard["status"] = batch["status"]
            print(f"{shard['name']}：{batch['status']}")
            if batch["status"] in TERMINAL:
                for key, suffix in (
                    ("output_file_id", "output"),
                    ("error_file_id", "errors"),
                ):
                    if batch.get(key):
                        download(
                            client,
                            batch[key],
                            args.work_dir / f"{shard['name']}.{suffix}.jsonl",
                        )
                if batch.get("output_file_id"):
                    if store is None:
                        db = Path(state["database"]).resolve()
                        if db == (ROOT / "data" / "memory" / "qdrant").resolve():
                            raise ValueError("禁止写入长期记忆数据库")
                        store = stack.enter_context(
                            closing(
                                QdrantClient(
                                    url=state.get(
                                        "database_url", "http://127.0.0.1:6333"
                                    ),
                                    timeout=60,
                                )
                            )
                        )
                    import_results(state, shard, args.work_dir, external_store=store)
                if not batch.get("output_file_id"):
                    print("没有成功输出，未入库；请查看平台任务错误")
            dump(state_path(args), state)


def status(args) -> None:
    """手动查询一次进度：不下载、不入库、不提交、不循环监测。"""
    state = load_state(args)
    totals = {"total": 0, "completed": 0, "failed": 0}
    submitted = 0
    with api_client() as client:
        for index, shard in enumerate(state["shards"], 1):
            if not shard.get("batch_id"):
                print(f"[{index}/{len(state['shards'])}] {shard['name']} 尚未提交")
                continue
            response = client.get(f"batches/{shard['batch_id']}")
            response.raise_for_status()
            batch = response.json()
            counts = batch.get("request_counts") or {}
            submitted += 1
            for key in totals:
                totals[key] += counts.get(key, 0)
            print(
                f"[{index}/{len(state['shards'])}] {shard['name']} {batch['status']} "
                f"成功 {counts.get('completed', 0)}/{counts.get('total', shard['count'])} "
                f"失败 {counts.get('failed', 0)}",
                flush=True,
            )
    done = totals["completed"] + totals["failed"]
    percent = done / state["count"] * 100 if state["count"] else 100
    print(
        f"提交任务 {submitted}/{len(state['shards'])}；处理进度 {percent:.2f}%："
        f"成功 {totals['completed']}，失败 {totals['failed']}，计划 {state['count']} 条"
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("prepare", "submit", "status", "collect"))
    parser.add_argument(
        "--source-dir", type=Path, default=ROOT / "laws" / "raw" / "npc"
    )
    parser.add_argument(
        "--work-dir", type=Path, default=ROOT / "data" / "rag" / "batch"
    )
    parser.add_argument(
        "--database", type=Path, default=ROOT / "data" / "rag" / "qdrant"
    )
    parser.add_argument("--collection", default="npc_laws")
    parser.add_argument("--database-url", default="http://127.0.0.1:6333")
    parser.add_argument("--requests-per-file", type=int, default=5000)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="准备时跳过数据库已有 point ID，避免重复付费",
    )
    args = parser.parse_args()
    args.work_dir = args.work_dir.resolve()
    if args.collection == "memories":
        raise ValueError("禁止写入长期记忆集合")
    server = QdrantServer()
    try:
        if args.command == "collect" or (
            args.command == "prepare" and args.skip_existing
        ):
            database_url = (
                load_state(args).get("database_url", args.database_url)
                if args.command == "collect"
                else args.database_url
            )
            if database_url.rstrip("/") == "http://127.0.0.1:6333":
                asyncio.run(server.start())
        {"prepare": prepare, "submit": submit, "status": status, "collect": collect}[
            args.command
        ](args)
    finally:
        asyncio.run(server.close())


if __name__ == "__main__":
    main()
