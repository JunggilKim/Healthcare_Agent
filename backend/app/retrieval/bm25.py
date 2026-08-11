from __future__ import annotations

from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]

from backend.app.domain.trials import RawTrialRecord
from backend.app.retrieval.tokenizer import RegexMedicalTokenizer


def build_trial_document(trial: RawTrialRecord) -> str:
    conditions = " ".join(trial.conditions)
    keywords = " ".join(trial.keywords)
    titles = " ".join(filter(None, [trial.brief_title, trial.official_title]))
    return "\n".join(
        [
            "[CONDITIONS x3]",
            " ".join([conditions] * 3),
            "[KEYWORDS x2]",
            " ".join([keywords] * 2),
            "[TITLES x2]",
            " ".join([titles] * 2),
            "[INTERVENTIONS]",
            " ".join(trial.intervention_names),
            "[SUMMARY]",
            trial.brief_summary or "",
            "[ELIGIBILITY PREVIEW]",
            (trial.eligibility_criteria or "")[:1500],
        ]
    )


def bm25_ranks(
    trials: list[RawTrialRecord], query: str, tokenizer: RegexMedicalTokenizer
) -> dict[str, int]:
    if not trials:
        return {}
    corpus = [tokenizer.tokenize(build_trial_document(trial)) for trial in trials]
    scores = BM25Okapi(corpus).get_scores(tokenizer.tokenize(query))
    ordered = sorted(zip(trials, scores, strict=True), key=lambda pair: (-pair[1], pair[0].nct_id))
    return {trial.nct_id: index for index, (trial, _) in enumerate(ordered, start=1)}
