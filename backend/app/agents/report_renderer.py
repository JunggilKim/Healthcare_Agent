from __future__ import annotations

from backend.app.domain.enums import CriterionVerdict
from backend.app.domain.proof import ProofPacket
from backend.app.domain.ranking import TrialEvaluation
from backend.app.domain.rendering import (
    CriterionExplanation,
    TrialReportProposal,
    ValidatedReport,
)

_STATUS_KO = {
    "PRE_SCREEN_PASS": "현재 제공된 정보로 사전 선별 기준을 통과했습니다.",
    "POTENTIAL_MATCH": "일부 기준 정보가 남아 있어 잠재적 일치로 표시됩니다.",
    "REVIEW_REQUIRED": "불투명하거나 충돌하는 기준이 있어 전문가 검토가 필요합니다.",
    "INELIGIBLE": "확인된 근거에서 충족하지 못한 기준이 있습니다.",
    "IRRELEVANT": "현재 검색 맥락과 관련성이 낮습니다.",
}


def deterministic_criterion_explanation(packet: ProofPacket) -> CriterionExplanation:
    if packet.final_verdict is CriterionVerdict.UNKNOWN:
        reason = "현재 허용 가능한 환자 근거가 없어 판정할 수 없습니다."
    elif packet.final_verdict is CriterionVerdict.CONFLICT:
        reason = "서로 충돌하는 근거가 있어 한 판정으로 확정할 수 없습니다."
    elif packet.final_verdict is CriterionVerdict.PASS:
        reason = "검증된 환자 근거가 이 기준을 충족합니다."
    elif packet.final_verdict is CriterionVerdict.FAIL:
        reason = "검증된 환자 근거가 이 기준을 충족하지 못합니다."
    else:
        reason = "이 기준은 현재 상황에 적용되지 않습니다."
    return CriterionExplanation(
        criterion_id=packet.criterion_id,
        verdict=packet.final_verdict,
        summary_ko=reason,
        evidence_refs=packet.evidence_fact_ids,
    )


def validate_or_fallback_report(
    *,
    evaluation: TrialEvaluation,
    decision_proofs: list[ProofPacket],
    proposal: TrialReportProposal | None,
) -> ValidatedReport:
    criterion_ids = {proof.criterion_id for proof in decision_proofs}
    evidence_ids = {
        evidence_id for proof in decision_proofs for evidence_id in proof.evidence_fact_ids
    }
    rejection_code: str | None = None
    if proposal is not None:
        if proposal.trial_id != evaluation.nct_id or proposal.status is not evaluation.decision:
            rejection_code = "REPORT_STATUS_OR_TRIAL_MISMATCH"
        elif not set(proposal.criterion_refs).issubset(criterion_ids):
            rejection_code = "REPORT_CRITERION_REFERENCE_MISMATCH"
        elif not set(proposal.evidence_refs).issubset(evidence_ids):
            rejection_code = "REPORT_EVIDENCE_REFERENCE_MISMATCH"
        else:
            return ValidatedReport(report=proposal, source="MODEL_VALIDATED")

    fallback = TrialReportProposal(
        trial_id=evaluation.nct_id,
        status=evaluation.decision,
        summary_ko=(
            f"{_STATUS_KO[evaluation.decision.value]} 이는 최종 적격 판정이 아니며, "
            "임상시험 연구팀의 확인이 필요합니다."
        ),
        criterion_refs=sorted(criterion_ids),
        evidence_refs=sorted(evidence_ids),
    )
    return ValidatedReport(
        report=fallback,
        source="DETERMINISTIC_TEMPLATE",
        rejection_code=rejection_code,
    )
