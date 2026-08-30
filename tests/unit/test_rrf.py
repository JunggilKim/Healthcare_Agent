from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.app.domain.trials import RawTrialRecord
from backend.app.retrieval.models import RegistryCandidate
from backend.app.retrieval.rrf import (
    cosine_ranks,
    exact_condition_match,
    min_max_scores,
    rrf_score,
)


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


def test_condition_phrase_match_accepts_a_qualified_registry_condition_only() -> None:
    raw_payload = json.loads(
        Path("data/demo/current/sessions/S001/raw_trials.json").read_text(encoding="utf-8")
    )[0]
    raw = RawTrialRecord.model_validate(raw_payload)
    candidate = RegistryCandidate(
        trial=raw.model_copy(update={"conditions": ["Pulmonary Mucormycosis"]}),
        registry_rank=1,
        retrieved_by_queries=["mucormycosis"],
    )

    assert exact_condition_match(candidate, ["mucormycosis"])
    assert not exact_condition_match(candidate, ["mucor"])
