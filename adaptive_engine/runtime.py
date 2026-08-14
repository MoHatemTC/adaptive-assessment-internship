"""The public runtime: start, advance, decide — a stateless wrapper over the engine loop.

    compiled assessment + previous adaptive state + optional response
                               |
                 new adaptive state + decision

Every decision is made by ``cat_engine``'s Orchestrator — the proven loop: per-competency
grading and rollup, person-fit checks, graph propagation, coverage and modality gates,
the shipped stopping rules, and the full report. This layer contributes only the
stateless boundary around it:

- threading the engine state and RNG in and out as one JSON-safe ``AdaptiveState``,
- pinning a state to its assessment id, version and content hash,
- rejecting stale, duplicate and out-of-order responses,
- accepting host-graded evidence (per-competency scores) for non-mcq modalities,
- never mutating the caller's state (the engine state is deep-copied at entry).

The LLM-assisted question pick is deliberately disabled (``use_llm=False`` everywhere):
measured against the deterministic choice it agreed 353/353 times, and a pure state
transition must not do network I/O. Selection randomness (exposure control) comes from
the RNG state carried in ``AdaptiveState``, so replay is exact on any process.
"""

from __future__ import annotations

from collections.abc import Coroutine, Mapping
from typing import Any, Literal

import numpy as np
from pydantic import Field, ValidationError, field_validator

from adaptive_engine.compilation import CompiledAssessment
from adaptive_engine.errors import (
    AssessmentFinished,
    InvalidAnswer,
    InvalidInitialCompetency,
    InvalidState,
    StaleResponse,
    StateMismatch,
)
from adaptive_engine.models import FrozenModel
from adaptive_engine.state import STATE_SCHEMA_VERSION, AdaptiveState
from cat_engine import projection
from cat_engine.contracts import AssessmentReportDTO, PresentingDTO
from cat_engine.engine.schemas.orchestration import (
    AssessmentState,
    BankItem,
    GradedResponse,
)


class InitialCompetency(FrozenModel):
    """A caller-supplied starting belief: a level on the 1-5 scale and how much to trust it.

    Seeds the prior only — never counted as observed evidence. The engine models trust as
    narrow-versus-wide prior; confidence at or above 0.5 selects the narrow (trusted)
    prior, below it the wide one.
    """

    level: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)

    # ValueError, not TypeError, below: pydantic only converts ValueError into a
    # field-level ValidationError; a TypeError would escape raw.

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


class GradedAnswer(FrozenModel):
    """A host-side grading result for one competency measurement.

    ``weight`` is how much of a full observation this evidence carries; left ``None`` it
    defaults to the item's declared loading scaled by ``confidence`` — the same rule the
    engine's own voice grader applies. An explicit ``weight=0`` records "graded, no
    usable evidence": the engine skips it entirely rather than counting a hollow
    observation.
    """

    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    weight: float | None = Field(default=None, ge=0.0, le=1.0)


class QuestionResponse(FrozenModel):
    """The caller's answer to the currently presented question.

    ``question_id`` is mandatory and always checked against the state's presented
    question — the engine never infers which question an answer belongs to.

    ``answer`` by question modality:

    - mcq: the chosen option index (int). Graded deterministically inside the engine.
    - code / open / voice: a ``GradedAnswer`` (one grade for every measured competency),
      or ``{sub_competency: GradedAnswer}`` for per-competency evidence — the host runs
      its grader and hands in only the validated outcome.
    """

    question_id: str = Field(min_length=1)
    answer: int | GradedAnswer | dict[str, GradedAnswer] = Field(
        union_mode="left_to_right"
    )

    @field_validator("answer", mode="before")
    @classmethod
    def _no_ambiguous_scalars(cls, value: object) -> object:
        if isinstance(value, bool | str | float):
            raise ValueError(  # noqa: TRY004 — pydantic converts ValueError only
                "answer must be an option index, a GradedAnswer, or a mapping of "
                "competency to GradedAnswer"
            )
        return value


class AdaptiveDecision(FrozenModel):
    """What every runtime call returns: the new state, plus what to do next.

        status "question"   -> question set,  report None,     converged False
        status "completed"  -> question None, report set,      converged True
        status "terminated" -> question None, report set,      converged False

    ``question`` is the engine's candidate-safe presentation DTO — no answer keys, no
    hidden tests, no reference solutions, by type. ``report`` is the engine's full report
    DTO: per-competency estimates with credible intervals and band probabilities,
    not-assessed statuses, per-competency stop reasons, graph provenance.
    """

    status: Literal["question", "completed", "terminated"]
    state: AdaptiveState
    question: PresentingDTO | None = None
    report: AssessmentReportDTO | None = None
    #: The engine's stop-reason vocabulary, e.g. ``all_variables_finalised``,
    #: ``item_budget``, ``time_limit``, ``no_candidates_available``.
    stop_reason: str | None = None
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
            valid = self.question is None and self.report is not None and self.converged
        else:
            valid = (
                self.question is None
                and self.report is not None
                and not self.converged
            )
        if not valid:  # pragma: no cover — guards engine bugs, not caller input
            raise ValueError(f"decision invariants violated for status {self.status!r}")


def start_assessment(
    assessment: CompiledAssessment,
    initial_competencies: Mapping[str, InitialCompetency] | None = None,
    seed: int | None = None,
) -> AdaptiveDecision:
    """Begin a run: seed beliefs, pick the first question, return state + decision.

    ``seed`` exists for deterministic replay; omitting it draws OS entropy once, after
    which the whole run is reproducible from the state alone.
    """
    _require_compiled(assessment)
    initial = _validated_initial(assessment, initial_competencies)
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise TypeError("seed must be an integer or None")

    rng = np.random.default_rng(seed)
    engine_state = assessment.orchestrator.begin(
        list(assessment.targets),
        intake={c: belief.level for c, belief in initial.items()},
        confidence={c: belief.confidence >= 0.5 for c, belief in initial.items()},
    )
    engine_state = _complete(
        assessment.orchestrator.fill_queue(engine_state, use_llm=False, rng=rng)
    )
    return _decide(assessment, engine_state, rng)


def advance_assessment(
    assessment: CompiledAssessment,
    state: AdaptiveState,
    response: QuestionResponse,
) -> AdaptiveDecision:
    """Apply one response and return the next decision. Never mutates ``state``."""
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
    if state.content_hash != assessment.content_hash:
        raise StateMismatch(
            "the assessment content changed since this state was created (content hash "
            "mismatch) — the definition was edited without changing its version"
        )

    # Deep copy: the caller's state object must survive this call bit-for-bit, whatever
    # the engine loop does with the copy.
    engine_state = state.engine_state.model_copy(deep=True)
    orchestrator = assessment.orchestrator

    stopped, _reason = orchestrator.should_stop(engine_state)
    if stopped:
        raise AssessmentFinished(
            "this assessment already finished; it cannot accept another response"
        )
    if engine_state.presenting is None:
        raise InvalidState("state presents no question yet was not stopped")
    if response.question_id != engine_state.presenting.item_id:
        if response.question_id in engine_state.served_item_ids:
            raise StaleResponse(
                f"question {response.question_id} was already answered; the current "
                f"question is {engine_state.presenting.item_id}"
            )
        raise StaleResponse(
            f"the response answers {response.question_id!r} but the current question "
            f"is {engine_state.presenting.item_id!r}"
        )

    pair = orchestrator.next_item(engine_state)
    if pair is None:
        raise InvalidState(
            f"state references item {engine_state.presenting.item_id!r}, which this "
            "assessment does not contain"
        )
    item, _candidate = pair

    raw_answer, graded = _grading_for(item, response.answer)
    try:
        engine_state, _graded = orchestrator.record_response(
            engine_state, item, raw_answer, graded=graded
        )
    except (TypeError, ValueError) as exc:
        raise InvalidAnswer(f"question {item.item_id}: {exc}") from exc

    rng = _restore_rng(state.rng_state)
    engine_state = _complete(
        orchestrator.after_response(engine_state, item, use_llm=False, rng=rng)
    )
    return _decide(assessment, engine_state, rng)


def _grading_for(
    item: BankItem, answer: int | GradedAnswer | dict[str, GradedAnswer]
) -> tuple[object, GradedResponse | None]:
    """Validate the answer against the item's modality; build host-graded evidence.

    Returns ``(raw_answer, None)`` for mcq — the engine grades it deterministically — or
    ``(None, GradedResponse)`` carrying the host's per-competency outcomes for every
    other modality.
    """
    if item.modality == "mcq":
        if not isinstance(answer, int) or isinstance(answer, bool):
            raise InvalidAnswer(
                f"question {item.item_id} is mcq: the answer must be an integer option "
                f"index, got {type(answer).__name__}"
            )
        return answer, None

    declared = {entry.variable: entry.weight for entry in item.measures}
    if isinstance(answer, GradedAnswer):
        by_variable = dict.fromkeys(declared, answer)
    elif isinstance(answer, Mapping):
        unknown = sorted(set(answer) - set(declared))
        if unknown:
            raise InvalidAnswer(
                f"question {item.item_id}: graded answers name competencies it does not "
                f"measure: {unknown}"
            )
        if not answer:
            raise InvalidAnswer(
                f"question {item.item_id}: the graded-answer mapping is empty"
            )
        by_variable = dict(answer)
    else:
        raise InvalidAnswer(
            f"question {item.item_id} is {item.modality}: the answer must be a "
            f"GradedAnswer or a mapping of competency to GradedAnswer, got "
            f"{type(answer).__name__}"
        )

    outcomes = [
        {
            "variable": variable,
            "score": graded_answer.score,
            "weight": (
                graded_answer.weight
                if graded_answer.weight is not None
                else declared[variable] * graded_answer.confidence
            ),
            "confidence": graded_answer.confidence,
            "source_item_id": item.item_id,
            "modality": item.modality,
        }
        for variable, graded_answer in by_variable.items()
    ]
    return None, GradedResponse(
        item_id=item.item_id,
        modality=item.modality,
        outcomes=outcomes,
        detail={"source": "host_graded"},
    )


def _decide(
    assessment: CompiledAssessment,
    engine_state: AssessmentState,
    rng: np.random.Generator,
) -> AdaptiveDecision:
    """The one transition tail shared by start and advance: present or conclude."""
    orchestrator = assessment.orchestrator
    engine_state = orchestrator.ensure_presenting(engine_state)
    stopped, reason = orchestrator.should_stop(engine_state)

    if stopped:
        report = projection.report_dto(orchestrator.summarise(engine_state, reason))
        converged = bool(report.variables) and all(
            row.converged for row in report.variables
        )
        return AdaptiveDecision(
            status="completed" if converged else "terminated",
            state=_wrap(assessment, engine_state, rng),
            report=report,
            stop_reason=reason,
            converged=converged,
        )

    pair = orchestrator.next_item(engine_state)
    if pair is None:  # pragma: no cover — presenting is set and the bank is immutable
        raise InvalidState("no presentable question in a running assessment")
    item, candidate = pair
    return AdaptiveDecision(
        status="question",
        state=_wrap(assessment, engine_state, rng),
        question=projection.presenting(item, candidate),
    )


def _wrap(
    assessment: CompiledAssessment,
    engine_state: AssessmentState,
    rng: np.random.Generator,
) -> AdaptiveState:
    return AdaptiveState(
        assessment_id=assessment.assessment_id,
        assessment_version=assessment.version,
        content_hash=assessment.content_hash,
        engine_state=engine_state,
        rng_state=rng.bit_generator.state,
    )


def _restore_rng(rng_state: dict[str, Any]) -> np.random.Generator:
    rng = np.random.default_rng()
    try:
        rng.bit_generator.state = rng_state
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidState(f"rng_state is not a valid generator state: {exc}") from exc
    return rng


def _complete(coroutine: Coroutine[Any, Any, AssessmentState]) -> AssessmentState:
    """Run an engine coroutine that never truly suspends on the deterministic path.

    With ``use_llm=False`` the loop performs no I/O, so the coroutine finishes on its
    first step — no event loop required, and safe to call from inside a host's own
    running loop (where ``asyncio.run`` would refuse).
    """
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    coroutine.close()
    raise RuntimeError(
        "the engine attempted asynchronous I/O on the deterministic path"
    )  # pragma: no cover — use_llm=False performs no I/O


def _require_compiled(assessment: object) -> None:
    if not isinstance(assessment, CompiledAssessment):
        raise TypeError(
            "assessment must be the result of compile_assessment(definition), "
            f"got {type(assessment).__name__}"
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
    known = set(assessment.targets)
    validated: dict[str, InitialCompetency] = {}
    for competency_id, value in initial_competencies.items():
        if competency_id not in known:
            raise InvalidInitialCompetency(
                f"initial competency {competency_id!r} is not a target of assessment "
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
