from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from backend.app.application.catalog import SlotCatalog
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.model_outputs import CompiledTrialProposal, CriterionCompilationProposal
from backend.app.domain.trials import CompiledCriterion, CompiledTrial, RawTrialRecord
from backend.app.domain.values import NumberValue, RangeValue
from backend.app.engine.ast_validator import AstValidationError, validate_ast_shape
from backend.app.engine.boundary_tests import BoundaryReport, run_boundary_tests
from backend.app.engine.coverage import CoverageReport, calculate_source_coverage


class ProtocolCompilationError(ValueError):
    pass


@dataclass(frozen=True)
class TrustedCompilation:
    compiled_trial: CompiledTrial
    coverage_report: CoverageReport
    boundary_reports: dict[str, BoundaryReport]


def _exact_source_offsets(source: str, *, start: int, end: int, quote: str) -> tuple[int, int]:
    if 0 <= start < end <= len(source) and source[start:end] == quote:
        return start, end
    matches: list[int] = []
    offset = source.find(quote)
    while offset >= 0:
        matches.append(offset)
        offset = source.find(quote, offset + 1)
    escaped_pattern: str | None = None
    if not matches and any(symbol in quote for symbol in "<>"):
        # ClinicalTrials.gov markdown can retain a presentation backslash in
        # the source while a model returns the same visible quote without it.
        # Match only optional escapes before comparison symbols; every other
        # byte remains exact and the final quote is rebound to source bytes.
        escaped_pattern = "".join(
            rf"\\?{re.escape(character)}" if character in "<>" else re.escape(character)
            for character in quote
        )
        matches = [match.start() for match in re.finditer(escaped_pattern, source)]
    if len(matches) != 1:
        raise ProtocolCompilationError("criterion quote is not uniquely anchored in source")
    anchored_start = matches[0]
    if escaped_pattern is None:
        return anchored_start, anchored_start + len(quote)
    escaped_match = re.match(escaped_pattern, source[anchored_start:])
    if escaped_match is None:
        raise ProtocolCompilationError("criterion quote is not uniquely anchored in source")
    return anchored_start, anchored_start + escaped_match.end()


def _anchor_proposal_source_spans(
    source: str, proposal: CompiledTrialProposal
) -> CompiledTrialProposal:
    repeated_assignments: dict[int, int] = {}
    quote_groups: dict[str, list[int]] = {}
    for index, criterion in enumerate(proposal.criteria):
        quote_groups.setdefault(criterion.quote, []).append(index)
    for quote, indexes in quote_groups.items():
        if len(indexes) < 2:
            continue
        matches: list[int] = []
        offset = source.find(quote)
        while offset >= 0:
            matches.append(offset)
            offset = source.find(quote, offset + 1)
        if len(matches) != len(indexes):
            continue
        ordered_indexes = sorted(
            indexes,
            key=lambda index: (
                proposal.criteria[index].start,
                proposal.criteria[index].end,
                index,
            ),
        )
        repeated_assignments.update(zip(ordered_indexes, sorted(matches), strict=True))

    criteria = []
    for index, criterion in enumerate(proposal.criteria):
        if index in repeated_assignments:
            start = repeated_assignments[index]
            end = start + len(criterion.quote)
        else:
            start, end = _exact_source_offsets(
                source,
                start=criterion.start,
                end=criterion.end,
                quote=criterion.quote,
            )
        criteria.append(
            criterion.model_copy(update={"start": start, "end": end, "quote": source[start:end]})
        )
    unassigned = []
    for span in proposal.unassigned_source_spans:
        start, end = _exact_source_offsets(
            source,
            start=span.start,
            end=span.end,
            quote=span.quote,
        )
        unassigned.append(
            span.model_copy(update={"start": start, "end": end, "quote": source[start:end]})
        )
    return proposal.model_copy(update={"criteria": criteria, "unassigned_source_spans": unassigned})


def _canonicalize_ast(criterion_id: str, proposal: CriterionCompilationProposal) -> CriterionAst:
    local_ids = [node.node_id for node in proposal.ast.nodes]
    expected_ids = [f"n{index}" for index in range(len(local_ids))]
    if (
        sorted(local_ids, key=lambda value: int(value[1:]) if value[1:].isdigit() else -1)
        != expected_ids
    ):
        raise ProtocolCompilationError("AST proposal node IDs must be contiguous n0..nK")
    mapping = {local_id: f"{criterion_id}:node:{local_id[1:]}" for local_id in local_ids}
    if proposal.ast.root_node_id not in mapping:
        raise ProtocolCompilationError("AST proposal root is not a local node")
    nodes = [
        AstNode(
            **node.model_dump(exclude={"node_id", "child_ids"}),
            node_id=mapping[node.node_id],
            child_ids=[mapping[child_id] for child_id in node.child_ids],
        )
        for node in sorted(proposal.ast.nodes, key=lambda item: int(item.node_id[1:]))
    ]
    return CriterionAst(root_node_id=mapping[proposal.ast.root_node_id], nodes=nodes)


def _normalize_single_allowed_numeric_unit(
    ast: CriterionAst, slot_catalog: SlotCatalog
) -> CriterionAst:
    slots = slot_catalog.by_id()
    normalized_nodes: list[AstNode] = []
    for node in ast.nodes:
        slot = slots.get(node.slot_id or "")
        if slot is None or slot.value_type != "number" or len(slot.allowed_units) != 1:
            normalized_nodes.append(node)
            continue
        canonical_unit = slot.allowed_units[0]
        value = node.value
        node_unit = node.unit
        if isinstance(value, (NumberValue, RangeValue)):
            if value.unit is None and node_unit in {None, canonical_unit}:
                value = value.model_copy(update={"unit": canonical_unit})
            if node_unit == canonical_unit and value.unit == canonical_unit:
                node_unit = None
        values = [
            item.model_copy(update={"unit": canonical_unit})
            if isinstance(item, NumberValue) and item.unit is None
            else item
            for item in node.values
        ]
        normalized_nodes.append(
            node.model_copy(update={"value": value, "values": values, "unit": node_unit})
        )
    return ast.model_copy(update={"nodes": normalized_nodes})


def _normalize_opaque_metadata(ast: CriterionAst, source_quote: str) -> CriterionAst:
    residual_hash = hashlib.sha256(source_quote.encode()).hexdigest()
    nodes = [
        node.model_copy(
            update={
                "slot_id": None,
                "value": None,
                "values": [],
                "child_ids": [],
                "unit": None,
                "metadata": {
                    "reason_code": "UNSUPPORTED_SOURCE_SEMANTICS",
                    "residual_source_sha256": residual_hash,
                },
            }
        )
        if node.op is AstOperator.OPAQUE
        else node
        for node in ast.nodes
    ]
    return ast.model_copy(update={"nodes": nodes})


def _criterion_opaque_ast(criterion_id: str, source_quote: str) -> CriterionAst:
    source_hash = hashlib.sha256(source_quote.encode()).hexdigest()
    node_id = f"{criterion_id}:node:0"
    return CriterionAst(
        root_node_id=node_id,
        nodes=[
            AstNode(
                node_id=node_id,
                op=AstOperator.OPAQUE,
                metadata={
                    "reason_code": "AST_TRUSTED_VALIDATION_FAILED",
                    "residual_source_sha256": source_hash,
                },
            )
        ],
    )


def _required_slots(ast: CriterionAst) -> list[str]:
    result = {node.slot_id for node in ast.nodes if node.slot_id is not None}
    result.update(
        str(node.metadata["reference_slot_id"])
        for node in ast.nodes
        if "reference_slot_id" in node.metadata
    )
    return sorted(result)


def construct_trusted_compilation(
    *,
    trial: RawTrialRecord,
    proposal: CompiledTrialProposal,
    slot_catalog: SlotCatalog,
    compiler_model_id: str,
    compiler_prompt_version: str,
    created_at: datetime,
    evaluation_date: date,
) -> TrustedCompilation:
    source = trial.eligibility_criteria
    if source is None or not source.strip():
        raise ProtocolCompilationError("trial has no eligibility source text")
    if proposal.nct_id != trial.nct_id:
        raise ProtocolCompilationError("proposal NCT ID does not match source trial")
    proposal = _anchor_proposal_source_spans(source, proposal)
    seen_order: set[tuple[str, int]] = set()
    occupied: set[int] = set()
    compiled_criteria: list[CompiledCriterion] = []
    for item in proposal.criteria:
        if source[item.start : item.end] != item.quote:
            raise ProtocolCompilationError("criterion span does not match eligibility source")
        order_key = (item.source_direction.value, item.source_order)
        if order_key in seen_order:
            raise ProtocolCompilationError("criterion source order must be unique per direction")
        seen_order.add(order_key)
        indexes = set(range(item.start, item.end))
        if occupied & indexes:
            raise ProtocolCompilationError(
                "overlapping criterion spans require unsupported hierarchy metadata"
            )
        occupied.update(indexes)
        source_hash = hashlib.sha256(item.quote.encode()).hexdigest()
        criterion_id = (
            f"{trial.nct_id}:{item.source_direction.value}:{item.source_order:03d}:"
            f"{source_hash[:8]}"
        )
        normalization_warnings: list[str] = []
        try:
            ast = _canonicalize_ast(criterion_id, item)
            ast = _normalize_single_allowed_numeric_unit(ast, slot_catalog)
            ast = _normalize_opaque_metadata(ast, item.quote)
            validate_ast_shape(ast, slot_catalog.by_id())
        except (KeyError, ValueError, AstValidationError, ProtocolCompilationError):
            ast = _criterion_opaque_ast(criterion_id, item.quote)
            normalization_warnings.append("AST_TRUSTED_VALIDATION_OPAQUE_FALLBACK")
        required_slots = _required_slots(ast)
        contains_opaque = any(node.op is AstOperator.OPAQUE for node in ast.nodes)
        criticality = "CRITICAL" if contains_opaque else item.criticality
        if sorted(set(item.required_slots)) != required_slots:
            normalization_warnings.append("MODEL_REQUIRED_SLOTS_NORMALIZED")
        if item.opaque != contains_opaque:
            normalization_warnings.append("MODEL_OPAQUE_FLAG_NORMALIZED")
        if contains_opaque and item.criticality != "CRITICAL":
            normalization_warnings.append("OPAQUE_CRITICALITY_NORMALIZED")
        compiled_criteria.append(
            CompiledCriterion(
                criterion_id=criterion_id,
                nct_id=trial.nct_id,
                source_direction=item.source_direction,
                source_order=item.source_order,
                source_span={
                    "source_id": f"ctgov:{trial.nct_id}:eligibility_criteria",
                    "start": item.start,
                    "end": item.end,
                    "quote": item.quote,
                    "sha256": source_hash,
                    "language": "en",
                },
                source_text_sha256=source_hash,
                normalized_summary=item.normalized_summary,
                ast=ast,
                required_slots=required_slots,
                criticality=criticality,
                compiler_confidence=item.compiler_confidence,
                protocol_verified=False,
                opaque=contains_opaque,
                warnings=sorted(set([*item.warnings, *normalization_warnings])),
            )
        )

    for span in proposal.unassigned_source_spans:
        if source[span.start : span.end] != span.quote:
            raise ProtocolCompilationError("unassigned span does not match eligibility source")
    coverage = calculate_source_coverage(source, proposal.criteria)
    boundary_reports = {
        criterion.criterion_id: run_boundary_tests(criterion, evaluation_date)
        for criterion in compiled_criteria
    }
    boundary_tests_passed = all(report.passed for report in boundary_reports.values())
    eligibility_hash = hashlib.sha256(source.encode()).hexdigest()
    draft = CompiledTrial(
        compiled_trial_id=f"compiled:{trial.nct_id}:{eligibility_hash[:16]}",
        nct_id=trial.nct_id,
        eligibility_text_sha256=eligibility_hash,
        criteria=compiled_criteria,
        source_character_coverage=coverage.ratio,
        protocol_verified=False,
        review_artifact_id=None,
        compiler_model_id=compiler_model_id,
        compiler_prompt_version=compiler_prompt_version,
        ast_schema_version="criterion-ast-v1",
        slot_catalog_version=slot_catalog.version,
        boundary_tests_passed=boundary_tests_passed,
        warnings=proposal.compiler_warnings,
        content_hash="0" * 64,
        created_at=created_at.astimezone(UTC),
    )
    content_hash = canonical_sha256(draft.model_dump(mode="json", exclude={"content_hash"}))
    return TrustedCompilation(
        compiled_trial=draft.model_copy(update={"content_hash": content_hash}),
        coverage_report=coverage,
        boundary_reports=boundary_reports,
    )


def opaque_fallback_compilation(
    *,
    trial: RawTrialRecord,
    slot_catalog: SlotCatalog,
    created_at: datetime,
    evaluation_date: date,
    reason_code: str,
) -> TrustedCompilation:
    source = trial.eligibility_criteria or "Eligibility criteria unavailable in registry source."
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    proposal = CompiledTrialProposal(
        nct_id=trial.nct_id,
        criteria=[
            CriterionCompilationProposal(
                source_direction="REGISTRY_FIELD",
                source_order=1,
                start=0,
                end=len(source),
                quote=source,
                normalized_summary="Eligibility source retained for qualified human review.",
                ast=CriterionAst(
                    root_node_id="n0",
                    nodes=[
                        AstNode(
                            node_id="n0",
                            op=AstOperator.OPAQUE,
                            metadata={
                                "reason_code": reason_code,
                                "residual_source_sha256": source_hash,
                            },
                        )
                    ],
                ),
                required_slots=[],
                compiler_confidence=0,
                opaque=True,
                warnings=["MODEL_OUTPUT_UNAVAILABLE_OR_UNVERIFIED"],
            )
        ],
        unassigned_source_spans=[],
        compiler_warnings=[reason_code],
    )
    if trial.eligibility_criteria is None:
        trial = trial.model_copy(update={"eligibility_criteria": source})
    return construct_trusted_compilation(
        trial=trial,
        proposal=proposal,
        slot_catalog=slot_catalog,
        compiler_model_id="deterministic-opaque-fallback",
        compiler_prompt_version="protocol_compiler-v1-fallback",
        created_at=created_at,
        evaluation_date=evaluation_date,
    )
