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

from cat.config import Approach, settings
from code_evaluation.sandbox import ExecutionEvidence
from code_evaluation.static_analysis import StaticSignals

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
    if signals.hard_coded_output_suspected:
        algorithm = 0.0  # no algorithm was demonstrated at all
    elif signals.nested_loop_depth >= 2:
        algorithm = 0.5
    scores["algorithm_choice"] = algorithm

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


def combine(
    criterion_id: str,
    *,
    tests: float | None,
    static: float | None,
    llm: float | None,
    llm_confidence: float = 1.0,
    approach: Approach | None = None,
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
    weights = dict(SOURCE_WEIGHTS[approach or settings.code_cat_approach].get(criterion_id, {}))
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
