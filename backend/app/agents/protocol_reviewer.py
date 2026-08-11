from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.model_outputs import ProtocolReviewProposal
from backend.app.domain.trials import CompiledTrial, ProtocolReviewArtifact


class ProtocolReviewValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewedCompilation:
    compiled_trial: CompiledTrial
    review_artifact: ProtocolReviewArtifact


def bind_semantic_review(
    *,
    compiled_trial: CompiledTrial,
    proposal: ProtocolReviewProposal,
    reviewer_model_id: str,
    reviewer_prompt_version: str,
    reviewed_at: datetime,
    compiler_used_fallback: bool = False,
    reviewer_used_fallback: bool = False,
) -> ReviewedCompilation:
    criterion_ids = {criterion.criterion_id for criterion in compiled_trial.criteria}
    if any(issue.criterion_id not in criterion_ids for issue in proposal.issues):
        raise ProtocolReviewValidationError("review issue references an unknown criterion")
    blocking_ids = {issue.criterion_id for issue in proposal.issues if issue.severity == "BLOCKING"}
    double_fallback_block = compiler_used_fallback and reviewer_used_fallback
    approved = proposal.approved and not blocking_ids and not double_fallback_block
    review_id = f"review_{uuid4()}"
    all_trial_conditions = (
        approved
        and compiled_trial.source_character_coverage >= 0.90
        and compiled_trial.boundary_tests_passed
        and not any(criterion.opaque for criterion in compiled_trial.criteria)
    )
    criteria = [
        criterion.model_copy(
            update={
                "protocol_verified": all_trial_conditions
                and criterion.criterion_id not in blocking_ids
            }
        )
        for criterion in compiled_trial.criteria
    ]
    warnings = list(compiled_trial.warnings)
    if double_fallback_block:
        warnings.append("PRIMARY_COMPILER_AND_REVIEWER_UNAVAILABLE")
    if compiled_trial.source_character_coverage < 0.90:
        warnings.append("SOURCE_COVERAGE_BELOW_VERIFICATION_FLOOR")
    elif compiled_trial.source_character_coverage < 0.95:
        warnings.append("SOURCE_COVERAGE_BELOW_TARGET")
    if not compiled_trial.boundary_tests_passed:
        warnings.append("BOUNDARY_TEST_FAILURE")
    draft_compiled = compiled_trial.model_copy(
        update={
            "criteria": criteria,
            "protocol_verified": all_trial_conditions,
            "review_artifact_id": review_id,
            "warnings": sorted(set(warnings)),
            "content_hash": "0" * 64,
        }
    )
    compiled_hash = canonical_sha256(
        draft_compiled.model_dump(mode="json", exclude={"content_hash"})
    )
    final_compiled = draft_compiled.model_copy(update={"content_hash": compiled_hash})
    draft_review = ProtocolReviewArtifact(
        review_id=review_id,
        nct_id=compiled_trial.nct_id,
        criterion_source_hashes=[item.source_text_sha256 for item in criteria],
        compiled_protocol_hash=compiled_hash,
        review_method="GEMINI_SEMANTIC_REVIEW",
        reviewer_label="protocol-semantic-reviewer",
        model_id=reviewer_model_id,
        prompt_version=reviewer_prompt_version,
        reviewed_at=reviewed_at.astimezone(UTC),
        approved=approved,
        issues=proposal.issues,
        content_hash="0" * 64,
    )
    review_hash = canonical_sha256(draft_review.model_dump(mode="json", exclude={"content_hash"}))
    return ReviewedCompilation(
        compiled_trial=final_compiled,
        review_artifact=draft_review.model_copy(update={"content_hash": review_hash}),
    )
