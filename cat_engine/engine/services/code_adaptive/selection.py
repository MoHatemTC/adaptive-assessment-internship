"""Adaptive selection: filter, rank, shortlist, constrained pick (sections 13-17).

Identical on all three branches. Approaches differ only in how a submission is SCORED, so
holding selection fixed is what makes a measured difference between them attributable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.code_adaptive.competency import LearnerModel
from cat_engine.engine.services.code_adaptive.irt import (
    beta_posterior_grid,
    expected_fisher_information,
    fisher_information,
    kl_delta,
    kl_information,
    selection_criterion,
)
from cat_engine.engine.services.code_adaptive.llm import LLMUnavailable, chat_json
from cat_engine.engine.services.code_adaptive.prompts import SELECTION_SYSTEM

logger = logging.getLogger(__name__)

ALLOWED_REASON_CODES = {
    "MAX_INFORMATION",
    "VERIFY_MISCONCEPTION",
    "RESOLVE_COMPETENCY_UNCERTAINTY",
    "IMPROVE_CONTENT_COVERAGE",
    "BALANCE_DIFFICULTY",
    "REDUCE_REPETITION",
    "BALANCE_EXPOSURE",
}


@dataclass
class StopDecision:
    should_stop: bool
    reason: str = ""
    converged: bool = False


@dataclass
class RankedCandidate:
    question: dict
    utility: float
    signals: dict[str, float] = field(default_factory=dict)
    reason: str = ""


@dataclass
class SelectionDecision:
    question: dict
    rank: int
    utility: float
    best_utility: float
    chosen_by_llm: bool
    fallback_used: bool = False
    reason_code: str = ""
    reason: str = ""
    llm_confidence: float = 0.0
    shortlist_ids: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def normalized_regret(self) -> float:
        """Fraction of the best available utility given up. 0 when the top pick was taken.

        The number that actually matters when judging a constrained picker: agreement
        alone cannot tell a choice that cost nothing from one that cost half the value.
        """
        if self.best_utility <= 0:
            return 0.0
        return max(0.0, (self.best_utility - self.utility) / self.best_utility)


# --- section 13: stopping ---------------------------------------------------
def competency_weight(question: dict, competency_id: str) -> float:
    """How much of this question assesses one competency. 0 when it does not at all."""
    for entry in question["competencies"]:
        if entry["competency_id"] == competency_id:
            return float(entry["weight"])
    return 0.0


def evaluate_stop(
    model: LearnerModel,
    answered: int,
    elapsed_minutes: float,
    remaining: int,
    target_competency: str | None = None,
) -> StopDecision:
    """Deterministic. The LLM cannot stop the assessment.

    When a session targets one competency, precision is judged on THAT competency alone.
    Requiring every touched competency to converge would make the test hostage to
    whichever incidental competency a question happened to brush against — a session
    aimed at boundary_conditions would run until it had also pinned down complexity
    awareness it was never trying to measure.
    """
    if answered >= settings.code_max_questions:
        return StopDecision(True, "max_questions", converged=False)
    if elapsed_minutes >= settings.code_time_limit_minutes:
        return StopDecision(True, "time_limit", converged=False)
    if remaining <= 0:
        return StopDecision(True, "bank_exhausted", converged=False)
    if answered >= settings.code_min_questions:
        if target_competency:
            state = model.states.get(target_competency)
            judged = [state] if state and state.observed else []
        else:
            judged = [s for s in model.states.values() if s.observed]
        if judged and all(s.standard_error <= settings.code_se_target for s in judged):
            return StopDecision(True, "target_precision", converged=True)
    return StopDecision(False)


# --- section 14: filtering --------------------------------------------------
def filter_candidates(
    bank: list[dict],
    answered_ids: set[str],
    model: LearnerModel,
    target_competency: str | None = None,
) -> list[dict]:
    """Remove everything ineligible before anything is scored or shown to a model.

    A target competency restricts the pool to questions that actually assess it. Note the
    bank is authored so most questions carry several competencies, so targeting
    boundary_conditions still leaves 10 questions while recursion leaves 1 — the caller
    is expected to warn when the remaining pool is too thin to adapt over.
    """

    def prerequisites_met(question: dict) -> bool:
        for prerequisite in question.get("prerequisites", []):
            state = model.states.get(prerequisite)
            # Unassessed prerequisites do not block: on a short test most competencies are
            # unmeasured, and blocking on them would strand the whole bank.
            if state and state.observed and state.mastery < 0.35:
                return False
        return True

    return [
        q
        for q in bank
        if q["question_id"] not in answered_ids
        and q.get("status") == "active"
        and prerequisites_met(q)
        and (not target_competency or competency_weight(q, target_competency) > 0)
    ]


# --- ranking ----------------------------------------------------------------
def _parameters(question: dict) -> tuple[float, float]:
    """A question's (discrimination, difficulty), with a floor on `a`.

    A zero or missing discrimination would make information identically zero and the
    question unrankable, so it degrades to a blunt-but-usable item rather than vanishing.
    """
    return max(float(question.get("discrimination", 1.0)), 0.1), float(
        question["difficulty"]
    )


def expected_information(question: dict, mastery: float, loading: float = 1.0) -> float:
    """Fisher information this question carries about a competency at a point estimate.

    A thin adapter over `irt.fisher_information` so callers can pass a bank question
    rather than unpacking its parameters. The maths lives in irt.py and only there.
    """
    a, b = _parameters(question)
    return fisher_information(mastery, a, b, loading)


def rank_candidates(
    candidates: list[dict], model: LearnerModel, target_competency: str | None = None
) -> list[RankedCandidate]:
    """Utility per candidate, driven by the CAT parameters of the competency under test.

    When a target is set, the ability estimate that steers difficulty is the TARGET's
    mastery and the uncertainty that motivates asking is the TARGET's standard error —
    not an average over every competency the question happens to touch. Averaging was the
    earlier behaviour and it quietly defeated the adaptation: a question could rank highly
    because something incidental was uncertain, and difficulty was matched to a blend that
    described no competency in particular.
    """
    ranked: list[RankedCandidate] = []
    scratch: list[tuple] = []
    open_misconceptions = {
        c for s in model.states.values() for c in s.misconception_codes
    }

    # Which criterion this step uses, and the belief it is computed against. Both are
    # properties of the session, not of a candidate question, so they are resolved once.
    if target_competency:
        target_state = model.get(target_competency)
        criterion = selection_criterion(target_state.evidence_count)
        posterior = beta_posterior_grid(target_state.alpha, target_state.beta)
        delta = kl_delta(target_state.evidence_count)
    else:
        criterion, posterior, delta = "E[Fisher]", None, kl_delta(0)

    for question in candidates:
        targets = [c["competency_id"] for c in question["competencies"]]
        states = [model.get(t) for t in targets]
        weights = [c["weight"] for c in question["competencies"]]

        if target_competency:
            loading = competency_weight(question, target_competency)
            # Ability the question is matched against: the target's own estimate, or the
            # midpoint while it is still unmeasured (where information is maximal anyway).
            ability = target_state.mastery if target_state.observed else 0.5
            uncertainty = target_state.standard_error
            # KL while the estimate is vague, posterior-expected Fisher once it is worth
            # localising around — the MCQ engine's phase switch, on the mastery scale.
            a, b = _parameters(question)
            if criterion == "KL":
                information = kl_information(ability, a, b, delta, loading)
            else:
                assert posterior is not None
                information = expected_fisher_information(posterior, a, b, loading)
        else:
            loading = 0.0
            observed = [s for s in states if s.observed]
            ability = (
                sum(s.mastery for s in observed) / len(observed) if observed else 0.5
            )
            uncertainty = sum(s.standard_error * w for s, w in zip(states, weights))
            # No target: information about the whole blueprint, each competency weighted
            # by how much the question loads on it.
            information = sum(
                expected_information(question, st.mastery if st.observed else 0.5, w)
                for st, w in zip(states, weights)
            )

        # Coverage: unassessed competencies first, so a report is not built on three of
        # eight competencies because the sharpest questions happened to cluster.
        coverage = sum(w for s, w in zip(states, weights) if not s.observed)

        # Misconception relevance: verifying a suspected misconception is the highest-value
        # evidence available — it either confirms a real gap or clears a false positive.
        relevance = (
            1.0
            if open_misconceptions
            and any(
                not model.get(t).observed or model.get(t).mastery < 0.5 for t in targets
            )
            else 0.0
        )

        signals = {
            "expected_information": round(information, 4),
            "criterion": criterion,
            "competency_uncertainty": round(uncertainty, 4),
            "ability_estimate": round(ability, 4),
            "blueprint_need": round(coverage, 4),
            "misconception_relevance": relevance,
            "target_loading": round(loading, 4),
        }

        scratch.append(
            (question, information, uncertainty, relevance, coverage, signals)
        )

    # Information is normalised against the best candidate BEFORE it is blended, because
    # KL and Fisher are not on the same scale — KL sums a divergence over a neighbourhood,
    # Fisher averages a variance. Blending either raw against fixed 0.20/0.15 weights would
    # silently change how much information counts at the moment the criterion switches, so
    # the phase change would alter selection for a reason unrelated to the candidate.
    # Relative to the best available option is the comparison the ranking actually needs.
    peak = max((info for _, info, *_ in scratch), default=0.0) or 1.0

    for question, information, uncertainty, relevance, coverage, signals in scratch:
        signals["information_relative"] = round(information / peak, 4)
        # Information leads. Uncertainty scales it: when the target is already precisely
        # estimated there is little left to learn and coverage/misconception work matter
        # relatively more — which is also when the stopping rule is about to fire anyway.
        utility = (
            0.45 * (information / peak)
            + 0.20 * uncertainty
            + 0.20 * relevance
            + 0.15 * coverage
        )
        ranked.append(
            RankedCandidate(question, round(utility, 4), signals, _explain(signals))
        )

    ranked.sort(key=lambda c: (c.utility, c.question["question_id"]), reverse=True)
    return ranked


def _explain(signals: dict[str, float]) -> str:
    # Numeric signals only: `criterion` is a label, and max() over a mix of str and float
    # raises rather than ranking.
    numeric = {k: v for k, v in signals.items() if isinstance(v, (int, float))}
    if not numeric:
        return "Best available utility."
    top = max(numeric, key=lambda key: numeric[key])
    return {
        "expected_information": "Most informative at the current ability estimate.",
        "competency_uncertainty": "The competency under test is still imprecise.",
        "ability_estimate": "Matched to current mastery.",
        "blueprint_need": "Covers competencies not yet assessed.",
        "misconception_relevance": "Directly verifies an open misconception.",
        "target_loading": "Most squarely targets the competency under test.",
    }.get(top, "Best available utility.")


# --- sections 16-17: constrained pick ---------------------------------------
def choose(
    ranked: list[RankedCandidate],
    model: LearnerModel,
    *,
    use_llm: bool = True,
    target_competency: str | None = None,
) -> SelectionDecision | None:
    """Pick the next question: the model choosing from the shortlist, else rank 1."""
    if not ranked:
        return None

    shortlist = ranked[: settings.code_shortlist_size]
    best = shortlist[0]

    def deterministic(flags: list[str], fallback: bool = True) -> SelectionDecision:
        return SelectionDecision(
            question=best.question,
            rank=1,
            utility=best.utility,
            best_utility=best.utility,
            chosen_by_llm=False,
            fallback_used=fallback,
            reason_code="MAX_INFORMATION",
            reason=best.reason,
            shortlist_ids=[c.question["question_id"] for c in shortlist],
            flags=flags,
        )

    if not use_llm:
        return deterministic([], fallback=False)

    target_state = model.states.get(target_competency) if target_competency else None
    payload = {
        # The CAT parameters, stated explicitly. Without them the model is choosing from
        # a ranked list without knowing what the ranking is FOR — it can see that a
        # question scores well but not that the candidate sits at mastery 0.31 with the
        # estimate still imprecise, which is the whole reason one question beats another.
        "competency_under_test": target_competency,
        "cat_parameters": {
            "ability_estimate": round(target_state.mastery, 4)
            if target_state
            else None,
            "standard_error": round(target_state.standard_error, 4)
            if target_state
            else None,
            "evidence_count": target_state.evidence_count if target_state else 0,
            "measured_yet": bool(target_state and target_state.observed),
            "precision_target": settings.code_se_target,
        },
        "learner_state_summary": {
            "weak_competencies": [
                {
                    "competency_id": s.competency_id,
                    "mastery": round(s.mastery, 3),
                    "standard_error": round(s.standard_error, 3),
                }
                for s in model.weakest()
            ],
            "open_misconceptions": sorted(
                {c for s in model.states.values() for c in s.misconception_codes}
            ),
        },
        "allowed_candidates": [
            {
                "question_id": c.question["question_id"],
                "rank": index + 1,
                "utility_score": c.utility,
                "difficulty": c.question["difficulty"],
                "discrimination": c.question.get("discrimination"),
                "expected_information": c.signals.get("expected_information"),
                "target_loading": c.signals.get("target_loading"),
                "target_competencies": [
                    x["competency_id"] for x in c.question["competencies"]
                ],
                "selection_reason": c.reason,
            }
            for index, c in enumerate(shortlist)
        ],
        "constraints": {
            "allowed_question_ids": [c.question["question_id"] for c in shortlist],
            "must_not_generate_new_questions": True,
        },
    }

    try:
        reply = chat_json(
            SELECTION_SYSTEM,
            json.dumps(payload, indent=2),
            require=("selected_question_id",),
        )
    except LLMUnavailable as exc:
        logger.warning("selection: model unavailable (%s) — deterministic rank 1", exc)
        return deterministic([f"LLM_UNAVAILABLE: {exc}"])

    by_id = {c.question["question_id"]: (index, c) for index, c in enumerate(shortlist)}
    selected_id = str(reply.get("selected_question_id", "")).strip()
    if selected_id not in by_id:
        logger.warning("selection: model returned %r, not in shortlist", selected_id)
        return deterministic([f"INVALID_SELECTION: {selected_id!r}"])

    reason_code = str(reply.get("reason_code", ""))
    flags: list[str] = []
    if reason_code not in ALLOWED_REASON_CODES:
        flags.append(f"UNKNOWN_REASON_CODE: {reason_code!r}")
        reason_code = "MAX_INFORMATION"

    try:
        confidence = float(reply.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    index, candidate = by_id[selected_id]

    # Section 17's relative-utility tolerance. The shortlist already bounds the damage;
    # this bounds it further, and costs nothing when the model picks sensibly.
    relative = candidate.utility / best.utility if best.utility > 0 else 1.0
    if relative < settings.code_minimum_relative_utility:
        flags.append(
            f"LOW_RELATIVE_UTILITY: {relative:.2f} < {settings.code_minimum_relative_utility}"
        )
        return deterministic(flags)

    return SelectionDecision(
        question=candidate.question,
        rank=index + 1,
        utility=candidate.utility,
        best_utility=best.utility,
        chosen_by_llm=True,
        fallback_used=False,
        reason_code=reason_code,
        reason=str(reply.get("reason", ""))[:300],
        llm_confidence=confidence,
        shortlist_ids=[c.question["question_id"] for c in shortlist],
        flags=flags,
    )
