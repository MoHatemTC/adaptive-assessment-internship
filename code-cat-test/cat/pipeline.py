"""One adaptive step, end to end (section 3), plus the audit record (section 18).

This is the only module that knows the whole sequence. Each stage is independently
testable and the wiring lives here, so a change to how submissions are scored cannot
accidentally alter how questions are selected.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cat.competency_state import LearnerModel, update
from cat.config import settings
from code_evaluation import scoring
from code_evaluation.evidence_normalizer import CompetencyEvidence, normalize
from code_evaluation.llm_evaluator import LLMEvaluation, evaluate as llm_evaluate
from code_evaluation.sandbox import ExecutionEvidence, run_submission
from code_evaluation.scoring import CriterionScore
from code_evaluation.static_analysis import StaticSignals, analyse
from code_evaluation.weight_profile import WeightProfile

BANK_PATH = Path(__file__).resolve().parent.parent / "bank" / "questions.json"


def load_bank(path: Path | str = BANK_PATH) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class EvaluationResult:
    """Everything one submission produced, from raw execution to competency evidence."""

    question_id: str
    approach: str
    rubric_id: str
    execution: ExecutionEvidence
    signals: StaticSignals
    llm: LLMEvaluation
    criterion_scores: list[CriterionScore]
    competency_evidence: list[CompetencyEvidence]
    overall_score: float | None
    evaluation_latency_ms: int
    flags: list[str] = field(default_factory=list)
    # The split that produced these scores. Carried on the result, not looked up later:
    # an admin can change the weights between submissions, so a score is only
    # reproducible if it travels with the weights that made it.
    weight_profile: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.execution.usable


def active_profile(approach: str | None = None) -> WeightProfile:
    """The split in force: the admin override if one is set, else the approach preset."""
    approach = approach or settings.code_cat_approach
    override = settings.llm_share_override()
    preset = WeightProfile.from_preset(approach, scoring.SOURCE_WEIGHTS)
    return preset.with_llm_shares(override) if override else preset


def evaluate_submission(
    question: dict,
    code: str,
    *,
    approach: str | None = None,
    rubric_id: str | None = None,
    profile: WeightProfile | None = None,
) -> EvaluationResult:
    """Run one submission through the whole evaluation layer.

    The LLM is called when the profile gives it weight, or when it is wanted for
    diagnosis alone (`llm_diagnosis_when_unweighted`). With that off, approach A does not
    call it at all — not merely down-weight it — so a measured cost difference between the
    approaches is a real one rather than tokens spent and discarded.

    Diagnosis without score authority is a supported configuration precisely because the
    two are worth such different amounts: misconception codes reach the learner model
    through normalize() regardless of criterion weights, so a deployment can keep 0.92
    recall while letting deterministic evidence decide the number.
    """
    approach = approach or settings.code_cat_approach
    rubric_id = rubric_id or settings.code_cat_rubric
    started = time.time()

    execution = run_submission(code, question["tests"], question["function_name"])
    signals = analyse(code, question["function_name"])

    profile = profile or active_profile(approach)
    # Driven by the profile, not the approach: an admin who has taken every criterion to
    # 0% model share should stop paying for model calls, and one who has given the model
    # weight under approach A must actually get them.
    llm_has_weight = profile.uses_llm() or settings.llm_diagnosis_when_unweighted
    llm = (
        llm_evaluate(question, code, execution, signals, rubric_id)
        if llm_has_weight and execution.usable
        else LLMEvaluation(available=False)
    )

    # An unusable run measured nothing, so every criterion is unscored. Deriving them
    # anyway produced a full score sheet for a submission that never executed: the
    # passed-test ratio reads 0.0 because zero of N tests passed, static analysis happily
    # scores the source, and the UI showed "Overall 0.537" beside "nothing about this
    # submission was measured". A ratio over tests that never ran is not a measurement.
    if not execution.usable:
        criterion_scores = [
            CriterionScore(c["criterion_id"], None, {}, 0.0, "NOT_ASSESSED")
            for c in question["rubric_criteria"]
        ]
        return EvaluationResult(
            question_id=question["question_id"], approach=approach, rubric_id=rubric_id,
            execution=execution, signals=signals, llm=llm,
            criterion_scores=criterion_scores,
            competency_evidence=normalize(question, criterion_scores, execution, llm, signals),
            overall_score=None,
            evaluation_latency_ms=int((time.time() - started) * 1000),
            flags=[f"SANDBOX_UNAVAILABLE: {execution.error_message[:160]}"],
            weight_profile=profile.to_dict(),
        )

    objective = scoring.objective_criterion_scores(execution, signals, question["tests"])
    static = scoring.static_criterion_scores(signals)

    criterion_scores: list[CriterionScore] = []
    for criterion in question["rubric_criteria"]:
        cid = criterion["criterion_id"]
        llm_score, llm_confidence = llm.score_for(cid)
        criterion_scores.append(
            scoring.combine(
                cid,
                tests=objective.get(cid),
                static=static.get(cid),
                llm=llm_score,
                llm_confidence=llm_confidence or 1.0,
                approach=approach,
                profile=profile,
            )
        )

    # Structure can void the inference from a passing test to a demonstrated competency.
    # Applied before normalisation so the learner model sees the cap, not just the report.
    criterion_scores, integrity_reason = scoring.apply_integrity_cap(criterion_scores, signals)

    competency_evidence = normalize(question, criterion_scores, execution, llm, signals)
    # None, not 0.0, when nothing could be assessed: an unusable run has no score, and
    # showing 0.0 (or the 0.5 an averaged stub produced) reads as a measurement.
    overall = scoring.overall_score(criterion_scores, question)

    return EvaluationResult(
        question_id=question["question_id"],
        approach=approach,
        rubric_id=rubric_id,
        execution=execution,
        signals=signals,
        llm=llm,
        criterion_scores=criterion_scores,
        competency_evidence=competency_evidence,
        overall_score=round(overall, 4) if overall is not None else None,
        evaluation_latency_ms=int((time.time() - started) * 1000),
        flags=[
            *llm.flags,
            *([integrity_reason] if integrity_reason else []),
            *{c.conflict_flag for c in criterion_scores if c.conflict_flag},
        ],
        weight_profile=profile.to_dict(),
    )


def apply_to_learner(model: LearnerModel, result: EvaluationResult) -> LearnerModel:
    """Update the learner model — but never from an unusable run.

    An infrastructure failure must not touch a candidate's record. Section 19: test
    infrastructure failure is not learner failure.
    """
    if not result.usable:
        return model
    return update(model, result.competency_evidence)


def audit_record(
    session_id: str,
    step: int,
    result: EvaluationResult,
    before: dict,
    after: dict,
    selection=None,
) -> dict:
    """The row that makes a run reconstructable afterwards (section 18)."""
    changed = {
        cid: {"before": before.get(cid, {}).get("mastery"), "after": state["mastery"]}
        for cid, state in after.items()
        if before.get(cid, {}).get("mastery") != state["mastery"]
    }
    record = {
        "session_id": session_id,
        "step_number": step,
        "approach": result.approach,
        "rubric_id": result.rubric_id,
        # Without this the record says which APPROACH ran but not which weights, and
        # under a tunable split those are no longer the same thing.
        "weight_fingerprint": result.weight_profile.get("fingerprint", ""),
        "weight_shares": result.weight_profile.get("overall_shares", {}),
        "question_type": "code",
        "answered_question_id": result.question_id,
        "passed_test_ratio": round(result.execution.passed_test_ratio, 4),
        "compiled": result.execution.compiled,
        "objective_score": round(result.execution.passed_test_ratio, 4),
        "overall_score": result.overall_score,
        "evaluation_confidence": result.llm.evaluation_confidence,
        "llm_used": result.llm.available,
        "updated_competencies": changed,
        "flags": result.flags,
        "evaluation_latency_ms": result.evaluation_latency_ms,
        "input_tokens": result.llm.input_tokens,
        "output_tokens": result.llm.output_tokens,
    }
    if selection is not None:
        record.update(
            {
                "code_best_question_id": selection.shortlist_ids[0] if selection.shortlist_ids else None,
                "llm_selected_question_id": selection.question["question_id"],
                "llm_selected_rank": selection.rank,
                "best_utility_score": selection.best_utility,
                "selected_utility_score": selection.utility,
                "normalized_regret": round(selection.normalized_regret, 4),
                "valid_selection": not selection.fallback_used,
                "fallback_used": selection.fallback_used,
                "llm_confidence": selection.llm_confidence,
                "reason_code": selection.reason_code,
            }
        )
    return record


def result_to_dict(result: EvaluationResult) -> dict:
    """JSON-safe view, for persistence and the measurement harness."""
    return {
        "question_id": result.question_id,
        "approach": result.approach,
        "rubric_id": result.rubric_id,
        # Without this the record says which APPROACH ran but not which weights, and
        # under a tunable split those are no longer the same thing.
        "weight_fingerprint": result.weight_profile.get("fingerprint", ""),
        "weight_shares": result.weight_profile.get("overall_shares", {}),
        "overall_score": result.overall_score,
        "passed_test_ratio": round(result.execution.passed_test_ratio, 4),
        "compiled": result.execution.compiled,
        "failed_test_ids": [o.test_id for o in result.execution.test_results if not o.passed],
        "criterion_scores": [asdict(c) for c in result.criterion_scores],
        "competency_evidence": [asdict(c) for c in result.competency_evidence],
        "static_analysis": result.signals.as_dict(),
        "llm_available": result.llm.available,
        "llm_diagnostic": result.llm.overall_diagnostic,
        "misconceptions": sorted(
            {c for e in result.competency_evidence for c in e.misconception_codes}
        ),
        "flags": result.flags,
        "latency_ms": result.evaluation_latency_ms,
        "tokens": {"input": result.llm.input_tokens, "output": result.llm.output_tokens},
    }
