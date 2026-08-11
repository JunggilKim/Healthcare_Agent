from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import CriterionVerdict
from backend.app.domain.values import TypedValue


class WorldFact(StrictModel):
    fact_id: str
    slot_id: str
    value: TypedValue
    grade: Literal["A"] = "A"


class CriterionTruth(StrictModel):
    criterion_id: str
    verdict: CriterionVerdict
    evidence_fact_ids: list[str]
    missing_slot_ids: list[str]


class PatientWorld(StrictModel):
    world_id: str
    nct_id: str
    world_type: Literal[
        "FULL_PASS",
        "SINGLE_FAIL",
        "MULTI_FAIL",
        "UNKNOWN",
        "CONFLICT",
        "BOUNDARY",
    ]
    split: Literal["development", "validation", "test"]
    compiled_protocol_hash: str
    criterion_source_hashes: list[str]
    facts: list[WorldFact]
    conflict_slots: list[str] = Field(default_factory=list)
    unavailable_slots: list[str] = Field(default_factory=list)
    narrative: str
    narrative_language: Literal["en", "ko"] = "en"
    narrative_method: Literal["DETERMINISTIC_TEMPLATE"] = "DETERMINISTIC_TEMPLATE"
    criterion_truth: list[CriterionTruth]


class OracleAnswer(StrictModel):
    slot_id: str
    value: TypedValue | None
    unknown: bool
    answer_sentence: str


class MissingnessObservation(StrictModel):
    observation_id: str
    world_id: str
    nct_id: str
    split: Literal["development", "validation", "test"]
    rate: float = Field(gt=0, lt=1)
    pattern: Literal["MCAR", "REALISTIC"]
    visible_fact_ids: list[str]
    hidden_slots: list[str]
    oracle: list[OracleAnswer]


class BenchmarkArtifact(StrictModel):
    schema_version: Literal["trial-opt-benchmark-v1"] = "trial-opt-benchmark-v1"
    seed: int
    scope_status: Literal["PROVISIONAL_FIXTURE_SMOKE", "RELEASE_DATASET_A"]
    acceptance_eligible: bool
    blocking_reasons: list[str]
    source_trials: list[str]
    worlds: list[PatientWorld]
    observations: list[MissingnessObservation]
    counts: dict[str, int]
