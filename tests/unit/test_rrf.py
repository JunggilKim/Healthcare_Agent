from __future__ import annotations

import numpy as np

from backend.app.retrieval.rrf import cosine_ranks, min_max_scores, rrf_score


def test_rrf_formula_bonus_and_normalization() -> None:
    assert rrf_score(1, 2, exact_match=True) == 1 / 61 + 1 / 62 + 0.05
    assert min_max_scores({"a": 2.0, "b": 2.0}) == {"a": 0.5, "b": 0.5}
    assert min_max_scores({"a": 1.0, "b": 3.0}) == {"a": 0.0, "b": 1.0}


def test_cosine_rank_tie_breaks_by_nct_id() -> None:
    ranks = cosine_ranks(
        np.array([1.0, 0.0]),
        {
            "NCT00000002": np.array([1.0, 0.0]),
            "NCT00000001": np.array([1.0, 0.0]),
            "NCT00000003": np.array([0.0, 1.0]),
        },
    )
    assert ranks == {"NCT00000001": 1, "NCT00000002": 2, "NCT00000003": 3}
