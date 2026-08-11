from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.agents.patient_evidence import (
    PatientExtractionValidationError,
    deterministic_surface_fallback,
    materialize_patient_extraction,
)
from backend.app.application.catalog import load_slot_catalog
from backend.app.domain.model_outputs import PatientExtractionResult


def test_backend_assigns_fact_contract_and_hypothesis_stays_firewalled() -> None:
    text = "68-year-old man has a bladder wall mass."
    proposal = PatientExtractionResult.model_validate(
        {
            "facts": [
                {
                    "slot_id": "demographics.age",
                    "value": {"kind": "number", "value": "68", "unit": "year"},
                    "start": 0,
                    "end": 11,
                    "quote": "68-year-old",
                },
                {
                    "slot_id": "imaging.bladder_wall_mass",
                    "value": {"kind": "boolean", "value": True},
                    "start": 22,
                    "end": 39,
                    "quote": "bladder wall mass",
                },
            ],
            "retrieval_hypotheses": [
                {
                    "concept": "bladder neoplasm",
                    "normalized_concept": "bladder cancer",
                    "source_proposal_indexes": [1],
                    "rationale_code": "IMAGING_MASS_RETRIEVAL_EXPANSION",
                }
            ],
            "language": "en",
        }
    )
    materialized = materialize_patient_extraction(
        patient_text=text,
        source_id="patient:test",
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        asserted_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert all(fact.grade.value == "A" for fact in materialized.state.confirmed_facts)
    hypothesis = materialized.state.retrieval_hypotheses[0]
    assert hypothesis.grade.value == "H"
    assert hypothesis.admissible_for_eligibility is False
    assert hypothesis.source_fact_ids == [materialized.state.confirmed_facts[1].fact_id]


def test_mismatched_source_span_and_model_supplied_extra_field_are_rejected() -> None:
    base = {
        "facts": [
            {
                "slot_id": "demographics.age",
                "value": {"kind": "number", "value": "68", "unit": "year"},
                "start": 0,
                "end": 2,
                "quote": "69",
            }
        ],
        "language": "en",
    }
    proposal = PatientExtractionResult.model_validate(base)
    with pytest.raises(PatientExtractionValidationError, match="source span"):
        materialize_patient_extraction(
            patient_text="68-year-old",
            source_id="patient:test",
            proposal=proposal,
            slot_catalog=load_slot_catalog(),
            asserted_at=datetime(2026, 8, 11, tzinfo=UTC),
        )
    base["facts"][0]["grade"] = "A"
    with pytest.raises(ValueError, match="extra"):
        PatientExtractionResult.model_validate(base)


def test_surface_fallback_extracts_demographics_without_diagnosis() -> None:
    proposal = deterministic_surface_fallback(
        "A 68-year-old man has an imaging finding suggestive of cancer.", language="en"
    )
    assert [fact.slot_id for fact in proposal.facts] == [
        "demographics.age",
        "demographics.sex",
    ]
    assert proposal.retrieval_hypotheses == []
