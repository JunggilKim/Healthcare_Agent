from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.app.domain.evidence import EligibilityContext
from backend.app.domain.proof import ProofPacket
from backend.app.domain.trials import CompiledTrial
from backend.app.engine.proof_verifier import replay_packet_matches


def replay_current_proofs(
    *,
    nct_id: str,
    patient_state_version: int,
    facts: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    compiled_trial: CompiledTrial,
    proof_packets: list[dict[str, Any]],
) -> dict[str, object]:
    """Re-execute decision proofs against the current stored session state."""

    context = EligibilityContext.model_validate({"facts": facts, "conflicts": conflicts})
    criteria = {criterion.criterion_id: criterion for criterion in compiled_trial.criteria}
    packets = [
        ProofPacket.model_validate(item)
        for item in proof_packets
        if item.get("nct_id") == nct_id
    ]
    results: list[dict[str, object]] = []
    for packet in packets:
        criterion = criteria.get(packet.criterion_id)
        passed = criterion is not None and replay_packet_matches(packet, criterion, context)
        results.append(
            {
                "proof_id": packet.proof_id,
                "criterion_id": packet.criterion_id,
                "patient_state_version": packet.patient_state_version,
                "passed": passed,
            }
        )

    return {
        "nct_id": nct_id,
        "patient_state_version": patient_state_version,
        "proof_packets": [packet.model_dump(mode="json") for packet in packets],
        "replay_executed": True,
        "replay_method": "DETERMINISTIC_EVALUATOR_CURRENT_SESSION",
        "replayed_at": datetime.now(UTC).isoformat(),
        "replay_passed": bool(results) and all(bool(item["passed"]) for item in results),
        "replay_results": results,
    }
