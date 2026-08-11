from __future__ import annotations

import re
import unicodedata

import numpy as np

from backend.app.retrieval.models import RegistryCandidate


def _normalized_condition(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", " ", normalized).strip()


def exact_condition_match(candidate: RegistryCandidate, query_conditions: list[str]) -> bool:
    trial_conditions = {_normalized_condition(value) for value in candidate.trial.conditions}
    return any(_normalized_condition(query) in trial_conditions for query in query_conditions)


def rrf_score(*ranks: int, k: int = 60, exact_match: bool = False, bonus: float = 0.05) -> float:
    return sum(1.0 / (k + rank) for rank in ranks) + (bonus if exact_match else 0.0)


def min_max_scores(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    minimum = min(scores.values())
    maximum = max(scores.values())
    if np.isclose(minimum, maximum):
        return {key: 0.5 for key in scores}
    return {key: (value - minimum) / (maximum - minimum) for key, value in scores.items()}


def cosine_ranks(
    query_vector: np.ndarray,
    document_vectors: dict[str, np.ndarray],
) -> dict[str, int]:
    query_norm = float(np.linalg.norm(query_vector))
    if query_norm == 0 or not np.isfinite(query_vector).all():
        raise ValueError("query embedding must be finite and non-zero")
    similarities: list[tuple[str, float]] = []
    for nct_id, vector in document_vectors.items():
        denominator = query_norm * float(np.linalg.norm(vector))
        if denominator == 0 or not np.isfinite(vector).all():
            raise ValueError("document embedding must be finite and non-zero")
        similarities.append((nct_id, float(np.dot(query_vector, vector) / denominator)))
    similarities.sort(key=lambda item: (-item[1], item[0]))
    return {nct_id: index for index, (nct_id, _) in enumerate(similarities, start=1)}
