"""法规标题与正文的 BM25S 磁盘倒排索引。"""

import json
import logging
from pathlib import Path
from typing import Any

import bm25s
import jieba
import numpy as np

from ai_agent_learning.paths import get_data_dir

INDEX_DIR = "law-bm25"

# jieba 首次分词时默认向终端输出词典加载进度，会破坏 Rich 的动态显示区。
# 只保留警告和错误；法规索引本身的异常仍会正常抛出并进入应用日志。
jieba.setLogLevel(logging.WARNING)


def tokenize(text: str) -> list[str]:
    return [token.strip().lower() for token in jieba.lcut(text) if token.strip()]


class LawBm25Index:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_data_dir() / INDEX_DIR
        manifest_path = self.path / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"法规倒排索引不存在：{manifest_path}；请运行 agent100 laws install"
            )
        self.manifest: dict[str, Any] = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        self.ids: list[str] = json.loads(
            (self.path / "ids.json").read_text(encoding="utf-8")
        )
        if len(self.ids) != self.manifest.get("count"):
            raise ValueError("法规倒排索引 ID 数量与清单不一致")
        self.positions = {point_id: index for index, point_id in enumerate(self.ids)}
        self.title = bm25s.BM25.load(self.path / "title", mmap=True)
        self.content = bm25s.BM25.load(self.path / "content", mmap=True)

    def title_candidates(self, keywords: list[str]) -> list[str]:
        tokens = tokenize(" ".join(keywords))
        if not tokens:
            return []
        scores = self.title.get_scores(tokens)
        max_score = scores.max()
        if max_score <= 0:
            return []
        return [self.ids[int(position)] for position in np.flatnonzero(scores == max_score)]

    def rerank_content(
        self, keywords: list[str], candidates: list[str], limit: int = 10
    ) -> list[tuple[str, float]]:
        tokens = tokenize(" ".join(keywords))
        if not tokens or not candidates:
            return []
        mask = np.zeros(len(self.ids), dtype=np.float32)
        candidate_set = set(candidates)
        for point_id in candidate_set:
            position = self.positions.get(point_id)
            if position is not None:
                mask[position] = 1
        documents, scores = self.content.retrieve(
            [tokens],
            corpus=self.ids,
            k=min(len(candidate_set), max(limit, 1)),
            weight_mask=mask,
            show_progress=False,
        )
        return [
            (str(item), float(score))
            for item, score in zip(documents[0], scores[0])
            if str(item) in candidate_set and score > 0
        ][:limit]
