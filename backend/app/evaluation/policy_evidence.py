from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

import orjson
from pydantic import Field, model_validator

from backend.app.domain.base import StrictModel


class DirectLLMChoiceStep(StrictModel):
    step_index: int = Field(ge=0)
    candidate_slot_ids: list[str] = Field(min_length=1)
    selected_slot_id: str
    prompt_sha256: str
    response_sha256: str

    @model_validator(mode="after")
    def choice_is_bound_to_candidates(self) -> DirectLLMChoiceStep:
        if len(set(self.candidate_slot_ids)) != len(self.candidate_slot_ids):
            raise ValueError("B5_CANDIDATE_SLOT_DUPLICATE")
        if self.selected_slot_id not in self.candidate_slot_ids:
            raise ValueError("B5_SELECTED_SLOT_OUTSIDE_RECORDED_CANDIDATES")
        if len(self.prompt_sha256) != 64 or len(self.response_sha256) != 64:
            raise ValueError("B5_ARTIFACT_HASH_INVALID")
        return self


class DirectLLMObservationRun(StrictModel):
    observation_id: str
    steps: list[DirectLLMChoiceStep]

    @model_validator(mode="after")
    def steps_are_contiguous(self) -> DirectLLMObservationRun:
        if [item.step_index for item in self.steps] != list(range(len(self.steps))):
            raise ValueError("B5_STEP_INDEX_NOT_CONTIGUOUS")
        return self


class DirectLLMPolicyEvidence(StrictModel):
    schema_version: Literal["trial-opt-b5-policy-v1"] = "trial-opt-b5-policy-v1"
    status: Literal["BATCH_COMPLETED"]
    benchmark_sha256: str
    retrieval_evidence_sha256: str
    model_id: Literal["gemini-3.6-flash"]
    thinking_level: Literal["MEDIUM"] = "MEDIUM"
    prompt_version: str
    random_seed: int
    batch_job_name: str
    completed_at: datetime
    runs: list[DirectLLMObservationRun] = Field(min_length=1)

    @model_validator(mode="after")
    def provenance_is_complete(self) -> DirectLLMPolicyEvidence:
        if len(self.benchmark_sha256) != 64 or len(self.retrieval_evidence_sha256) != 64:
            raise ValueError("B5_PROVENANCE_HASH_INVALID")
        observation_ids = [item.observation_id for item in self.runs]
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("B5_OBSERVATION_DUPLICATE")
        if not self.batch_job_name.strip() or not self.prompt_version.strip():
            raise ValueError("B5_PROVENANCE_LABEL_MISSING")
        return self


def load_direct_llm_policy_evidence(path: str) -> DirectLLMPolicyEvidence:
    return DirectLLMPolicyEvidence.model_validate(orjson.loads(Path(path).read_bytes()))


def validate_direct_llm_policy_evidence(
    evidence: DirectLLMPolicyEvidence,
    *,
    benchmark_bytes: bytes,
    retrieval_evidence_bytes: bytes,
    seed: int,
) -> None:
    if hashlib.sha256(benchmark_bytes).hexdigest() != evidence.benchmark_sha256:
        raise ValueError("B5_BENCHMARK_HASH_MISMATCH")
    if hashlib.sha256(retrieval_evidence_bytes).hexdigest() != evidence.retrieval_evidence_sha256:
        raise ValueError("B5_RETRIEVAL_EVIDENCE_HASH_MISMATCH")
    if evidence.random_seed != seed:
        raise ValueError("B5_RANDOM_SEED_MISMATCH")
