"""Evaluate title BM25 -> content BM25 top 50 -> BGE top 5, without embeddings."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from qdrant_client import QdrantClient

from chinese_legal_agent.law_index import LawBm25Index
from chinese_legal_agent.qdrant_config import DEFAULT_QDRANT_URL
from chinese_legal_agent.reranker import MODEL_PATH, load_reranker, rerank_laws


def law_key(payload):
    payload = payload or {}
    return (
        str(payload.get("title") or "").strip(),
        str(payload.get("article_no_cn") or "").strip(),
    )


def recall(expected, retrieved, k):
    return len(expected & set(retrieved[:k])) / len(expected)


def summary(rows):
    count = len(rows)
    if not count:
        return {"cases": 0}
    return {
        "cases": count,
        **{
            f"recall_at_{k}": sum(row[f"recall_at_{k}"] for row in rows) / count
            for k in range(1, 6)
        },
        "mrr": sum(
            1 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0
            for row in rows
        )
        / count,
        "empty_results": sum(row["retrieved_count"] == 0 for row in rows),
        "full_target_coverage_at_5": sum(row["recall_at_5"] == 1 for row in rows),
        "bm25_candidate_recall_at_50": sum(
            row["bm25_candidate_recall_at_50"] for row in rows
        )
        / count,
        "bm25_recall_at_1": sum(row["bm25_recall_at_1"] for row in rows) / count,
        "bm25_recall_at_5": sum(row["bm25_recall_at_5"] for row in rows) / count,
        "average_latency_ms_per_case": round(
            sum(row["latency_ms"] for row in rows) / count, 2
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("evals/legal_retrieval_titled_300.jsonl")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval-results/legal-retrieval-titled-300-bm25-rerank.json"),
    )
    parser.add_argument("--index-dir", type=Path)
    parser.add_argument(
        "--qdrant-url", default=os.getenv("QDRANT_URL") or DEFAULT_QDRANT_URL
    )
    args = parser.parse_args()
    dataset_bytes = args.dataset.read_bytes()
    cases = [
        json.loads(line)
        for line in dataset_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    if not cases or any(
        not case["title_keywords"] or not case["expected"] for case in cases
    ):
        raise ValueError(
            "This evaluation requires title keywords and expected targets in every case"
        )
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    started = time.perf_counter()
    index = LawBm25Index(args.index_dir)
    _tokenizer, model = load_reranker()
    initialization_ms = (time.perf_counter() - started) * 1000
    rows = []
    with closing(QdrantClient(url=args.qdrant_url, timeout=120)) as client:
        corpus_count = client.count("npc_laws", exact=True).count
        if corpus_count != index.manifest["count"]:
            raise ValueError(
                f"Collection/index count mismatch: {corpus_count} != {index.manifest['count']}"
            )
        # Confirm target existence before retrieval; no changes based on retrieval outcomes.
        targets = {law_key(item) for case in cases for item in case["expected"]}
        target_payloads = {}
        for title in sorted({title for title, _ in targets}):
            offset = None
            while True:
                points, offset = client.scroll(
                    "npc_laws",
                    scroll_filter={
                        "must": [{"key": "title", "match": {"value": title}}]
                    },
                    limit=10000,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    key = law_key(point.payload)
                    if key in targets:
                        target_payloads.setdefault(key, []).append(str(point.id))
                if offset is None:
                    break
        missing_targets = targets - target_payloads.keys()
        if missing_targets:
            raise ValueError(
                f"Expected targets missing from corpus: {sorted(missing_targets)}"
            )
        missing_index_ids = {
            key
            for key, ids in target_payloads.items()
            if not any(point_id in index.positions for point_id in ids)
        }
        if missing_index_ids:
            raise ValueError(
                f"Expected targets missing from BM25 index: {sorted(missing_index_ids)}"
            )
        evaluation_started = time.perf_counter()
        for number, case in enumerate(cases, 1):
            case_started = time.perf_counter()
            expected = {law_key(item) for item in case["expected"]}
            title_ids = index.title_candidates(case["title_keywords"])
            lexical = index.rerank_content(case["content_keywords"], title_ids, 50)
            points = (
                client.retrieve(
                    "npc_laws",
                    ids=[point_id for point_id, _ in lexical],
                    with_payload=True,
                    with_vectors=False,
                )
                if lexical
                else []
            )
            payloads = {str(point.id): point.payload for point in points}
            if any(point_id not in payloads for point_id, _ in lexical):
                raise RuntimeError("BM25 index contains IDs absent from Qdrant")
            candidates = [
                {
                    "id": point_id,
                    "payload": payloads[point_id],
                    "bm25_score": score,
                    "vector_score": None,
                }
                for point_id, score in lexical
            ]
            lexical_keys = [law_key(item["payload"]) for item in candidates]
            laws = rerank_laws(case["case_facts"], candidates, limit=5)
            retrieved = [law_key(item["payload"]) for item in laws]
            first_rank = next(
                (rank for rank, key in enumerate(retrieved, 1) if key in expected), None
            )
            rows.append(
                {
                    "id": case["id"],
                    "question": case["case_facts"],
                    "group": case.get("eval_group", "original-titled-130"),
                    "expected": [list(key) for key in sorted(expected)],
                    "title_candidate_count": len(title_ids),
                    "bm25_candidate_count": len(candidates),
                    "bm25_candidate_recall_at_50": recall(expected, lexical_keys, 50),
                    "bm25_recall_at_1": recall(expected, lexical_keys, 1),
                    "bm25_recall_at_5": recall(expected, lexical_keys, 5),
                    "bm25_first_relevant_rank": next(
                        (
                            rank
                            for rank, key in enumerate(lexical_keys, 1)
                            if key in expected
                        ),
                        None,
                    ),
                    "retrieved_count": len(retrieved),
                    "first_relevant_rank": first_rank,
                    **{
                        f"recall_at_{k}": recall(expected, retrieved, k)
                        for k in range(1, 6)
                    },
                    "top_5": [list(key) for key in retrieved],
                    "top_5_details": [
                        {
                            "id": item["id"],
                            "title": (item["payload"] or {}).get("title"),
                            "article_no_cn": (item["payload"] or {}).get(
                                "article_no_cn"
                            ),
                            "bm25_score": item["bm25_score"],
                            "rerank_score": item["rerank_score"],
                        }
                        for item in laws
                    ],
                    "latency_ms": round((time.perf_counter() - case_started) * 1000, 2),
                }
            )
            if number % 25 == 0 or number == len(cases):
                print(f"Completed {number}/{len(cases)}", flush=True)
        evaluation_ms = (time.perf_counter() - evaluation_started) * 1000
    report = {
        "metadata": {
            "evaluated_at_utc": datetime.now(UTC).isoformat(),
            "dataset": str(args.dataset),
            "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
            "retrieval_mode": "title_bm25_max_score_ties -> content_bm25_top50 -> bge_top5",
            "vector_retrieval": False,
            "embedding_requests": 0,
            "reranker": str(MODEL_PATH),
            "device": str(next(model.parameters()).device),
            "index_path": str(index.path),
            "index_manifest": index.manifest,
            "index_ids_sha256": hashlib.sha256(
                (index.path / "ids.json").read_bytes()
            ).hexdigest(),
            "qdrant_url": args.qdrant_url,
            "corpus_records": corpus_count,
            "git_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "initialization_ms": round(initialization_ms, 2),
            "expected_match_fields": ["title", "article_no_cn"],
            "version_matching": "Any document version with the expected title/article, consistent with previous evaluations",
            "unique_expected_targets_verified": len(targets),
        },
        "summary": {**summary(rows), "total_latency_ms": round(evaluation_ms, 2)},
        "by_group": {
            group: summary([row for row in rows if row["group"] == group])
            for group in sorted({row["group"] for row in rows})
        },
        "by_law": {
            title: summary(
                [
                    row
                    for row in rows
                    if any(item[0] == title for item in row["expected"])
                ]
            )
            for title in sorted({title for title, _ in targets})
        },
        "first_relevant_rank_counts": dict(
            Counter(str(row["first_relevant_rank"]) for row in rows)
        ),
        "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
