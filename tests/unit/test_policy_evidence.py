from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from backend.app.evaluation.policy_evidence import (
    DirectLLMChoiceStep,
    DirectLLMObservationRun,
    DirectLLMPolicyEvidence,
    validate_direct_llm_policy_evidence,
)


def _evidence() -> DirectLLMPolicyEvidence:
    return DirectLLMPolicyEvidence(
        status="BATCH_COMPLETED",
        benchmark_sha256=hashlib.sha256(b"benchmark").hexdigest(),
        retrieval_evidence_sha256=hashlib.sha256(b"retrieval").hexdigest(),
        model_id="gemini-3.6-flash",
        prompt_version="direct-question-policy-v1",
        random_seed=20260811,
        batch_job_name="projects/test/locations/global/batchPredictionJobs/1",
        completed_at=datetime(2026, 8, 12, tzinfo=UTC),
        runs=[
            DirectLLMObservationRun(
                observation_id="obs-1",
                steps=[
                    DirectLLMChoiceStep(
                        step_index=0,
                        candidate_slot_ids=["pathology.histology"],
                        selected_slot_id="pathology.histology",
                        prompt_sha256="c" * 64,
                        response_sha256="d" * 64,
                    )
                ],
            )
        ],
    )


def test_direct_llm_evidence_is_bound_to_benchmark_retrieval_and_seed() -> None:
    validate_direct_llm_policy_evidence(
        _evidence(),
        benchmark_bytes=b"benchmark",
        retrieval_evidence_bytes=b"retrieval",
        seed=20260811,
    )


def test_direct_llm_evidence_rejects_non_candidate_choice() -> None:
    with pytest.raises(ValueError, match="B5_SELECTED_SLOT_OUTSIDE_RECORDED_CANDIDATES"):
        DirectLLMChoiceStep(
            step_index=0,
            candidate_slot_ids=["pathology.histology"],
            selected_slot_id="staging.clinical_group",
            prompt_sha256="c" * 64,
            response_sha256="d" * 64,
        )
