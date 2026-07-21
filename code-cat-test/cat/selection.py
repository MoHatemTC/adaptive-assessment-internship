"""Adaptive selection: filter, rank, shortlist, constrained pick (sections 13-17).

Identical on all three branches. Approaches differ only in how a submission is SCORED, so
holding selection fixed is what makes a measured difference between them attributable.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field

from cat.competency_state import LearnerModel
from cat.config import settings
from cat.llm_client import LLMUnavailable, chat_json

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

SELECTION_SYSTEM = """You choose the next coding question for an adaptive assessment.

The assessment engine has already filtered and ranked the candidates. You may ONLY return
an id from `allowed_question_ids`. You may not invent a question, and you may not ask for
one outside the list.

You are given the CAT parameters for the competency under test: the current ability
estimate, its standard error, and how much evidence it rests on. `expected_information`
is how much each candidate would tell you AT THAT ESTIMATE — it is highest where the
candidate could plausibly pass or fail, and low for questions far above or below them.

Prefer, in this order:
1. A question that directly verifies an unresolved misconception.
2. The highest `expected_information`, which is what shrinks the standard error fastest.
3. A question that improves competency coverage.

Do not simply pick the hardest or the easiest question. A question the candidate is
almost certain to pass, or almost certain to fail, moves the estimate very little
whatever its difficulty.

Return JSON only:
{"selected_question_id": "<id>", "reason_code": "<one of the allowed codes>",
 "reason": "<one sentence>", "confidence": 0.0-1.0}

Allowed reason codes: MAX_INFORMATION, VERIFY_MISCONCEPTION,
RESOLVE_COMPETENCY_UNCERTAINTY, IMPROVE_CONTENT_COVERAGE, BALANCE_DIFFICULTY,
REDUCE_REPETITION, BALANCE_EXPOSURE
"""


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
    if answered >= settings.max_questions:
        return StopDecision(True, "max_questions", converged=False)
    if elapsed_minutes >= settings.time_limit_minutes:
        return StopDecision(True, "time_limit", converged=False)
    if remaining <= 0:
        return StopDecision(True, "bank_exhausted", converged=False)
    if answered >= settings.min_questions:
        if target_competency:
            state = model.states.get(target_competency)
            judged = [state] if state and state.observed else []
        else:
            judged = [s for s in model.states.values() if s.observed]
        if judged and all(s.standard_error <= settings.target_standard_error for s in judged):
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


# --- section 15: ranking ----------------------------------------------------
# Mastery grid for posterior-weighted quantities. 41 points over [0, 1], matching the
# MCQ engine's 41 points over [-4, 4]: fine enough that discretisation error is far below
# any realistic standard error, coarse enough to be microseconds.
MASTERY_GRID = [i / 40.0 for i in range(41)]

# KL while the estimate is still vague, expected Fisher once it is worth localising
# around. Three items is where the MCQ engine found the standard error worth trusting,
# and the Beta posterior here follows the same shape: SE 0.289 -> 0.220 -> 0.199 over the
# first three, after which it is flat enough for a local criterion to mean something.
KL_PHASE_ITEMS = 3

# KL neighbourhood at the first item, as a fraction of the ability range. The MCQ engine
# uses delta = 3.0 on a theta range of 8, and mastery spans 1, so the same proportion is
# 0.375. It shrinks as sqrt(n+1): early on ask which question separates a wide region of
# ability, later a narrow one.
KL_DELTA_AT_START = 0.375


def _probability_correct(question: dict, mastery: float) -> float:
    a = max(float(question.get("discrimination", 1.0)), 0.1)
    difficulty = float(question["difficulty"])
    return 1.0 / (1.0 + math.exp(-1.7 * a * (mastery - difficulty)))


def beta_posterior_grid(alpha: float, beta: float) -> list[float]:
    """The Beta posterior evaluated on MASTERY_GRID and normalised to sum to 1.

    Makes the belief an explicit object rather than a mean and a standard deviation, which
    is what posterior-expected information needs: you cannot average a function over a
    distribution you only have two moments of.

    Computed in log space — alpha and beta reach double figures after a few questions, and
    m**(alpha-1) underflows to zero across most of the grid well before that, which would
    silently return a degenerate posterior concentrated on one point.
    """
    logs = []
    for m in MASTERY_GRID:
        m = min(max(m, 1e-9), 1.0 - 1e-9)
        logs.append((alpha - 1.0) * math.log(m) + (beta - 1.0) * math.log(1.0 - m))
    peak = max(logs)
    weights = [math.exp(v - peak) for v in logs]
    total = sum(weights) or 1.0
    return [w / total for w in weights]


def fisher_information(question: dict, mastery: float, loading: float = 1.0) -> float:
    """Fisher information at a point on the mastery scale.

        I = a^2 * P * (1 - P) * loading

    This IS the exact 3PL quantity the MCQ engine uses, at c = 0:

        a^2 * ((P - c)/(1 - c))^2 * (1 - P)/P   ->   a^2 * P * (1 - P)

    and c = 0 is correct here rather than an approximation — a candidate cannot guess
    their way to a passing test suite the way they can guess a multiple-choice option.
    """
    p = _probability_correct(question, mastery)
    a = max(float(question.get("discrimination", 1.0)), 0.1)
    return (a**2) * p * (1.0 - p) * loading


def expected_fisher_information(
    question: dict, posterior: list[float], loading: float = 1.0
) -> float:
    """Fisher information averaged over the posterior rather than taken at its mean.

    Information at a point estimate is only the right criterion if the point estimate is
    right. Early in a session it is not, so maximising I(mastery_hat) chases a number that
    is still moving and can select a question the next response invalidates. Weighting by
    where the candidate plausibly IS uses the same belief the estimate is read from.
    """
    return sum(
        w * fisher_information(question, m, loading)
        for m, w in zip(MASTERY_GRID, posterior)
    )


def kl_information(
    question: dict, mastery_hat: float, delta: float, loading: float = 1.0
) -> float:
    """KL divergence between response distributions at `mastery_hat` and nearby.

    Used for the first few questions, where Fisher information is a poor criterion because
    it is LOCAL: it asks which question is most informative exactly here, when "here" is
    barely known. KL integrates over a neighbourhood, so it prefers questions that separate
    a whole region of ability — which is what an early question is for.
    """
    p_hat = min(max(_probability_correct(question, mastery_hat), 1e-9), 1.0 - 1e-9)
    total = 0.0
    for m in MASTERY_GRID:
        if abs(m - mastery_hat) > delta:
            continue
        p = min(max(_probability_correct(question, m), 1e-9), 1.0 - 1e-9)
        total += p_hat * math.log(p_hat / p) + (1.0 - p_hat) * math.log((1.0 - p_hat) / (1.0 - p))
    return total * loading


def selection_criterion(evidence_count: int) -> str:
    """Which information criterion applies at this point in the session."""
    return "KL" if evidence_count < KL_PHASE_ITEMS else "E[Fisher]"


def kl_delta(evidence_count: int) -> float:
    return KL_DELTA_AT_START / math.sqrt(evidence_count + 1)


def expected_information(question: dict, mastery: float, loading: float) -> float:
    """Information this question carries about a competency at the current estimate.

    The IRT quantity, on the 0..1 mastery scale the Beta model uses:

        P = 1 / (1 + exp(-1.7 * a * (mastery - difficulty)))
        I = a^2 * P * (1 - P) * loading

    Two properties make this the right selection criterion, and both matter:

    It PEAKS WHERE THE ANSWER IS UNCERTAIN. Information is maximal at P = 0.5 — where the
    question is matched to the estimate and the response genuinely could go either way. A
    question far below the estimate is almost certainly passed and one far above almost
    certainly failed, and neither outcome tells you much. This is what makes a test
    adaptive rather than merely ordered by difficulty.

    It SCALES WITH DISCRIMINATION SQUARED. A sharply discriminating question at the right
    difficulty is worth several blunt ones, which is why `a` cannot just be a tie-break.

    `loading` is the question's weight on the competency being measured, so a question
    that merely brushes the target contributes proportionally less. 1.7 is the usual
    logistic-to-normal scaling constant.
    """
    a = max(float(question.get("discrimination", 1.0)), 0.1)
    difficulty = float(question["difficulty"])
    p = 1.0 / (1.0 + math.exp(-1.7 * a * (mastery - difficulty)))
    return (a**2) * p * (1.0 - p) * loading


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
    open_misconceptions = {c for s in model.states.values() for c in s.misconception_codes}

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
            if criterion == "KL":
                information = kl_information(question, ability, delta, loading)
            else:
                information = expected_fisher_information(question, posterior, loading)
        else:
            loading = 0.0
            observed = [s for s in states if s.observed]
            ability = sum(s.mastery for s in observed) / len(observed) if observed else 0.5
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
        relevance = 1.0 if open_misconceptions and any(
            not model.get(t).observed or model.get(t).mastery < 0.5 for t in targets
        ) else 0.0

        signals = {
            "expected_information": round(information, 4),
            "criterion": criterion,
            "competency_uncertainty": round(uncertainty, 4),
            "ability_estimate": round(ability, 4),
            "blueprint_need": round(coverage, 4),
            "misconception_relevance": relevance,
            "target_loading": round(loading, 4),
        }

        scratch.append((question, information, uncertainty, relevance, coverage, signals))

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
    top = max(numeric, key=numeric.get)
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

    shortlist = ranked[: settings.shortlist_size]
    best = shortlist[0]

    def deterministic(flags: list[str], fallback: bool = True) -> SelectionDecision:
        return SelectionDecision(
            question=best.question, rank=1, utility=best.utility, best_utility=best.utility,
            chosen_by_llm=False, fallback_used=fallback, reason_code="MAX_INFORMATION",
            reason=best.reason, shortlist_ids=[c.question["question_id"] for c in shortlist],
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
            "ability_estimate": round(target_state.mastery, 4) if target_state else None,
            "standard_error": round(target_state.standard_error, 4) if target_state else None,
            "evidence_count": target_state.evidence_count if target_state else 0,
            "measured_yet": bool(target_state and target_state.observed),
            "precision_target": settings.target_standard_error,
        },
        "learner_state_summary": {
            "weak_competencies": [
                {"competency_id": s.competency_id, "mastery": round(s.mastery, 3),
                 "standard_error": round(s.standard_error, 3)}
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
                "target_competencies": [x["competency_id"] for x in c.question["competencies"]],
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
            SELECTION_SYSTEM, json.dumps(payload, indent=2), require=("selected_question_id",)
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
    if relative < settings.minimum_relative_utility:
        flags.append(
            f"LOW_RELATIVE_UTILITY: {relative:.2f} < {settings.minimum_relative_utility}"
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


def stop_rule_calibration(
    target: float | None = None,
    min_questions: int | None = None,
    max_questions: int | None = None,
) -> dict:
    """Can the precision target actually END a session, between the floor and the cap?

    A stopping rule is only adaptive if it can bind. Two ways it fails silently, and this
    system has now hit BOTH — once per question type:

      TRIVIAL      the target is above where the prior already sits, so it is met almost
                   immediately and min_questions decides the length. Every code session
                   traced so far crossed SE 0.20 at question 2 and still ran to 5.
      UNREACHABLE  the target is below what max_questions can deliver, so the rule never
                   fires and the cap decides. The MCQ bank needed 13 questions to reach
                   SE 0.65 with a 12-question limit.

    Both look like a working adaptive test from the outside, which is exactly why this is
    computed rather than assumed. Modelled on the median candidate (p=0.5, the slowest
    case) with one unit of evidence per question — a lower bound on convergence speed,
    since real questions often carry evidence for several competencies at once.
    """
    target = settings.target_standard_error if target is None else target
    lo = settings.min_questions if min_questions is None else min_questions
    hi = settings.max_questions if max_questions is None else max_questions

    def se_after(n: int, p: float = 0.5) -> float:
        a, b = 1.0 + n * p, 1.0 + n * (1.0 - p)
        return math.sqrt((a * b) / (((a + b) ** 2) * (a + b + 1.0)))

    reached = next((n for n in range(0, hi + 1) if se_after(n) <= target), None)
    floor = se_after(hi)

    if reached is None:
        verdict, detail = "UNREACHABLE", (
            f"SE {target} is below the {floor:.4f} floor reachable in {hi} questions — "
            "the rule can never fire and max_questions decides every session"
        )
    elif reached <= lo:
        verdict, detail = "TRIVIAL", (
            f"SE {target} is met by question {reached}, at or before the min_questions "
            f"floor of {lo} — min_questions decides every session, not precision"
        )
    else:
        verdict, detail = "BINDING", (
            f"SE {target} is met around question {reached}, between the floor of {lo} and "
            f"the cap of {hi} — the rule can end a session"
        )
    return {
        "verdict": verdict,
        "detail": detail,
        "expected_stop_question": reached,
        "reachable_floor": round(floor, 4),
        "target": target,
    }
