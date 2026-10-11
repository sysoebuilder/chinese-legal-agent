"""从服务端 npc_laws 集合建立标题、正文 BM25S 倒排索引。"""

import json
import shutil
from contextlib import closing

import bm25s
from qdrant_client import QdrantClient

from chinese_legal_agent.law_index import INDEX_DIR, tokenize
from chinese_legal_agent.paths import get_data_dir
from chinese_legal_agent.qdrant_config import DEFAULT_QDRANT_URL


def read_corpus(client):
    offset = None
    while True:
        rows, offset = client.scroll(
            "npc_laws", limit=1000, offset=offset, with_payload=True, with_vectors=False
        )
        for row in rows:
            payload = row.payload or {}
            yield (
                str(row.id),
                str(payload.get("title") or ""),
                str(payload.get("text") or ""),
            )
        if offset is None:
            break


def main() -> None:
    destination = get_data_dir() / INDEX_DIR
    temporary = destination.with_name(f"{INDEX_DIR}.building")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    with closing(QdrantClient(url=DEFAULT_QDRANT_URL, timeout=120)) as client:
        expected = client.count("npc_laws", exact=True).count
        rows = list(read_corpus(client))
    if len(rows) != expected:
        raise RuntimeError(
            f"读取期间集合发生变化：开始 {expected} 条，读到 {len(rows)} 条"
        )
    ids = [point_id for point_id, _, _ in rows]
    (temporary / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    for name, field in (("title", 1), ("content", 2)):
        retriever = bm25s.BM25()
        retriever.index([tokenize(row[field]) for row in rows], show_progress=True)
        retriever.save(temporary / name, show_progress=False)
        print(f"{name} index completed: {len(rows)}")
    (temporary / "manifest.json").write_text(
        json.dumps({"collection": "npc_laws", "count": len(ids)}, indent=2),
        encoding="utf-8",
    )
    if destination.exists():
        shutil.rmtree(destination)
    temporary.replace(destination)
    print(f"BM25 indexes ready: {len(ids)}")


if __name__ == "__main__":
    main()
