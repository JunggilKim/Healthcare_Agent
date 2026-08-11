from __future__ import annotations

from dataclasses import dataclass

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.questions import QuestionCandidate
from backend.app.domain.rendering import QuestionRenderProposal

_PROHIBITED = (
    "새 검사를",
    "검사를 받으",
    "치료를 변경",
    "약을 중단",
    "약을 시작",
)


@dataclass(frozen=True)
class RenderedQuestion:
    text_ko: str
    reason_ko: str
    source: str
    rejection_code: str | None = None


def render_question(
    *,
    candidate: QuestionCandidate,
    slot: SlotDefinition,
    deterministic_rationale: str,
    proposal: QuestionRenderProposal | None = None,
) -> RenderedQuestion:
    fallback = RenderedQuestion(
        text_ko=slot.question_template_ko,
        reason_ko=deterministic_rationale,
        source="DETERMINISTIC_TEMPLATE",
    )
    if proposal is None:
        return fallback
    identifiers_match = (
        proposal.question_id == candidate.question_id
        and proposal.slot_id == candidate.slot_id
        and proposal.action is candidate.action
        and proposal.answer_type == candidate.answer_type
    )
    safe_text = bool(proposal.question_ko.strip()) and not any(
        phrase in proposal.question_ko for phrase in _PROHIBITED
    )
    if not identifiers_match:
        return RenderedQuestion(
            text_ko=fallback.text_ko,
            reason_ko=fallback.reason_ko,
            source=fallback.source,
            rejection_code="QUESTION_IDENTIFIERS_CHANGED",
        )
    if not safe_text:
        return RenderedQuestion(
            text_ko=fallback.text_ko,
            reason_ko=fallback.reason_ko,
            source=fallback.source,
            rejection_code="UNSAFE_QUESTION_TEXT",
        )
    return RenderedQuestion(
        text_ko=proposal.question_ko.strip(),
        reason_ko=deterministic_rationale,
        source="MODEL_VALIDATED",
    )
