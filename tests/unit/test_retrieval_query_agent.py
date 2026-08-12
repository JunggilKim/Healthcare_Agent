from __future__ import annotations

from datetime import UTC, datetime

from backend.app.agents.patient_evidence import materialize_patient_extraction
from backend.app.agents.retrieval_query import retrieval_query_semantic_payload
from backend.app.application.catalog import load_slot_catalog
from backend.app.domain.model_outputs import PatientExtractionResult


def test_semantic_payload_ignores_only_session_local_provenance() -> None:
    text = "68-year-old man"
    proposal = PatientExtractionResult.model_validate(
        {
            "facts": [
                {
                    "slot_id": "demographics.age",
                    "value": {"kind": "number", "value": "68", "unit": "year"},
                    "start": 0,
                    "end": 11,
                    "quote": "68-year-old",
                }
            ],
            "language": "en",
        }
    )
    first = materialize_patient_extraction(
        patient_text=text,
        source_id="session:first:input",
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        asserted_at=datetime(2026, 8, 11, tzinfo=UTC),
    ).state
    second = materialize_patient_extraction(
        patient_text=text,
        source_id="session:second:input",
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        asserted_at=datetime(2026, 8, 12, tzinfo=UTC),
    ).state

    assert first != second
    assert retrieval_query_semantic_payload(first) == retrieval_query_semantic_payload(second)
    payload = retrieval_query_semantic_payload(first)
    assert payload["confirmed_facts"][0]["fact_id"] == first.confirmed_facts[0].fact_id
    assert "asserted_at" not in payload["confirmed_facts"][0]
    assert "source_id" not in payload["confirmed_facts"][0]["source_spans"][0]
