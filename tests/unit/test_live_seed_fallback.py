from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.app.agents.patient_evidence import PatientEvidenceAgent
from backend.app.application.catalog import load_slot_catalog
from backend.app.application.live_session_service import _pinned_seed_proposal, _seed_text
from backend.app.infrastructure.structured_generation import StructuredGenerationUnavailable


class _UnavailableGenerator:
    async def generate_primary_with_lite_fallback(self, **_kwargs: object) -> None:
        raise StructuredGenerationUnavailable("recorded model failure")


def test_release_seed_has_exact_pinned_extraction_fallback() -> None:
    proposal = _pinned_seed_proposal("S004", _seed_text("S004"))

    assert proposal is not None
    assert [fact.slot_id for fact in proposal.facts] == [
        "demographics.age",
        "demographics.sex",
        "smoking.history",
        "symptom.gross_hematuria",
        "imaging.bladder_wall_mass",
    ]
    assert proposal.retrieval_hypotheses[0].normalized_concept == "bladder cancer"
    assert _pinned_seed_proposal("S004", "different text") is None
    assert _pinned_seed_proposal(None, _seed_text("S004")) is None


@pytest.mark.asyncio
async def test_seed_model_failure_uses_full_pinned_extraction() -> None:
    text = _seed_text("S004")
    pinned = _pinned_seed_proposal("S004", text)
    assert pinned is not None
    agent = PatientEvidenceAgent(_UnavailableGenerator(), load_slot_catalog())  # type: ignore[arg-type]

    materialized, degraded = await agent.extract(
        patient_text=text,
        source_id="seed:S004",
        language_hint="en",
        evaluation_date=date(2026, 8, 11),
        asserted_at=datetime(2026, 8, 13, tzinfo=UTC),
        pinned_fallback=pinned,
    )

    assert degraded is True
    assert len(materialized.state.confirmed_facts) == 5
    assert materialized.state.retrieval_hypotheses[0].normalized_concept == "bladder cancer"
