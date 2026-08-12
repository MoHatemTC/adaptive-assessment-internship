"""Let a candidate run their solution before submitting it.

WHY THIS EXISTS

Without it, the first time a candidate's code ever executes is the moment it is graded.
That measures something other than competency: a misread signature or an off-by-one they
would have caught in five seconds becomes a permanent observation about what they know.
Every real development loop includes running the thing, and an assessment that removes the
loop is measuring recall under an artificial constraint, not skill.

WHY IT CANNOT LEAK THE MEASUREMENT

Two invariants, both enforced here rather than at the call site:

    PUBLIC CASES ONLY. The hidden cases are the whole reason a submission cannot be tuned
    to the examples. If a trial run could reach them, a candidate could converge on a
    lookup table by trial and error and the score would be meaningless. The filter lives
    in this module, so a UI change cannot widen it by accident.

    NOTHING IS GRADED. This returns a report, never a `GradedOutcome`, and touches no
    estimate. There is no code path from here into the learner model — not "a path that is
    not currently taken", none at all, which is a property a reader can verify from the
    return type.

The run count is bounded (`code_trial_runs_per_question`) because execution costs sandbox
time, and because an unbounded budget turns a check into a search procedure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.code_adaptive.execution import run_submission

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrialCase:
    """One public test case, as it may be shown to the candidate."""

    test_id: str
    arguments: list
    expected: object
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class TrialResult:
    """The outcome of one trial run. Carries no score, by construction.

    `available` is False when the sandbox itself failed. That is not a statement about the
    candidate's code and is not shown as one — the same distinction the graded path draws
    between an infrastructure fault and a learner failure.
    """

    available: bool
    compiled: bool | None
    cases: list[TrialCase]
    error_message: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for case in self.cases if case.passed)

    @property
    def total(self) -> int:
        return len(self.cases)


def public_tests(question: dict, limit: int | None = None) -> list[dict]:
    """The cases a candidate may run against, in bank order.

    Explicit `visibility == "public"`, never "not hidden": an authoring slip that omits
    the field must fail closed. A case nobody marked public is treated as hidden, so a
    missing field costs a candidate one example rather than voiding the question.
    """
    limit = settings.code_trial_run_tests if limit is None else limit
    visible = [t for t in question.get("tests", []) if t.get("visibility") == "public"]
    return visible[: max(limit, 0)]


def trial_run(question: dict, code: str, limit: int | None = None) -> TrialResult:
    """Execute `code` against this question's public cases. Grades nothing.

    Never raises for anything the candidate did: a syntax error and a wrong answer are
    both results worth showing, and the point of the feature is to show them.
    """
    tests = public_tests(question, limit)
    if not tests:
        return TrialResult(
            available=False,
            compiled=None,
            cases=[],
            error_message="This question publishes no example cases to run against.",
        )

    evidence = run_submission(code, tests, question["function_name"])

    if not evidence.usable:
        return TrialResult(
            available=False,
            compiled=None,
            cases=[],
            error_message=f"The sandbox was unavailable: {evidence.error_message[:200]}",
        )

    by_id = {outcome.test_id: outcome for outcome in evidence.test_results}
    cases = [
        TrialCase(
            test_id=test["test_id"],
            arguments=list(test.get("input", [])),
            expected=test.get("expected"),
            passed=bool(by_id[test["test_id"]].passed)
            if test["test_id"] in by_id
            else False,
            detail=by_id[test["test_id"]].detail
            if test["test_id"] in by_id
            else "not run",
        )
        for test in tests
    ]
    return TrialResult(
        available=True,
        compiled=evidence.compiled,
        cases=cases,
        error_message=evidence.error_message if evidence.compiled is False else "",
    )
