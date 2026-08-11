from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel
from backend.app.domain.enums import CriterionVerdict
from backend.app.domain.evidence import SourceSpan
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
    evaluation_date: date
    compiled_protocol_hash: str
    criterion_source_hashes: list[str]
    facts: list[WorldFact]
    conflict_slots: list[str] = Field(default_factory=list)
    unavailable_slots: list[str] = Field(default_factory=list)
    template_narrative: str
    narrative: str
    narrative_language: Literal["en", "ko"] = "en"
    narrative_method: Literal["DETERMINISTIC_TEMPLATE", "FLASH_LITE_PARAPHRASE"] = (
        "DETERMINISTIC_TEMPLATE"
    )
    fact_span_map: dict[str, list[SourceSpan]]
    paraphrase_model_id: str | None = None
    paraphrase_prompt_version: str | None = None
    paraphrase_artifact_hash: str | None = None
    criterion_truth: list[CriterionTruth]

    @model_validator(mode="after")
    def narrative_provenance_is_complete(self) -> PatientWorld:
        fact_ids = {fact.fact_id for fact in self.facts}
        if set(self.fact_span_map) != fact_ids:
            raise ValueError("WORLD_FACT_SPAN_MAP_INCOMPLETE")
        for fact_id, spans in self.fact_span_map.items():
            if not spans:
                raise ValueError(f"WORLD_FACT_SPAN_EMPTY:{fact_id}")
            for span in spans:
                if span.end > len(self.narrative):
                    raise ValueError(f"WORLD_FACT_SPAN_OUT_OF_BOUNDS:{fact_id}")
                if self.narrative[span.start : span.end] != span.quote:
                    raise ValueError(f"WORLD_FACT_SPAN_QUOTE_MISMATCH:{fact_id}")
                if hashlib.sha256(span.quote.encode()).hexdigest() != span.sha256:
                    raise ValueError(f"WORLD_FACT_SPAN_HASH_MISMATCH:{fact_id}")
        lowered = self.narrative.casefold()
        if self.nct_id.casefold() in lowered or "ineligible" in lowered or "eligible" in lowered:
            raise ValueError("WORLD_NARRATIVE_TARGET_LEAKAGE")
        if self.narrative_method == "DETERMINISTIC_TEMPLATE":
            if self.narrative != self.template_narrative:
                raise ValueError("WORLD_TEMPLATE_NARRATIVE_CHANGED")
            if any(
                item is not None
                for item in (
                    self.paraphrase_model_id,
                    self.paraphrase_prompt_version,
                    self.paraphrase_artifact_hash,
                )
            ):
                raise ValueError("WORLD_TEMPLATE_HAS_PARAPHRASE_PROVENANCE")
        elif not all(
            (
                self.paraphrase_model_id,
                self.paraphrase_prompt_version,
                self.paraphrase_artifact_hash,
            )
        ):
            raise ValueError("WORLD_PARAPHRASE_PROVENANCE_MISSING")
        return self


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
    generation_coverage: dict[str, dict[str, int]] = Field(default_factory=dict)
