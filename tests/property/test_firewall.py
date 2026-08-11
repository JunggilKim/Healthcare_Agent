from __future__ import annotations

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.evidence import EligibilityContext, RetrievalContext, RetrievalHypothesis
from backend.app.engine.evaluator import evaluate_criterion


@given(st.text(min_size=1, max_size=40))
def test_arbitrary_grade_h_hypothesis_cannot_change_hard_verdict(concept: str) -> None:
    fixture = load_vertical_slice()
    criterion = fixture.compiled_trial.criteria[1]
    eligibility = EligibilityContext(facts=list(fixture.facts), conflicts=[])
    before = evaluate_criterion(criterion, eligibility, date(2026, 8, 11)).verdict
    hypothesis = RetrievalHypothesis(
        hypothesis_id="hyp_00000000-0000-4000-8000-000000009999",
        concept=concept,
        normalized_concept="urothelial_carcinoma",
        source_fact_ids=[fixture.facts[-1].fact_id],
        rationale_code="PROPERTY_TEST",
        grade="H",
        admissible_for_eligibility=False,
    )
    retrieval = RetrievalContext(facts=list(fixture.facts), hypotheses=[hypothesis])
    assert retrieval.hypotheses[0].admissible_for_eligibility is False
    after = evaluate_criterion(criterion, eligibility, date(2026, 8, 11)).verdict
    assert before == after
