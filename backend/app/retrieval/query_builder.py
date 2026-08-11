from __future__ import annotations

from backend.app.domain.evidence import PatientFact, RetrievalHypothesis
from backend.app.retrieval.models import ConditionQuery, RetrievalQuery


def build_deterministic_query(
    facts: list[PatientFact], hypotheses: list[RetrievalHypothesis]
) -> RetrievalQuery:
    """Build an auditable retrieval-only query without promoting hypotheses to evidence."""
    concepts: dict[str, tuple[list[str], list[str]]] = {}
    for hypothesis in sorted(hypotheses, key=lambda item: item.hypothesis_id):
        label = hypothesis.normalized_concept.strip().lower()
        if label:
            concepts.setdefault(label, ([], []))[1].append(hypothesis.hypothesis_id)

    condition_facts = [
        fact
        for fact in facts
        if fact.slot_id.startswith(("condition.", "diagnosis.", "pathology.histology"))
    ]
    for fact in sorted(condition_facts, key=lambda item: item.fact_id):
        value = getattr(fact.value, "value", None)
        if isinstance(value, str) and value.strip() and value.lower() != "unknown":
            concepts.setdefault(value.strip().lower(), ([], []))[0].append(fact.fact_id)

    queries = [
        ConditionQuery(
            text=text,
            source_fact_ids=sorted(source_ids[0]),
            source_hypothesis_ids=sorted(source_ids[1]),
            priority=index,
        )
        for index, (text, source_ids) in enumerate(sorted(concepts.items()), start=1)
    ][:4]
    if not queries:
        symptom_terms = sorted(
            fact.slot_id.removeprefix("symptom.").replace("_", " ")
            for fact in facts
            if fact.slot_id.startswith("symptom.")
        )
        fallback = " ".join(symptom_terms) or "clinical trial"
        queries = [ConditionQuery(text=fallback, priority=1)]

    context_terms = [query.text for query in queries]
    for fact in sorted(facts, key=lambda item: item.fact_id):
        if fact.slot_id.startswith(("demographics.", "symptom.", "imaging.")):
            context_terms.append(fact.slot_id.replace(".", " ").replace("_", " "))
    return RetrievalQuery(
        condition_queries=queries,
        dense_query="; ".join(dict.fromkeys(context_terms))[:800],
    )
