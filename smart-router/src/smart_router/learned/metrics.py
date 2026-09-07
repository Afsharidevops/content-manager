from __future__ import annotations

from typing import Iterable

from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

from .schema import ALLOWED_TIERS


def classification_metrics(y_true: Iterable[str], y_pred: Iterable[str]) -> dict:
    truth = list(y_true)
    pred = list(y_pred)
    precision, recall, _, _ = precision_recall_fscore_support(
        truth, pred, labels=list(ALLOWED_TIERS), zero_division=0
    )
    confusion = confusion_matrix(truth, pred, labels=list(ALLOWED_TIERS)).tolist()
    false_fast = sum(
        1 for actual, selected in zip(truth, pred) if selected == "fast" and actual != "fast"
    )
    strong_overroute = sum(
        1 for actual, selected in zip(truth, pred) if selected == "strong" and actual != "strong"
    )
    n = max(1, len(truth))
    return {
        "accuracy": float(accuracy_score(truth, pred)) if truth else 0.0,
        "precision": {tier: float(value) for tier, value in zip(ALLOWED_TIERS, precision)},
        "recall": {tier: float(value) for tier, value in zip(ALLOWED_TIERS, recall)},
        "confusion_matrix": confusion,
        "false_fast_rate": false_fast / n,
        "strong_overroute_rate": strong_overroute / n,
        "tier_distribution": {
            tier: sum(1 for item in pred if item == tier) / n for tier in ALLOWED_TIERS
        },
    }
