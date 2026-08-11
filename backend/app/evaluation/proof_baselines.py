from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import CriterionVerdict
from backend.app.evaluation.annotations import (
    AdjudicatedAnnotation,
    AnnotationAssignment,
    AnnotationVerdict,
)
from backend.app.evaluation.metrics import classification_metrics

_LABELS = [item.value for item in CriterionVerdict]


class ProofBaselinePrediction(StrictModel):
    record_id: str
    assignment_hash: str
    p0_verdict: AnnotationVerdict
    p0_explanation: str = Field(min_length=1)
    p1_verdict: AnnotationVerdict
    p1_evidence_fact_ids: list[str]
    p1_explanation: str = Field(min_length=1)
    prompt_sha256: str
    response_sha256: str

    @model_validator(mode="after")
    def hashes_are_valid(self) -> ProofBaselinePrediction:
        if len(self.prompt_sha256) != 64 or len(self.response_sha256) != 64:
            raise ValueError("PROOF_BASELINE_ARTIFACT_HASH_INVALID")
        return self


class ProofBaselineEvidence(StrictModel):
    schema_version: Literal["trial-opt-proof-baselines-v1"] = "trial-opt-proof-baselines-v1"
    status: Literal["BATCH_COMPLETED"]
    annotation_manifest_sha256: str
    assignment_jsonl_sha256: str
    model_id: Literal["gemini-3.6-flash"]
    thinking_level: Literal["MEDIUM"] = "MEDIUM"
    prompt_version: str
    batch_job_name: str
    completed_at: datetime
    predictions: list[ProofBaselinePrediction] = Field(min_length=1)

    @model_validator(mode="after")
    def evidence_is_unique_and_bound(self) -> ProofBaselineEvidence:
        if len(self.annotation_manifest_sha256) != 64 or len(self.assignment_jsonl_sha256) != 64:
            raise ValueError("PROOF_BASELINE_PROVENANCE_HASH_INVALID")
        record_ids = [item.record_id for item in self.predictions]
        if len(set(record_ids)) != len(record_ids):
            raise ValueError("PROOF_BASELINE_RECORD_DUPLICATE")
        if not self.prompt_version.strip() or not self.batch_job_name.strip():
            raise ValueError("PROOF_BASELINE_PROVENANCE_LABEL_MISSING")
        return self


def validate_proof_baseline_evidence(
    evidence: ProofBaselineEvidence,
    *,
    annotation_manifest_bytes: bytes,
    assignment_jsonl_bytes: bytes,
    assignments: list[AnnotationAssignment],
) -> None:
    if hashlib.sha256(annotation_manifest_bytes).hexdigest() != evidence.annotation_manifest_sha256:
        raise ValueError("PROOF_BASELINE_ANNOTATION_MANIFEST_HASH_MISMATCH")
    if hashlib.sha256(assignment_jsonl_bytes).hexdigest() != evidence.assignment_jsonl_sha256:
        raise ValueError("PROOF_BASELINE_ASSIGNMENT_HASH_MISMATCH")
    assignment_by_id = {item.record_id: item for item in assignments}
    prediction_by_id = {item.record_id: item for item in evidence.predictions}
    if set(prediction_by_id) != set(assignment_by_id):
        raise ValueError("PROOF_BASELINE_RECORD_COVERAGE_MISMATCH")
    for record_id, prediction in prediction_by_id.items():
        assignment = assignment_by_id[record_id]
        if prediction.assignment_hash != assignment.assignment_hash:
            raise ValueError(f"PROOF_BASELINE_ASSIGNMENT_BINDING_MISMATCH:{record_id}")
        fact_ids = {fact.fact_id for fact in assignment.facts}
        if not set(prediction.p1_evidence_fact_ids).issubset(fact_ids):
            raise ValueError(f"PROOF_BASELINE_EVIDENCE_FACT_UNKNOWN:{record_id}")


def evaluate_proof_baselines(
    evidence: ProofBaselineEvidence,
    *,
    assignments: list[AnnotationAssignment],
    gold: list[AdjudicatedAnnotation],
    p2_p3_criterion_metrics: dict[str, Any],
    p3_unsupported_hard_decision_rate: float,
    p3_proof_replay_success_rate: float,
) -> dict[str, Any]:
    assignment_by_id = {item.record_id: item for item in assignments}
    prediction_by_id = {item.record_id: item for item in evidence.predictions}
    gold_by_id = {item.record_id: item for item in gold}
    eligible_ids = [
        record_id
        for record_id, annotation in gold_by_id.items()
        if annotation.safely_executable
        and annotation.verdict.value in _LABELS
        and record_id in assignment_by_id
    ]
    truth = [gold_by_id[record_id].verdict.value for record_id in eligible_ids]
    p0_predictions = [prediction_by_id[record_id].p0_verdict.value for record_id in eligible_ids]
    p1_predictions = [prediction_by_id[record_id].p1_verdict.value for record_id in eligible_ids]
    p1_correct = 0
    p1_total = 0
    for record_id in eligible_ids:
        expected = set(gold_by_id[record_id].evidence_fact_ids)
        predicted = set(prediction_by_id[record_id].p1_evidence_fact_ids)
        p1_correct += len(expected & predicted)
        p1_total += len(predicted)
    p0_metrics = classification_metrics(truth, p0_predictions, _LABELS)
    p1_metrics = classification_metrics(truth, p1_predictions, _LABELS)
    return {
        "P0": {
            "status": "BATCH_COMPLETED",
            "system": "LLM verdict and free-form explanation",
            "macro_f1": p0_metrics["macro_f1"],
            "predictions": len(eligible_ids),
        },
        "P1": {
            "status": "BATCH_COMPLETED",
            "system": "structured verdict plus evidence spans; no deterministic replay",
            "macro_f1": p1_metrics["macro_f1"],
            "evidence_precision": p1_correct / p1_total if p1_total else 0.0,
            "predictions": len(eligible_ids),
        },
        "P2": {
            "status": "COMPLETED",
            "system": "deterministic AST evaluator without Evidence Grades/Firewall",
            "macro_f1": p2_p3_criterion_metrics["macro_f1"],
            "firewall_enabled": False,
            "evidence_grade_gate_enabled": False,
        },
        "P3": {
            "status": "COMPLETED",
            "system": "full ProofTrial",
            "macro_f1": p2_p3_criterion_metrics["macro_f1"],
            "unsupported_hard_decision_rate": p3_unsupported_hard_decision_rate,
            "proof_replay_success_rate": p3_proof_replay_success_rate,
        },
    }
