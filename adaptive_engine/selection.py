"""The one question-selection path: pick the competency, then pick its question.

COMPETENCY choice is deterministic and graph-aware: among competencies still worth
asking about (not converged, under their question cap, with unadministered questions
left), prefer unblocked over blocked, then the widest posterior, then the id — so
prerequisites that are failing steer the run toward investigating them before their
dependents, and two runs from the same state always agree.

QUESTION choice uses the ported measurement criteria: KL information while the estimate
is still vague (first three observations), expected Fisher information over the whole
posterior once it is worth localising. Exposure control administers a uniformly random
rank from the top ``exposure_top_k`` — randomesque selection — with the randomness drawn
purely from (seed, questions administered), so it lives entirely in the adaptive state
and replays identically anywhere.
"""

from __future__ import annotations

import hashlib

import numpy as np

from adaptive_engine.compilation import CompiledAssessment
from adaptive_engine.convergence import converged_competencies
from adaptive_engine.graph import blocked_competencies
from adaptive_engine.irt import (
    estimate,
    expected_fisher_information,
    kullback_leibler_information,
)
from adaptive_engine.models import Question
from adaptive_engine.state import AdaptiveState

# KL is the criterion while the estimate is still vague; Fisher once it is worth
# localising around. Three observations is where the standard error typically drops
# below ~1.0.
KL_PHASE_OBSERVATIONS = 3


def eligible_question_ids(
    compiled: CompiledAssessment, state: AdaptiveState, competency_id: str
) -> tuple[str, ...]:
    """Questions measuring this competency that have not been administered."""
    administered = set(state.administered_question_ids)
    return tuple(
        question_id
        for question_id in compiled.question_ids_by_competency[competency_id]
        if question_id not in administered
    )


def open_competencies(
    compiled: CompiledAssessment, state: AdaptiveState
) -> tuple[str, ...]:
    """Competencies not yet converged, in definition order."""
    converged = converged_competencies(compiled, state)
    return tuple(c for c in compiled.competency_ids if c not in converged)


def _exposure_offset(seed: int, questions_administered: int, window: int) -> int:
    """A deterministic draw that behaves like a fresh uniform pick per selection.

    Hashing (seed, position) gives every selection its own independent-looking offset
    while keeping the whole stream a pure function of the state — no evolving RNG object
    to serialize, nothing to drift when a state is restored elsewhere.
    """
    if window <= 1:
        return 0
    digest = hashlib.sha256(f"{seed}:{questions_administered}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % window


def select_next_question(
    compiled: CompiledAssessment, state: AdaptiveState
) -> Question | None:
    """The next question to administer, or None when nothing remains askable."""
    policy = compiled.definition.policy
    blocked = blocked_competencies(compiled, state.graph_evidence)

    candidates: list[str] = []
    for competency_id in open_competencies(compiled, state):
        competency_state = state.competency_states[competency_id]
        if competency_state.observations >= policy.max_questions_per_competency:
            continue
        if not eligible_question_ids(compiled, state, competency_id):
            continue
        candidates.append(competency_id)
    if not candidates:
        return None

    def competency_key(competency_id: str) -> tuple[bool, float, str]:
        _, standard_error = estimate(
            np.asarray(state.competency_states[competency_id].posterior)
        )
        return (competency_id in blocked, -standard_error, competency_id)

    chosen = min(candidates, key=competency_key)
    return _best_question(compiled, state, chosen)


def _best_question(
    compiled: CompiledAssessment, state: AdaptiveState, competency_id: str
) -> Question:
    competency_state = state.competency_states[competency_id]
    posterior = np.asarray(competency_state.posterior, dtype=float)
    theta_hat, _ = estimate(posterior)

    def information(question: Question) -> float:
        if competency_state.observations < KL_PHASE_OBSERVATIONS:
            # Neighbourhood shrinks as the estimate sharpens: early on, ask which
            # question separates a wide region; later, a narrow one.
            delta = 3.0 / np.sqrt(competency_state.observations + 1)
            return kullback_leibler_information(
                theta_hat,
                question.discrimination,
                question.difficulty,
                question.guessing,
                delta,
            )
        return expected_fisher_information(
            posterior, question.discrimination, question.difficulty, question.guessing
        )

    eligible = eligible_question_ids(compiled, state, competency_id)
    ranked = sorted(
        (compiled.questions_by_id[qid] for qid in eligible),
        key=lambda q: (-information(q), abs(q.difficulty - theta_hat), q.question_id),
    )
    window = min(compiled.definition.policy.exposure_top_k, len(ranked))
    offset = _exposure_offset(state.random_seed, len(state.administered_question_ids), window)
    return ranked[offset]
