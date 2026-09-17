"""将内嵌法规库复制到本机服务端，验证全部记录后更新 Batch 断点。"""

import json
import math
from contextlib import closing
from pathlib import Path

from qdrant_client import QdrantClient, models

ROOT = Path(__file__).resolve().parents[2]
URL = "http://127.0.0.1:6333"


def main():
    name = "npc_laws"
    with (
        closing(QdrantClient(path=str(ROOT / "data/rag/qdrant"))) as source,
        closing(QdrantClient(url=URL, timeout=120)) as target,
    ):
        vectors = source.get_collection(name).config.params.vectors
        if target.collection_exists(name):
            if target.get_collection(name).config.params.vectors != vectors:
                raise ValueError("目标法规集合向量配置不匹配")
        else:
            target.create_collection(name, vectors_config=vectors)
        offset, total = None, 0
        while True:
            rows, offset = source.scroll(
                name, limit=64, offset=offset, with_vectors=True
            )
            if rows:
                target.upsert(
                    name,
                    points=[
                        models.PointStruct(id=r.id, payload=r.payload, vector=r.vector)
                        for r in rows
                    ],
                    wait=True,
                )
                copied = {
                    r.id: r
                    for r in target.retrieve(
                        name, ids=[r.id for r in rows], with_vectors=True
                    )
                }
                for row in rows:
                    actual = copied.get(row.id)
                    if (
                        actual is None
                        or actual.payload != row.payload
                        or set(actual.vector) != set(row.vector)
                    ):
                        raise ValueError(f"迁移验证失败：{row.id}")
                    for key, values in row.vector.items():
                        norm = math.hypot(*values)
                        expected = [v / norm for v in values] if norm else values
                        if len(actual.vector[key]) != len(expected) or not all(
                            math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)
                            for a, b in zip(actual.vector[key], expected, strict=True)
                        ):
                            raise ValueError(f"向量验证失败：{row.id}")
                total += len(rows)
            if offset is None:
                break
        print(
            f"Migrated and verified: {total}; target: {target.count(name, exact=True).count}"
        )
    # 仅变更连接目标，保留批次 ID、成功标记和入库计数。
    for path in (ROOT / "data/rag").glob("*/manifest.json"):
        state = json.loads(path.read_text(encoding="utf-8"))
        if (
            state.get("collection") == name
            and Path(state.get("database", "")).resolve()
            == (ROOT / "data/rag/qdrant").resolve()
        ):
            state["database_url"] = URL
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
            print(f"Updated checkpoint: {path.parent.name}")


if __name__ == "__main__":
    main()
