from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.agents.answer_interpreter import InterpretedAnswer, interpret_answer
from backend.app.application.catalog import SlotCatalog
from backend.app.domain.questions import QuestionCandidate, QuestionSelection
from backend.app.domain.ranking import RankDelta
from backend.app.domain.rendering import AnswerInterpretationProposal
from backend.app.engine.incremental import reevaluate_for_answered_slot
from backend.app.engine.multi_trial_optimizer import FullOptimizationState, select_next_action


@dataclass(frozen=True)
class AnswerTurnResult:
    interpreted_answer: InterpretedAnswer
    rank_deltas: list[RankDelta]
    changed_criterion_ids: list[str]
    next_selection: QuestionSelection
    recompiled_trial_ids: list[str]


class InteractiveTrialOptLoop:
    """Pure application loop; persistence adapters append the returned events separately."""

    def __init__(self, state: FullOptimizationState, slot_catalog: SlotCatalog) -> None:
        self.state = state
        self.slot_catalog = slot_catalog

    def prepare_next_question(self) -> QuestionSelection:
        selection = select_next_action(self.state)
        self.state.aggregate = self.state.aggregate.model_copy(
            update={
                "current_question_id": (
                    selection.selected.question_id if selection.selected else None
                )
            }
        )
        return selection

    def submit_answer(
        self,
        *,
        candidate: QuestionCandidate,
        answer_text: str,
        source_id: str,
        asserted_at: datetime,
        proposal: AnswerInterpretationProposal | None = None,
    ) -> AnswerTurnResult:
        aggregate = self.state.aggregate
        if aggregate.current_question_id != candidate.question_id:
            raise ValueError("QUESTION_NOT_CURRENT")
        if candidate.slot_id in aggregate.asked_slot_ids:
            raise ValueError("QUESTION_ALREADY_ANSWERED")
        interpreted = interpret_answer(
            candidate=candidate,
            answer_text=answer_text,
            source_id=source_id,
            slot_catalog=self.slot_catalog,
            asserted_at=asserted_at,
            proposal=proposal,
        )
        facts = list(aggregate.facts)
        conflicts = list(aggregate.conflicts)
        answer_fact_ids: list[str] = []
        unavailable = set(aggregate.unavailable_slot_ids)
        if interpreted.materialized is not None:
            answer_facts = interpreted.materialized.state.confirmed_facts
            facts = [item for item in facts if item.slot_id != candidate.slot_id]
            facts.extend(answer_facts)
            conflicts.extend(interpreted.materialized.state.conflicts)
            answer_fact_ids = [item.fact_id for item in answer_facts]
            self.state.source_texts[source_id] = answer_text
        if interpreted.unknown or interpreted.declined:
            unavailable.add(candidate.slot_id)

        incremental = reevaluate_for_answered_slot(
            aggregate=aggregate,
            answered_slot_id=candidate.slot_id,
            updated_facts=facts,
            updated_conflicts=conflicts,
            answer_fact_ids=answer_fact_ids,
            proofs_by_trial=self.state.proofs_by_trial,
            raw_trials=self.state.raw_trials,
            reviews=self.state.reviews,
            registry_data_versions=self.state.registry_data_versions,
            source_texts=self.state.source_texts,
            slots=self.state.slots,
            evaluated_at=asserted_at,
        )
        self.state.aggregate = incremental.aggregate.model_copy(
            update={
                "question_count": aggregate.question_count + 1,
                "asked_slot_ids": [*aggregate.asked_slot_ids, candidate.slot_id],
                "unavailable_slot_ids": sorted(unavailable),
                "current_question_id": None,
            }
        )
        self.state.proofs_by_trial = incremental.proofs_by_trial
        self.state.recompiled_trial_ids.extend(incremental.recompiled_trial_ids)
        next_selection = self.prepare_next_question()
        return AnswerTurnResult(
            interpreted_answer=interpreted,
            rank_deltas=incremental.rank_deltas,
            changed_criterion_ids=incremental.changed_criterion_ids,
            next_selection=next_selection,
            recompiled_trial_ids=incremental.recompiled_trial_ids,
        )
