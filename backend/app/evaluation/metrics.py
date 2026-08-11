from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median(values: list[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def classification_metrics(
    truth: list[str], predictions: list[str], labels: list[str]
) -> dict[str, Any]:
    if len(truth) != len(predictions):
        raise ValueError("truth and predictions must have equal length")
    per_class: dict[str, Any] = {}
    f1_values: list[float] = []
    for label in labels:
        true_positive = sum(
            t == label and p == label for t, p in zip(truth, predictions, strict=True)
        )
        false_positive = sum(
            t != label and p == label for t, p in zip(truth, predictions, strict=True)
        )
        false_negative = sum(
            t == label and p != label for t, p in zip(truth, predictions, strict=True)
        )
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recall = (
            true_positive / (true_positive + false_negative)
            if true_positive + false_negative
            else 0.0
        )
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": precision, "recall": recall, "f1": f1}
        f1_values.append(f1)
    accuracy = mean([float(t == p) for t, p in zip(truth, predictions, strict=True)])
    return {
        "count": len(truth),
        "accuracy": accuracy,
        "macro_f1": mean(f1_values),
        "per_class": per_class,
        "truth_counts": dict(sorted(Counter(truth).items())),
        "prediction_counts": dict(sorted(Counter(predictions).items())),
    }


def retrieval_metrics(ranked_ids: list[str], relevant: dict[str, int]) -> dict[str, Any]:
    binary_relevant = {key for key, grade in relevant.items() if grade > 0}

    def precision_at(k: int) -> float:
        return sum(item in binary_relevant for item in ranked_ids[:k]) / k

    recall_20 = (
        sum(item in binary_relevant for item in ranked_ids[:20]) / len(binary_relevant)
        if binary_relevant
        else 0.0
    )
    reciprocal_rank = next(
        (1.0 / rank for rank, item in enumerate(ranked_ids, start=1) if item in binary_relevant),
        0.0,
    )
    gains = [relevant.get(item, 0) for item in ranked_ids[:10]]
    dcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sorted(relevant.values(), reverse=True)[:10]
    idcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(ideal))
    return {
        "recall_at_20": recall_20,
        "precision_at_5": precision_at(5),
        "precision_at_10": precision_at(10),
        "ndcg_at_10": dcg / idcg if idcg else 0.0,
        "mrr": reciprocal_rank,
    }
