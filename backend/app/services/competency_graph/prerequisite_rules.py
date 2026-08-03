"""Is this response strong enough to license a graph consequence?

One definition, imported everywhere. The same two comparisons used to be inlined in three
places — shadow propagation, live propagation, and the rollup — and they had drifted: the
rollup applied no modality gate at all, so a single MCQ hit would infer prerequisite
mastery that shadow mode explicitly refused. Divergent copies of a threshold are not a
style problem; they are two parts of one system disagreeing about what a candidate
demonstrated.

THRESHOLD PRECEDENCE (review C11)

Three floors on grader confidence exist in the specification with no stated precedence: a
global propagation floor, a stricter one for blocking, and a per-item floor an author may
set. **The most restrictive wins.** A per-item floor exists to tighten a claim about that
item, never to loosen the global policy — an item cannot license an inference the engine's
own configuration would refuse.

Blocking additionally uses `downward_block_confidence`, which is at or above the success
floor by default. That asymmetry is deliberate: inferring mastery a candidate may not have
costs one unnecessary question, while blocking denies them the chance to demonstrate a
skill they do have.
"""

from __future__ import annotations

from .config import PropagationConfig


def success_confidence_floor(
    config: PropagationConfig, item_minimum_confidence: float | None = None
) -> float:
    return max(config.minimum_propagation_confidence, item_minimum_confidence or 0.0)


def failure_confidence_floor(
    config: PropagationConfig, item_minimum_confidence: float | None = None
) -> float:
    return max(config.downward_block_confidence, item_minimum_confidence or 0.0)


def is_strong_success(
    score: float,
    confidence: float,
    *,
    config: PropagationConfig,
    item_minimum_confidence: float | None = None,
) -> bool:
    """Strong enough to support prerequisite ancestors."""
    return score >= config.strong_success_threshold and confidence >= success_confidence_floor(
        config, item_minimum_confidence
    )


def is_strong_failure(
    score: float,
    confidence: float,
    *,
    config: PropagationConfig,
    item_minimum_confidence: float | None = None,
) -> bool:
    """Strong enough to block dependent descendants."""
    return score <= config.strong_failure_threshold and confidence >= failure_confidence_floor(
        config, item_minimum_confidence
    )


def is_scorable(weight: float, confidence: float) -> bool:
    """Whether this response carries evidence at all.

    An unscorable response — audio failure, sandbox failure — is not a wrong answer. It
    licenses no update, no propagation, and no observation.
    """
    return weight > 0.0 and confidence > 0.0
