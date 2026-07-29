from __future__ import annotations


def is_strong_success(score: float, confidence: float, *, threshold: float, min_conf: float) -> bool:
    return score >= threshold and confidence >= min_conf


def is_strong_failure(score: float, confidence: float, *, threshold: float, min_conf: float) -> bool:
    return score <= threshold and confidence >= min_conf

