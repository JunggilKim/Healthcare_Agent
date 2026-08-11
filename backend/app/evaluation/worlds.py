from __future__ import annotations

import hashlib
import random
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

from backend.app.application.vertical_slice import VerticalSliceFixture
from backend.app.domain.ast import AstNode, AstOperator
from backend.app.domain.enums import EvidenceGrade
from backend.app.domain.evidence import EligibilityContext, FactConflict, PatientFact, SourceSpan
from backend.app.domain.values import (
    BooleanValue,
    CategoricalValue,
    DateValue,
    DurationValue,
    NumberValue,
    StringValue,
    TypedValue,
)
from backend.app.engine.evaluator import evaluate_criterion
from backend.app.evaluation.models import (
    BenchmarkArtifact,
    CriterionTruth,
    MissingnessObservation,
    OracleAnswer,
    PatientWorld,
    WorldFact,
)

_REALISTIC_WEIGHT = {
    "pathology": 5.0,
    "staging": 4.5,
    "prior_treatment": 4.0,
    "performance_status": 3.5,
    "organ_function": 3.5,
    "laboratory": 3.5,
    "reproductive": 3.0,
    "demographics": 0.8,
}

_SLOT_LABEL = {
    "demographics.age": "Age",
    "pathology.histology": "Pathology histology",
    "pathology.muscle_invasion": "Muscle invasion documented",
    "staging.clinical_group": "Clinical stage group",
    "prior_treatment.mibc_systemic": "Prior systemic treatment documented",
    "performance_status.ecog": "ECOG performance status",
    "organ_function.renal.gfr_or_crcl": "GFR or creatinine clearance",
}


def _stable_number(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:16], 16)


def _root_node(fixture: VerticalSliceFixture, criterion_id: str) -> AstNode:
    criterion = next(
        item for item in fixture.compiled_trial.criteria if item.criterion_id == criterion_id
    )
    return next(item for item in criterion.ast.nodes if item.node_id == criterion.ast.root_node_id)


def _passing_value(node: AstNode) -> TypedValue:
    if node.op is AstOperator.IN and node.values:
        return node.values[0]
    if node.value is None:
        raise ValueError(f"unsupported benchmark root without value: {node.node_id}")
    return node.value


def _failing_value(node: AstNode) -> TypedValue:
    target = _passing_value(node)
    if isinstance(target, BooleanValue):
        return BooleanValue(kind="boolean", value=not target.value)
    if isinstance(target, NumberValue):
        delta = Decimal("1")
        value = (
            target.value - delta
            if node.op in {AstOperator.GTE, AstOperator.GT}
            else target.value + delta
        )
        return NumberValue(kind="number", value=value, unit=target.unit)
    if isinstance(target, CategoricalValue):
        return CategoricalValue(
            kind="categorical", value="outside_allowed_set", system=target.system
        )
    if isinstance(target, StringValue):
        return StringValue(
            kind="string", value="outside allowed set", normalized="outside_allowed_set"
        )
    if isinstance(target, DateValue):
        shift = -1 if node.op in {AstOperator.AFTER, AstOperator.GTE} else 1
        return target.model_copy(
            update={"value": date.fromordinal(target.value.toordinal() + shift)}
        )
    if isinstance(target, DurationValue):
        return DurationValue(kind="duration", days=max(0, target.days - 1))
    raise ValueError(f"unsupported benchmark value kind: {target.kind}")


def _value_text(value: TypedValue) -> str:
    if isinstance(value, NumberValue):
        return f"{value.value} {value.unit or ''}".strip()
    if isinstance(value, BooleanValue):
        return "yes" if value.value else "no"
    if isinstance(value, CategoricalValue | StringValue | DateValue):
        return str(value.value)
    if isinstance(value, DurationValue):
        return f"{value.days} days"
    return value.model_dump_json()


def _facts_for_values(world_id: str, values: dict[str, list[TypedValue]]) -> list[WorldFact]:
    return [
        WorldFact(
            fact_id=f"fact_{hashlib.sha256(f'{world_id}:{slot_id}:{index}'.encode()).hexdigest()[:24]}",
            slot_id=slot_id,
            value=value,
        )
        for slot_id, slot_values in sorted(values.items())
        for index, value in enumerate(slot_values)
    ]


def _render_narrative(facts: list[WorldFact]) -> str:
    statements = [
        f"{_SLOT_LABEL.get(fact.slot_id, fact.slot_id)}: {_value_text(fact.value)}"
        for fact in facts
    ]
    return "Synthetic structured record. " + "; ".join(statements) + "."


def _patient_facts(facts: list[WorldFact], evaluation_date: date) -> list[PatientFact]:
    result: list[PatientFact] = []
    for fact in facts:
        quote = f"{_SLOT_LABEL.get(fact.slot_id, fact.slot_id)}: {_value_text(fact.value)}"
        result.append(
            PatientFact(
                fact_id=fact.fact_id,
                slot_id=fact.slot_id,
                value=fact.value,
                grade=EvidenceGrade.A_DIRECT,
                source_spans=[
                    SourceSpan(
                        source_id=f"benchmark:{fact.fact_id}",
                        start=0,
                        end=len(quote),
                        quote=quote,
                        sha256=hashlib.sha256(quote.encode()).hexdigest(),
                        language="en",
                    )
                ],
                asserted_at=datetime(2026, 8, 11, tzinfo=UTC),
                effective_date=evaluation_date,
                admissible_for_hard_decision=True,
            )
        )
    return result


def _truth(
    fixture: VerticalSliceFixture,
    facts: list[WorldFact],
    conflict_slots: list[str],
    evaluation_date: date,
) -> list[CriterionTruth]:
    patient_facts = _patient_facts(facts, evaluation_date)
    conflicts = [
        FactConflict(
            conflict_id=f"conflict_{hashlib.sha256(slot.encode()).hexdigest()[:16]}",
            slot_id=slot,
            fact_ids=[fact.fact_id for fact in patient_facts if fact.slot_id == slot],
            conflict_type="VALUE_MISMATCH",
            status="OPEN",
        )
        for slot in conflict_slots
    ]
    context = EligibilityContext(facts=patient_facts, conflicts=conflicts)
    return [
        CriterionTruth(
            criterion_id=criterion.criterion_id,
            verdict=result.verdict,
            evidence_fact_ids=result.evidence_fact_ids,
            missing_slot_ids=result.missing_slot_ids,
        )
        for criterion in fixture.compiled_trial.criteria
        if not criterion.opaque
        for result in [evaluate_criterion(criterion, context, evaluation_date)]
    ]


def _split(nct_id: str) -> Literal["development", "validation", "test"]:
    bucket = _stable_number(nct_id) % 100
    if bucket < 60:
        return "development"
    if bucket < 80:
        return "validation"
    return "test"


def _make_world(
    fixture: VerticalSliceFixture,
    *,
    suffix: str,
    world_type: Literal[
        "FULL_PASS",
        "SINGLE_FAIL",
        "MULTI_FAIL",
        "UNKNOWN",
        "CONFLICT",
        "BOUNDARY",
    ],
    values: dict[str, list[TypedValue]],
    conflict_slots: list[str] | None = None,
    unavailable_slots: list[str] | None = None,
) -> PatientWorld:
    world_id = f"S004-{suffix}"
    facts = _facts_for_values(world_id, values)
    conflicts = conflict_slots or []
    return PatientWorld(
        world_id=world_id,
        nct_id=fixture.compiled_trial.nct_id,
        world_type=world_type,
        split=_split(fixture.compiled_trial.nct_id),
        compiled_protocol_hash=fixture.compiled_trial.content_hash,
        criterion_source_hashes=[
            criterion.source_text_sha256 for criterion in fixture.compiled_trial.criteria
        ],
        facts=facts,
        conflict_slots=conflicts,
        unavailable_slots=unavailable_slots or [],
        narrative=_render_narrative(facts),
        criterion_truth=_truth(fixture, facts, conflicts, date(2026, 8, 11)),
    )


def _worlds(fixture: VerticalSliceFixture) -> list[PatientWorld]:
    nodes = {
        criterion.required_slots[0]: _root_node(fixture, criterion.criterion_id)
        for criterion in fixture.compiled_trial.criteria
        if len(criterion.required_slots) == 1 and not criterion.opaque
    }
    passing = {slot: [_passing_value(node)] for slot, node in nodes.items()}

    def changed(updates: dict[str, list[TypedValue]]) -> dict[str, list[TypedValue]]:
        return {**passing, **updates}

    histology = nodes["pathology.histology"]
    renal = nodes["organ_function.renal.gfr_or_crcl"]
    age = nodes["demographics.age"]
    return [
        _make_world(fixture, suffix="pass-1", world_type="FULL_PASS", values=passing),
        _make_world(
            fixture,
            suffix="pass-2",
            world_type="FULL_PASS",
            values=changed(
                {
                    "demographics.age": [NumberValue(kind="number", value=68, unit="year")],
                    "organ_function.renal.gfr_or_crcl": [
                        NumberValue(kind="number", value=55, unit="mL/min")
                    ],
                }
            ),
        ),
        _make_world(
            fixture,
            suffix="fail-histology",
            world_type="SINGLE_FAIL",
            values=changed({"pathology.histology": [_failing_value(histology)]}),
        ),
        _make_world(
            fixture,
            suffix="fail-renal",
            world_type="SINGLE_FAIL",
            values=changed({"organ_function.renal.gfr_or_crcl": [_failing_value(renal)]}),
        ),
        _make_world(
            fixture,
            suffix="multi-fail",
            world_type="MULTI_FAIL",
            values=changed(
                {
                    "demographics.age": [_failing_value(age)],
                    "pathology.histology": [_failing_value(histology)],
                }
            ),
        ),
        _make_world(
            fixture,
            suffix="unknown-histology",
            world_type="UNKNOWN",
            values={
                slot: value for slot, value in passing.items() if slot != "pathology.histology"
            },
            unavailable_slots=["pathology.histology"],
        ),
        _make_world(
            fixture,
            suffix="unknown-records",
            world_type="UNKNOWN",
            values={
                slot: value
                for slot, value in passing.items()
                if slot not in {"performance_status.ecog", "organ_function.renal.gfr_or_crcl"}
            },
            unavailable_slots=["performance_status.ecog", "organ_function.renal.gfr_or_crcl"],
        ),
        _make_world(
            fixture,
            suffix="conflict-histology",
            world_type="CONFLICT",
            values=changed(
                {
                    "pathology.histology": [
                        _passing_value(histology),
                        _failing_value(histology),
                    ]
                }
            ),
            conflict_slots=["pathology.histology"],
        ),
        _make_world(
            fixture,
            suffix="boundary",
            world_type="BOUNDARY",
            values=changed(
                {
                    "demographics.age": [_passing_value(age)],
                    "organ_function.renal.gfr_or_crcl": [_passing_value(renal)],
                }
            ),
        ),
    ]


def _weighted_slots(slots: list[str], count: int, rng: random.Random) -> list[str]:
    scored = []
    for slot in slots:
        prefix = slot.split(".")[0]
        weight = _REALISTIC_WEIGHT.get(prefix, 1.0)
        scored.append((rng.random() ** (1.0 / weight), slot))
    return [slot for _, slot in sorted(scored, reverse=True)[:count]]


def _observations(worlds: list[PatientWorld], seed: int) -> list[MissingnessObservation]:
    observations: list[MissingnessObservation] = []
    for world in worlds:
        unique_slots = sorted({fact.slot_id for fact in world.facts})
        for rate in (0.2, 0.4, 0.6):
            count = max(1, min(len(unique_slots) - 1, round(len(unique_slots) * rate)))
            for pattern in ("MCAR", "REALISTIC"):
                key = f"{seed}:{world.world_id}:{rate}:{pattern}"
                rng = random.Random(seed + _stable_number(key))
                hidden = (
                    sorted(rng.sample(unique_slots, count))
                    if pattern == "MCAR"
                    else sorted(_weighted_slots(unique_slots, count, rng))
                )
                visible = [fact.fact_id for fact in world.facts if fact.slot_id not in hidden]
                oracle = []
                for slot in hidden:
                    values = [fact.value for fact in world.facts if fact.slot_id == slot]
                    unavailable = slot in world.unavailable_slots or not values
                    oracle.append(
                        OracleAnswer(
                            slot_id=slot,
                            value=None if unavailable else values[0],
                            unknown=unavailable,
                            answer_sentence=(
                                "The synthetic record does not provide this value."
                                if unavailable
                                else f"The synthetic record states {_value_text(values[0])}."
                            ),
                        )
                    )
                observations.append(
                    MissingnessObservation(
                        observation_id=(
                            f"obs-{world.world_id}-{int(rate * 100)}-{pattern.lower()}"
                        ),
                        world_id=world.world_id,
                        nct_id=world.nct_id,
                        split=world.split,
                        rate=rate,
                        pattern=pattern,
                        visible_fact_ids=visible,
                        hidden_slots=hidden,
                        oracle=oracle,
                    )
                )
    return observations


def generate_fixture_benchmark(fixture: VerticalSliceFixture, seed: int) -> BenchmarkArtifact:
    worlds = _worlds(fixture)
    observations = _observations(worlds, seed)
    return BenchmarkArtifact(
        seed=seed,
        scope_status="PROVISIONAL_FIXTURE_SMOKE",
        acceptance_eligible=False,
        blocking_reasons=[
            "Dataset A requires 24-36 exact-hash reviewed interventional trials; only the "
            "frozen S004 specification fixture is available.",
            "The 200-pair manually adjudicated subset and 50 dual reviews are not yet supplied.",
            "S008 and S001 reviewed compiled trial corpora are pending external model and "
            "project-review validation.",
        ],
        source_trials=[fixture.compiled_trial.nct_id],
        worlds=worlds,
        observations=observations,
        counts={
            "trials": 1,
            "worlds": len(worlds),
            "observations": len(observations),
            "criterion_labels": sum(len(world.criterion_truth) for world in worlds),
            "manual_reviews": 0,
            "dual_reviews": 0,
        },
    )
