"""Turn criterion scores into per-competency evidence (sections 9-10).

A question's criteria and its competencies are different axes: `edge_case_handling` is a
criterion, `boundary_conditions` is a competency, and the same criterion can bear on
several competencies with different weight. This module projects one onto the other so
the updater downstream only ever sees competencies.

`evidence_strength` is the quantity that governs how much a result is allowed to move a
learner's estimate, and it is deliberately not the score. A submission that failed to
compile still carries a score of 0, but it demonstrates very little — punishing a
competency estimate at full strength for a typo would be measuring the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from code_evaluation.llm_evaluator import LLMEvaluation
from code_evaluation.sandbox import ExecutionEvidence
from code_evaluation.scoring import CriterionScore
from code_evaluation.static_analysis import StaticSignals


@dataclass
class CompetencyEvidence:
    """What one submission says about one competency."""

    competency_id: str
    score: float
    confidence: float
    evidence_strength: float
    source: str
    misconception_codes: list[str] = field(default_factory=list)


def _criterion_to_competency_weights(question: dict) -> dict[str, dict[str, float]]:
    """Map criterion -> {competency: weight}, from the question's own test metadata.

    Derived from the tests rather than declared separately, so the mapping cannot drift
    away from what is actually measured: if a criterion's tests all probe
    boundary_conditions, that criterion informs boundary_conditions, by construction.
    """
    mapping: dict[str, dict[str, float]] = {}
    for test in question.get("tests", []):
        for criterion, criterion_weight in (test.get("criterion_weights") or {}).items():
            bucket = mapping.setdefault(criterion, {})
            for competency, competency_weight in (test.get("competency_weights") or {}).items():
                bucket[competency] = bucket.get(competency, 0.0) + criterion_weight * competency_weight

    # Any criterion with no test mapping falls back to the question's own competency
    # weights, so a criterion scored only by static analysis or the model still lands
    # somewhere rather than being silently dropped.
    default = {c["competency_id"]: c["weight"] for c in question["competencies"]}
    for criterion in {c["criterion_id"] for c in question["rubric_criteria"]}:
        if criterion not in mapping or not mapping[criterion]:
            mapping[criterion] = dict(default)

    for criterion, weights in mapping.items():
        total = sum(weights.values())
        if total > 0:
            mapping[criterion] = {k: v / total for k, v in weights.items()}
    return mapping


def evidence_strength(evidence: ExecutionEvidence, llm: LLMEvaluation) -> float:
    """How much this submission is entitled to move an estimate, in [0, 1].

    Capped hard for submissions that never ran: section 12.3. A learner whose code did not
    compile has shown a syntax problem, not an absence of every competency the question
    touches, and letting that update at full strength would drag unrelated estimates down.
    """
    if evidence.infrastructure_error:
        return 0.0  # our fault; must not touch the learner model at all
    if not evidence.compiled:
        return 0.25
    if not evidence.execution_completed:
        return 0.4
    strength = 1.0
    if llm.available and llm.evaluation_confidence < 0.5:
        strength *= 0.8
    return strength


# Which competency a structural warning is evidence ABOUT. Static analysis knows the
# defect but not the blueprint, so the mapping lives here where the question is in scope.
_WARNING_TO_COMPETENCY = {
    "MISSING_EMPTY_INPUT_GUARD": ("boundary_conditions",),
    "HARDCODED_OUTPUT": ("algorithmic_reasoning",),
    "NESTED_LOOPS": ("complexity_awareness", "algorithmic_reasoning"),
    "MUTATES_INPUT": ("data_structures",),
    "SYNTAX_ERROR": (),
}


def _competencies_for_misconception(code: str, question: dict) -> list[str]:
    """Attach a structural warning to the competencies this question actually assesses.

    Restricted to the question's own blueprint: flagging a competency the question does
    not assess would put a misconception on a learner's record from a question that never
    tested it.
    """
    assessed = {c["competency_id"] for c in question["competencies"]}
    return [c for c in _WARNING_TO_COMPETENCY.get(code, ()) if c in assessed]


def normalize(
    question: dict,
    criterion_scores: list[CriterionScore],
    evidence: ExecutionEvidence,
    llm: LLMEvaluation,
    signals: StaticSignals | None = None,
) -> list[CompetencyEvidence]:
    """Project criterion scores onto competencies."""
    mapping = _criterion_to_competency_weights(question)
    strength = evidence_strength(evidence, llm)

    misconceptions_by_competency: dict[str, list[str]] = {}

    # Static analysis emits misconception codes too, and they must reach the learner model
    # on EVERY approach. Routing them only through the model would have made approach A
    # score 0% misconception recall by construction — measuring which approaches call an
    # LLM rather than which ones diagnose correctly, and quietly rigging the comparison
    # this whole study exists to settle.
    static_codes = [w.split(":")[0] for w in (signals.warnings if signals else [])]
    for code in static_codes:
        for competency in _competencies_for_misconception(code, question):
            misconceptions_by_competency.setdefault(competency, []).append(code)

    for item in llm.criterion_evidence:
        if item.misconception_code:
            misconceptions_by_competency.setdefault(item.competency_id, []).append(
                item.misconception_code
            )

    accumulated: dict[str, list[tuple[float, float, float]]] = {}
    for criterion in criterion_scores:
        for competency, weight in mapping.get(criterion.criterion_id, {}).items():
            if weight <= 0:
                continue
            accumulated.setdefault(competency, []).append(
                (criterion.score, weight, criterion.confidence)
            )

    results: list[CompetencyEvidence] = []
    for competency, parts in sorted(accumulated.items()):
        total_weight = sum(w for _, w, _ in parts)
        if total_weight <= 0:
            continue
        score = sum(s * w for s, w, _ in parts) / total_weight
        confidence = sum(c * w for _, w, c in parts) / total_weight
        used_llm = any("llm" in cs.sources_used for cs in criterion_scores)
        results.append(
            CompetencyEvidence(
                competency_id=competency,
                score=round(score, 4),
                confidence=round(confidence, 3),
                evidence_strength=round(strength * total_weight / max(total_weight, 1.0), 3),
                source="hybrid" if used_llm else "objective",
                misconception_codes=misconceptions_by_competency.get(competency, []),
            )
        )
    return results
