from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from backend.app.agents.prompts import render_prompt
from backend.app.agents.protocol_compiler import (
    ProtocolCompilationError,
    TrustedCompilation,
    construct_trusted_compilation,
    opaque_fallback_compilation,
)
from backend.app.agents.protocol_reviewer import ReviewedCompilation, bind_semantic_review
from backend.app.application.catalog import SlotCatalog
from backend.app.domain.canonical import canonical_json_bytes
from backend.app.domain.model_outputs import CompiledTrialProposal, ProtocolReviewProposal
from backend.app.domain.trials import ProtocolReviewArtifact, RawTrialRecord
from backend.app.infrastructure.structured_generation import (
    StructuredGenerationUnavailable,
    StructuredGenerator,
)


@dataclass(frozen=True)
class CompilationWorkflowResult:
    compilation: TrustedCompilation
    review_artifact: ProtocolReviewArtifact | None
    repair_attempted: bool
    degradation_codes: list[str]


class ProtocolCompilationService:
    def __init__(self, generator: StructuredGenerator, slot_catalog: SlotCatalog) -> None:
        self.generator = generator
        self.slot_catalog = slot_catalog

    def _compiler_prompt(self, trial: RawTrialRecord, repair_issues: object | None = None) -> str:
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
        session_id: str = "unscoped",
    ) -> tuple[CompiledTrialProposal, bool]:
        prompt = self._compiler_prompt(trial, repair_issues)
        proposal, record = await self.generator.generate_primary_with_lite_fallback(
            primary_model_id="gemini-3.6-flash",
            lite_model_id="gemini-3.5-flash-lite",
            task_name="protocol_compiler_repair" if repair_issues else "protocol_compiler",
            prompt=prompt,
            prompt_version="1.0.3",
            output_schema_version="compiled-trial-proposal-v1",
            slot_catalog_version=self.slot_catalog.version,
            normalized_input={
                "trial": trial.model_dump(mode="json"),
                "repair_issues": repair_issues,
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

    async def _review(
        self, compilation: TrustedCompilation, *, session_id: str = "unscoped"
    ) -> tuple[ProtocolReviewProposal, bool]:
        compiled = compilation.compiled_trial
        review_payload = {
            "nct_id": compiled.nct_id,
            "criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "source_quote": criterion.source_span.quote,
                    "ast": criterion.ast.model_dump(mode="json"),
                }
                for criterion in compiled.criteria
            ],
        }
        prompt = render_prompt(
            "protocol_reviewer_v1.md",
            review_payload=canonical_json_bytes(review_payload).decode(),
        )
        proposal, record = await self.generator.generate_primary_with_lite_fallback(
            primary_model_id="gemini-3.6-flash",
            lite_model_id="gemini-3.5-flash-lite",
            task_name="protocol_reviewer",
            prompt=prompt,
            prompt_version="1.0.2",
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
        return proposal, record.used_fallback

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
        try:
            proposal, compiler_fallback = await self._generate_compilation(
                trial, session_id=session_id
            )
            compilation = construct_trusted_compilation(
                trial=trial,
                proposal=proposal,
                slot_catalog=self.slot_catalog,
                compiler_model_id=(
                    "gemini-3.5-flash-lite" if compiler_fallback else "gemini-3.6-flash"
                ),
                compiler_prompt_version="1.0.3",
                created_at=now,
                evaluation_date=evaluation_date,
            )
            review, reviewer_fallback = await self._review(compilation, session_id=session_id)
            if not review.approved or any(issue.severity == "BLOCKING" for issue in review.issues):
                repair_attempted = True
                proposal, compiler_fallback = await self._generate_compilation(
                    trial,
                    repair_issues=[issue.model_dump(mode="json") for issue in review.issues],
                    session_id=session_id,
                )
                compilation = construct_trusted_compilation(
                    trial=trial,
                    proposal=proposal,
                    slot_catalog=self.slot_catalog,
                    compiler_model_id=(
                        "gemini-3.5-flash-lite" if compiler_fallback else "gemini-3.6-flash"
                    ),
                    compiler_prompt_version="1.0.3",
                    created_at=now,
                    evaluation_date=evaluation_date,
                )
                review, reviewer_fallback = await self._review(compilation, session_id=session_id)
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
                        repair_attempted=True,
                        degradation_codes=["PROTOCOL_REVIEW_OPAQUE_AFTER_SINGLE_REPAIR"],
                    )
            reviewed: ReviewedCompilation = bind_semantic_review(
                compiled_trial=compilation.compiled_trial,
                proposal=review,
                reviewer_model_id=(
                    "gemini-3.5-flash-lite" if reviewer_fallback else "gemini-3.6-flash"
                ),
                reviewer_prompt_version="1.0.2",
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
                repair_attempted=repair_attempted,
                degradation_codes=degradation_codes,
            )
