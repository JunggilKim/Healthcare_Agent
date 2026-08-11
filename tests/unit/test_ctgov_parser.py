from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import orjson

from backend.app.retrieval.ctgov_parser import (
    is_interactive_candidate,
    parse_study,
    validate_study_page,
)


def test_interactive_population_rejects_wrong_type_and_non_enrolling_status() -> None:
    content = Path("data/fixtures/retrieval/S004/search_response.json").read_bytes()
    study = validate_study_page(content)[0]
    trial = parse_study(
        study,
        api_version="2.0.5",
        retrieved_at=datetime(2026, 8, 11, tzinfo=UTC),
        raw_bytes=orjson.dumps(study),
    )
    assert is_interactive_candidate(trial) is True
    assert (
        is_interactive_candidate(trial.model_copy(update={"study_type": "OBSERVATIONAL"})) is False
    )
    assert (
        is_interactive_candidate(
            trial.model_copy(update={"overall_status": "ACTIVE_NOT_RECRUITING"})
        )
        is False
    )
