"""assessment-orchestrator — the loop, the posterior, selection and stopping.

WHAT THIS SERVICE DECIDES, AND WHAT IT DELEGATES

It owns exactly two decisions: WHICH competency to probe next, and WHEN one is finished.
Which item measures a competency belongs to the Picking Agent; how a response is graded
belongs to the grader service; what a response implies about neighbouring nodes belongs to
the graph service. Keeping those apart is what lets a modality be added without touching
the loop.

A model picks an item from a shortlist the engine has already ranked, and interprets code.
It does not grade multiple choice, does not write an estimate, does not choose which
competency to probe, and cannot stop an assessment. Every number in a report is computed by
deterministic code from inputs recorded beside it.

STATELESS ENGINE, STATEFUL SERVICE

The `Orchestrator` takes a state and returns one; it holds nothing. This service holds the
states, in memory, with the monolith's retention rules — see `sessions.py` for why that is
deliberate and what it costs.

THE CANDIDATE BOUNDARY IS HERE

Responses on the assessment routes carry no answer key, no hidden test and no grading
detail. That is enforced by which types they return, not by a client choosing what to
render — see `presentation.py`. The instrumented view lives behind
`AUTHOR_DIAGNOSTICS_ENABLED` and is refused, not thinned, when off.
"""

from __future__ import annotations

import logging

import numpy as np
from adaptive_clients import ServiceRefused, ServiceUnavailable
from adaptive_contracts import (
    AnswerRequest,
    AssessmentReportDTO,
    AssessmentStateResponse,
    BankSummary,
    CreateAssessmentRequest,
    DiagnosticsResponse,
    ErrorResponse,
    PresentingDTO,
    ScopeManifest,
)
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from app.config.settings import settings as engine_settings
from app.schemas.orchestration import AssessmentState, BankItem
from app.schemas.voice import VoiceResponsePackage
from app.services.orchestrator import session_dump
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import diagnostics as diagnostics_view
from . import engine, presentation
from .config import settings
from .sessions import Session, SessionConflict, SessionStore

logger = logging.getLogger(__name__)

RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "no such assessment"},
    409: {"model": ErrorResponse, "description": "the presented item changed"},
    422: {"model": ErrorResponse, "description": "the answer does not fit the item"},
    503: {"model": ErrorResponse, "description": "a dependency is unreachable"},
}

app = FastAPI(
    title="assessment-orchestrator",
    version=settings.release,
    summary="The loop, the posterior, selection and stopping.",
    description=__doc__,
)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    # There is no cookie-authenticated API here. Wildcard origins plus credentials is
    # invalid browser policy and would risk reflecting ambient credentials if auth were
    # added later; public non-credentialed access is the actual contract.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _session_persistence():
    """The session store's backing database, when this deployment has one.

    Imported lazily and only on this branch, so an orchestrator without `adaptive-store`
    installed cannot fail at import for a dependency it is not using.
    """
    dsn = settings.session_database_url.strip()
    if not dsn:
        logger.info("SESSION_DATABASE_URL is unset — sessions live in this process only")
        return None
    from adaptive_store import SqlSessionStore

    store = SqlSessionStore(dsn)
    store.ensure_schema()
    logger.info("sessions are persisted; a restart resumes rather than loses them")
    return store


SESSIONS = SessionStore(_session_persistence())

app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {
            "sessions": len(SESSIONS),
            "bank_registry_url": settings.bank_registry_url,
            "grader_url": settings.grader_url,
            "propagation": "competency-graph"
            if engine.propagation_is_remote()
            else "in-process",
            # WHICH DEPLOYMENT THIS IS. `in-process` means a restart loses every live
            # assessment and a second replica cannot serve one this replica started — a
            # supported configuration, and one worth being able to read off a dashboard
            # rather than infer from an outage.
            "sessions_persisted": SESSIONS.persistent,
            "author_diagnostics_enabled": settings.author_diagnostics_enabled,
        },
        config_extra=lambda: {
            "bank_registry_url": settings.bank_registry_url,
            "grader_url": settings.grader_url,
            "competency_graph_url": settings.competency_graph_url,
        },
    )
)


# --- helpers ---------------------------------------------------------------
def _session(assessment_id: str) -> Session:
    session = SESSIONS.get(assessment_id)
    if session is None:
        raise ServiceError(404, "assessment_unknown", "no such assessment")
    return session


def _scope_for(body: CreateAssessmentRequest, bank_id: str) -> ScopeManifest:
    """Build the scope this session will run under, and refuse it if it cannot be assessed.

    Built HERE rather than accepted as an id from the client. `scope_id` is a hash of its
    inputs, so an id cannot be turned back into an allowlist, and accepting one would mean
    applying a set of item ids nobody in this process derived. Because the manifest is a
    pure function of (bank version, selection), a client that previewed the scope against
    `competency-scope` gets the identical `scope_id` back on the session anyway.

    An unassessable scope is refused BEFORE a candidate is involved. The alternative is a
    session that opens, presents two questions, exhausts its pool and finalises every
    competency as `bank_exhausted` — a shape indistinguishable afterwards from a candidate
    who simply stopped answering.
    """
    if body.target_variables:
        raise ServiceError(
            400,
            "scope_and_targets",
            "send either `scope` or `target_variables`; `target_variables` is the same "
            "idea restricted to main competencies, and two answers to which competencies "
            "this assessment covers is one too many",
        )

    client = engine.scope_client()
    if client is None:
        raise ServiceError(
            503,
            "scope_unavailable",
            "competency-scoped assessments are disabled on this deployment "
            "(COMPETENCY_SCOPE_URL is unset)",
        )

    try:
        scope = client.scope(bank_id, body.scope)
    except ServiceRefused as exc:
        raise ServiceError(
            exc.status_code if 400 <= exc.status_code < 500 else 400,
            exc.code or "scope_invalid",
            exc.detail,
        ) from exc
    except ServiceUnavailable as exc:
        raise ServiceError(503, "scope_unavailable", str(exc)) from exc

    if not scope.assessable:
        raise ServiceError(
            422,
            "scope_unassessable",
            _why_unassessable(scope),
        )
    return scope


def _why_unassessable(scope: ScopeManifest) -> str:
    """Name the competency at fault. A refusal nobody can act on is a refusal that gets
    retried unchanged."""
    reasons: list[str] = []
    if scope.rejected:
        reasons.append(
            f"not measurable in this bank: {', '.join(scope.rejected)}"
        )
    if scope.coverage.unserved:
        reasons.append(
            "required but measured by no active item: "
            f"{', '.join(scope.coverage.unserved)}"
        )
    if scope.coverage.over_budget:
        reasons.append(
            f"{', '.join(scope.coverage.over_budget)} would require more "
            f"sub-competencies than the {scope.coverage.question_budget}-question budget "
            "can cover, so the coverage gate could never be satisfied"
        )
    if not scope.item_ids:
        reasons.append("no active item measures anything in this selection")
    return "; ".join(reasons) or "this selection cannot be assessed"


def _orchestrator(session: Session):
    return engine.orchestrator_for(session.bank_id, session.bank_version, session.scope)


def _state_response(
    session: Session, *, stop_reason: str = "", last_graded=None
) -> AssessmentStateResponse:
    """The one shape every lifecycle call returns.

    `presenting` when there is a question, `report` when there is not — so a client has one
    thing to render and one place to look for "what now".
    """
    orchestrator = _orchestrator(session)
    state = session.state
    stop, reason = orchestrator.should_stop(state)
    reason = stop_reason or reason

    shown: PresentingDTO | None = None
    pair = orchestrator.next_item(state)
    if pair is not None:
        ranked_item, candidate = pair
        # The ranked item carries no payload — the repository behind selection cannot read
        # a question. Fetch the renderable copy for exactly the item being presented.
        full = engine.bank_for(
            session.bank_id, session.bank_version, session.scope
        ).payload_for(ranked_item.item_id)
        if full is None:
            raise ServiceError(
                503,
                "bank_registry_unavailable",
                f"could not read the payload for {ranked_item.item_id}",
            )
        shown = presentation.presenting(full, candidate)

    report = None
    if stop:
        report = presentation.report_dto(orchestrator.summarise(state, reason))
        report.scope = presentation.scope_summary(session.scope)
        if not session.recorded:
            # Once per session: a poll of the assessment must not append it again.
            session.recorded = True
            SESSIONS.mark_finished(state.session_id)
            session_dump.record_session(state, session.bank_id, stop_reason=reason)

    return AssessmentStateResponse(
        session_id=state.session_id,
        bank_id=session.bank_id,
        bank_version=session.bank_version,
        stop=stop,
        stop_reason=reason if stop else "",
        items_administered=state.items_administered,
        open_variables=state.open_variables,
        presenting=shown,
        last_graded=last_graded,
        report=report,
    )


def _require_diagnostics() -> None:
    if not settings.author_diagnostics_enabled:
        raise ServiceError(
            404,
            "diagnostics_disabled",
            "author diagnostics are disabled on this deployment "
            "(AUTHOR_DIAGNOSTICS_ENABLED)",
        )


# --- catalogue -------------------------------------------------------------
@app.get(
    "/banks",
    response_model=list[BankSummary],
    tags=["banks"],
    responses=RESPONSES,
    summary="Registered banks, proxied so a frontend has one base URL",
)
def list_banks() -> list[BankSummary]:
    try:
        return engine.bank_client().banks()
    except ServiceUnavailable as exc:
        raise ServiceError(503, "bank_registry_unavailable", str(exc)) from exc


# --- lifecycle -------------------------------------------------------------
@app.post(
    "/assessments",
    response_model=AssessmentStateResponse,
    status_code=201,
    tags=["assessment"],
    responses=RESPONSES,
    summary="Begin an assessment over several competencies",
    description=(
        "The bank id travels with the session from here on, not with each request: a "
        "client that began against one bank must not be able to answer it against "
        "another, and re-sending the id every call would make that possible by omission.\n\n"
        "The bank VERSION is pinned too. Replacing a bank mid-session would otherwise "
        "change the item pool underneath a live candidate."
    ),
)
async def create_assessment(body: CreateAssessmentRequest) -> AssessmentStateResponse:
    try:
        bank_id = body.bank_id or engine_settings.active_bank
        version = engine.current_version(bank_id)
    except ServiceRefused as exc:
        raise ServiceError(400, exc.code or "bank_unknown", exc.detail) from exc
    except ServiceUnavailable as exc:
        raise ServiceError(503, "bank_registry_unavailable", str(exc)) from exc

    scope = _scope_for(body, bank_id) if body.scope is not None else None
    orchestrator = engine.orchestrator_for(bank_id, version, scope)

    # THE SCOPE DECIDES THE TARGETS, AND THEY ARE ALWAYS MAINS.
    #
    # A sub-competency must never become a target variable. `rollup_outcomes` folds every
    # graded outcome to `variable.split(".")[0]` and drops it when that main is not in the
    # session, so a session begun on ["C1.1"] would compute outcomes, propagate them to the
    # graph, and then discard every one before the posterior — running to the question cap
    # with the standard error exactly where it started, raising nothing and logging nothing.
    #
    # So a selection of sub-competencies opens their MAINS and is expressed as a narrower
    # item pool and a narrower coverage requirement, which is what `ScopedBank` and the
    # induced graph already do. Ability is estimated per main; a target variable is by
    # definition something that has one.
    if scope is not None:
        targets = [row.main for row in scope.mains]
    else:
        targets = body.target_variables or orchestrator._bank.variables()  # noqa: SLF001
    try:
        state = orchestrator.begin(
            targets,
            intake=body.intake or {},
            confidence=body.confidence or {},
        )
    except ValueError as exc:
        raise ServiceError(400, "targets_invalid", str(exc)) from exc

    # Explicit seeds are for deterministic tests and replay. Omission draws OS entropy:
    # exposure control must not be predictable in production.
    rng = np.random.default_rng(body.seed)
    state = await orchestrator.fill_queue(state, use_llm=body.use_llm, rng=rng)
    state = orchestrator.ensure_presenting(state)

    if SESSIONS.at_capacity():
        raise ServiceError(
            503,
            "capacity_reached",
            "assessment session capacity reached; retry after a session finishes",
        )
    session = Session(
        bank_id=bank_id, bank_version=version, state=state, rng=rng, scope=scope
    )
    SESSIONS.add(session)
    SESSIONS.save(session)
    return _state_response(session)


@app.get(
    "/assessments/{assessment_id}",
    response_model=AssessmentStateResponse,
    tags=["assessment"],
    responses=RESPONSES,
    summary="Current state, with the presented item or the report",
)
def get_assessment(assessment_id: str) -> AssessmentStateResponse:
    session = _session(assessment_id)
    session.state = _orchestrator(session).ensure_presenting(session.state)
    return _state_response(session)


@app.get(
    "/assessments/{assessment_id}/next",
    response_model=PresentingDTO,
    tags=["assessment"],
    responses={
        **RESPONSES,
        409: {"model": ErrorResponse, "description": "the assessment has stopped"},
    },
    summary="The next item to present",
    description=(
        "A convenience over `GET /assessments/{id}`, for a client that only wants the "
        "question. 409 once the assessment has stopped — an empty 200 would read as "
        "'nothing to do yet' rather than 'this is over'."
    ),
)
def next_item(assessment_id: str) -> PresentingDTO:
    session = _session(assessment_id)
    session.state = _orchestrator(session).ensure_presenting(session.state)
    response = _state_response(session)
    if response.presenting is None:
        raise ServiceError(
            409,
            "assessment_stopped",
            f"this assessment has stopped ({response.stop_reason or 'no reason recorded'})",
        )
    return response.presenting


@app.delete(
    "/assessments/{assessment_id}",
    tags=["assessment"],
    responses=RESPONSES,
    summary="Discard an assessment",
)
async def delete_assessment(assessment_id: str) -> dict:
    lock = SESSIONS.lock_for(assessment_id)
    if lock is None:
        raise ServiceError(404, "assessment_unknown", "no such assessment")
    async with lock:
        if not SESSIONS.drop(assessment_id):
            raise ServiceError(404, "assessment_unknown", "no such assessment")
    return {"deleted": assessment_id}


@app.get(
    "/assessments/{assessment_id}/report",
    response_model=AssessmentReportDTO,
    tags=["assessment"],
    responses={
        **RESPONSES,
        409: {"model": ErrorResponse, "description": "the assessment is still running"},
    },
    summary="The assessment report",
    description=(
        "Report the BAND RANGE, not the point band. At the standard-error target, "
        "within-one-level accuracy runs about 99% while exact-band accuracy runs about "
        "65% — a single reported level is more confident than the measurement supports."
    ),
)
def report(assessment_id: str) -> AssessmentReportDTO:
    session = _session(assessment_id)
    response = _state_response(session)
    if response.report is None:
        raise ServiceError(
            409,
            "assessment_running",
            "this assessment has not stopped; a report before then would state a "
            "measurement that has not been made",
        )
    return response.report


# --- answering -------------------------------------------------------------
@app.post(
    "/assessments/{assessment_id}/responses",
    response_model=AssessmentStateResponse,
    tags=["assessment"],
    responses=RESPONSES,
    summary="Record a response and advance the loop",
    description=(
        "JSON, or multipart with an `audio` file for a spoken answer.\n\n"
        "Two concurrent posts to one assessment are serialised, and the second gets a 409 "
        "if the presented item changed while it waited. Serialising alone is not enough: "
        "the second request would otherwise wake up and apply its stale answer to the next "
        "item whenever the modality happened to match."
    ),
)
async def record_response(
    assessment_id: str,
    request: Request,
    type_form: str | None = Form(default=None, alias="type"),
    chosen_index_form: int | None = Form(default=None, alias="chosen_index"),
    code_form: str | None = Form(default=None, alias="code"),
    transcript_form: str | None = Form(default=None, alias="transcript"),
    use_llm_form: bool | None = Form(default=None, alias="use_llm"),
    audio: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI marker
) -> AssessmentStateResponse:
    # Capture which item this request is answering BEFORE it waits for the lock. Two
    # concurrent posts can otherwise both read the same state, grade the same presentation,
    # then race to overwrite the session.
    session = _session(assessment_id)
    orchestrator = _orchestrator(session)
    session.state = orchestrator.ensure_presenting(session.state)
    snapshot_pair = orchestrator.next_item(session.state)
    expected_item_id = snapshot_pair[0].item_id if snapshot_pair else None

    async with session.lock:
        session.state = orchestrator.ensure_presenting(session.state)
        current_pair = orchestrator.next_item(session.state)
        current_item_id = current_pair[0].item_id if current_pair else None
        if current_item_id != expected_item_id:
            raise ServiceError(
                409,
                "stale_answer",
                "the presented item changed while this request waited",
            )
        return await _record_unlocked(
            session=session,
            request=request,
            type_form=type_form,
            chosen_index_form=chosen_index_form,
            code_form=code_form,
            transcript_form=transcript_form,
            use_llm_form=use_llm_form,
            audio=audio,
        )


async def _record_unlocked(
    *,
    session: Session,
    request: Request,
    type_form: str | None,
    chosen_index_form: int | None,
    code_form: str | None,
    transcript_form: str | None,
    use_llm_form: bool | None,
    audio: UploadFile | None,
) -> AssessmentStateResponse:
    orchestrator = _orchestrator(session)
    session.state = orchestrator.ensure_presenting(session.state)
    pair = orchestrator.next_item(session.state)
    if pair is None:
        return _state_response(session)
    item, _candidate = pair

    if request.headers.get("content-type", "").startswith("application/json"):
        body = AnswerRequest.model_validate(await request.json())
        answer_type, chosen_index = body.type, body.chosen_index
        code, transcript, use_llm = body.code, body.transcript, body.use_llm
    else:
        answer_type = type_form or item.modality
        chosen_index, code, transcript = chosen_index_form, code_form, transcript_form
        use_llm = True if use_llm_form is None else use_llm_form

    if answer_type != item.modality:
        raise ServiceError(
            422,
            "answer_type_mismatch",
            f"expected a {item.modality} answer, got {answer_type}",
        )

    response = await _coerce_response(
        item=item,
        session=session,
        chosen_index=chosen_index,
        code=code,
        transcript=transcript,
        audio=audio,
        use_llm=use_llm,
    )

    try:
        new_state, graded = orchestrator.record_response(session.state, item, response)
    except ServiceUnavailable as exc:
        # The grader is down. Nothing has been recorded, so the candidate can retry this
        # same item — which is why this is a 503 and not a graded response of weight 0.
        raise ServiceError(503, "grader_unavailable", str(exc)) from exc
    except ServiceRefused as exc:
        # The grader looked at the answer and said no — an option index out of range, a
        # code payload that is not source text. That is the CANDIDATE's request being
        # wrong, so it is a 422 carrying the grader's own reason rather than a 500 that
        # says nothing and looks like our fault.
        raise ServiceError(
            exc.status_code if 400 <= exc.status_code < 500 else 422,
            exc.code or "answer_invalid",
            exc.detail,
        ) from exc
    except (TypeError, ValueError) as exc:
        raise ServiceError(422, "answer_invalid", str(exc)) from exc

    new_state = await orchestrator.after_response(
        new_state, item, use_llm=use_llm, rng=session.rng
    )
    session.state = orchestrator.ensure_presenting(new_state)
    try:
        SESSIONS.save(session)
    except SessionConflict as exc:
        # Another replica recorded an answer to this assessment while this one was grading.
        # The same 409 the in-process path returns for the same reason: applying this answer
        # would apply it to a question the candidate is no longer looking at.
        raise ServiceError(409, "stale_answer", str(exc)) from exc
    return _state_response(session, last_graded=presentation.grade_receipt(graded))


async def _coerce_response(
    *,
    item: BankItem,
    session: Session,
    chosen_index: int | None,
    code: str | None,
    transcript: str | None,
    audio: UploadFile | None,
    use_llm: bool,
) -> object:
    """One answer, in whatever form the modality takes."""
    if item.modality == "mcq":
        if chosen_index is None:
            raise ServiceError(422, "answer_missing", "chosen_index is required for mcq")
        return chosen_index

    if item.modality == "code":
        if not code:
            raise ServiceError(422, "answer_missing", "code is required for a code item")
        return code

    if item.modality in ("open", "voice"):
        text = (transcript or "").strip()
        if audio is not None:
            audio_bytes = await audio.read()
            if not audio_bytes:
                raise ServiceError(422, "audio_empty", "empty audio payload")
            grader = _orchestrator(session)._grader  # noqa: SLF001
            text = grader.transcribe(
                item.item_id, audio_bytes, audio.filename or "answer.wav"
            ).strip()
        if not text:
            raise ServiceError(
                422,
                "answer_missing",
                f"a transcript or an audio file is required for a {item.modality} item",
            )
        # The grader evaluates the package against the rubric — that is a model call and
        # belongs in the service with egress.
        return VoiceResponsePackage.from_text(item.item_id, text)

    raise ServiceError(422, "modality_unsupported", f"unsupported modality {item.modality}")


# --- the instrumented view, gated -----------------------------------------
@app.get(
    "/assessments/{assessment_id}/diagnostics",
    response_model=DiagnosticsResponse,
    tags=["author"],
    responses=RESPONSES,
    summary="Posteriors, selection reasoning and graph state. NOT candidate-safe.",
    description=(
        "Refused unless `AUTHOR_DIAGNOSTICS_ENABLED`. It carries the posterior, the "
        "shortlist and which item the engine would have picked — enough to reverse-engineer "
        "difficulty, and enough for a candidate to tell how they are doing while they are "
        "still being measured."
    ),
)
def diagnostics(assessment_id: str) -> DiagnosticsResponse:
    _require_diagnostics()
    session = _session(assessment_id)
    return diagnostics_view.build(
        orchestrator=_orchestrator(session),
        state=session.state,
        bank_id=session.bank_id,
    )


@app.get(
    "/assessments/{assessment_id}/state",
    tags=["author"],
    responses=RESPONSES,
    summary="The raw AssessmentState. NOT candidate-safe.",
    description=(
        "Refused unless `AUTHOR_DIAGNOSTICS_ENABLED`. The whole session object, "
        "unprojected — for replay, for an appeal, and for debugging a selection nobody "
        "can explain. It contains every posterior and every served item, so it is the "
        "single most useful thing a candidate could read about their own assessment "
        "while still taking it.\n\n"
        "Untyped on purpose: it is the engine's own schema, and a wire copy of it here "
        "would be a second definition that drifts from the one sessions are stored in."
    ),
)
def raw_state(assessment_id: str) -> AssessmentState:
    _require_diagnostics()
    return _session(assessment_id).state
