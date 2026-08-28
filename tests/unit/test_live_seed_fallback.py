from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from backend.app.agents.patient_evidence import PatientEvidenceAgent
from backend.app.application.catalog import load_slot_catalog
from backend.app.application.live_session_service import _pinned_seed_proposal, _seed_text
from backend.app.domain.model_outputs import PatientExtractionResult
from backend.app.infrastructure.structured_generation import StructuredGenerationUnavailable


class _UnavailableGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_primary_with_lite_fallback(self, **_kwargs: object) -> None:
        self.calls += 1
        try:
            PatientExtractionResult.model_validate_json('{"facts":[')
        except ValidationError as error:
            raise StructuredGenerationUnavailable("recorded model failure") from error
        raise AssertionError("invalid JSON must fail validation")


class _InvalidDomainGenerator:
    async def generate_primary_with_lite_fallback(
        self, **_kwargs: object
    ) -> tuple[PatientExtractionResult, None]:
        return (
            PatientExtractionResult.model_validate(
                {
                    "retrieval_hypotheses": [
                        {
                            "concept": "bladder cancer",
                            "normalized_concept": "bladder cancer",
                            "source_proposal_indexes": [],
                            "rationale_code": "UNSUPPORTED_UNGROUNDED_HYPOTHESIS",
                        }
                    ],
                    "language": "en",
                }
            ),
            None,
        )


class _TransientUnavailableGenerator:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_primary_with_lite_fallback(self, **_kwargs: object) -> None:
        self.calls += 1
        try:
            raise RuntimeError("recorded 503")
        except RuntimeError as error:
            raise StructuredGenerationUnavailable("temporary outage") from error


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
    generator = _UnavailableGenerator()
    agent = PatientEvidenceAgent(generator, load_slot_catalog())  # type: ignore[arg-type]

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

    repeated, repeated_degraded = await agent.extract(
        patient_text=text,
        source_id="seed:S004",
        language_hint="en",
        evaluation_date=date(2026, 8, 11),
        asserted_at=datetime(2026, 8, 13, tzinfo=UTC),
        pinned_fallback=pinned,
    )
    assert repeated_degraded is True
    assert len(repeated.state.confirmed_facts) == 5
    assert generator.calls == 1


@pytest.mark.asyncio
async def test_preferred_seed_extraction_avoids_unnecessary_live_model_calls() -> None:
    text = _seed_text("S004")
    pinned = _pinned_seed_proposal("S004", text)
    assert pinned is not None
    generator = _UnavailableGenerator()
    agent = PatientEvidenceAgent(generator, load_slot_catalog())  # type: ignore[arg-type]

    materialized, degraded = await agent.extract(
        patient_text=text,
        source_id="seed:S004",
        language_hint="en",
        evaluation_date=date(2026, 8, 11),
        asserted_at=datetime(2026, 8, 13, tzinfo=UTC),
        pinned_fallback=pinned,
        prefer_pinned_fallback=True,
        primary_max_attempts=1,
        generation_attempt_timeout_seconds=10,
    )

    assert degraded is False
    assert len(materialized.state.confirmed_facts) == 5
    assert generator.calls == 0


@pytest.mark.asyncio
async def test_domain_invalid_model_output_uses_conservative_fallback() -> None:
    agent = PatientEvidenceAgent(_InvalidDomainGenerator(), load_slot_catalog())  # type: ignore[arg-type]

    materialized, degraded = await agent.extract(
        patient_text="68-year-old man.",
        source_id="synthetic:test",
        language_hint="en",
        evaluation_date=date(2026, 8, 11),
        asserted_at=datetime(2026, 8, 13, tzinfo=UTC),
    )

    assert degraded is True
    assert [fact.slot_id for fact in materialized.state.confirmed_facts] == [
        "demographics.age",
        "demographics.sex",
    ]
    assert materialized.state.retrieval_hypotheses == []


@pytest.mark.asyncio
async def test_transient_seed_failure_is_not_memoized() -> None:
    text = _seed_text("S004")
    pinned = _pinned_seed_proposal("S004", text)
    assert pinned is not None
    generator = _TransientUnavailableGenerator()
    agent = PatientEvidenceAgent(generator, load_slot_catalog())  # type: ignore[arg-type]

    for _ in range(2):
        _, degraded = await agent.extract(
            patient_text=text,
            source_id="seed:S004",
            language_hint="en",
            evaluation_date=date(2026, 8, 11),
            asserted_at=datetime(2026, 8, 13, tzinfo=UTC),
            pinned_fallback=pinned,
        )
        assert degraded is True

    assert generator.calls == 2
