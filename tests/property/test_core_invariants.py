from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from backend.app.application.catalog import load_slot_catalog
from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.canonical import canonical_json_bytes, canonical_sha256
from backend.app.domain.enums import TrialDecision
from backend.app.domain.evidence import EligibilityContext
from backend.app.domain.questions import OptimizerRuntimeConfig
from backend.app.domain.ranking import RankingKey, TrialEvaluation
from backend.app.domain.sessions import SessionAggregate
from backend.app.domain.values import NumberValue
from backend.app.engine.branch_builder import build_branches, deterministic_question_id
from backend.app.engine.evaluator import evaluate_criterion
from backend.app.engine.incremental import build_reverse_slot_index, reevaluate_for_answered_slot
from backend.app.engine.multi_trial_optimizer import FullOptimizationState
from backend.app.engine.proof_verifier import build_verified_proof, canonical_replay_payload
from backend.app.engine.ranker import rank_trials
from backend.app.engine.trial_aggregator import aggregate_trial

EVALUATION_DATE = date(2026, 8, 11)
EVALUATED_AT = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
SESSION_ID = "00000000-0000-4000-8000-000000000099"


def _full_state() -> FullOptimizationState:
    fixture = load_vertical_slice()
    slots = load_slot_catalog().by_id()
    context = EligibilityContext(facts=list(fixture.facts), conflicts=list(fixture.conflicts))
    proofs = [
        build_verified_proof(
            session_id=SESSION_ID,
            patient_state_version=0,
            evaluation_date=EVALUATION_DATE,
            criterion=criterion,
            compiled_trial=fixture.compiled_trial,
            review=fixture.review,
            raw_trial=fixture.raw_trial,
            registry_data_version="2026-08-11T09:00:06",
            eligibility_context=context,
            source_texts=fixture.source_texts,
            slots=slots,
            evaluated_at=EVALUATED_AT,
        )
        for criterion in fixture.compiled_trial.criteria
    ]
    evaluation = aggregate_trial(
        session_id=SESSION_ID,
        patient_state_version=0,
        compiled_trial=fixture.compiled_trial,
        raw_trial=fixture.raw_trial,
        proofs=proofs,
        retrieval_score=1.0,
    )
    aggregate = SessionAggregate(
        session_id=SESSION_ID,
        mode="snapshot",
        evaluation_date=EVALUATION_DATE,
        patient_state_version=0,
        question_count=0,
        facts=list(fixture.facts),
        retrieval_hypotheses=list(fixture.hypotheses),
        conflicts=list(fixture.conflicts),
        compiled_trials={fixture.compiled_trial.nct_id: fixture.compiled_trial},
        trial_evaluations={evaluation.nct_id: evaluation},
        ranked_nct_ids=[evaluation.nct_id],
        asked_slot_ids=[],
        unavailable_slot_ids=[],
        current_question_id=None,
        config=OptimizerRuntimeConfig(
            top_k=5,
            max_questions=5,
            hard_max_questions=7,
            max_branches=6,
            stop_utility_threshold=0.10,
            stable_risk_reduction_threshold=0.05,
        ),
    )
    return FullOptimizationState(
        aggregate=aggregate,
        proofs_by_trial={evaluation.nct_id: proofs},
        raw_trials={evaluation.nct_id: fixture.raw_trial},
        reviews={evaluation.nct_id: fixture.review},
        registry_data_versions={evaluation.nct_id: "2026-08-11T09:00:06"},
        source_texts=dict(fixture.source_texts),
        slots=slots,
        evaluated_at=EVALUATED_AT,
    )


@settings(max_examples=30)
@given(
    operator=st.sampled_from([AstOperator.ALL, AstOperator.ANY]),
    keep_facts=st.lists(st.booleans(), min_size=1, max_size=12),
)
def test_all_any_duplicate_child_is_idempotent(
    operator: AstOperator, keep_facts: list[bool]
) -> None:
    fixture = load_vertical_slice()
    criterion = fixture.compiled_trial.criteria[0]
    facts = [
        fact for index, fact in enumerate(fixture.facts) if keep_facts[index % len(keep_facts)]
    ]
    context = EligibilityContext(facts=facts, conflicts=[])
    leaf = criterion.ast.nodes[0]
    first = leaf.model_copy(update={"node_id": f"{criterion.criterion_id}:property:leaf:0"})
    second = leaf.model_copy(update={"node_id": f"{criterion.criterion_id}:property:leaf:1"})
    root = AstNode(
        node_id=f"{criterion.criterion_id}:property:root",
        op=operator,
        child_ids=[first.node_id, second.node_id],
    )
    composite = criterion.model_copy(
        update={
            "ast": CriterionAst(root_node_id=root.node_id, nodes=[root, first, second]),
        }
    )

    leaf_result = evaluate_criterion(criterion, context, EVALUATION_DATE)
    composite_result = evaluate_criterion(composite, context, EVALUATION_DATE)
    assert composite_result.verdict is leaf_result.verdict


def _evaluation(
    *, nct_id: str, decision: TrialDecision, retrieval: float, completeness: float
) -> TrialEvaluation:
    tier = {
        TrialDecision.PRE_SCREEN_PASS: 0,
        TrialDecision.POTENTIAL_MATCH: 1,
        TrialDecision.REVIEW_REQUIRED: 2,
        TrialDecision.INELIGIBLE: 3,
        TrialDecision.IRRELEVANT: 4,
    }[decision]
    fail_count = 1 if decision is TrialDecision.INELIGIBLE else 0
    return TrialEvaluation(
        session_id=SESSION_ID,
        patient_state_version=0,
        nct_id=nct_id,
        criterion_proof_ids=[],
        decision=decision,
        retrieval_score=retrieval,
        proof_completeness=completeness,
        critical_unknown_count=0,
        verified_fail_count=fail_count,
        conflict_count=0,
        opaque_critical_count=0,
        ranking_key=RankingKey(
            tier_order=tier,
            verified_fail_count=fail_count,
            critical_unknown_count=0,
            proof_completeness=Decimal(str(completeness)),
            retrieval_score=Decimal(str(retrieval)),
            recruitment_status_priority=0,
            last_update_epoch_days=0,
            nct_id=nct_id,
        ),
        display_score=0,
        degradation_codes=[],
    )


@settings(max_examples=30)
@given(
    fail_retrieval=st.floats(min_value=0, max_value=1, allow_nan=False),
    nonfail_retrieval=st.floats(min_value=0, max_value=1, allow_nan=False),
    fail_completeness=st.floats(min_value=0, max_value=1, allow_nan=False),
    nonfail_completeness=st.floats(min_value=0, max_value=1, allow_nan=False),
    nonfail_decision=st.sampled_from(
        [
            TrialDecision.PRE_SCREEN_PASS,
            TrialDecision.POTENTIAL_MATCH,
            TrialDecision.REVIEW_REQUIRED,
        ]
    ),
)
def test_verified_fail_never_ranks_above_nonfail_tier(
    fail_retrieval: float,
    nonfail_retrieval: float,
    fail_completeness: float,
    nonfail_completeness: float,
    nonfail_decision: TrialDecision,
) -> None:
    failed = _evaluation(
        nct_id="NCT00000001",
        decision=TrialDecision.INELIGIBLE,
        retrieval=fail_retrieval,
        completeness=fail_completeness,
    )
    nonfailed = _evaluation(
        nct_id="NCT00000002",
        decision=nonfail_decision,
        retrieval=nonfail_retrieval,
        completeness=nonfail_completeness,
    )
    assert rank_trials([failed, nonfailed]) == [nonfailed, failed]


@settings(max_examples=30)
@given(keep_facts=st.lists(st.booleans(), min_size=1, max_size=12))
def test_replay_is_deterministic_for_the_same_state(keep_facts: list[bool]) -> None:
    fixture = load_vertical_slice()
    criterion = fixture.compiled_trial.criteria[1]
    facts = [
        fact for index, fact in enumerate(fixture.facts) if keep_facts[index % len(keep_facts)]
    ]
    context = EligibilityContext(facts=facts, conflicts=[])
    first = evaluate_criterion(criterion, context, EVALUATION_DATE)
    second = evaluate_criterion(criterion, context, EVALUATION_DATE)
    assert canonical_sha256(canonical_replay_payload(criterion.criterion_id, 0, first)) == (
        canonical_sha256(canonical_replay_payload(criterion.criterion_id, 0, second))
    )


@settings(max_examples=30)
@given(threshold=st.integers(min_value=1, max_value=120))
def test_numeric_branches_cover_each_gte_threshold_region(threshold: int) -> None:
    fixture = load_vertical_slice()
    criterion = fixture.compiled_trial.criteria[0]
    leaf = criterion.ast.nodes[0].model_copy(
        update={"value": NumberValue(kind="number", value=Decimal(threshold), unit="year")}
    )
    criterion = criterion.model_copy(
        update={"ast": CriterionAst(root_node_id=leaf.node_id, nodes=[leaf])}
    )
    branches = build_branches(
        question_id=deterministic_question_id(SESSION_ID, 0, "demographics.age"),
        slot=load_slot_catalog().by_id()["demographics.age"],
        affected_criteria=[criterion],
        evaluation_date=EVALUATION_DATE,
    )
    values = [
        branch.synthetic_value.value
        for branch in branches
        if isinstance(branch.synthetic_value, NumberValue)
    ]
    boundary = Decimal(threshold)
    assert boundary in values
    assert {value >= boundary for value in values} == {False, True}


@settings(max_examples=20)
@given(
    answered_slot=st.sampled_from(
        [
            "demographics.age",
            "pathology.histology",
            "pathology.muscle_invasion",
            "staging.clinical_group",
            "prior_treatment.mibc_systemic",
            "performance_status.ecog",
            "organ_function.renal.gfr_or_crcl",
            "property.unused_slot",
        ]
    )
)
def test_answer_reevaluation_rebuilds_only_affected_criteria(answered_slot: str) -> None:
    state = _full_state()
    before = {
        packet.criterion_id: packet
        for packets in state.proofs_by_trial.values()
        for packet in packets
    }
    expected = {
        criterion_id
        for _nct_id, criterion_id in build_reverse_slot_index(state.aggregate).get(
            answered_slot, []
        )
    }
    result = reevaluate_for_answered_slot(
        aggregate=state.aggregate,
        answered_slot_id=answered_slot,
        updated_facts=state.aggregate.facts,
        updated_conflicts=state.aggregate.conflicts,
        answer_fact_ids=[],
        proofs_by_trial=state.proofs_by_trial,
        raw_trials=state.raw_trials,
        reviews=state.reviews,
        registry_data_versions=state.registry_data_versions,
        source_texts=state.source_texts,
        slots=state.slots,
        evaluated_at=EVALUATED_AT,
    )
    after = {
        packet.criterion_id: packet
        for packets in result.proofs_by_trial.values()
        for packet in packets
    }
    assert set(result.changed_criterion_ids) == expected
    assert all(
        after[criterion_id] is packet
        for criterion_id, packet in before.items()
        if criterion_id not in expected
    )


json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31 - 1),
    st.text(max_size=40),
)


@settings(max_examples=40)
@given(payload=st.dictionaries(st.text(min_size=1, max_size=20), json_scalar, max_size=20))
def test_canonical_hash_is_stable_across_mapping_insertion_order(
    payload: dict[str, object],
) -> None:
    reversed_payload = dict(reversed(list(payload.items())))
    assert canonical_json_bytes(payload) == canonical_json_bytes(reversed_payload)
    assert canonical_sha256(payload) == canonical_sha256(reversed_payload)
