from __future__ import annotations

from pathlib import Path

import orjson
import pytest
from pydantic import ValidationError

from backend.app.domain.evidence import PatientState
from backend.app.retrieval.models import RetrievalQuery
from backend.app.retrieval.query_builder import build_deterministic_query


def test_s004_fallback_query_preserves_hypothesis_provenance() -> None:
    payload = orjson.loads(
        Path("data/fixtures/vertical_slice/S004.initial_evidence.json").read_bytes()
    )
    state = PatientState.model_validate(
        {
            "confirmed_facts": payload["facts"],
            "retrieval_hypotheses": payload["retrieval_hypotheses"],
            "conflicts": payload["conflicts"],
        }
    )
    query = build_deterministic_query(state.confirmed_facts, state.retrieval_hypotheses)
    assert [item.text for item in query.condition_queries] == ["bladder cancer"]
    assert query.condition_queries[0].source_fact_ids == []
    assert query.condition_queries[0].source_hypothesis_ids == [
        "hyp_00000000-0000-4000-8000-000000000001"
    ]
    assert query.must_not_use_as_eligibility_evidence is True
    assert len(query.dense_query) <= 800


@pytest.mark.parametrize(
    "payload",
    [
        {"condition_queries": [], "dense_query": "clinical trial"},
        {"condition_queries": [{"text": "  ", "priority": 1}], "dense_query": "trial"},
        {"condition_queries": [{"text": "cancer", "priority": 1}], "dense_query": "  "},
    ],
)
def test_retrieval_query_rejects_empty_search_inputs(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RetrievalQuery.model_validate(payload)
