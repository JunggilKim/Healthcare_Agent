from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast

from backend.app.agents.patient_evidence import (
    MaterializedPatientExtraction,
    deterministic_surface_fallback,
    materialize_patient_extraction,
)
from backend.app.application.catalog import load_slot_catalog
from backend.app.settings import REPOSITORY_ROOT


def _seed(case_id: str) -> str:
    payload = json.loads(
        (REPOSITORY_ROOT / "data/seeds/synthetic-patients.json").read_text(encoding="utf-8")
    )
    return cast(
        str,
        next(item["title"] for item in payload["topics"] if item["num"] == case_id),
    )


def _materialize(case_id: str) -> MaterializedPatientExtraction:
    text = _seed(case_id)
    proposal = deterministic_surface_fallback(text, language="en")
    return materialize_patient_extraction(
        patient_text=text,
        source_id=f"seed:{case_id}",
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        asserted_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
    )


def test_s008_keeps_ild_as_hypothesis_and_extracts_direct_surface_evidence() -> None:
    extracted = _materialize("S008")
    slots = {fact.slot_id for fact in extracted.state.confirmed_facts}
    assert {
        "demographics.age",
        "demographics.sex",
        "symptom.dyspnea",
        "symptom.dry_cough",
        "imaging.honeycombing",
    } <= slots
    assert "diagnosis.interstitial_lung_disease.confirmed" not in slots
    assert [item.normalized_concept for item in extracted.state.retrieval_hypotheses] == [
        "interstitial lung disease"
    ]
    assert all(not item.admissible_for_eligibility for item in extracted.state.retrieval_hypotheses)


def test_s001_does_not_equate_alcohol_history_with_proven_etiology_or_diagnosis() -> None:
    extracted = _materialize("S001")
    slots = {fact.slot_id for fact in extracted.state.confirmed_facts}
    assert {
        "demographics.age",
        "demographics.sex",
        "alcohol.chronic_use",
        "symptom.epigastric_pain",
        "lab.lipase_interpretation",
        "lab.amylase_interpretation",
    } <= slots
    assert "diagnosis.pancreatitis_etiology" not in slots
    assert "diagnosis.acute_pancreatitis.confirmed" not in slots
    assert "condition.current_organ_failure" not in slots
    assert [item.normalized_concept for item in extracted.state.retrieval_hypotheses] == [
        "acute pancreatitis"
    ]
