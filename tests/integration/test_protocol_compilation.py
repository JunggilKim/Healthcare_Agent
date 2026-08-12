from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from backend.app.agents.protocol_compiler import (
    ProtocolCompilationError,
    construct_trusted_compilation,
)
from backend.app.agents.protocol_reviewer import bind_semantic_review
from backend.app.application.catalog import load_slot_catalog
from backend.app.application.compilation_service import ProtocolCompilationService
from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.evidence import EligibilityContext
from backend.app.domain.model_outputs import (
    CompiledTrialProposal,
    ProtocolReviewProposal,
)
from backend.app.domain.trials import CompiledTrial
from backend.app.engine.boundary_tests import run_boundary_tests
from backend.app.engine.evaluator import evaluate_criterion
from backend.app.infrastructure.structured_generation import StructuredGenerationUnavailable


def _trial_and_proposal(source: str = "Patients must be at least 18 years of age."):
    raw = load_vertical_slice().raw_trial.model_copy(
        update={
            "eligibility_criteria": source,
            "source_json_sha256": hashlib.sha256(source.encode()).hexdigest(),
        }
    )
    proposal = CompiledTrialProposal.model_validate(
        {
            "nct_id": raw.nct_id,
            "criteria": [
                {
                    "source_direction": "INCLUSION",
                    "source_order": 1,
                    "start": 0,
                    "end": len(source),
                    "quote": source,
                    "normalized_summary": "age is at least 18 years",
                    "ast": {
                        "root_node_id": "n0",
                        "nodes": [
                            {
                                "node_id": "n0",
                                "op": "GTE",
                                "slot_id": "demographics.age",
                                "value": {
                                    "kind": "number",
                                    "value": "18",
                                    "unit": "year",
                                },
                            }
                        ],
                    },
                    "required_slots": ["demographics.age"],
                    "compiler_confidence": 0.99,
                    "opaque": False,
                }
            ],
        }
    )
    return raw, proposal


def test_trusted_compiler_rewrites_local_ids_runs_coverage_and_boundaries() -> None:
    raw, proposal = _trial_and_proposal()
    result = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.0",
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        evaluation_date=date(2026, 8, 11),
    )
    criterion = result.compiled_trial.criteria[0]
    assert criterion.ast.root_node_id == f"{criterion.criterion_id}:node:0"
    assert result.coverage_report.ratio == 1.0
    assert result.boundary_reports[criterion.criterion_id].passed is True
    assert len(result.boundary_reports[criterion.criterion_id].cases) == 5
    assert result.compiled_trial.boundary_tests_passed is True
    assert result.compiled_trial.protocol_verified is False
    assert result.compiled_trial.content_hash == canonical_sha256(
        result.compiled_trial.model_dump(mode="json", exclude={"content_hash"})
    )


def test_semantic_review_is_hash_bound_and_double_lite_fallback_cannot_verify() -> None:
    raw, proposal = _trial_and_proposal()
    compilation = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.0",
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        evaluation_date=date(2026, 8, 11),
    )
    approved = bind_semantic_review(
        compiled_trial=compilation.compiled_trial,
        proposal=ProtocolReviewProposal(approved=True),
        reviewer_model_id="gemini-3.6-flash",
        reviewer_prompt_version="1.0.0",
        reviewed_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert approved.compiled_trial.protocol_verified is True
    assert approved.review_artifact.compiled_protocol_hash == approved.compiled_trial.content_hash
    assert approved.review_artifact.content_hash == canonical_sha256(
        approved.review_artifact.model_dump(mode="json", exclude={"content_hash"})
    )

    blocked = bind_semantic_review(
        compiled_trial=compilation.compiled_trial,
        proposal=ProtocolReviewProposal(approved=True),
        reviewer_model_id="gemini-3.5-flash-lite",
        reviewer_prompt_version="1.0.0",
        reviewed_at=datetime(2026, 8, 11, tzinfo=UTC),
        compiler_used_fallback=True,
        reviewer_used_fallback=True,
    )
    assert blocked.compiled_trial.protocol_verified is False
    assert blocked.review_artifact.approved is False


def test_approved_review_verifies_executable_subset_but_not_opaque_trial() -> None:
    raw, proposal = _trial_and_proposal()
    opaque = proposal.criteria[0].model_copy(deep=True)
    opaque.source_order = 2
    opaque.ast.nodes[0].slot_id = "unsupported.slot"
    opaque.start = 0
    opaque.end = len(raw.eligibility_criteria or "")
    opaque.quote = raw.eligibility_criteria or ""
    proposal.criteria = [opaque]
    compilation = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    approved = bind_semantic_review(
        compiled_trial=compilation.compiled_trial,
        proposal=ProtocolReviewProposal(approved=True),
        reviewer_model_id="gemini-3.6-flash",
        reviewer_prompt_version="1.0.2",
        reviewed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    assert approved.review_artifact.approved is True
    assert approved.compiled_trial.protocol_verified is False
    assert approved.compiled_trial.criteria[0].protocol_verified is False


def test_noncontiguous_model_ast_ids_become_source_bound_opaque() -> None:
    raw, proposal = _trial_and_proposal()
    proposal.criteria[0].ast.nodes[0].node_id = "n1"
    proposal.criteria[0].ast.root_node_id = "n1"
    result = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    assert result.compiled_trial.criteria[0].opaque is True


def test_unique_exact_quote_is_deterministically_reanchored() -> None:
    raw, proposal = _trial_and_proposal()
    criterion_text = raw.eligibility_criteria
    assert criterion_text is not None
    prefix = "Eligibility Criteria:\n"
    raw = raw.model_copy(
        update={
            "eligibility_criteria": prefix + criterion_text,
            "source_json_sha256": hashlib.sha256((prefix + criterion_text).encode()).hexdigest(),
        }
    )
    result = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    span = result.compiled_trial.criteria[0].source_span
    assert span.source_id == f"ctgov:{raw.nct_id}:eligibility_criteria"
    assert span.start == len(prefix)
    assert span.end == len(prefix) + len(criterion_text)
    assert raw.eligibility_criteria[span.start : span.end] == span.quote


def test_ambiguous_quote_cannot_be_reanchored() -> None:
    raw, proposal = _trial_and_proposal()
    criterion_text = raw.eligibility_criteria
    assert criterion_text is not None
    raw = raw.model_copy(update={"eligibility_criteria": f"X{criterion_text} {criterion_text}"})
    with pytest.raises(ProtocolCompilationError, match="not uniquely anchored"):
        construct_trusted_compilation(
            trial=raw,
            proposal=proposal,
            slot_catalog=load_slot_catalog(),
            compiler_model_id="gemini-3.6-flash",
            compiler_prompt_version="1.0.3",
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            evaluation_date=date(2026, 8, 12),
        )


def test_missing_numeric_unit_uses_slot_single_canonical_unit() -> None:
    raw, proposal = _trial_and_proposal()
    proposal.criteria[0].ast.nodes[0].value.unit = None
    result = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    value = result.compiled_trial.criteria[0].ast.nodes[0].value
    assert value is not None
    assert value.unit == "year"


def test_numeric_node_unit_is_moved_to_typed_value() -> None:
    raw, proposal = _trial_and_proposal()
    node = proposal.criteria[0].ast.nodes[0]
    node.value.unit = None
    node.unit = "year"
    result = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    normalized = result.compiled_trial.criteria[0].ast.nodes[0]
    assert normalized.unit is None
    assert normalized.value is not None and normalized.value.unit == "year"


def test_invalid_criterion_ast_becomes_source_bound_opaque_only() -> None:
    raw, proposal = _trial_and_proposal()
    proposal.criteria[0].ast.nodes[0].slot_id = "unsupported.slot"
    proposal.criteria[0].opaque = False
    result = construct_trusted_compilation(
        trial=raw,
        proposal=proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    criterion = result.compiled_trial.criteria[0]
    node = criterion.ast.nodes[0]
    assert criterion.opaque is True
    assert criterion.required_slots == []
    assert node.op.value == "OPAQUE"
    assert node.metadata["residual_source_sha256"] == criterion.source_text_sha256
    assert "AST_TRUSTED_VALIDATION_OPAQUE_FALLBACK" in criterion.warnings


@pytest.mark.asyncio
async def test_model_schema_failure_becomes_opaque_unknown_without_hard_verdict() -> None:
    class FailingGenerator:
        async def generate_primary_with_lite_fallback(self, **_kwargs):
            raise StructuredGenerationUnavailable("recorded schema failure")

    raw, _ = _trial_and_proposal()
    service = ProtocolCompilationService(FailingGenerator(), load_slot_catalog())
    result = await service.compile_and_review(
        trial=raw,
        evaluation_date=date(2026, 8, 11),
        now=datetime(2026, 8, 11, tzinfo=UTC),
    )
    compiled = result.compilation.compiled_trial
    assert compiled.protocol_verified is False
    assert compiled.criteria[0].opaque is True
    evaluation = evaluate_criterion(
        compiled.criteria[0],
        EligibilityContext(facts=[], conflicts=[]),
        date(2026, 8, 11),
    )
    assert evaluation.verdict.value == "UNKNOWN"
    assert evaluation.requires_review is True


@pytest.mark.asyncio
async def test_rejected_review_gets_exactly_one_repair_then_becomes_opaque() -> None:
    raw, compiler_proposal = _trial_and_proposal()

    class RejectingReviewGenerator:
        def __init__(self) -> None:
            self.calls = 0
            self.arguments: list[dict[str, object]] = []

        async def generate_primary_with_lite_fallback(self, **kwargs):
            self.calls += 1
            self.arguments.append(kwargs)
            if kwargs["output_model"] is CompiledTrialProposal:
                return compiler_proposal.model_copy(deep=True), SimpleNamespace(used_fallback=False)
            return ProtocolReviewProposal(approved=False), SimpleNamespace(used_fallback=False)

    generator = RejectingReviewGenerator()
    service = ProtocolCompilationService(generator, load_slot_catalog())
    result = await service.compile_and_review(
        trial=raw,
        evaluation_date=date(2026, 8, 11),
        now=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert generator.calls == 4
    assert result.repair_attempted is True
    assert result.review_artifact is None
    assert result.compilation.compiled_trial.criteria[0].opaque is True
    assert result.degradation_codes == ["PROTOCOL_REVIEW_OPAQUE_AFTER_SINGLE_REPAIR"]
    initial_compile = generator.arguments[0]
    repair_compile = generator.arguments[2]
    reviewer_call = generator.arguments[1]
    assert initial_compile["prompt_version"] == "1.0.3"
    assert initial_compile["primary_thinking_level"] is None
    assert initial_compile["fallback_thinking_level"] is None
    assert initial_compile["primary_thinking_budget"] == 1024
    assert initial_compile["fallback_thinking_budget"] == 1024
    assert initial_compile["primary_max_output_tokens"] == 4000
    assert repair_compile["primary_thinking_budget"] == 1024
    assert repair_compile["primary_max_output_tokens"] == 2500
    assert reviewer_call["prompt_version"] == "1.0.2"
    reviewer_input = reviewer_call["normalized_input"]
    assert isinstance(reviewer_input, dict)
    assert set(reviewer_input) == {"nct_id", "criteria"}
    assert set(reviewer_input["criteria"][0]) == {"criterion_id", "source_quote", "ast"}


@pytest.mark.asyncio
async def test_offline_reviewer_chunks_merge_without_changing_live_default() -> None:
    raw, compiler_proposal = _trial_and_proposal()
    source = raw.eligibility_criteria or ""
    raw = raw.model_copy(update={"eligibility_criteria": f"{source}\n{source}"})
    second = compiler_proposal.criteria[0].model_copy(deep=True)
    second.source_order = 2
    second.start = len(source) + 1
    second.end = len(source) + 1 + len(source)
    compiler_proposal.criteria.append(second)

    class ChunkGenerator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def generate_primary_with_lite_fallback(self, **kwargs):
            self.calls.append(kwargs)
            return ProtocolReviewProposal(approved=True), SimpleNamespace(used_fallback=False)

    generator = ChunkGenerator()
    service = ProtocolCompilationService(
        generator,
        load_slot_catalog(),
        offline_reviewer_chunk_size=1,
    )
    compilation = construct_trusted_compilation(
        trial=raw,
        proposal=compiler_proposal,
        slot_catalog=load_slot_catalog(),
        compiler_model_id="gemini-3.6-flash",
        compiler_prompt_version="1.0.3",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        evaluation_date=date(2026, 8, 12),
    )
    review, used_fallback = await service._review(compilation)
    assert review.approved is True
    assert used_fallback is False
    assert [call["task_name"] for call in generator.calls] == [
        "protocol_reviewer_chunk_000",
        "protocol_reviewer_chunk_001",
    ]
    assert all(len(call["normalized_input"]["criteria"]) == 1 for call in generator.calls)


def test_all_phase3_top8_cache_entries_are_hash_bound_opaque_and_loadable() -> None:
    root = Path("data/fixtures/compiled/S004")
    manifest = orjson.loads((root / "manifest.json").read_bytes())
    assert len(manifest["entries"]) == 8
    for entry in manifest["entries"]:
        compiled_bytes = (root / entry["compiled_path"]).read_bytes()
        report_bytes = (root / entry["report_path"]).read_bytes()
        assert hashlib.sha256(compiled_bytes).hexdigest() == entry["compiled_sha256"]
        assert hashlib.sha256(report_bytes).hexdigest() == entry["report_sha256"]
        compiled = CompiledTrial.model_validate_json(compiled_bytes)
        assert compiled.protocol_verified is False
        assert compiled.source_character_coverage == 1.0
        assert all(criterion.opaque for criterion in compiled.criteria)
        report = orjson.loads(report_bytes)
        assert report["hard_verdict_allowed"] is False


def test_frozen_executable_criteria_boundary_suites_pass() -> None:
    fixture = load_vertical_slice()
    reports = [
        run_boundary_tests(criterion, date(2026, 8, 11))
        for criterion in fixture.compiled_trial.criteria
    ]
    assert all(report.passed for report in reports)
    assert sum(len(report.cases) for report in reports) >= 15
