from __future__ import annotations

from copy import deepcopy

import orjson

from backend.app.application.proof_replay import replay_current_proofs
from backend.app.domain.trials import CompiledTrial
from backend.app.settings import REPOSITORY_ROOT


def _fixture() -> tuple[dict[str, object], CompiledTrial]:
    root = REPOSITORY_ROOT / "data/demo/current/sessions/S004"
    branch = orjson.loads(
        (root / "branches/q_4b4118fc-32ad-499a-9823-eb3ec6f0ee5a:0.json").read_bytes()
    )
    compiled = orjson.loads((root / "compiled_trials.json").read_bytes())
    trial = next(item for item in compiled if item["nct_id"] == "NCT05239624")
    return branch, CompiledTrial.model_validate(trial)


def test_current_branch_proofs_are_reexecuted_against_current_facts() -> None:
    branch, compiled_trial = _fixture()
    result = replay_current_proofs(
        nct_id="NCT05239624",
        patient_state_version=int(branch["patient_state_version"]),
        facts=list(branch["facts"]),
        conflicts=list(branch["conflicts"]),
        compiled_trial=compiled_trial,
        proof_packets=list(branch["proofs"]),
    )

    assert result["replay_passed"] is True
    assert len(result["replay_results"]) == 7


def test_current_branch_replay_rejects_a_tampered_hash() -> None:
    branch, compiled_trial = _fixture()
    packets = deepcopy(branch["proofs"])
    packets[1]["canonical_replay_hash"] = "0" * 64
    result = replay_current_proofs(
        nct_id="NCT05239624",
        patient_state_version=int(branch["patient_state_version"]),
        facts=list(branch["facts"]),
        conflicts=list(branch["conflicts"]),
        compiled_trial=compiled_trial,
        proof_packets=packets,
    )

    assert result["replay_passed"] is False
    assert result["replay_results"][1]["passed"] is False
