from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime

from backend.app.application.catalog import SlotCatalog
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.model_outputs import CompiledTrialProposal, CriterionCompilationProposal
from backend.app.domain.trials import CompiledCriterion, CompiledTrial, RawTrialRecord
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
    seen_order: set[tuple[str, int]] = set()
    occupied: set[int] = set()
    compiled_criteria: list[CompiledCriterion] = []
    for item in proposal.criteria:
        if (
            not 0 <= item.start < item.end <= len(source)
            or source[item.start : item.end] != item.quote
        ):
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
        try:
            ast = _canonicalize_ast(criterion_id, item)
            validate_ast_shape(ast, slot_catalog.by_id())
        except ProtocolCompilationError:
            raise
        except (KeyError, ValueError, AstValidationError) as error:
            raise ProtocolCompilationError("criterion AST failed trusted validation") from error
        required_slots = _required_slots(ast)
        if sorted(set(item.required_slots)) != required_slots:
            raise ProtocolCompilationError("required_slots must equal AST and reference slots")
        contains_opaque = any(node.op is AstOperator.OPAQUE for node in ast.nodes)
        if item.opaque != contains_opaque:
            raise ProtocolCompilationError("opaque flag must match AST OPAQUE nodes")
        compiled_criteria.append(
            CompiledCriterion(
                criterion_id=criterion_id,
                nct_id=trial.nct_id,
                source_direction=item.source_direction,
                source_order=item.source_order,
                source_span={
                    "source_id": f"ctgov:{trial.nct_id}:{trial.source_json_sha256}",
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
                criticality=item.criticality,
                compiler_confidence=item.compiler_confidence,
                protocol_verified=False,
                opaque=item.opaque,
                warnings=item.warnings,
            )
        )

    for span in proposal.unassigned_source_spans:
        if (
            not 0 <= span.start < span.end <= len(source)
            or source[span.start : span.end] != span.quote
        ):
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
