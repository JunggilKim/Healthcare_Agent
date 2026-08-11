from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from backend.app.domain.base import StrictModel


class EvaluationAblationConfig(StrictModel):
    app_env: Literal["eval", "test", "local", "demo", "prod"]
    evidence_firewall: bool = True
    proof_verifier: bool = True
    evidence_grade_hard_gate: bool = True
    minimum_branch_utility: bool = True
    burden_penalty: bool = True
    branch_discrimination: bool = True
    slot_level_deduplication: bool = True
    policy: Literal["trial_opt", "max_coverage"] = "trial_opt"

    @model_validator(mode="after")
    def safety_removals_are_eval_only(self) -> EvaluationAblationConfig:
        removes_safety = not (
            self.evidence_firewall and self.proof_verifier and self.evidence_grade_hard_gate
        )
        if removes_safety and self.app_env != "eval":
            raise ValueError("SAFETY_ABLATIONS_REQUIRE_APP_ENV_EVAL")
        return self


ABLATION_OVERRIDES: dict[str, dict[str, bool | str]] = {
    "A1": {"evidence_firewall": False},
    "A2": {"proof_verifier": False},
    "A3": {"evidence_grade_hard_gate": False},
    "A4": {"minimum_branch_utility": False},
    "A5": {"burden_penalty": False},
    "A6": {"branch_discrimination": False},
    "A7": {"slot_level_deduplication": False},
    "A8": {"policy": "max_coverage"},
}


def ablation_config(ablation_id: str, *, app_env: str = "eval") -> EvaluationAblationConfig:
    if ablation_id not in ABLATION_OVERRIDES:
        raise ValueError(f"unknown ablation: {ablation_id}")
    return EvaluationAblationConfig.model_validate(
        {"app_env": app_env, **ABLATION_OVERRIDES[ablation_id]}
    )
