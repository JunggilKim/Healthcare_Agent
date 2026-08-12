from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, TypeAlias

from backend.app.application.catalog import SlotDefinition, load_slot_catalog
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.enums import CriterionVerdict
from backend.app.domain.trials import CompiledCriterion, CompiledTrial, RawTrialRecord
from backend.app.domain.values import (
    BooleanValue,
    CategoricalValue,
    DateValue,
    DurationValue,
    NumberValue,
    RangeValue,
    StringValue,
    TypedValue,
)
from backend.app.engine.evaluator import evaluate_criterion
from backend.app.evaluation.execution import eligibility_context_from_world
from backend.app.evaluation.models import (
    BenchmarkArtifact,
    CriterionTruth,
    PatientWorld,
    WorldFact,
)
from backend.app.evaluation.worlds import _observations, _render_narrative

Plan: TypeAlias = dict[str, TypedValue | None]


def _plan_key(plan: Plan) -> bytes:
    return canonical_json_bytes(
        {
            slot: None if value is None else value.model_dump(mode="json")
            for slot, value in sorted(plan.items())
        }
    )


def _dedupe_plans(plans: list[Plan], *, limit: int = 64) -> list[Plan]:
    result: list[Plan] = []
    seen: set[bytes] = set()
    for plan in plans:
        key = _plan_key(plan)
        if key in seen:
            continue
        seen.add(key)
        result.append(plan)
        if len(result) >= limit:
            break
    return result


def _merge_two(left: Plan, right: Plan) -> list[Plan]:
    variants = [dict(left)]
    for slot_id, value in right.items():
        next_variants: list[Plan] = []
        for variant in variants:
            if slot_id not in variant or variant[slot_id] == value:
                merged = dict(variant)
                merged[slot_id] = value
                next_variants.append(merged)
            else:
                keep_existing = dict(variant)
                use_new = dict(variant)
                use_new[slot_id] = value
                next_variants.extend([keep_existing, use_new])
        variants = _dedupe_plans(next_variants, limit=16)
    return variants


def _combine_plan_groups(groups: list[list[Plan]]) -> list[Plan]:
    combined: list[Plan] = [{}]
    for group in groups:
        combined = _dedupe_plans(
            [
                merged
                for current in combined
                for item in group
                for merged in _merge_two(current, item)
            ]
        )
        if not combined:
            break
    return combined


def _default_value(slot: SlotDefinition, evaluation_date: date) -> TypedValue:
    if slot.value_type == "boolean":
        return BooleanValue(kind="boolean", value=True)
    if slot.value_type == "number":
        numeric_default = slot.allowed_range[0] if slot.allowed_range else 1
        unit = slot.allowed_units[0] if slot.allowed_units else None
        return NumberValue(kind="number", value=numeric_default, unit=unit)
    if slot.value_type == "categorical":
        category_default = slot.canonical_values[0] if slot.canonical_values else "documented_value"
        return CategoricalValue(kind="categorical", value=category_default, system=None)
    if slot.value_type == "categorical_free_string":
        return StringValue(kind="string", value="documented value", normalized="documented_value")
    if slot.value_type == "date":
        return DateValue(kind="date", value=evaluation_date, precision="DAY")
    return DurationValue(kind="duration", days=30)


def _alternative_value(
    value: TypedValue, slot: SlotDefinition | None, *, delta: Decimal = Decimal(1)
) -> TypedValue:
    if isinstance(value, BooleanValue):
        return BooleanValue(kind="boolean", value=not value.value)
    if isinstance(value, NumberValue):
        return NumberValue(kind="number", value=value.value + delta, unit=value.unit)
    if isinstance(value, CategoricalValue):
        alternatives = [] if slot is None else slot.canonical_values
        candidate = next(
            (item for item in alternatives if item != value.value), "outside_allowed_set"
        )
        return CategoricalValue(kind="categorical", value=candidate, system=value.system)
    if isinstance(value, StringValue):
        return StringValue(
            kind="string", value="outside documented set", normalized="outside_documented_set"
        )
    if isinstance(value, DateValue):
        return DateValue(
            kind="date", value=value.value + timedelta(days=int(delta)), precision="DAY"
        )
    if isinstance(value, DurationValue):
        return DurationValue(kind="duration", days=max(0, value.days + int(delta)))
    raise ValueError(f"WORLD_GENERATOR_UNSUPPORTED_ALTERNATIVE:{value.kind}")


def _leaf_plans(
    node: AstNode,
    desired: CriterionVerdict,
    slots: dict[str, SlotDefinition],
    evaluation_date: date,
) -> list[Plan]:
    if node.slot_id is None:
        return []
    slot = slots[node.slot_id]
    if node.op is AstOperator.EXISTS:
        return [
            {node.slot_id: _default_value(slot, evaluation_date)}
            if desired is CriterionVerdict.PASS
            else {node.slot_id: None}
        ]
    if node.op is AstOperator.EQ:
        assert node.value is not None
        return [
            {node.slot_id: node.value}
            if desired is CriterionVerdict.PASS
            else {node.slot_id: _alternative_value(node.value, slot)}
        ]
    if node.op is AstOperator.IN:
        if desired is CriterionVerdict.PASS:
            return [{node.slot_id: value} for value in node.values[:8]]
        assert node.values
        return [{node.slot_id: _alternative_value(node.values[0], slot)}]
    if node.op in {AstOperator.GTE, AstOperator.GT, AstOperator.LTE, AstOperator.LT}:
        assert isinstance(node.value, NumberValue)
        threshold = node.value
        if node.op is AstOperator.GTE:
            comparison_values = (
                [
                    threshold,
                    _alternative_value(threshold, slot),
                    _alternative_value(threshold, slot, delta=Decimal(2)),
                ]
                if desired is CriterionVerdict.PASS
                else [
                    _alternative_value(threshold, slot, delta=Decimal(-1)),
                    _alternative_value(threshold, slot, delta=Decimal(-2)),
                ]
            )
        elif node.op is AstOperator.GT:
            comparison_values = (
                [
                    _alternative_value(threshold, slot),
                    _alternative_value(threshold, slot, delta=Decimal(2)),
                    _alternative_value(threshold, slot, delta=Decimal(3)),
                ]
                if desired is CriterionVerdict.PASS
                else [threshold, _alternative_value(threshold, slot, delta=Decimal(-1))]
            )
        elif node.op is AstOperator.LTE:
            comparison_values = (
                [
                    threshold,
                    _alternative_value(threshold, slot, delta=Decimal(-1)),
                    _alternative_value(threshold, slot, delta=Decimal(-2)),
                ]
                if desired is CriterionVerdict.PASS
                else [
                    _alternative_value(threshold, slot),
                    _alternative_value(threshold, slot, delta=Decimal(2)),
                ]
            )
        else:
            comparison_values = (
                [
                    _alternative_value(threshold, slot, delta=Decimal(-1)),
                    _alternative_value(threshold, slot, delta=Decimal(-2)),
                    _alternative_value(threshold, slot, delta=Decimal(-3)),
                ]
                if desired is CriterionVerdict.PASS
                else [threshold, _alternative_value(threshold, slot)]
            )
        return [{node.slot_id: value} for value in comparison_values]
    if node.op is AstOperator.BETWEEN_INCLUSIVE:
        assert isinstance(node.value, RangeValue)
        assert node.value.lower is not None and node.value.upper is not None
        if desired is CriterionVerdict.PASS:
            midpoint = (node.value.lower + node.value.upper) / Decimal(2)
            lower_middle = (node.value.lower + midpoint) / Decimal(2)
            range_values: list[TypedValue] = [
                NumberValue(kind="number", value=node.value.lower, unit=node.value.unit),
                NumberValue(kind="number", value=node.value.upper, unit=node.value.unit),
                NumberValue(kind="number", value=midpoint, unit=node.value.unit),
                NumberValue(kind="number", value=lower_middle, unit=node.value.unit),
            ]
        else:
            range_values = [
                NumberValue(
                    kind="number", value=node.value.lower - Decimal(1), unit=node.value.unit
                ),
                NumberValue(
                    kind="number", value=node.value.upper + Decimal(1), unit=node.value.unit
                ),
            ]
        return [{node.slot_id: value} for value in range_values]
    if node.op is AstOperator.DURATION_AT_LEAST_DAYS:
        assert isinstance(node.value, DurationValue)
        days = (
            [node.value.days, node.value.days + 1, node.value.days + 2]
            if desired is CriterionVerdict.PASS
            else [max(0, node.value.days - 1), max(0, node.value.days - 2)]
        )
        return [
            {node.slot_id: DurationValue(kind="duration", days=value)}
            for value in dict.fromkeys(days)
        ]
    if node.op is AstOperator.IS_A:
        if desired is CriterionVerdict.FAIL or not isinstance(node.value, CategoricalValue):
            return []
        return [{node.slot_id: node.value}]
    if node.op is AstOperator.WITHIN_DAYS:
        assert isinstance(node.value, DurationValue)
        reference = evaluation_date
        reference_kind = node.metadata.get("reference_kind")
        direction = node.metadata.get("direction")
        offset = 0 if desired is CriterionVerdict.PASS else node.value.days + 1
        event = reference - timedelta(days=offset)
        if direction == "AFTER_OR_ON":
            event = reference + timedelta(days=offset)
        plan: Plan = {node.slot_id: DateValue(kind="date", value=event, precision="DAY")}
        if reference_kind == "SLOT":
            reference_slot = str(node.metadata["reference_slot_id"])
            plan[reference_slot] = DateValue(kind="date", value=reference, precision="DAY")
        return [plan]
    if node.op in {AstOperator.BEFORE, AstOperator.AFTER}:
        if isinstance(node.value, DateValue):
            reference = node.value.value
            inclusive = False
        else:
            reference = evaluation_date
            inclusive = bool(node.metadata["inclusive"])
        if node.op is AstOperator.BEFORE:
            event = (
                reference
                if desired is CriterionVerdict.PASS and inclusive
                else reference - timedelta(days=1)
            )
            if desired is CriterionVerdict.FAIL:
                event = reference + timedelta(days=1)
        else:
            event = (
                reference
                if desired is CriterionVerdict.PASS and inclusive
                else reference + timedelta(days=1)
            )
            if desired is CriterionVerdict.FAIL:
                event = reference - timedelta(days=1)
        plan = {node.slot_id: DateValue(kind="date", value=event, precision="DAY")}
        if node.metadata.get("reference_kind") == "SLOT":
            plan[str(node.metadata["reference_slot_id"])] = DateValue(
                kind="date", value=reference, precision="DAY"
            )
        return [plan]
    return []


def _node_plans(
    ast: CriterionAst,
    node_id: str,
    desired: CriterionVerdict,
    slots: dict[str, SlotDefinition],
    evaluation_date: date,
) -> list[Plan]:
    nodes = {node.node_id: node for node in ast.nodes}
    node = nodes[node_id]
    if node.op is AstOperator.OPAQUE:
        return []
    if node.op is AstOperator.NOT:
        inverse = (
            CriterionVerdict.FAIL if desired is CriterionVerdict.PASS else CriterionVerdict.PASS
        )
        return _node_plans(ast, node.child_ids[0], inverse, slots, evaluation_date)
    if node.op is AstOperator.IMPLIES:
        antecedent = _node_plans(
            ast, node.child_ids[0], CriterionVerdict.PASS, slots, evaluation_date
        )
        consequent = _node_plans(ast, node.child_ids[1], desired, slots, evaluation_date)
        return _combine_plan_groups([antecedent, consequent])
    if node.op is AstOperator.ALL:
        if desired is CriterionVerdict.PASS:
            groups = [
                _node_plans(ast, child, CriterionVerdict.PASS, slots, evaluation_date)
                for child in node.child_ids
            ]
            return _combine_plan_groups(groups)
        return _dedupe_plans(
            [
                plan
                for child in node.child_ids
                for plan in _node_plans(ast, child, CriterionVerdict.FAIL, slots, evaluation_date)
            ]
        )
    if node.op is AstOperator.ANY:
        if desired is CriterionVerdict.PASS:
            return _dedupe_plans(
                [
                    plan
                    for child in node.child_ids
                    for plan in _node_plans(
                        ast, child, CriterionVerdict.PASS, slots, evaluation_date
                    )
                ]
            )
        groups = [
            _node_plans(ast, child, CriterionVerdict.FAIL, slots, evaluation_date)
            for child in node.child_ids
        ]
        return _combine_plan_groups(groups)
    return _leaf_plans(node, desired, slots, evaluation_date)


def _facts_from_plan(plan: Plan, world_id: str) -> list[WorldFact]:
    return [
        WorldFact(
            fact_id=f"fact_{hashlib.sha256(f'{world_id}:{slot_id}'.encode()).hexdigest()[:24]}",
            slot_id=slot_id,
            value=value,
        )
        for slot_id, value in sorted(plan.items())
        if value is not None
    ]


def _criterion_matches(
    criterion: CompiledCriterion,
    plan: Plan,
    desired: CriterionVerdict,
    evaluation_date: date,
) -> bool:
    facts = _facts_from_plan(plan, "solver")
    context = eligibility_context_from_world(
        facts, [], evaluation_date=evaluation_date, language="en"
    )
    return evaluate_criterion(criterion, context, evaluation_date).verdict is desired


def _criterion_plans(
    criterion: CompiledCriterion,
    desired: CriterionVerdict,
    slots: dict[str, SlotDefinition],
    evaluation_date: date,
) -> list[Plan]:
    plans = _node_plans(
        criterion.ast,
        criterion.ast.root_node_id,
        desired,
        slots,
        evaluation_date,
    )
    return [
        plan
        for plan in _dedupe_plans(plans)
        if _criterion_matches(criterion, plan, desired, evaluation_date)
    ]


def _solve(
    criteria: list[CompiledCriterion],
    targets: dict[str, CriterionVerdict],
    *,
    evaluation_date: date,
    max_solutions: int = 12,
) -> list[Plan]:
    slots = load_slot_catalog().by_id()
    by_id = {criterion.criterion_id: criterion for criterion in criteria}
    options = {
        criterion_id: _criterion_plans(by_id[criterion_id], desired, slots, evaluation_date)
        for criterion_id, desired in targets.items()
    }
    if any(not item for item in options.values()):
        return []
    order = sorted(targets, key=lambda criterion_id: (len(options[criterion_id]), criterion_id))
    solutions: list[Plan] = []

    def visit(index: int, current: Plan) -> None:
        if len(solutions) >= max_solutions:
            return
        if index == len(order):
            solutions.append(current)
            return
        criterion_id = order[index]
        for option in options[criterion_id]:
            for merged in _merge_two(current, option):
                processed = order[: index + 1]
                if all(
                    _criterion_matches(by_id[item], merged, targets[item], evaluation_date)
                    for item in processed
                ):
                    visit(index + 1, merged)

    visit(0, {})
    return _dedupe_plans(solutions, limit=max_solutions)


def _split(nct_id: str) -> Literal["development", "validation", "test"]:
    bucket = int(hashlib.sha256(nct_id.encode()).hexdigest()[:16], 16) % 100
    if bucket < 60:
        return "development"
    if bucket < 80:
        return "validation"
    return "test"


def _make_world(
    trial: CompiledTrial,
    *,
    world_id: str,
    world_type: Literal[
        "FULL_PASS", "SINGLE_FAIL", "MULTI_FAIL", "UNKNOWN", "CONFLICT", "BOUNDARY"
    ],
    plan: Plan,
    evaluation_date: date,
    conflict_slot: str | None = None,
    unavailable_slots: list[str] | None = None,
) -> PatientWorld:
    facts = _facts_from_plan(plan, world_id)
    conflict_slots: list[str] = []
    if conflict_slot is not None:
        original = next(fact for fact in facts if fact.slot_id == conflict_slot)
        slot = load_slot_catalog().by_id()[conflict_slot]
        alternative = _alternative_value(original.value, slot)
        facts.append(
            WorldFact(
                fact_id=f"fact_{hashlib.sha256(f'{world_id}:{conflict_slot}:conflict'.encode()).hexdigest()[:24]}",
                slot_id=conflict_slot,
                value=alternative,
            )
        )
        facts.sort(key=lambda item: item.fact_id)
        conflict_slots = [conflict_slot]
    context = eligibility_context_from_world(
        facts,
        conflict_slots,
        evaluation_date=evaluation_date,
        language="en",
    )
    truth = [
        CriterionTruth(
            criterion_id=criterion.criterion_id,
            verdict=result.verdict,
            evidence_fact_ids=result.evidence_fact_ids,
            missing_slot_ids=result.missing_slot_ids,
        )
        for criterion in trial.criteria
        for result in [evaluate_criterion(criterion, context, evaluation_date)]
    ]
    narrative, fact_span_map = _render_narrative(world_id, facts)
    return PatientWorld(
        world_id=world_id,
        nct_id=trial.nct_id,
        world_type=world_type,
        split=_split(trial.nct_id),
        evaluation_date=evaluation_date,
        compiled_protocol_hash=trial.content_hash,
        criterion_source_hashes=[item.source_text_sha256 for item in trial.criteria],
        facts=facts,
        conflict_slots=conflict_slots,
        unavailable_slots=unavailable_slots or [],
        template_narrative=narrative,
        narrative=narrative,
        fact_span_map=fact_span_map,
        criterion_truth=truth,
    )


def generate_trial_worlds(
    trial: CompiledTrial,
    *,
    evaluation_date: date,
) -> tuple[list[PatientWorld], dict[str, int]]:
    critical = [
        item
        for item in trial.criteria
        if item.criticality == "CRITICAL" and not item.opaque and item.protocol_verified
    ]
    if not critical:
        raise ValueError(f"DATASET_A_NO_EXECUTABLE_CRITICAL_CRITERIA:{trial.nct_id}")
    pass_targets = {item.criterion_id: CriterionVerdict.PASS for item in critical}
    full = _solve(critical, pass_targets, evaluation_date=evaluation_date)
    if not full:
        raise ValueError(f"DATASET_A_FULL_PASS_WORLD_UNSATISFIABLE:{trial.nct_id}")
    worlds: list[PatientWorld] = []
    coverage: dict[str, int] = {
        name: 0
        for name in ("FULL_PASS", "SINGLE_FAIL", "MULTI_FAIL", "UNKNOWN", "CONFLICT", "BOUNDARY")
    }

    def add(world_type: str, plan: Plan, suffix: str, **kwargs: object) -> None:
        typed_world_type = world_type
        assert typed_world_type in coverage
        worlds.append(
            _make_world(
                trial,
                world_id=f"dataset-a-{trial.nct_id}-{suffix}",
                world_type=typed_world_type,  # type: ignore[arg-type]
                plan=plan,
                evaluation_date=evaluation_date,
                **kwargs,  # type: ignore[arg-type]
            )
        )
        coverage[world_type] += 1

    boundary_plan = (
        full[0]
        if len(full) >= 3
        and any(
            isinstance(value, (NumberValue, DateValue, DurationValue)) for value in full[0].values()
        )
        else None
    )
    passing_plans = full[1:] if boundary_plan is not None else full
    add("FULL_PASS", passing_plans[0], "pass-1")
    if len(passing_plans) > 1:
        add("FULL_PASS", passing_plans[1], "pass-2")

    failed_criteria: list[str] = []
    single_fail_count = 0
    for criterion in critical:
        targets = dict(pass_targets)
        targets[criterion.criterion_id] = CriterionVerdict.FAIL
        solutions = _solve(critical, targets, evaluation_date=evaluation_date, max_solutions=2)
        if solutions:
            failed_criteria.append(criterion.criterion_id)
            for solution in solutions:
                if single_fail_count == 2:
                    break
                single_fail_count += 1
                add("SINGLE_FAIL", solution, f"fail-{single_fail_count}")
        if single_fail_count == 2 and len(failed_criteria) >= 2:
            break
    if len(failed_criteria) >= 2:
        targets = dict(pass_targets)
        for criterion_id in failed_criteria[:2]:
            targets[criterion_id] = CriterionVerdict.FAIL
        solutions = _solve(critical, targets, evaluation_date=evaluation_date, max_solutions=1)
        if solutions:
            add("MULTI_FAIL", solutions[0], "multi-fail")

    unknown_count = 0
    for criterion in critical:
        missing = sorted(set(criterion.required_slots))
        unknown_plan = {slot: value for slot, value in full[0].items() if slot not in missing}
        for unavailable in (missing, []):
            unknown_count += 1
            candidate = _make_world(
                trial,
                world_id=f"dataset-a-{trial.nct_id}-unknown-{unknown_count}",
                world_type="UNKNOWN",
                plan=unknown_plan,
                evaluation_date=evaluation_date,
                unavailable_slots=unavailable,
            )
            result = next(
                item
                for item in candidate.criterion_truth
                if item.criterion_id == criterion.criterion_id
            )
            if result.verdict is CriterionVerdict.UNKNOWN:
                worlds.append(candidate)
                coverage["UNKNOWN"] += 1
            if coverage["UNKNOWN"] == 2:
                break
        if coverage["UNKNOWN"] == 2:
            break

    used_slots = [
        slot for criterion in critical for slot in criterion.required_slots if slot in full[0]
    ]
    for slot_id in sorted(set(used_slots)):
        try:
            candidate = _make_world(
                trial,
                world_id=f"dataset-a-{trial.nct_id}-conflict",
                world_type="CONFLICT",
                plan=full[0],
                evaluation_date=evaluation_date,
                conflict_slot=slot_id,
            )
        except ValueError:
            continue
        if any(item.verdict is CriterionVerdict.CONFLICT for item in candidate.criterion_truth):
            worlds.append(candidate)
            coverage["CONFLICT"] += 1
            break

    if boundary_plan is not None:
        add("BOUNDARY", boundary_plan, "boundary")
    if not 5 <= len(worlds) <= 10:
        raise ValueError(
            f"DATASET_A_WORLD_COVERAGE_OUT_OF_RANGE:{trial.nct_id}:count={len(worlds)}"
        )
    return worlds, coverage


def generate_dataset_a_benchmark(
    compiled_trials: list[CompiledTrial],
    raw_trials: list[RawTrialRecord],
    *,
    seed: int,
    evaluation_date: date,
) -> BenchmarkArtifact:
    if not 24 <= len(compiled_trials) <= 36:
        raise ValueError("DATASET_A_TRIAL_COUNT_MUST_BE_24_TO_36")
    compiled_by_id = {item.nct_id: item for item in compiled_trials}
    raw_by_id = {item.nct_id: item for item in raw_trials}
    if len(compiled_by_id) != len(compiled_trials) or len(raw_by_id) != len(raw_trials):
        raise ValueError("DATASET_A_DUPLICATE_TRIAL_ID")
    if set(compiled_by_id) != set(raw_by_id):
        raise ValueError("DATASET_A_RAW_COMPILED_TRIAL_SET_MISMATCH")
    for nct_id, trial in compiled_by_id.items():
        raw = raw_by_id[nct_id]
        if raw.study_type != "INTERVENTIONAL":
            raise ValueError(f"DATASET_A_NON_INTERVENTIONAL_TRIAL:{nct_id}")
        has_verified_critical = any(
            criterion.criticality == "CRITICAL"
            and criterion.protocol_verified
            and not criterion.opaque
            for criterion in trial.criteria
        )
        if not has_verified_critical or not trial.boundary_tests_passed:
            raise ValueError(f"DATASET_A_UNVERIFIED_COMPILED_TRIAL:{nct_id}")
    worlds: list[PatientWorld] = []
    coverage: dict[str, dict[str, int]] = {}
    for nct_id in sorted(compiled_by_id):
        trial_worlds, trial_coverage = generate_trial_worlds(
            compiled_by_id[nct_id], evaluation_date=evaluation_date
        )
        worlds.extend(trial_worlds)
        coverage[nct_id] = trial_coverage
    observations = _observations(worlds, seed)
    counts = {
        "trials": len(compiled_trials),
        "worlds": len(worlds),
        "observations": len(observations),
        "criterion_labels": sum(len(world.criterion_truth) for world in worlds),
        "manual_reviews": 0,
        "dual_reviews": 0,
        "paraphrased_worlds": 0,
    }
    blocking: list[str] = []
    if counts["worlds"] < 300:
        blocking.append("Dataset A requires at least 300 generated patient-trial worlds.")
    if counts["criterion_labels"] < 1500:
        blocking.append("Dataset A requires at least 1,500 criterion-level labels.")
    target_paraphrases = min(120, round(counts["worlds"] * 0.30))
    if counts["paraphrased_worlds"] != target_paraphrases:
        blocking.append(
            "Fixed-seed Flash-Lite paraphrase validation is pending for "
            f"{target_paraphrases} worlds."
        )
    return BenchmarkArtifact(
        seed=seed,
        scope_status="RELEASE_DATASET_A",
        acceptance_eligible=not blocking,
        blocking_reasons=blocking,
        source_trials=sorted(compiled_by_id),
        worlds=worlds,
        observations=observations,
        counts=counts,
        generation_coverage=coverage,
    )
