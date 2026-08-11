from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.enums import CriterionVerdict, EvidenceGrade
from backend.app.domain.evidence import EligibilityContext, PatientFact, SourceSpan
from backend.app.domain.values import CategoricalValue
from backend.app.engine.evaluator import evaluate_criterion


def test_is_a_allows_exact_direct_category_but_not_unlisted_inference() -> None:
    fixture = load_vertical_slice()
    criterion = next(
        item
        for item in fixture.compiled_trial.criteria
        if item.required_slots == ["pathology.histology"]
    )
    value = CategoricalValue(
        kind="categorical",
        value="urothelial_carcinoma",
        system="trial-opt-canonical-v1",
    )
    quote = "Pathology confirms urothelial carcinoma."
    fact = PatientFact(
        fact_id="fact_ontology_direct",
        slot_id="pathology.histology",
        value=value,
        grade=EvidenceGrade.A_DIRECT,
        source_spans=[
            SourceSpan(
                source_id="test:ontology",
                start=0,
                end=len(quote),
                quote=quote,
                sha256=hashlib.sha256(quote.encode()).hexdigest(),
                language="en",
            )
        ],
        asserted_at=datetime(2026, 8, 11, tzinfo=UTC),
        effective_date=date(2026, 8, 11),
        admissible_for_hard_decision=True,
    )
    node_id = f"{criterion.criterion_id}:ontology:node:0"
    exact = criterion.model_copy(
        update={
            "ast": CriterionAst(
                root_node_id=node_id,
                nodes=[
                    AstNode(
                        node_id=node_id,
                        op=AstOperator.IS_A,
                        slot_id="pathology.histology",
                        value=value,
                        metadata={"ontology_version": "ontology-whitelist-v1"},
                    )
                ],
            )
        }
    )
    context = EligibilityContext(facts=[fact], conflicts=[])
    exact_result = evaluate_criterion(exact, context, date(2026, 8, 11))
    assert exact_result.verdict is CriterionVerdict.PASS

    inferred = exact.model_copy(
        update={
            "ast": exact.ast.model_copy(
                update={
                    "nodes": [
                        exact.ast.nodes[0].model_copy(
                            update={
                                "value": CategoricalValue(
                                    kind="categorical",
                                    value="unlisted_ancestor",
                                    system="trial-opt-canonical-v1",
                                )
                            }
                        )
                    ]
                }
            )
        }
    )
    inferred_result = evaluate_criterion(inferred, context, date(2026, 8, 11))
    assert inferred_result.verdict is CriterionVerdict.UNKNOWN
    assert inferred_result.issue_codes == ["ONTOLOGY_RELATION_NOT_WHITELISTED"]
