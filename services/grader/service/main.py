"""grader — one response becomes `GradedOutcome`s. Per modality.

THE NARROW WAIST

An MCQ answer, a code submission and a spoken response are graded by completely different
machinery — an index comparison, a sandbox and a static analyser, a rubric and a model —
and all three end in the same statement: *this response says this much about this
variable*. That is the whole of what the measurement layer knows about, and it is why this
can be a separate service without the orchestrator ever learning what a test case is.

THE ONLY SERVICE WITH EGRESS

A sandbox for candidate code, and a model for rubric grading and code interpretation.
Everything else in the system can run with no network policy at all, which is most of the
security argument for splitting this out. `/grade/open` evaluates as well as grades for
exactly that reason: rubric evaluation is a model call, and in the monolith it ran in
`app.main`, which no longer exists. Putting it anywhere else would give a second service
egress.

WEIGHT ZERO IS NOT A SCORE OF ZERO

A sandbox that fell over produces outcomes with weight 0, which make the likelihood
identically 1 and move nothing. That rule is arithmetic rather than a special case, so it
cannot be forgotten at a call site — but it does mean an infrastructure failure and a
candidate who wrote nothing are only distinguishable by the flags, which is why
INFRASTRUCTURE_* survives into the candidate's receipt.
"""

from __future__ import annotations

import base64
import binascii
import logging

from adaptive_clients import BankRegistryClient, ServiceRefused, ServiceUnavailable
from cat_engine.contracts import (
    ErrorResponse,
    GradeCodeRequest,
    GradeMcqRequest,
    GradedResponseDTO,
    GradeOpenRequest,
    PublicTestsResponse,
    TranscribeRequest,
    TranscribeResponse,
    TrialCaseDTO,
    TrialRunRequest,
    TrialRunResponse,
)
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from cat_engine.engine.schemas.orchestration import GradedResponse
from cat_engine.engine.schemas.voice import VoiceResponsePackage
from cat_engine.engine.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
from cat_engine.engine.services.code_adaptive import trial as code_trial
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.voice.evaluator import evaluate as evaluate_voice
from fastapi import FastAPI

from .config import settings
from .items import ItemSource

logger = logging.getLogger(__name__)

RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "no such bank or item"},
    409: {"model": ErrorResponse, "description": "the item is not of that modality"},
    503: {"model": ErrorResponse, "description": "the bank registry is unreachable"},
}

app = FastAPI(
    title="grader",
    version=settings.release,
    summary="One response becomes GradedOutcomes. Per modality.",
    description=__doc__,
)
install_error_handlers(app)

_bank_client = BankRegistryClient(
    settings.bank_registry_url, timeout=settings.bank_timeout_seconds
)
_items = ItemSource(_bank_client)
# One code engine for the process. It holds no session state — `evaluate` takes a question
# and a submission and returns a report — so sharing it costs nothing and avoids
# reconstructing a repository per request.
_grader = GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))

app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {"bank_registry_url": settings.bank_registry_url},
        config_extra=lambda: {"bank_registry_url": settings.bank_registry_url},
    )
)


def _item(bank_id: str, item_id: str, *expected: str):
    try:
        item = _items.get(bank_id, item_id)
    except ServiceRefused as exc:
        raise ServiceError(404, exc.code or "item_unknown", exc.detail) from exc
    except ServiceUnavailable as exc:
        raise ServiceError(
            503,
            "bank_registry_unavailable",
            f"could not read {item_id} from the bank registry: {exc}",
        ) from exc
    if expected and item.modality not in expected:
        # A mismatch here means the caller graded the wrong thing, and grading an MCQ as
        # code would produce a confident zero rather than an error.
        raise ServiceError(
            409,
            "modality_mismatch",
            f"{item_id} is a {item.modality} item; this endpoint grades {list(expected)}",
        )
    return item


def _as_dto(graded: GradedResponse) -> GradedResponseDTO:
    return GradedResponseDTO.model_validate(graded.model_dump())


@app.post(
    "/grade/mcq",
    response_model=GradedResponseDTO,
    tags=["grading"],
    responses=RESPONSES,
    summary="Grade a multiple-choice answer",
    description=(
        "Exact index comparison. No model is involved, and none ever will be — a model "
        "that graded multiple choice could disagree with the bank about its own answer key."
    ),
)
def grade_mcq(request: GradeMcqRequest) -> GradedResponseDTO:
    item = _item(request.bank_id, request.item_id, "mcq")
    try:
        return _as_dto(_grader.grade(item, request.chosen_index))
    except ValueError as exc:
        raise ServiceError(422, "answer_invalid", str(exc)) from exc


@app.post(
    "/grade/code",
    response_model=GradedResponseDTO,
    tags=["grading"],
    responses=RESPONSES,
    summary="Grade a code submission in a sandbox",
    description=(
        "Tests 60%, static analysis 15%, model 25% — the measured split. A sandbox failure "
        "produces outcomes of weight 0, which move no estimate: an infrastructure fault is "
        "not evidence about a candidate."
    ),
)
def grade_code(request: GradeCodeRequest) -> GradedResponseDTO:
    item = _item(request.bank_id, request.item_id, "code")
    try:
        return _as_dto(_grader.grade(item, request.source))
    except (TypeError, ValueError) as exc:
        raise ServiceError(422, "answer_invalid", str(exc)) from exc


@app.post(
    "/grade/open",
    response_model=GradedResponseDTO,
    tags=["grading"],
    responses=RESPONSES,
    summary="Evaluate and grade a spoken or typed open answer",
    description=(
        "Takes the transcript package and does both halves: rubric evaluation, which is a "
        "model call, then the deterministic projection onto competencies. `open` and "
        "`voice` grade identically — the modality records how the answer was collected, "
        "not how it was judged."
    ),
)
async def grade_open(request: GradeOpenRequest) -> GradedResponseDTO:
    item = _item(request.bank_id, request.item_id, "open", "voice")
    package = VoiceResponsePackage.model_validate(request.package.model_dump())
    graded_voice = await evaluate_voice(item, package, use_llm=request.use_llm)
    return _as_dto(_grader.grade(item, graded_voice))


@app.post(
    "/transcribe",
    response_model=TranscribeResponse,
    tags=["grading"],
    responses=RESPONSES,
    summary="Turn recorded audio into a transcript",
    description=(
        "Separate from grading because the two fail differently: a transcription failure "
        "is a retry, a grading failure is an unscorable response that must move no "
        "estimate. Folding them together would make the second look like the first."
    ),
)
def transcribe(request: TranscribeRequest) -> TranscribeResponse:
    from cat_engine.engine.services.voice_live.transcribe import transcribe_audio_bytes

    try:
        audio = base64.b64decode(request.audio_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ServiceError(422, "audio_invalid", "audio_base64 is not valid base64") from exc
    if not audio:
        raise ServiceError(422, "audio_empty", "empty audio payload")
    return TranscribeResponse(
        item_id=request.item_id,
        transcript=transcribe_audio_bytes(audio, filename=request.filename).strip(),
    )


@app.get(
    "/trial/{bank_id}/{item_id}/public-tests",
    response_model=PublicTestsResponse,
    tags=["trial"],
    responses=RESPONSES,
    summary="The example cases a candidate may run against",
    description=(
        "Public cases only, and the filter lives in the engine's trial module so a client "
        "change cannot widen it. A case nobody explicitly marked public is treated as "
        "hidden: an authoring slip must cost a candidate one example rather than void the "
        "question."
    ),
)
def public_tests(bank_id: str, item_id: str) -> PublicTestsResponse:
    item = _item(bank_id, item_id, "code")
    question = GraderAgent.as_code_question(item)
    return PublicTestsResponse(item_id=item_id, tests=code_trial.public_tests(question))


@app.post(
    "/trial/code",
    response_model=TrialRunResponse,
    tags=["trial"],
    responses=RESPONSES,
    summary="Run a candidate's code against the public cases. Grades nothing.",
    description=(
        "Without this, the first time a candidate's code ever executes is the moment it is "
        "graded — which measures something other than competency: a misread signature they "
        "would have caught in five seconds becomes a permanent observation about what they "
        "know.\n\n"
        "There is no code path from here into a learner model. Not one that is currently "
        "unused — none at all, which is a property the return type makes checkable. "
        "`available: false` means the SANDBOX failed and is not a statement about the "
        "candidate's code."
    ),
)
def trial_code(request: TrialRunRequest) -> TrialRunResponse:
    item = _item(request.bank_id, request.item_id, "code")
    question = GraderAgent.as_code_question(item)
    result = code_trial.trial_run(question, request.source)
    return TrialRunResponse(
        available=result.available,
        compiled=result.compiled,
        cases=[
            TrialCaseDTO(
                test_id=case.test_id,
                arguments=list(case.arguments),
                expected=case.expected,
                passed=case.passed,
                detail=case.detail,
            )
            for case in result.cases
        ],
        error_message=result.error_message,
        passed=result.passed,
        total=result.total,
    )
