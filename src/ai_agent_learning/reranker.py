"""用本地 BGE 模型重排法律与记忆检索结果。"""

from functools import lru_cache
from pathlib import Path
from typing import Any

MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "bge-reranker-v2-m3"


@lru_cache(maxsize=1)
def load_reranker():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    if not MODEL_PATH.is_dir():
        raise FileNotFoundError(f"本地重排模型不存在：{MODEL_PATH}")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    use_cuda = torch.cuda.is_available()
    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_PATH,
        local_files_only=True,
        dtype=torch.float16 if use_cuda else torch.float32,
        low_cpu_mem_usage=False,
    )
    model.eval()
    if use_cuda:
        model.to("cuda")
    return tokenizer, model


def rerank_laws(query: str, laws: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    if not laws:
        return []

    import torch

    tokenizer, model = load_reranker()
    pairs = [
        (
            query,
            "\n".join(
                str((law.get("payload") or {}).get(key) or "")
                for key in ("title", "text")
            ),
        )
        for law in laws
    ]
    scores: list[float] = []
    device = next(model.parameters()).device
    for start in range(0, len(pairs), 8):
        batch = tokenizer(
            pairs[start : start + 8],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with torch.inference_mode():
            logits = model(
                **{key: value.to(device) for key, value in batch.items()}
            ).logits.view(-1)
        scores.extend(logits.float().cpu().tolist())
    for law, score in zip(laws, scores, strict=True):
        law["rerank_score"] = score
    return sorted(laws, key=lambda law: law["rerank_score"], reverse=True)[:limit]


def rerank_memories(
    query: str,
    memories: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not memories:
        return []

    import torch

    tokenizer, model = load_reranker()
    pairs = [(query, item["memory"]["content"]) for item in memories]

    scores: list[float] = []
    device = next(model.parameters()).device
    for start in range(0, len(pairs), 8):
        batch = tokenizer(
            pairs[start : start + 8],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        with torch.inference_mode():
            logits = model(
                **{key: value.to(device) for key, value in batch.items()}
            ).logits.view(-1)
        scores.extend(logits.float().cpu().tolist())
    for item, score in zip(memories, scores, strict=True):
        item["rerank_score"] = score
    return sorted(
        memories,
        key=lambda item: item["rerank_score"],
        reverse=True,
    )[:limit]
