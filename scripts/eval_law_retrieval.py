"""运行法规检索冻结集并输出 Recall@K 报告。"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ai_agent_learning.paths import get_data_dir
from ai_agent_learning.qdrant_server import QdrantServer
from ai_agent_learning.tools.builtin.search_raws import handler


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def law_key(payload: dict[str, Any] | None) -> tuple[str, str]:
    payload = payload or {}
    return (
        str(payload.get("title") or "").strip(),
        str(payload.get("article_no_cn") or "").strip(),
    )


def recall_at_k(
    expected: set[tuple[str, str]],
    retrieved: list[tuple[str, str]],
    k: int,
) -> float:
    if not expected:
        return 1.0
    return len(expected & set(retrieved[:k])) / len(expected)


async def evaluate(cases: list[dict[str, Any]], batch_size: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    for offset in range(0, len(cases), batch_size):
        batch = cases[offset : offset + batch_size]
        retrieval_cases = [
            {
                "issue": case["issue"],
                "title_keywords": case["title_keywords"],
                "content_keywords": case["content_keywords"],
                "case_facts": case["case_facts"],
            }
            for case in batch
        ]
        batch_started = time.perf_counter()
        response = await handler(retrieval_cases)
        batch_latency_ms = (time.perf_counter() - batch_started) * 1000
        for case, result in zip(batch, response["results"], strict=True):
            expected = {
                (item["title"], item["article_no_cn"])
                for item in case["expected"]
            }
            retrieved = [law_key(item.get("payload")) for item in result["laws"]]
            first_rank = next(
                (index for index, item in enumerate(retrieved, start=1) if item in expected),
                None,
            )
            rows.append(
                {
                    "id": case["id"],
                    "question": case["case_facts"],
                    "expected": [list(item) for item in sorted(expected)],
                    "retrieved_count": len(retrieved),
                    "first_relevant_rank": first_rank,
                    "recall_at_1": recall_at_k(expected, retrieved, 1),
                    "recall_at_5": recall_at_k(expected, retrieved, 5),
                    "recall_at_10": recall_at_k(expected, retrieved, 10),
                    "top_10": [list(item) for item in retrieved[:10]],
                    "batch_latency_ms": round(batch_latency_ms, 2),
                }
            )
        print(f"已完成 {min(offset + batch_size, len(cases))}/{len(cases)}", flush=True)

    elapsed_ms = (time.perf_counter() - started) * 1000
    count = len(rows)
    return {
        "summary": {
            "cases": count,
            "recall_at_1": sum(row["recall_at_1"] for row in rows) / count,
            "recall_at_5": sum(row["recall_at_5"] for row in rows) / count,
            "recall_at_10": sum(row["recall_at_10"] for row in rows) / count,
            "mrr": sum(
                1 / row["first_relevant_rank"]
                if row["first_relevant_rank"] is not None
                else 0
                for row in rows
            ) / count,
            "total_latency_ms": round(elapsed_ms, 2),
            "average_latency_ms_per_case": round(elapsed_ms / count, 2),
        },
        "cases": rows,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/legal_retrieval.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval-results/legal-retrieval.json"),
    )
    parser.add_argument("--batch-size", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 5:
        raise ValueError("batch-size 必须在 1 到 5 之间")

    load_dotenv(get_data_dir() / ".env", override=False)
    if not os.getenv("ZAI_API_KEY", "").strip():
        raise ValueError("缺少 ZAI_API_KEY，无法运行向量查询")

    cases = load_cases(args.dataset)
    server = QdrantServer()
    try:
        await server.start()
        report = await evaluate(cases, args.batch_size)
    finally:
        await server.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告：{args.output.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
