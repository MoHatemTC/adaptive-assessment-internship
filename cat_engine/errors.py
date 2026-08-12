"""One error type, so a host has one except clause.

WHY THESE CARRY AN HTTP STATUS WHEN NOTHING HERE SPEAKS HTTP

They used to be `ServiceError(HTTPException)` — a FastAPI type, raised by seven services
and rendered by a shared exception handler. There is no FastAPI here any more, so they are
plain exceptions; but a host that puts routes over this module has to answer the same
questions the services answered, and it should not have to re-derive them.

`code` is the greppable identifier — the thing a caller branches on, stable across
releases and unchanged from the HTTP era, so a frontend written against the old API keeps
working when the host maps these back onto responses. `detail` is the sentence a person
reads. `status_code` is the answer this module already knows and the host would otherwise
guess: whether a bad answer is the candidate's fault (422), a race (409), or a dependency
that is down and can be retried (503).

That mapping is one line in the host:

    except CatError as exc:
        return JSONResponse({"detail": exc.detail, "code": exc.code}, exc.status_code)

WHAT IS NOT HERE

No error means "the candidate answered incorrectly". Being wrong is a measurement, not a
failure, and the only thing a candidate learns from a response is whether their answer was
RECORDED — see `GradeReceiptDTO`, which carries no score.
"""

from __future__ import annotations

__all__ = [
    "AnswerInvalid",
    "AnswerTypeMismatch",
    "AssessmentUnknown",
    "BankUnknown",
    "CapacityReached",
    "CatError",
    "ConfigConflict",
    "DiagnosticsDisabled",
    "GraderUnavailable",
    "ScopeUnassessable",
    "ScopeUnavailable",
    "StaleAnswer",
]


class CatError(Exception):
    """Base for everything this module raises deliberately.

    A host can catch this one type and get a code, a message and a status. Anything else
    escaping the module is a bug in it, not a condition it models.
    """

    #: Default status for the class; instances may override per construction.
    status_code: int = 500
    #: Default greppable code for the class.
    code: str = "internal_error"

    def __init__(
        self,
        detail: str = "",
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.detail = detail or self.__class__.__doc__ or self.__class__.__name__
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.detail)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, detail={self.detail!r})"


# --- the assessment does not exist, or may not be touched -------------------
class AssessmentUnknown(CatError):
    """No such assessment — it expired, was discarded, or never existed."""

    status_code = 404
    code = "assessment_unknown"


class CapacityReached(CatError):
    """No new assessment can be admitted right now.

    Fails CLOSED: at the limit a new assessment is refused rather than an in-progress one
    being evicted. A candidate halfway through a test losing their session to a capacity
    policy is not a trade-off anyone chose.
    """

    status_code = 503
    code = "capacity_reached"


# --- the answer does not fit the question -----------------------------------
class StaleAnswer(CatError):
    """The presented item changed while this answer was in flight.

    A double-submit, or two clients on one assessment. Re-read the session and render what
    it shows; the answer was not recorded.
    """

    status_code = 409
    code = "stale_answer"


class AnswerTypeMismatch(CatError):
    """The answer's type does not match the modality currently presented.

    A client bug rather than a candidate one — and a refusal rather than a silent
    mis-grade, because grading an `mcq` payload against a `code` item produces a number,
    not an error.
    """

    status_code = 422
    code = "answer_type_mismatch"


class AnswerInvalid(CatError):
    """The answer cannot be graded as submitted — show the message and let them retry."""

    status_code = 422
    code = "answer_invalid"


class AnswerMissing(AnswerInvalid):
    """Nothing was submitted for a modality that requires something."""

    code = "answer_missing"


# --- scoping ----------------------------------------------------------------
class ScopeUnassessable(CatError):
    """This selection of competencies cannot be assessed within the question budget.

    Refused BEFORE a candidate is involved. The alternative is a session that opens,
    presents two questions, exhausts its pool and finalises every competency as
    `bank_exhausted` — indistinguishable afterwards from a candidate who stopped answering.
    """

    status_code = 422
    code = "scope_unassessable"


class ScopeInvalid(CatError):
    """The selection names competencies this bank cannot measure."""

    status_code = 400
    code = "scope_invalid"


class ScopeUnavailable(CatError):
    """Scoped assessments are disabled on this deployment.

    Refused rather than silently widened to the whole bank: "you asked for three
    competencies and were assessed on eleven" is not something a candidate or a report
    could detect afterwards.
    """

    status_code = 503
    code = "scope_unavailable"


# --- banks ------------------------------------------------------------------
class BankUnknown(CatError):
    """No bank is registered under this id."""

    status_code = 400
    code = "bank_unknown"


class BankInvalid(CatError):
    """The bank failed validation and was not registered.

    Every finding names the item, node or competency at fault — a refusal nobody can act
    on is a refusal that gets retried unchanged.
    """

    status_code = 422
    code = "bank_invalid"


class WritesDisabled(CatError):
    """The bank write path is turned off on this deployment."""

    status_code = 403
    code = "writes_disabled"


# --- dependencies -----------------------------------------------------------
class GraderUnavailable(CatError):
    """Grading could not run. NOTHING was recorded, so the same item can be retried.

    A 503 rather than a graded response of weight 0, because an infrastructure failure
    that silently contributed an observation would move a standard error — which is what
    the assessment stops on.
    """

    status_code = 503
    code = "grader_unavailable"


# --- author surfaces --------------------------------------------------------
class DiagnosticsDisabled(CatError):
    """Author diagnostics are disabled. Refused, not thinned.

    They carry the posterior, the shortlist and which item the engine would have picked —
    enough to reverse-engineer difficulty, and enough for a candidate to tell how they are
    doing while still being measured. Returning a reduced version would make it a judgement
    call at every call site whether what remained was safe.
    """

    status_code = 404
    code = "diagnostics_disabled"


# --- configuration ----------------------------------------------------------
class ConfigConflict(CatError):
    """A second module was built with different measurement policy in one process.

    Engine policy decides what a candidate is SCORED by, and it is read from a
    process-wide singleton. Two modules disagreeing about it produces sessions whose
    scores were computed one way and whose stopping rule assumed another: internally
    consistent, entirely wrong, and nothing failing. Raised rather than merged.
    """

    status_code = 500
    code = "config_conflict"
