"""The grader surface: one response in, `GradedOutcome[]` out.

WHY EVERY REQUEST NAMES A BANK AND AN ITEM RATHER THAN CARRYING THE ITEM

The grader fetches the item from the bank registry itself. It could have been handed one,
and that would be one fewer hop — but the grading payload contains the answer index, the
hidden test cases and the reference solution, and the caller is the orchestrator, whose
own responses go to a candidate's browser. Naming the item instead of passing it means
those three things are never in a message the orchestrator has to be trusted not to
forward.

WHY `/grade/open` EVALUATES AS WELL AS GRADES

The original plan was a pre-evaluated rubric package, because in the monolith the
evaluation ran in `app.main` before the sync grading path. There is no `app.main` any
more, and rubric evaluation is a model call — so putting it anywhere but here would give
a second component with egress, which is most of what having one grader buys.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class GradeRequestBase(BaseModel):
    bank_id: str
    item_id: str
    #: Carried through to the audit record and to the graph's idempotency key, so a replay
    #: of the same response for the same session cannot be counted twice.
    session_id: str = ""


class GradeMcqRequest(GradeRequestBase):
    chosen_index: int = Field(ge=0)


class GradeCodeRequest(GradeRequestBase):
    source: str


class VoiceTurnDTO(BaseModel):
    turn_id: str
    role: Literal["candidate", "interviewer"]
    text: str
    #: None means the ASR provider supplied no confidence. Do not invent 0.9 — a fabricated
    #: confidence is indistinguishable from a measured one downstream, and this number
    #: gates whether a spoken answer is scorable at all.
    transcript_confidence: float | None = None
    truncated: bool = False


class VoicePackageDTO(BaseModel):
    """Frozen transport output before grading. Transcript only — never audio bytes."""

    item_id: str
    outcome_status: Literal[
        "complete", "truncated", "unscorable", "infrastructure_error"
    ] = "complete"
    reason_code: str = ""
    turns: list[VoiceTurnDTO] = Field(default_factory=list)
    total_speech_seconds: float = 0.0
    mean_transcript_confidence: float | None = None
    word_count: int = 0
    explicit_decline: bool = False
    cut_off: bool = False
    live_text: str = ""
    final_text: str | None = None


class GradeOpenRequest(GradeRequestBase):
    package: VoicePackageDTO
    #: False runs the deterministic rubric heuristic instead of the model. Tests and
    #: offline replay use it; it is not a degraded mode a deployment should sit in.
    use_llm: bool = True


class TranscribeRequest(BaseModel):
    """Audio in, text out. Separate from grading because the two fail differently.

    A transcription that fails is a retry; a grading that fails is an unscorable response
    that must move no estimate. Folding them together would make the second look like the
    first.
    """

    item_id: str
    filename: str = "answer.wav"
    #: base64. Bytes rather than a URL because the grader is the only component with egress,
    #: and giving it one more reason to fetch from an arbitrary host is not a trade worth
    #: making for a payload this size.
    audio_base64: str


class TranscribeResponse(BaseModel):
    item_id: str
    transcript: str


class TrialCaseDTO(BaseModel):
    test_id: str
    arguments: list[Any] = Field(default_factory=list)
    expected: Any = None
    passed: bool = False
    detail: str = ""


class TrialRunRequest(GradeRequestBase):
    source: str


class TrialRunResponse(BaseModel):
    """The outcome of a candidate's own trial run. Carries no score, by construction.

    There is no code path from here into a learner model — not "a path not currently
    taken", none at all, which is a property a reader can verify from this type. Public
    cases only: if a trial run could reach the hidden ones, a candidate could converge on a
    lookup table by trial and error and the score would mean nothing.

    `available` False means the SANDBOX failed. That is not a statement about the
    candidate's code and must not be shown as one.
    """

    available: bool
    compiled: bool | None = None
    cases: list[TrialCaseDTO] = Field(default_factory=list)
    error_message: str = ""
    passed: int = 0
    total: int = 0


class PublicTestsResponse(BaseModel):
    """The example cases a candidate may see, in bank order."""

    item_id: str
    tests: list[dict[str, Any]] = Field(default_factory=list)
