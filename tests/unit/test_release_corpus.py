from __future__ import annotations

import pytest

from backend.app.application.vertical_slice import load_vertical_slice
from backend.app.evaluation.corpus import build_release_corpus


def test_release_corpus_binds_raw_compiled_and_reviewed_protocol() -> None:
    fixture = load_vertical_slice()

    corpus = build_release_corpus(
        [fixture.compiled_trial],
        [fixture.raw_trial],
        [fixture.review],
    )

    assert set(corpus.compiled_trials) == {"NCT05239624"}
    assert corpus.source_texts["ctgov:NCT05239624:eligibility_criteria"] == (
        fixture.eligibility_text
    )


def test_release_corpus_rejects_raw_source_tampering() -> None:
    fixture = load_vertical_slice()
    tampered_raw = fixture.raw_trial.model_copy(
        update={"eligibility_criteria": f"{fixture.eligibility_text}\nTampered."}
    )

    with pytest.raises(ValueError, match="RELEASE_CORPUS_ELIGIBILITY_HASH_INVALID"):
        build_release_corpus(
            [fixture.compiled_trial],
            [tampered_raw],
            [fixture.review],
        )


def test_release_corpus_rejects_review_binding_tampering() -> None:
    fixture = load_vertical_slice()
    tampered_review = fixture.review.model_copy(update={"approved": False})

    with pytest.raises(ValueError, match="RELEASE_CORPUS_REVIEW_HASH_INVALID"):
        build_release_corpus(
            [fixture.compiled_trial],
            [fixture.raw_trial],
            [tampered_review],
        )
