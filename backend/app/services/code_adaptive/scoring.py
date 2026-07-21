"""Combining evidence sources into criterion scores.

THIS MODULE IS THE SEAM THE THREE APPROACHES DIFFER ON. Everything else — sandbox, static
analysis, updater, ranking, selection — is identical across branches, so a measured
difference between approaches is attributable to the weights here and nowhere else.

    A  objective only     tests + static analysis; the model contributes nothing
    B  test-anchored      the doc's section 26 pattern; functional correctness is pinned
                          to the tests, the model interprets everything softer
    C  LLM-led            the model scores every criterion; only hard contradictions
                          are blocked

Weights per criterion come from section 9. They are data, not branching logic, so the
difference between the approaches is one table rather than three code paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config.settings import Approach, settings
from app.services.code_adaptive.execution import ExecutionEvidence
from app.services.code_adaptive.static_analysis import StaticSignals

if TYPE_CHECKING:  # avoids a cycle: weight_profile builds itself from SOURCE_WEIGHTS
    from app.services.code_adaptive.weights import WeightProfile

# criterion -> {source: weight}. Sources absent from a submission are dropped and the
# remaining weights renormalised, so a missing source never silently scores zero.
SOURCE_WEIGHTS: dict[Approach, dict[str, dict[str, float]]] = {
    "A": {
        "functional_correctness": {"tests": 1.00},
        "edge_case_handling": {"tests": 0.85, "static": 0.15},
        "algorithm_choice": {"tests": 0.50, "static": 0.50},
        "code_quality": {"static": 1.00},
    },
    "B": {
        "functional_correctness": {"tests": 1.00},
        "edge_case_handling": {"tests": 0.80, "static": 0.05, "llm": 0.15},
        "algorithm_choice": {"tests": 0.35, "static": 0.30, "llm": 0.35},
        "code_quality": {"static": 0.30, "llm": 0.70},
    },
    "C": {
        # Functional correctness still carries a test term: a model asserting a
        # submission works when it demonstrably does not is the one claim no weighting
        # should be able to express. Approach C tests how much authority the model can
        # take, not whether it may contradict an execution result.
        "functional_correctness": {"tests": 0.50, "llm": 0.50},
        "edge_case_handling": {"tests": 0.25, "llm": 0.75},
        "algorithm_choice": {"llm": 1.00},
        "code_quality": {"llm": 1.00},
    },
}


@dataclass
class CriterionScore:
    """One criterion, scored 0..1, with the provenance that produced it.

    `score` is None when the criterion could NOT be assessed — see combine().
    """

    criterion_id: str
    score: float | None
    sources_used: dict[str, float]
    confidence: float = 1.0
    conflict_flag: str = ""


def objective_criterion_scores(
    evidence: ExecutionEvidence, signals: StaticSignals, tests: list[dict]
) -> dict[str, float]:
    """Criterion scores derivable without any model. Available on every approach."""
    scores: dict[str, float] = {}

    scores["functional_correctness"] = evidence.passed_test_ratio

    # Edge-case handling is measured on the tests that actually probe boundaries, not on
    # the whole suite: a submission can pass 90% overall and fail every boundary case,
    # and the global ratio hides exactly the defect this criterion exists to detect.
    boundary_ids = {
        t["test_id"]
        for t in tests
        if t.get("criterion_weights", {}).get("edge_case_handling", 0) > 0.5
    }
    if boundary_ids:
        outcomes = [o for o in evidence.test_results if o.test_id in boundary_ids]
        scores["edge_case_handling"] = (
            sum(1 for o in outcomes if o.passed) / len(outcomes) if outcomes else 0.0
        )

    return scores


def static_criterion_scores(signals: StaticSignals) -> dict[str, float]:
    """Criterion scores derivable from structure alone."""
    if not signals.syntax_valid:
        return {"algorithm_choice": 0.0, "code_quality": 0.0, "edge_case_handling": 0.0}

    scores: dict[str, float] = {}
    scores["edge_case_handling"] = 1.0 if signals.has_boundary_guard else 0.35

    algorithm = 1.0
    if signals.not_implemented or signals.hard_coded_output_suspected:
        algorithm = 0.0  # no algorithm was demonstrated at all
    elif signals.nested_loop_depth >= 2:
        algorithm = 0.5
    scores["algorithm_choice"] = algorithm

    if signals.not_implemented:
        # Nothing was written, so there is nothing to judge for readability either.
        return {**scores, "algorithm_choice": 0.0, "code_quality": 0.0}

    quality = 1.0
    if signals.cyclomatic_complexity > 10:
        quality -= 0.4
    elif signals.cyclomatic_complexity > 6:
        quality -= 0.2
    if signals.max_function_length > 40:
        quality -= 0.2
    if signals.mutates_argument:
        quality -= 0.3
    scores["code_quality"] = max(quality, 0.0)

    return scores


def criterion_weights(question: dict) -> dict[str, float]:
    """Each criterion's share of the overall score, from the question's `maximum_score`.

    The bank has always declared these and the code has always ignored them: the overall
    was a plain mean over scored criteria, so functional correctness could never be worth
    more than a quarter however the bank was authored. A submission passing 1 of 8 tests
    scored 0.61 in a live session — not because any criterion was wrong, but because
    "produces correct results" was structurally capped at 25% of the result.
    """
    declared = {
        c["criterion_id"]: float(c.get("maximum_score", 1.0))
        for c in question.get("rubric_criteria", [])
    }
    total = sum(declared.values())
    if total <= 0:
        return {k: 1.0 / len(declared) for k in declared} if declared else {}
    return {k: v / total for k, v in declared.items()}


def overall_score(scores: list[CriterionScore], question: dict) -> float | None:
    """Weighted mean over the criteria that could be assessed.

    Renormalised across the SCORED criteria, so an unassessable criterion redistributes
    its weight rather than counting as a zero — the same principle combine() applies to a
    missing source. Returns None when nothing was assessed at all.
    """
    weights = criterion_weights(question)
    usable = [(c, weights.get(c.criterion_id, 0.0)) for c in scores if c.score is not None]
    total = sum(w for _, w in usable)
    if not usable or total <= 0:
        # Weights all zero but scores exist: fall back to the plain mean rather than
        # returning None, which would report "not assessed" for work that was assessed.
        plain = [c.score for c in scores if c.score is not None]
        return round(sum(plain) / len(plain), 4) if plain else None
    return round(sum(c.score * w for c, w in usable) / total, 4)


def integrity_cap(signals: StaticSignals) -> tuple[float | None, str]:
    """A ceiling on what a submission can score when its structure invalidates its tests.

    WHY A CAP AND NOT A WEIGHT

    Hard-coding is not a weak answer, it is a void one: returning literals that ignore the
    arguments passes whichever tests happen to be visible while demonstrating none of the
    competency those tests exist to probe. A weighted average cannot express that. Static
    analysis already scores `algorithm_choice` 0.0 for it — and is then outvoted, because
    under the default split that criterion is only 30% static. Measured consequence:
    hardcoded submissions separated 0 of 25 on BOTH the objective and the test-anchored
    approach. Every arm of the study failed the same way, which is what a weighting
    problem looks like when the real fault is that averaging is the wrong operation.

    WHAT THIS DOES NOT DO

    It never touches ExecutionEvidence. The tests really did pass and that fact stays in
    the record intact — the anchor is untouched. What is capped is the INFERENCE from
    "tests passed" to "competency demonstrated", which is a different claim and the only
    one hard-coding actually breaks.

    Not zero for hard-coding: the candidate wrote something, and 0.0 is the score for
    having written nothing. That distinction is why `not_implemented` caps lower.
    """
    if not signals.syntax_valid:
        return None, ""  # a syntax error is already scored by execution; not fraud
    if signals.not_implemented:
        return 0.0, "NOT_IMPLEMENTED: the function body is empty — nothing was demonstrated"
    if signals.hard_coded_output_suspected:
        return 0.25, (
            "HARDCODED_OUTPUT: returns literals and ignores its arguments — passing tests "
            "does not demonstrate the competency they probe"
        )
    return None, ""


def apply_integrity_cap(
    scores: list[CriterionScore], signals: StaticSignals
) -> tuple[list[CriterionScore], str]:
    """Cap every scored criterion, so the learner model sees the cap too.

    Applied to criteria rather than to the overall score alone: competency evidence is
    projected from criteria, so capping only the headline would leave the learner model
    updating as though the submission had demonstrated competence.
    """
    cap, reason = integrity_cap(signals)
    if cap is None:
        return scores, ""

    capped = [
        CriterionScore(
            criterion_id=s.criterion_id,
            score=None if s.score is None else min(s.score, cap),
            sources_used=s.sources_used,
            confidence=s.confidence,
            conflict_flag=s.conflict_flag or ("INTEGRITY_CAP" if s.score and s.score > cap else ""),
        )
        for s in scores
    ]
    return capped, reason


def combine(
    criterion_id: str,
    *,
    tests: float | None,
    static: float | None,
    llm: float | None,
    llm_confidence: float = 1.0,
    approach: Approach | None = None,
    profile: "WeightProfile | None" = None,
) -> CriterionScore:
    """Weighted combination of whichever sources are present.

    Two guards apply on every approach, including C:

    ANCHORING — the model may not assert functional correctness above what execution
    showed. A model claiming a failing submission works is the failure mode that puts a
    wrong score on a real person, and no weighting should be able to express it.

    CONFIDENCE — below the configured threshold the model's weight is halved rather than
    dropped, so a hedged interpretation degrades toward the objective evidence instead of
    disappearing (which would silently change which sources scored the criterion).
    """
    # An explicit profile wins over the approach preset: it is what an admin tuned, and
    # falling back to the preset when one is set would silently discard the setting.
    if profile is not None:
        weights = profile.for_criterion(criterion_id)
    else:
        weights = dict(SOURCE_WEIGHTS[approach or settings.code_approach].get(criterion_id, {}))
    conflict = ""

    if llm is not None and llm_confidence < settings.minimum_llm_confidence:
        weights["llm"] = weights.get("llm", 0.0) * 0.5

    if criterion_id == "functional_correctness" and llm is not None and tests is not None:
        if llm > tests + 0.15:
            conflict = "LLM_OBJECTIVE_CONFLICT: model scored above the test result"
            llm = tests

    available = {"tests": tests, "static": static, "llm": llm}
    total_weight = 0.0
    weighted = 0.0
    used: dict[str, float] = {}
    for source, value in available.items():
        weight = weights.get(source, 0.0)
        if value is None or weight <= 0.0:
            continue
        weighted += value * weight
        total_weight += weight
        used[source] = weight

    if total_weight == 0.0:
        # No configured source produced a value, so this criterion CANNOT be assessed and
        # is returned unscored.
        #
        # It previously fell back to whichever source happened to exist, which was a
        # silent disaster on approach C: algorithm_choice and code_quality are weighted
        # llm-only there, so whenever the model was unavailable both fell through to
        # static analysis and a stub that ran nothing scored 1.00 on each. Absence of
        # evidence became full marks. Scoring 0.0 instead would be the mirror error — a
        # claim the learner demonstrated nothing, when the truth is that we did not look.
        return CriterionScore(criterion_id, None, {}, 0.0, "NOT_ASSESSED")

    return CriterionScore(
        criterion_id=criterion_id,
        score=round(weighted / total_weight, 4),
        sources_used={k: round(v / total_weight, 3) for k, v in used.items()},
        confidence=llm_confidence if "llm" in used else 1.0,
        conflict_flag=conflict,
    )
