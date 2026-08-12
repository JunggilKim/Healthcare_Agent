from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

from backend.app.agents.prompts import render_prompt
from backend.app.agents.protocol_compiler import (
    ProtocolCompilationError,
    TrustedCompilation,
    _anchor_proposal_source_spans,
    construct_trusted_compilation,
    opaque_fallback_compilation,
)
from backend.app.agents.protocol_reviewer import ReviewedCompilation, bind_semantic_review
from backend.app.application.catalog import SlotCatalog
from backend.app.domain.ast import AstNode, AstOperator, CriterionAst
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.enums import SourceDirection
from backend.app.domain.model_outputs import (
    CompiledTrialProposal,
    CriterionCompilationProposal,
    ProtocolReviewProposal,
)
from backend.app.domain.trials import ProtocolReviewArtifact, RawTrialRecord
from backend.app.infrastructure.structured_generation import (
    StructuredGenerationUnavailable,
    StructuredGenerator,
)


@dataclass(frozen=True)
class CompilationWorkflowResult:
    compilation: TrustedCompilation
    review_artifact: ProtocolReviewArtifact | None
    review_attempts: list[ProtocolReviewProposal]
    repair_attempted: bool
    degradation_codes: list[str]


@dataclass(frozen=True)
class _OfflineSourceItem:
    start: int
    end: int
    direction: SourceDirection


_HEADING = re.compile(r"^\s*(inclusion|exclusion)\s+criteria\s*:?\s*$", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*•°]|\d+[.)])\s+")


class ProtocolCompilationService:
    def __init__(
        self,
        generator: StructuredGenerator,
        slot_catalog: SlotCatalog,
        *,
        offline_reviewer_chunk_size: int | None = None,
        offline_compiler_chunk_size: int | None = None,
    ) -> None:
        if offline_reviewer_chunk_size is not None and offline_reviewer_chunk_size < 1:
            raise ValueError("offline_reviewer_chunk_size must be positive")
        if offline_compiler_chunk_size is not None and offline_compiler_chunk_size < 1:
            raise ValueError("offline_compiler_chunk_size must be positive")
        self.generator = generator
        self.slot_catalog = slot_catalog
        self.offline_reviewer_chunk_size = offline_reviewer_chunk_size
        self.offline_compiler_chunk_size = offline_compiler_chunk_size

    def _compiler_prompt(
        self,
        trial: RawTrialRecord,
        repair_issues: object | None = None,
        source_direction_hint: SourceDirection | None = None,
    ) -> str:
        payload = {
            "nct_id": trial.nct_id,
            "eligibility_criteria": trial.eligibility_criteria,
            "sex": trial.sex,
            "minimum_age": trial.minimum_age,
            "maximum_age": trial.maximum_age,
            "healthy_volunteers": trial.healthy_volunteers,
            "study_type": trial.study_type,
            "overall_status": trial.overall_status,
            "conditions": trial.conditions,
            "repair_issues": repair_issues,
            "source_direction_hint": (
                source_direction_hint.value if source_direction_hint is not None else None
            ),
        }
        operators = (
            "ALL ANY NOT IMPLIES EXISTS EQ IN GTE GT LTE LT BETWEEN_INCLUSIVE "
            "WITHIN_DAYS BEFORE AFTER DURATION_AT_LEAST_DAYS IS_A OPAQUE"
        )
        return render_prompt(
            "protocol_compiler_v1.md",
            slot_catalog=canonical_json_bytes(self.slot_catalog).decode(),
            operator_definitions=operators,
            trial_payload=canonical_json_bytes(payload).decode(),
        )

    async def _generate_compilation(
        self,
        trial: RawTrialRecord,
        *,
        repair_issues: object | None = None,
        source_direction_hint: SourceDirection | None = None,
        session_id: str = "unscoped",
    ) -> tuple[CompiledTrialProposal, bool]:
        prompt = self._compiler_prompt(trial, repair_issues, source_direction_hint)
        proposal, record = await self.generator.generate_primary_with_lite_fallback(
            primary_model_id="gemini-3.6-flash",
            lite_model_id="gemini-3.5-flash-lite",
            task_name="protocol_compiler_repair" if repair_issues else "protocol_compiler",
            prompt=prompt,
            prompt_version="1.0.4",
            output_schema_version="compiled-trial-proposal-v1",
            slot_catalog_version=self.slot_catalog.version,
            normalized_input={
                "trial": trial.model_dump(mode="json"),
                "repair_issues": repair_issues,
                "source_direction_hint": (
                    source_direction_hint.value if source_direction_hint is not None else None
                ),
            },
            output_model=CompiledTrialProposal,
            primary_thinking_level=None,
            fallback_thinking_level=None,
            primary_max_output_tokens=4000 if repair_issues is None else 2500,
            fallback_max_output_tokens=2500,
            primary_thinking_budget=1024,
            fallback_thinking_budget=1024,
            session_id=session_id,
        )
        return proposal, record.used_fallback

    @staticmethod
    def _offline_source_items(source: str) -> list[_OfflineSourceItem]:
        items: list[_OfflineSourceItem] = []
        direction = SourceDirection.REGISTRY_FIELD
        current_start: int | None = None
        current_direction = direction
        offset = 0
        for line in source.splitlines(keepends=True):
            content = line.rstrip("\r\n")
            heading = _HEADING.fullmatch(content)
            if heading is not None:
                if current_start is not None and current_start < offset:
                    items.append(_OfflineSourceItem(current_start, offset, current_direction))
                direction = (
                    SourceDirection.INCLUSION
                    if heading.group(1).lower() == "inclusion"
                    else SourceDirection.EXCLUSION
                )
                current_start = None
            elif content.strip():
                if _LIST_MARKER.match(content) and current_start is not None:
                    items.append(_OfflineSourceItem(current_start, offset, current_direction))
                    current_start = offset
                    current_direction = direction
                elif current_start is None:
                    current_start = offset
                    current_direction = direction
            offset += len(line)
        if current_start is not None and current_start < len(source):
            items.append(_OfflineSourceItem(current_start, len(source), current_direction))
        return [item for item in items if source[item.start : item.end].strip()]

    @staticmethod
    def _opaque_item_proposal(
        trial: RawTrialRecord, item: _OfflineSourceItem, source_order: int
    ) -> CriterionCompilationProposal:
        source = trial.eligibility_criteria or ""
        quote = source[item.start : item.end]
        residual_hash = hashlib.sha256(quote.encode()).hexdigest()
        return CriterionCompilationProposal(
            source_direction=item.direction,
            source_order=source_order,
            start=item.start,
            end=item.end,
            quote=quote,
            normalized_summary="Source criterion requires qualified review.",
            ast=CriterionAst(
                root_node_id="n0",
                nodes=[
                    AstNode(
                        node_id="n0",
                        op=AstOperator.OPAQUE,
                        metadata={
                            "reason_code": "OFFLINE_CHUNK_COMPILATION_FAILED",
                            "residual_source_sha256": residual_hash,
                        },
                    )
                ],
            ),
            required_slots=[],
            compiler_confidence=0,
            opaque=True,
            warnings=["OFFLINE_CHUNK_COMPILATION_OPAQUE_FALLBACK"],
        )

    async def _generate_offline_compilation(
        self,
        trial: RawTrialRecord,
        *,
        repair_issues: list[dict[str, object]] | None = None,
        session_id: str,
    ) -> tuple[CompiledTrialProposal, bool]:
        source = trial.eligibility_criteria or ""
        items = self._offline_source_items(source)
        if not items:
            raise ProtocolCompilationError("offline compiler found no source items")
        assert self.offline_compiler_chunk_size is not None
        groups: list[list[_OfflineSourceItem]] = []
        for item in items:
            if (
                not groups
                or groups[-1][-1].direction != item.direction
                or len(groups[-1]) >= self.offline_compiler_chunk_size
            ):
                groups.append([])
            groups[-1].append(item)

        merged: list[CriterionCompilationProposal] = []
        compiler_warnings: list[str] = []
        used_fallback = False
        for chunk_index, group in enumerate(groups):
            chunk_start = group[0].start
            chunk_end = group[-1].end
            chunk_text = source[chunk_start:chunk_end]
            relevant_issues = [
                issue
                for issue in repair_issues or []
                if isinstance(issue.get("source_quote"), str)
                and str(issue["source_quote"]) in chunk_text
            ]
            chunk_trial = trial.model_copy(update={"eligibility_criteria": chunk_text})
            try:
                proposal, chunk_fallback = await self._generate_compilation(
                    chunk_trial,
                    repair_issues=(relevant_issues if repair_issues is not None else None),
                    source_direction_hint=group[0].direction,
                    session_id=f"{session_id}:compiler-chunk:{chunk_index:03d}",
                )
                proposal = _anchor_proposal_source_spans(chunk_text, proposal)
                for criterion in proposal.criteria:
                    merged.append(
                        criterion.model_copy(
                            update={
                                "source_direction": group[0].direction,
                                "start": criterion.start + chunk_start,
                                "end": criterion.end + chunk_start,
                            }
                        )
                    )
                for span in proposal.unassigned_source_spans:
                    compiler_warnings.append(
                        f"UNASSIGNED_CHUNK_SPAN:{chunk_index}:{span.reason_code}"
                    )
                compiler_warnings.extend(proposal.compiler_warnings)
                used_fallback = used_fallback or chunk_fallback
            except (StructuredGenerationUnavailable, ProtocolCompilationError, ValueError):
                merged.extend(
                    self._opaque_item_proposal(trial, item, source_order=1) for item in group
                )
                compiler_warnings.append(f"OFFLINE_CHUNK_OPAQUE_FALLBACK:{chunk_index:03d}")

        ordered: list[CriterionCompilationProposal] = []
        direction_orders: dict[SourceDirection, int] = {}
        for criterion in sorted(merged, key=lambda item: (item.start, item.end)):
            next_order = direction_orders.get(criterion.source_direction, 0) + 1
            direction_orders[criterion.source_direction] = next_order
            ordered.append(criterion.model_copy(update={"source_order": next_order}))
        return (
            CompiledTrialProposal(
                nct_id=trial.nct_id,
                criteria=ordered,
                compiler_warnings=sorted(set(compiler_warnings)),
            ),
            used_fallback,
        )

    async def _compile_proposal(
        self,
        trial: RawTrialRecord,
        *,
        repair_issues: list[dict[str, object]] | None = None,
        session_id: str,
    ) -> tuple[CompiledTrialProposal, bool]:
        if self.offline_compiler_chunk_size is not None:
            return await self._generate_offline_compilation(
                trial,
                repair_issues=repair_issues,
                session_id=session_id,
            )
        return await self._generate_compilation(
            trial,
            repair_issues=repair_issues,
            session_id=session_id,
        )

    async def _review(
        self, compilation: TrustedCompilation, *, session_id: str = "unscoped"
    ) -> tuple[ProtocolReviewProposal, bool]:
        compiled = compilation.compiled_trial
        criterion_rows = [
            {
                "criterion_id": criterion.criterion_id,
                "source_direction": criterion.source_direction.value,
                "source_quote": criterion.source_span.quote,
                "ast": criterion.ast.model_dump(mode="json"),
            }
            for criterion in compiled.criteria
        ]
        chunk_size = self.offline_reviewer_chunk_size or len(criterion_rows)
        proposals: list[ProtocolReviewProposal] = []
        used_fallback = False
        for chunk_index, start in enumerate(range(0, len(criterion_rows), chunk_size)):
            review_payload = {
                "nct_id": compiled.nct_id,
                "criteria": criterion_rows[start : start + chunk_size],
            }
            prompt = render_prompt(
                "protocol_reviewer_v1.md",
                review_payload=canonical_json_bytes(review_payload).decode(),
            )
            proposal, record = await self.generator.generate_primary_with_lite_fallback(
                primary_model_id="gemini-3.6-flash",
                lite_model_id="gemini-3.5-flash-lite",
                task_name=(
                    "protocol_reviewer"
                    if self.offline_reviewer_chunk_size is None
                    else f"protocol_reviewer_chunk_{chunk_index:03d}"
                ),
                prompt=prompt,
                prompt_version="1.0.3",
                output_schema_version="protocol-review-proposal-v1",
                slot_catalog_version=self.slot_catalog.version,
                normalized_input=review_payload,
                output_model=ProtocolReviewProposal,
                primary_thinking_level="MEDIUM",
                fallback_thinking_level="HIGH",
                primary_max_output_tokens=1500,
                fallback_max_output_tokens=1500,
                session_id=session_id,
            )
            proposals.append(proposal)
            used_fallback = used_fallback or record.used_fallback
        return (
            ProtocolReviewProposal(
                approved=all(proposal.approved for proposal in proposals),
                issues=[issue for proposal in proposals for issue in proposal.issues],
            ),
            used_fallback,
        )

    async def compile_and_review(
        self,
        *,
        trial: RawTrialRecord,
        evaluation_date: date,
        now: datetime,
        session_id: str = "unscoped",
    ) -> CompilationWorkflowResult:
        repair_attempted = False
        degradation_codes: list[str] = []
        review_attempts: list[ProtocolReviewProposal] = []
        try:
            proposal, compiler_fallback = await self._compile_proposal(trial, session_id=session_id)
            compilation = construct_trusted_compilation(
                trial=trial,
                proposal=proposal,
                slot_catalog=self.slot_catalog,
                compiler_model_id=(
                    "gemini-3.5-flash-lite" if compiler_fallback else "gemini-3.6-flash"
                ),
                compiler_prompt_version="1.0.4",
                created_at=now,
                evaluation_date=evaluation_date,
            )
            review, reviewer_fallback = await self._review(compilation, session_id=session_id)
            review_attempts.append(review)
            if not review.approved or any(issue.severity == "BLOCKING" for issue in review.issues):
                repair_attempted = True
                repair_issue_rows = [issue.model_dump(mode="json") for issue in review.issues]
                proposal, compiler_fallback = await self._compile_proposal(
                    trial,
                    repair_issues=repair_issue_rows,
                    session_id=session_id,
                )
                compilation = construct_trusted_compilation(
                    trial=trial,
                    proposal=proposal,
                    slot_catalog=self.slot_catalog,
                    compiler_model_id=(
                        "gemini-3.5-flash-lite" if compiler_fallback else "gemini-3.6-flash"
                    ),
                    compiler_prompt_version="1.0.4",
                    created_at=now,
                    evaluation_date=evaluation_date,
                )
                review, reviewer_fallback = await self._review(compilation, session_id=session_id)
                review_attempts.append(review)
                if not review.approved or any(
                    issue.severity == "BLOCKING" for issue in review.issues
                ):
                    return CompilationWorkflowResult(
                        compilation=opaque_fallback_compilation(
                            trial=trial,
                            slot_catalog=self.slot_catalog,
                            created_at=now,
                            evaluation_date=evaluation_date,
                            reason_code="SEMANTIC_REVIEW_REJECTED_AFTER_REPAIR",
                        ),
                        review_artifact=None,
                        review_attempts=review_attempts,
                        repair_attempted=True,
                        degradation_codes=["PROTOCOL_REVIEW_OPAQUE_AFTER_SINGLE_REPAIR"],
                    )
            reviewed: ReviewedCompilation = bind_semantic_review(
                compiled_trial=compilation.compiled_trial,
                proposal=review,
                reviewer_model_id=(
                    "gemini-3.5-flash-lite" if reviewer_fallback else "gemini-3.6-flash"
                ),
                reviewer_prompt_version="1.0.3",
                reviewed_at=now,
                compiler_used_fallback=compiler_fallback,
                reviewer_used_fallback=reviewer_fallback,
            )
            return CompilationWorkflowResult(
                compilation=TrustedCompilation(
                    compiled_trial=reviewed.compiled_trial,
                    coverage_report=compilation.coverage_report,
                    boundary_reports=compilation.boundary_reports,
                ),
                review_artifact=reviewed.review_artifact,
                review_attempts=review_attempts,
                repair_attempted=repair_attempted,
                degradation_codes=(
                    ["DOUBLE_LITE_FALLBACK_UNVERIFIED"]
                    if compiler_fallback and reviewer_fallback
                    else []
                ),
            )
        except (StructuredGenerationUnavailable, ProtocolCompilationError, ValueError):
            degradation_codes.append("PROTOCOL_COMPILATION_OPAQUE_FALLBACK")
            return CompilationWorkflowResult(
                compilation=opaque_fallback_compilation(
                    trial=trial,
                    slot_catalog=self.slot_catalog,
                    created_at=now,
                    evaluation_date=evaluation_date,
                    reason_code="MODEL_OR_SCHEMA_VALIDATION_FAILED",
                ),
                review_artifact=None,
                review_attempts=review_attempts,
                repair_attempted=repair_attempted,
                degradation_codes=degradation_codes,
            )
