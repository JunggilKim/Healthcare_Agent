from __future__ import annotations

import copy
from datetime import date
from enum import Enum
from typing import Literal

from backend.app.domain.base import StrictModel
from backend.app.domain.evidence import FactConflict, PatientFact, RetrievalHypothesis
from backend.app.domain.questions import OptimizerRuntimeConfig
from backend.app.domain.ranking import TrialEvaluation
from backend.app.domain.trials import CompiledTrial


class SessionState(str, Enum):
    CREATED = "CREATED"
    INPUT_VALIDATING = "INPUT_VALIDATING"
    PATIENT_EXTRACTING = "PATIENT_EXTRACTING"
    RETRIEVING = "RETRIEVING"
    CANDIDATES_READY = "CANDIDATES_READY"
    COMPILING = "COMPILING"
    EVALUATING = "EVALUATING"
    VERIFYING = "VERIFYING"
    RANKING = "RANKING"
    QUESTION_SELECTING = "QUESTION_SELECTING"
    QUESTION_READY = "QUESTION_READY"
    ANSWER_INTERPRETING = "ANSWER_INTERPRETING"
    REEVALUATING = "REEVALUATING"
    COMPLETE = "COMPLETE"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    RESET = "RESET"


class SessionAggregate(StrictModel):
    session_id: str
    mode: Literal["snapshot", "live", "hybrid_degraded"]
    evaluation_date: date
    patient_state_version: int
    question_count: int
    facts: list[PatientFact]
    retrieval_hypotheses: list[RetrievalHypothesis]
    conflicts: list[FactConflict]
    compiled_trials: dict[str, CompiledTrial]
    trial_evaluations: dict[str, TrialEvaluation]
    ranked_nct_ids: list[str]
    asked_slot_ids: list[str]
    unavailable_slot_ids: list[str]
    current_question_id: str | None
    config: OptimizerRuntimeConfig

    def deep_copy_for_simulation(self) -> SessionAggregate:
        return copy.deepcopy(self)
