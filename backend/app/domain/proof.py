from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import CriterionVerdict
from backend.app.domain.values import JsonValue


class DerivationStep(StrictModel):
    step_id: str
    operation: str
    input_fact_ids: list[str]
    input_step_ids: list[str]
    parameters: dict[str, JsonValue]
    output: JsonValue
    code_version: str


class VerifierCheck(StrictModel):
    check_id: Literal[
        "PV-001",
        "PV-002",
        "PV-003",
        "PV-004",
        "PV-005",
        "PV-006",
        "PV-007",
        "PV-008",
        "PV-009",
        "PV-010",
        "PV-011",
        "PV-012",
        "PV-013",
        "PV-014",
        "PV-015",
    ]
    applicable: bool
    passed: bool
    blocking: bool
    detail_code: str
    artifact_hashes: list[str] = Field(default_factory=list)


class ProofPacket(StrictModel):
    proof_id: str
    proof_revision: int = Field(ge=0)
    verification_phase: Literal["DECISION", "POST_RENDER"]
    supersedes_proof_id: str | None = None
    session_id: str
    patient_state_version: int = Field(ge=0)
    nct_id: str
    criterion_id: str
    criterion_source_hash: str
    compiled_protocol_hash: str
    registry_api_version: str
    registry_data_version: str | None
    registry_retrieved_at: datetime
    evaluated_at: datetime
    evaluation_date: date
    provisional_verdict: CriterionVerdict
    final_verdict: CriterionVerdict
    evidence_fact_ids: list[str]
    missing_slot_ids: list[str]
    conflict_ids: list[str]
    derivation_steps: list[DerivationStep]
    verifier_checks: list[VerifierCheck]
    hard_decision_allowed: bool
    blocking_issue_codes: list[str]
    canonical_replay_hash: str
