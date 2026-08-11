from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.app.application.catalog import SlotDefinition
from backend.app.domain.enums import TrialDecision
from backend.app.domain.evidence import EligibilityContext, FactConflict, PatientFact
from backend.app.domain.proof import ProofPacket
from backend.app.domain.ranking import RankDelta
from backend.app.domain.sessions import SessionAggregate
from backend.app.domain.trials import ProtocolReviewArtifact, RawTrialRecord
from backend.app.engine.proof_verifier import build_verified_proof
from backend.app.engine.ranker import rank_trials
from backend.app.engine.trial_aggregator import aggregate_trial, is_trial_irrelevant


def build_reverse_slot_index(
    aggregate: SessionAggregate,
) -> dict[str, list[tuple[str, str]]]:
    index: dict[str, list[tuple[str, str]]] = {}
    for nct_id, compiled in aggregate.compiled_trials.items():
        for criterion in compiled.criteria:
            for slot_id in criterion.required_slots:
                index.setdefault(slot_id, []).append((nct_id, criterion.criterion_id))
    return {slot_id: sorted(set(references)) for slot_id, references in sorted(index.items())}


@dataclass(frozen=True)
class IncrementalResult:
    aggregate: SessionAggregate
    proofs_by_trial: dict[str, list[ProofPacket]]
    changed_criterion_ids: list[str]
    rank_deltas: list[RankDelta]
    recompiled_trial_ids: list[str]


def reevaluate_for_answered_slot(
    *,
    aggregate: SessionAggregate,
    answered_slot_id: str,
    updated_facts: list[PatientFact],
    updated_conflicts: list[FactConflict],
    answer_fact_ids: list[str],
    proofs_by_trial: dict[str, list[ProofPacket]],
    raw_trials: dict[str, RawTrialRecord],
    reviews: dict[str, ProtocolReviewArtifact],
    registry_data_versions: dict[str, str | None],
    source_texts: dict[str, str],
    slots: dict[str, SlotDefinition],
    evaluated_at: datetime,
) -> IncrementalResult:
    reverse = build_reverse_slot_index(aggregate)
    affected = reverse.get(answered_slot_id, [])
    affected_by_trial: dict[str, set[str]] = {}
    for nct_id, criterion_id in affected:
        affected_by_trial.setdefault(nct_id, set()).add(criterion_id)
    new_version = aggregate.patient_state_version + 1
    context = EligibilityContext(facts=updated_facts, conflicts=updated_conflicts)
    next_proofs: dict[str, list[ProofPacket]] = {}
    next_evaluations = dict(aggregate.trial_evaluations)

    for nct_id, compiled in aggregate.compiled_trials.items():
        previous = {packet.criterion_id: packet for packet in proofs_by_trial[nct_id]}
        changed_ids = affected_by_trial.get(nct_id, set())
        packets: list[ProofPacket] = []
        for criterion in compiled.criteria:
            if criterion.criterion_id not in changed_ids:
                packets.append(previous[criterion.criterion_id])
                continue
            packets.append(
                build_verified_proof(
                    session_id=aggregate.session_id,
                    patient_state_version=new_version,
                    evaluation_date=aggregate.evaluation_date,
                    criterion=criterion,
                    compiled_trial=compiled,
                    review=reviews[nct_id],
                    raw_trial=raw_trials[nct_id],
                    registry_data_version=registry_data_versions.get(nct_id),
                    eligibility_context=context,
                    source_texts=source_texts,
                    slots=slots,
                    evaluated_at=evaluated_at,
                )
            )
        next_proofs[nct_id] = packets
        if changed_ids:
            previous_evaluation = aggregate.trial_evaluations[nct_id]
            remains_irrelevant = (
                previous_evaluation.decision is TrialDecision.IRRELEVANT
                and is_trial_irrelevant(
                    retrieval_score=previous_evaluation.retrieval_score,
                    exact_condition_match=False,
                    compiled_trial=compiled,
                    facts=updated_facts,
                )
            )
            next_evaluations[nct_id] = aggregate_trial(
                session_id=aggregate.session_id,
                patient_state_version=new_version,
                compiled_trial=compiled,
                raw_trial=raw_trials[nct_id],
                proofs=packets,
                retrieval_score=previous_evaluation.retrieval_score,
                irrelevant=remains_irrelevant,
            )

    ranked = rank_trials(list(next_evaluations.values()))
    ranked_ids = [evaluation.nct_id for evaluation in ranked]
    before_rank = {nct_id: index for index, nct_id in enumerate(aggregate.ranked_nct_ids, start=1)}
    after_rank = {nct_id: index for index, nct_id in enumerate(ranked_ids, start=1)}
    rank_deltas = [
        RankDelta(
            nct_id=nct_id,
            before_rank=before_rank[nct_id],
            after_rank=after_rank[nct_id],
            before_decision=aggregate.trial_evaluations[nct_id].decision,
            after_decision=next_evaluations[nct_id].decision,
            changed_criterion_ids=sorted(affected_by_trial.get(nct_id, set())),
            answer_fact_ids=answer_fact_ids,
        )
        for nct_id in ranked_ids
        if before_rank[nct_id] != after_rank[nct_id]
        or aggregate.trial_evaluations[nct_id].decision is not next_evaluations[nct_id].decision
    ]
    next_aggregate = aggregate.model_copy(
        deep=True,
        update={
            "patient_state_version": new_version,
            "facts": updated_facts,
            "conflicts": updated_conflicts,
            "trial_evaluations": next_evaluations,
            "ranked_nct_ids": ranked_ids,
        },
    )
    return IncrementalResult(
        aggregate=next_aggregate,
        proofs_by_trial=next_proofs,
        changed_criterion_ids=sorted(
            criterion_id
            for criterion_ids in affected_by_trial.values()
            for criterion_id in criterion_ids
        ),
        rank_deltas=rank_deltas,
        recompiled_trial_ids=[],
    )
