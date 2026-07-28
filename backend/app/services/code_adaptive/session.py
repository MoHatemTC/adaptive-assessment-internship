"""The public entry point: run one competency, submission by submission.

`CodeAdaptiveSession` is stateless between calls — it takes a `SessionState`, returns a
new one, and never holds a session in memory. That is what lets an assessment span HTTP
requests, be persisted between them, and resume on a different worker.

Typical use:

    session = CodeAdaptiveSession(JsonQuestionRepository())
    state = session.begin("T1.4", self_rating=3)

    while True:
        selected = session.next_question(state)
        if selected is None:              # bank exhausted
            break

        # present the question to the candidate, collect their code
        state, result, stop = session.record_submission(state, selected.question_id, code)
        if stop.should_stop:
            break

    report = session.summarise(state, stop)

WHAT IS NEVER DELEGATED

The model interprets evidence and picks from a shortlist. It does not grade, does not
write a competency estimate, and cannot stop an assessment. Every number in the report is
computed by deterministic code from inputs that are recorded alongside it.
"""

from __future__ import annotations

import logging
import time
import uuid

from app.config.settings import settings
from app.schemas.code_adaptive import (
    CompetencyReport,
    CompetencySnapshot,
    SelectedQuestion,
    SessionState,
    StopDecision,
    SubmissionResult,
)
from app.services.code_adaptive import scoring
from app.services.code_adaptive.bank import QuestionRepository
from app.services.code_adaptive.competency import CompetencyState, LearnerModel, update
from app.services.code_adaptive.evidence import normalize
from app.services.code_adaptive.execution import run_submission
from app.services.code_adaptive.llm_evaluator import LLMEvaluation
from app.services.code_adaptive.llm_evaluator import evaluate as llm_evaluate
from app.services.code_adaptive.selection import (
    choose,
    evaluate_stop,
    filter_candidates,
    rank_candidates,
)
from app.services.code_adaptive.static_analysis import analyse
from app.services.code_adaptive.weights import WeightProfile

logger = logging.getLogger(__name__)

# A self-rating is weak evidence and seeds the prior only: enough to bias the first
# question's difficulty, far too little to survive contrary evidence. `observed` stays
# False so the UI never reports a self-rating as a measurement.
SELF_RATING_STRENGTH = 0.8


def active_profile(approach: str | None = None) -> WeightProfile:
    """The scoring split in force: the operator override if set, else the approach preset."""
    approach = approach or settings.code_approach
    preset = WeightProfile.from_preset(approach, scoring.SOURCE_WEIGHTS)
    override = settings.llm_share_override()
    return preset.with_llm_shares(override) if override else preset


class CodeAdaptiveSession:
    """Drives one competency of a code-question adaptive assessment."""

    def __init__(self, repository: QuestionRepository) -> None:
        self._repository = repository

    # --- state <-> learner model ------------------------------------------
    @staticmethod
    def _to_model(state: SessionState) -> LearnerModel:
        model = LearnerModel()
        for competency_id, raw in state.competencies.items():
            model.states[competency_id] = CompetencyState(
                competency_id=competency_id,
                alpha=float(raw.get("alpha", 1.0)),
                beta=float(raw.get("beta", 1.0)),
                evidence_count=int(raw.get("evidence_count", 0)),
                misconception_codes=list(raw.get("misconception_codes", [])),
                last_updated_by=str(raw.get("last_updated_by", "init")),
            )
        return model

    @staticmethod
    def _from_model(model: LearnerModel) -> dict[str, dict]:
        return {
            cid: {
                "alpha": s.alpha,
                "beta": s.beta,
                "evidence_count": s.evidence_count,
                "misconception_codes": list(s.misconception_codes),
                "last_updated_by": s.last_updated_by,
            }
            for cid, s in model.states.items()
        }

    # --- lifecycle ---------------------------------------------------------
    def begin(self, target_competency: str, self_rating: int | None = None) -> SessionState:
        """Open a session scoped to one competency.

        The target scopes everything: which questions are eligible, which competency the
        utility function aims at, and which one the stopping rule judges precision on.
        """
        model = LearnerModel()
        if self_rating is not None:
            state = model.get(target_competency)
            target = (float(self_rating) - 1.0) / 4.0
            state.alpha += SELF_RATING_STRENGTH * target
            state.beta += SELF_RATING_STRENGTH * (1.0 - target)
            state.last_updated_by = "self_rating"

        return SessionState(
            session_id=f"code_{uuid.uuid4().hex[:12]}",
            target_competency=target_competency,
            competencies=self._from_model(model),
            started_at=time.time(),
        )

    def next_question(self, state: SessionState, *, use_llm: bool = True) -> SelectedQuestion | None:
        """Pick the next question, or None when nothing eligible remains."""
        model = self._to_model(state)
        bank = [q.model_dump() for q in self._repository.all_questions()]

        candidates = filter_candidates(
            bank, set(state.answered_question_ids), model, state.target_competency
        )
        if not candidates:
            return None

        ranked = rank_candidates(candidates, model, state.target_competency)
        decision = choose(ranked, model, use_llm=use_llm, target_competency=state.target_competency)
        if decision is None:
            return None

        return SelectedQuestion(
            question_id=decision.question["question_id"],
            rank=decision.rank,
            utility=round(decision.utility, 4),
            best_utility=round(decision.best_utility, 4),
            normalized_regret=round(decision.normalized_regret, 4),
            engine_top_pick=decision.shortlist_ids[0] if decision.shortlist_ids else "",
            chosen_by_llm=decision.chosen_by_llm,
            fallback_used=decision.fallback_used,
            reason_code=decision.reason_code,
            reason=decision.reason,
            criterion=str(ranked[0].signals.get("criterion", "")),
            shortlist_ids=list(decision.shortlist_ids),
            flags=list(decision.flags),
        )

    def record_submission(
        self, state: SessionState, question_id: str, code: str
    ) -> tuple[SessionState, SubmissionResult, StopDecision]:
        """Grade one submission, update the learner model, and re-evaluate stopping.

        Returns a NEW state; the input is not mutated, so a caller can persist the result
        of a step without worrying about aliasing the one they still hold.
        """
        question = self._repository.get(question_id)
        if question is None:
            raise ValueError(f"unknown question: {question_id}")

        result = self.evaluate(question.model_dump(), code)
        model = self._to_model(state)

        # Only real evidence moves an estimate. An unusable run carries strength 0 and is
        # skipped entirely by update(), so an infrastructure failure never becomes a
        # candidate's inability.
        update(model, result["competency_evidence"])

        answered = [*state.answered_question_ids, question_id]
        elapsed = (time.time() - state.started_at) / 60.0 if state.started_at else 0.0
        remaining = len(
            filter_candidates(
                [q.model_dump() for q in self._repository.all_questions()],
                set(answered), model, state.target_competency,
            )
        )
        stop = evaluate_stop(model, len(answered), elapsed, remaining, state.target_competency)

        new_state = SessionState(
            session_id=state.session_id,
            target_competency=state.target_competency,
            answered_question_ids=answered,
            competencies=self._from_model(model),
            started_at=state.started_at,
            elapsed_minutes=round(elapsed, 3),
        )
        return new_state, result["report"], StopDecision(**stop.__dict__)

    # --- evaluation --------------------------------------------------------
    def evaluate(self, question: dict, code: str, profile: WeightProfile | None = None) -> dict:
        """Run one submission through execution, analysis, scoring and projection.

        Exposed so a submission can be graded outside a session — reviewing a past answer,
        or calibrating a bank — without the caller reconstructing the pipeline.
        """
        started = time.time()
        profile = profile or active_profile()

        execution = run_submission(code, question["tests"], question["function_name"])
        signals = analyse(code, question["function_name"])

        # Driven by the profile, not the approach: an operator who has taken every share to
        # zero should stop paying for model calls, and diagnosis can be bought separately.
        wants_llm = profile.uses_llm() or settings.code_llm_diagnosis_when_unweighted
        llm = (
            llm_evaluate(question, code, execution, signals, settings.code_rubric)
            if wants_llm and execution.usable
            else LLMEvaluation(available=False)
        )

        # An unusable run measured nothing, so every criterion is unscored. Deriving them
        # anyway produced a full score sheet for a submission that never executed: the
        # passed-test ratio reads 0.0 because zero of N tests passed, and a ratio over
        # tests that never ran is not a measurement.
        if not execution.usable:
            criterion_scores = [
                scoring.CriterionScore(c["criterion_id"], None, {}, 0.0, "NOT_ASSESSED")
                for c in question["rubric_criteria"]
            ]
            integrity_reason = ""
        else:
            objective = scoring.objective_criterion_scores(execution, signals, question["tests"])
            static = scoring.static_criterion_scores(signals)
            criterion_scores = [
                scoring.combine(
                    c["criterion_id"],
                    tests=objective.get(c["criterion_id"]),
                    static=static.get(c["criterion_id"]),
                    llm=llm.score_for(c["criterion_id"])[0],
                    llm_confidence=llm.score_for(c["criterion_id"])[1] or 1.0,
                    profile=profile,
                )
                for c in question["rubric_criteria"]
            ]
            # Structure can void the inference from a passing test to a demonstrated
            # competency. Applied before projection so the learner model sees the cap too.
            criterion_scores, integrity_reason = scoring.apply_integrity_cap(criterion_scores, signals)

        competency_evidence = normalize(question, criterion_scores, execution, llm, signals)
        overall = scoring.overall_score(criterion_scores, question) if execution.usable else None

        flags = [
            *llm.flags,
            *([integrity_reason] if integrity_reason else []),
            *sorted({c.conflict_flag for c in criterion_scores if c.conflict_flag}),
        ]
        if not execution.usable:
            flags.append(f"SANDBOX_UNAVAILABLE: {execution.error_message[:160]}")

        report = SubmissionResult(
            question_id=question["question_id"],
            approach=settings.code_approach,
            rubric_id=settings.code_rubric,
            weight_fingerprint=profile.fingerprint(),
            weight_shares=profile.overall_shares(scoring.criterion_weights(question)),
            compiled=execution.compiled,
            passed_tests=execution.passed_tests,
            total_tests=execution.total_tests,
            overall_score=overall,
            criterion_scores={c.criterion_id: c.score for c in criterion_scores},
            misconception_codes=sorted({c for e in competency_evidence for c in e.misconception_codes}),
            diagnostic=llm.overall_diagnostic,
            llm_used=llm.available,
            evaluation_latency_ms=int((time.time() - started) * 1000),
            flags=flags,
        )
        return {"report": report, "competency_evidence": competency_evidence}

    # --- reporting ---------------------------------------------------------
    def summarise(self, state: SessionState, stop: StopDecision) -> CompetencyReport:
        """The end-of-session report. Nothing here is inferred; it is all read off the model."""
        model = self._to_model(state)
        target = model.get(state.target_competency)
        return CompetencyReport(
            session_id=state.session_id,
            target_competency=state.target_competency,
            mastery=round(target.mastery, 4),
            standard_error=round(target.standard_error, 4),
            level=target.level,
            band=target.band,
            questions_answered=len(state.answered_question_ids),
            converged=stop.converged,
            stop_reason=stop.reason,
            competencies=[
                CompetencySnapshot(
                    competency_id=cid,
                    mastery=round(s.mastery, 4),
                    standard_error=round(s.standard_error, 4),
                    level=s.level,
                    band=s.band,
                    evidence_count=s.evidence_count,
                    observed=s.observed,
                    misconception_codes=list(s.misconception_codes),
                )
                for cid, s in sorted(model.states.items())
            ],
            open_misconceptions=sorted(
                {c for s in model.states.values() for c in s.misconception_codes}
            ),
        )
