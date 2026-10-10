"""标题检索不应截断同分的法条。"""

from types import SimpleNamespace

import numpy as np

from chinese_legal_agent.law_index import LawBm25Index


def test_title_candidates_include_all_top_score_ties() -> None:
    index = object.__new__(LawBm25Index)
    index.ids = ["first", "second", "lower", "last-tie"]
    index.title = SimpleNamespace(
        get_scores=lambda tokens: np.array([3.0, 3.0, 2.0, 3.0])
    )

    assert index.title_candidates(["民法典"]) == ["first", "second", "last-tie"]
    assert index.title_candidates([]) == []
