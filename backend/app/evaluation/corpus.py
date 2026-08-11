from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import orjson

from backend.app.domain.canonical import canonical_sha256
from backend.app.domain.trials import CompiledTrial, ProtocolReviewArtifact, RawTrialRecord
from backend.app.evaluation.annotations import load_compiled_trials


@dataclass(frozen=True, slots=True)
class ReleaseCorpus:
    compiled_trials: dict[str, CompiledTrial]
    raw_trials: dict[str, RawTrialRecord]
    reviews: dict[str, ProtocolReviewArtifact]
    source_texts: dict[str, str]


def _load_objects(paths: list[Path]) -> list[object]:
    rows: list[object] = []
    for path in paths:
        payload = orjson.loads(path.read_bytes())
        if isinstance(payload, list):
            rows.extend(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("trials"), list):
            rows.extend(payload["trials"])
        elif isinstance(payload, dict) and isinstance(payload.get("reviews"), list):
            rows.extend(payload["reviews"])
        elif isinstance(payload, dict) and ("nct_id" in payload or "review_id" in payload):
            rows.append(payload)
        elif isinstance(payload, dict):
            rows.extend(payload.values())
        else:
            raise ValueError(f"RELEASE_CORPUS_ARTIFACT_SHAPE_INVALID:{path}")
    return rows


def load_raw_trials(paths: list[Path]) -> list[RawTrialRecord]:
    return [RawTrialRecord.model_validate(item) for item in _load_objects(paths)]


def load_reviews(paths: list[Path]) -> list[ProtocolReviewArtifact]:
    return [ProtocolReviewArtifact.model_validate(item) for item in _load_objects(paths)]


def load_release_corpus(
    *,
    compiled_paths: list[Path],
    raw_paths: list[Path],
    review_paths: list[Path],
) -> ReleaseCorpus:
    return build_release_corpus(
        load_compiled_trials(compiled_paths),
        load_raw_trials(raw_paths),
        load_reviews(review_paths),
    )


def build_release_corpus(
    compiled_rows: list[CompiledTrial],
    raw_rows: list[RawTrialRecord],
    review_rows: list[ProtocolReviewArtifact],
) -> ReleaseCorpus:
    compiled = {item.nct_id: item for item in compiled_rows}
    raw = {item.nct_id: item for item in raw_rows}
    reviews = {item.nct_id: item for item in review_rows}
    if len(compiled) != len(compiled_rows):
        raise ValueError("RELEASE_CORPUS_DUPLICATE_COMPILED_TRIAL")
    if len(raw) != len(raw_rows):
        raise ValueError("RELEASE_CORPUS_DUPLICATE_RAW_TRIAL")
    if len(reviews) != len(review_rows):
        raise ValueError("RELEASE_CORPUS_DUPLICATE_REVIEW")
    if set(compiled) != set(raw) or set(compiled) != set(reviews):
        raise ValueError("RELEASE_CORPUS_TRIAL_SET_MISMATCH")
    source_texts: dict[str, str] = {}
    for nct_id in sorted(compiled):
        trial = compiled[nct_id]
        raw_trial = raw[nct_id]
        review = reviews[nct_id]
        if canonical_sha256(trial.model_dump(mode="json", exclude={"content_hash"})) != (
            trial.content_hash
        ):
            raise ValueError(f"RELEASE_CORPUS_COMPILED_HASH_INVALID:{nct_id}")
        eligibility = raw_trial.eligibility_criteria or ""
        if hashlib.sha256(eligibility.encode()).hexdigest() != trial.eligibility_text_sha256:
            raise ValueError(f"RELEASE_CORPUS_ELIGIBILITY_HASH_INVALID:{nct_id}")
        source_id = f"ctgov:{nct_id}:eligibility_criteria"
        source_texts[source_id] = eligibility
        for criterion in trial.criteria:
            span = criterion.source_span
            if span.source_id != source_id or span.end > len(eligibility):
                raise ValueError(f"RELEASE_CORPUS_SOURCE_SPAN_INVALID:{criterion.criterion_id}")
            if eligibility[span.start : span.end] != span.quote:
                raise ValueError(f"RELEASE_CORPUS_SOURCE_QUOTE_INVALID:{criterion.criterion_id}")
            if hashlib.sha256(span.quote.encode()).hexdigest() != span.sha256:
                raise ValueError(f"RELEASE_CORPUS_SOURCE_HASH_INVALID:{criterion.criterion_id}")
        if canonical_sha256(review.model_dump(mode="json", exclude={"content_hash"})) != (
            review.content_hash
        ):
            raise ValueError(f"RELEASE_CORPUS_REVIEW_HASH_INVALID:{nct_id}")
        if (
            review.compiled_protocol_hash != trial.content_hash
            or review.criterion_source_hashes
            != [criterion.source_text_sha256 for criterion in trial.criteria]
            or review.review_id != trial.review_artifact_id
            or not review.approved
        ):
            raise ValueError(f"RELEASE_CORPUS_REVIEW_BINDING_INVALID:{nct_id}")
    return ReleaseCorpus(compiled, raw, reviews, source_texts)
