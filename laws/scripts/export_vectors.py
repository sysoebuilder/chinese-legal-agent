"""将 npc_laws 的 point ID 与 1024 维正文向量导出为离线压缩包。"""

import argparse
import hashlib
import json
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from qdrant_client import QdrantClient

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = "npc_laws"
VECTOR_NAME = "text"
DIMENSIONS = 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_shard(path: Path, rows) -> None:
    ids, vectors = [], []
    for row in rows:
        vector = row.vector
        if not isinstance(vector, dict) or VECTOR_NAME not in vector:
            raise ValueError(f"记录缺少 {VECTOR_NAME} 命名向量：{row.id}")
        values = vector[VECTOR_NAME]
        if len(values) != DIMENSIONS:
            raise ValueError(f"向量维度错误：{row.id}，实际 {len(values)}")
        ids.append(str(row.id))
        vectors.extend(float(value) for value in values)
    vector_array = pa.FixedSizeListArray.from_arrays(
        pa.array(vectors, type=pa.float32()), DIMENSIONS
    )
    table = pa.Table.from_arrays(
        [pa.array(ids, type=pa.string()), vector_array],
        names=["point_id", "text_vector"],
    )
    pq.write_table(table, path, compression="zstd", compression_level=9)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:6333")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "laws/npc-law-vectors.zip"
    )
    parser.add_argument("--shard-size", type=int, default=5000)
    args = parser.parse_args()
    if args.shard_size <= 0:
        raise ValueError("shard-size 必须大于 0")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    temporary_output.unlink(missing_ok=True)
    manifest = {
        "format_version": 1,
        "collection": COLLECTION,
        "vector_name": VECTOR_NAME,
        "dimensions": DIMENSIONS,
        "count": 0,
        "shards": [],
    }
    with (
        closing(QdrantClient(url=args.url, timeout=120)) as client,
        tempfile.TemporaryDirectory(dir=output.parent) as temporary_dir,
        zipfile.ZipFile(temporary_output, "w", allowZip64=True) as archive,
    ):
        expected = client.count(COLLECTION, exact=True).count
        offset = None
        index = 0
        while True:
            rows, offset = client.scroll(
                COLLECTION,
                limit=args.shard_size,
                offset=offset,
                with_payload=False,
                with_vectors=[VECTOR_NAME],
            )
            if rows:
                name = f"vectors/part-{index:05d}.parquet"
                shard_path = Path(temporary_dir) / Path(name).name
                write_shard(shard_path, rows)
                shard_hash = sha256(shard_path)
                archive.write(shard_path, name, compress_type=zipfile.ZIP_STORED)
                manifest["shards"].append(
                    {"name": name, "count": len(rows), "sha256": shard_hash}
                )
                manifest["count"] += len(rows)
                index += 1
                print(
                    f"[{manifest['count']}/{expected}] {name}，"
                    f"压缩后 {shard_path.stat().st_size / 1024 / 1024:.1f} MiB",
                    flush=True,
                )
            if offset is None:
                break
        if manifest["count"] != expected:
            raise RuntimeError(
                f"导出期间数据库数量不一致：预期 {expected}，实际 {manifest['count']}"
            )
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    temporary_output.replace(output)
    digest = sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )
    print(f"导出完成：{output}，{manifest['count']} 条，SHA-256 {digest}")


if __name__ == "__main__":
    main()
