"""The public runtime: start, advance, decide. A pure state transition.

    assessment definition + previous adaptive state + optional response
                               |
                 new adaptive state + decision

The engine stores nothing between calls — no sessions, no users, no caches. The caller
persists ``decision.state`` and sends it back with the next response; two processes
holding the same state and the same compiled assessment make the same decision.

Every call returns the same shape, ``AdaptiveDecision``, whose invariants are enforced
by the model itself:

    status "question"   -> question set,  report None,     converged False
    status "completed"  -> question None, report set,      converged True
    status "terminated" -> question None, report set,      converged False

Concurrency is the host's problem by design: the engine holds no locks, but it makes
invalid transitions detectable — a response for anything but the currently presented
question is rejected as stale, whichever replica sends it.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from adaptive_engine.compilation import CompiledAssessment
from adaptive_engine.convergence import StopReason, converged_competencies
from adaptive_engine.errors import (
    AssessmentFinished,
    InvalidInitialCompetency,
    InvalidState,
    StaleResponse,
    StateMismatch,
)
from adaptive_engine.evidence import GradedAnswer, apply_evidence, evidence_from, grade_answer
from adaptive_engine.graph import record_graph_evidence
from adaptive_engine.irt import prior_from_initial, uniform_prior
from adaptive_engine.models import FrozenModel, Question, QuestionKind
from adaptive_engine.report import AssessmentReport, build_report
from adaptive_engine.selection import eligible_question_ids, open_competencies, select_next_question
from adaptive_engine.state import STATE_SCHEMA_VERSION, AdaptiveState, CompetencyState


class InitialCompetency(FrozenModel):
    """A caller-supplied starting belief: a level on the documented 1-5 scale and how
    much to trust it.

    Seeds the prior — where the search starts — and nothing else: it is never counted as
    observed evidence, and a few responses are enough to override it.
    """

    level: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)

    # ValueError, not TypeError, in the three validators below: pydantic only converts
    # ValueError into a field-level ValidationError; a TypeError would escape raw.

    @field_validator("level", mode="before")
    @classmethod
    def _level_is_an_integer(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("level must be an integer on the 1-5 scale")  # noqa: TRY004
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_is_a_number(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError("confidence must be a number between 0 and 1")  # noqa: TRY004
        return value


class QuestionResponse(FrozenModel):
    """The caller's answer to the currently presented question.

    ``question_id`` is mandatory and always checked against the state's current
    question — the engine never infers which question an answer belongs to. The answer
    is an option index for mcq questions, or a ``GradedAnswer`` for externally graded
    ones; which one is required is validated against the question when the response is
    applied.
    """

    question_id: str = Field(min_length=1)
    answer: int | GradedAnswer = Field(union_mode="left_to_right")

    @field_validator("answer", mode="before")
    @classmethod
    def _no_ambiguous_scalars(cls, value: object) -> object:
        if isinstance(value, bool | str):
            raise ValueError("answer must be an option index or a GradedAnswer")  # noqa: TRY004
        return value


class PresentedQuestion(FrozenModel):
    """The candidate-safe view of a question: what to render, nothing that grades.

    No answer index, no grading data, no selection diagnostics — built by the one
    projection below from an explicit allowlist of fields, so a new internal field can
    never leak by default.
    """

    question_id: str
    kind: QuestionKind
    prompt: str
    options: list[str] | None = None


def present_question(question: Question) -> PresentedQuestion:
    """THE safe projection from the internal question to the candidate-facing one."""
    return PresentedQuestion(
        question_id=question.question_id,
        kind=question.kind,
        prompt=question.prompt,
        options=list(question.options) if question.options is not None else None,
    )


class AdaptiveDecision(FrozenModel):
    """What every runtime call returns: the new state, plus what to do next."""

    status: Literal["question", "completed", "terminated"]
    state: AdaptiveState
    question: PresentedQuestion | None = None
    report: AssessmentReport | None = None
    stop_reason: StopReason | None = None
    converged: bool = False

    def model_post_init(self, __context: object) -> None:
        if self.status == "question":
            valid = (
                self.question is not None
                and self.report is None
                and self.stop_reason is None
                and self.converged is False
            )
        elif self.status == "completed":
            valid = self.question is None and self.report is not None and self.converged is True
        else:
            valid = self.question is None and self.report is not None and self.converged is False
        if not valid:  # pragma: no cover — guards engine bugs, not caller input
            raise ValueError(f"decision invariants violated for status {self.status!r}")


def start_assessment(
    assessment: CompiledAssessment,
    initial_competencies: Mapping[str, InitialCompetency] | None = None,
    seed: int | None = None,
) -> AdaptiveDecision:
    """Begin a run: seed the beliefs, return the initial state and the first question.

    ``seed`` exists for deterministic replay; omitting it draws OS entropy once and
    stores it in the state, after which the whole run is reproducible from the state
    alone.
    """
    _require_compiled(assessment)
    initial = _validated_initial(assessment, initial_competencies)
    if seed is None:
        seed = secrets.randbits(63)
    elif isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise TypeError("seed must be a non-negative integer or None")

    competency_states: dict[str, CompetencyState] = {}
    for competency_id in assessment.competency_ids:
        belief = initial.get(competency_id)
        prior = (
            prior_from_initial(belief.level, belief.confidence)
            if belief is not None
            else uniform_prior()
        )
        # Initial beliefs shape the prior only: observations start at zero and the
        # belief is never counted as an answered question.
        competency_states[competency_id] = CompetencyState(posterior=prior.tolist())

    state = AdaptiveState(
        assessment_id=assessment.assessment_id,
        assessment_version=assessment.version,
        competency_states=competency_states,
        random_seed=seed,
    )
    return _decide(assessment, state)


def advance_assessment(
    assessment: CompiledAssessment,
    state: AdaptiveState,
    response: QuestionResponse,
) -> AdaptiveDecision:
    """Apply one response and return the next decision. Never mutates ``state``.

    The response must answer ``state.current_question_id`` — stale, duplicate and
    out-of-order answers are rejected, and a finished run accepts nothing at all.
    """
    _require_compiled(assessment)
    if not isinstance(state, AdaptiveState):
        raise TypeError("state must be an AdaptiveState")
    if not isinstance(response, QuestionResponse):
        raise TypeError("response must be a QuestionResponse")

    if state.schema_version != STATE_SCHEMA_VERSION:
        raise InvalidState(
            f"state schema_version {state.schema_version} is not supported "
            f"(expected {STATE_SCHEMA_VERSION})"
        )
    if (state.assessment_id, state.assessment_version) != (
        assessment.assessment_id,
        assessment.version,
    ):
        raise StateMismatch(
            f"state belongs to {state.assessment_id!r} version "
            f"{state.assessment_version!r}, not {assessment.assessment_id!r} version "
            f"{assessment.version!r}"
        )
    _require_consistent(assessment, state)

    if state.current_question_id is None:
        raise AssessmentFinished(
            "this assessment already finished; it cannot accept another response"
        )
    if response.question_id != state.current_question_id:
        if response.question_id in state.administered_question_ids:
            raise StaleResponse(
                f"question {response.question_id} was already answered; the current "
                f"question is {state.current_question_id}"
            )
        raise StaleResponse(
            f"the response answers {response.question_id!r} but the current question is "
            f"{state.current_question_id!r}"
        )

    question = assessment.questions_by_id[state.current_question_id]
    graded = grade_answer(question, response.answer)
    evidence = evidence_from(question, graded)

    competency_states = dict(state.competency_states)
    for piece in evidence:
        competency_states[piece.competency_id] = apply_evidence(
            competency_states[piece.competency_id], question, piece
        )

    advanced = state.model_copy(
        update={
            "competency_states": competency_states,
            "graph_evidence": record_graph_evidence(state.graph_evidence, evidence),
            "administered_question_ids": [
                *state.administered_question_ids,
                question.question_id,
            ],
            "current_question_id": None,
        }
    )
    return _decide(assessment, advanced)


def _decide(assessment: CompiledAssessment, state: AdaptiveState) -> AdaptiveDecision:
    """The one transition shared by start and advance: stop if earned, else select."""
    converged = converged_competencies(assessment, state)
    policy = assessment.definition.policy

    if len(converged) == len(assessment.competency_ids):
        return _terminal(assessment, state, StopReason.ALL_COMPETENCIES_CONVERGED, converged)

    if len(state.administered_question_ids) >= policy.max_questions_total:
        return _terminal(assessment, state, StopReason.QUESTION_BUDGET, converged)

    question = select_next_question(assessment, state)
    if question is None:
        # Nothing is askable. If any open competency still has questions left, only the
        # per-competency budget stands in the way; otherwise the bank itself ran dry.
        budget_bound = any(
            eligible_question_ids(assessment, state, competency_id)
            for competency_id in open_competencies(assessment, state)
        )
        reason = StopReason.QUESTION_BUDGET if budget_bound else StopReason.QUESTIONS_EXHAUSTED
        return _terminal(assessment, state, reason, converged)

    presenting = state.model_copy(update={"current_question_id": question.question_id})
    return AdaptiveDecision(
        status="question",
        state=presenting,
        question=present_question(question),
    )


def _terminal(
    assessment: CompiledAssessment,
    state: AdaptiveState,
    stop_reason: StopReason,
    converged: frozenset[str],
) -> AdaptiveDecision:
    final = state.model_copy(update={"current_question_id": None})
    completed = stop_reason is StopReason.ALL_COMPETENCIES_CONVERGED
    return AdaptiveDecision(
        status="completed" if completed else "terminated",
        state=final,
        report=build_report(assessment, final, stop_reason, converged),
        stop_reason=stop_reason,
        converged=completed,
    )


def _require_compiled(assessment: object) -> None:
    if not isinstance(assessment, CompiledAssessment):
        raise TypeError(
            "assessment must be the result of compile_assessment(definition), "
            f"got {type(assessment).__name__}"
        )


def _require_consistent(assessment: CompiledAssessment, state: AdaptiveState) -> None:
    """The state must describe THIS assessment's content, not merely share its name."""
    missing = [c for c in assessment.competency_ids if c not in state.competency_states]
    if missing:
        raise InvalidState(f"state is missing competency states for {missing}")
    known_questions = assessment.questions_by_id
    for question_id in (state.current_question_id, *state.administered_question_ids):
        if question_id is not None and question_id not in known_questions:
            raise InvalidState(
                f"state references question {question_id!r}, which this assessment "
                "does not contain"
            )


def _validated_initial(
    assessment: CompiledAssessment,
    initial_competencies: Mapping[str, InitialCompetency] | None,
) -> dict[str, InitialCompetency]:
    if initial_competencies is None:
        return {}
    if not isinstance(initial_competencies, Mapping):
        raise InvalidInitialCompetency(
            "initial_competencies must map competency ids to InitialCompetency values"
        )
    known = set(assessment.competency_ids)
    validated: dict[str, InitialCompetency] = {}
    for competency_id, value in initial_competencies.items():
        if competency_id not in known:
            raise InvalidInitialCompetency(
                f"initial competency {competency_id!r} is not part of assessment "
                f"{assessment.assessment_id!r}"
            )
        if isinstance(value, InitialCompetency):
            validated[competency_id] = value
            continue
        try:
            validated[competency_id] = InitialCompetency.model_validate(value)
        except ValidationError as exc:
            raise InvalidInitialCompetency(
                f"initial competency {competency_id!r} is invalid: {exc}"
            ) from exc
    return validated
