"""Run untrusted learner code and collect objective evidence.

Learner code is untrusted input. It only ever executes inside an E2B sandbox — never in
this process — and the sandbox is killed after each submission so nothing persists
between candidates.

Everything this module produces is DETERMINISTIC EVIDENCE: it compiled or it did not,
these tests passed and those failed. Nothing downstream is permitted to contradict it.
That is the anchor the whole approach rests on: an LLM may interpret why a submission
failed, never whether it did.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from cat_engine.engine.config.settings import settings

logger = logging.getLogger(__name__)

# Per-submission wall clock inside the sandbox. Generous enough that a correct but slow
# solution is not marked as a timeout, tight enough that an infinite loop is caught.
# Configurable because it is a policy decision about how long a candidate's code may run,
# not a property of the harness.
EXECUTION_TIMEOUT_SECONDS = settings.code_execution_timeout_seconds

# Inside the sandbox only; never a host path. The E2B container is single-tenant and
# discarded after the submission, so the usual /tmp hazards — a symlink planted by another
# user, a predictable name racing a second writer — have nobody to come from.
_HARNESS_PATH = "/tmp/_cat_harness.py"  # noqa: S108


@dataclass
class TestOutcome:
    """Result of one test case. `passed` is the ground truth for that case.

    `__test__ = False` because the name starts with "Test": pytest would otherwise try to
    collect this dataclass as a test class and warn on every run.
    """

    __test__ = False

    test_id: str
    passed: bool
    score: float
    failure_type: str = ""  # wrong_output | exception | timeout | not_run
    detail: str = ""


@dataclass
class ExecutionEvidence:
    """Objective evidence from one submission. Every field is measured, never inferred.

    `compiled` is None when the sandbox never ran, because then we do not KNOW whether
    the submission compiles — reporting False there is a claim about the candidate's code
    that no evidence supports, and the UI rendered it as "Compiled: no".
    """

    compiled: bool | None
    execution_completed: bool
    runtime_error: bool = False
    timeout: bool = False
    infrastructure_error: bool = False
    passed_tests: int = 0
    total_tests: int = 0
    execution_time_ms: int = 0
    error_message: str = ""
    test_results: list[TestOutcome] = field(default_factory=list)

    @property
    def passed_test_ratio(self) -> float:
        return self.passed_tests / self.total_tests if self.total_tests else 0.0

    @property
    def usable(self) -> bool:
        """False when the harness itself failed, so nothing may be scored.

        Distinct from the learner failing. An infrastructure fault must never be recorded
        as a candidate's inability — that is a wrong score attached to a real person.
        """
        return not self.infrastructure_error


# The harness that runs inside the sandbox. Written as a template rather than assembled
# from f-strings at call time so the quoting is auditable in one place.
#
# Each test is called in a FRESH namespace re-exec'd from the submission, so a test that
# mutates module state cannot change the outcome of the next one — otherwise test order
# would silently affect the score.
_HARNESS = """
import json, sys, traceback

SUBMISSION = {submission!r}
TESTS = json.loads({tests!r})
FUNCTION_NAME = {function_name!r}

results = []
compile_ok = True
compile_err = ""
try:
    compile(SUBMISSION, "<submission>", "exec")
except SyntaxError as exc:
    compile_ok = False
    compile_err = "{{}}: line {{}}".format(type(exc).__name__, exc.lineno)

if compile_ok:
    for test in TESTS:
        namespace = {{}}
        try:
            exec(SUBMISSION, namespace)
            fn = namespace.get(FUNCTION_NAME)
            if fn is None:
                results.append({{"test_id": test["test_id"], "passed": False,
                                "failure_type": "exception",
                                "detail": "function {{}} not defined".format(FUNCTION_NAME)}})
                continue
            actual = fn(*test["input"])
            passed = actual == test["expected"]
            results.append({{
                "test_id": test["test_id"],
                "passed": bool(passed),
                "failure_type": "" if passed else "wrong_output",
                "detail": "" if passed else "returned {{!r}}".format(actual)[:200],
            }})
        except Exception as exc:
            results.append({{"test_id": test["test_id"], "passed": False,
                            "failure_type": "exception",
                            "detail": "{{}}: {{}}".format(type(exc).__name__, exc)[:200]}})

print("__RESULT__" + json.dumps({{
    "compiled": compile_ok, "compile_error": compile_err, "results": results,
}}))
"""


def _create_sandbox():
    """Open a sandbox across e2b SDK generations.

    2.x exposes a `Sandbox.create(...)` classmethod; 1.x only has the constructor and
    takes different keyword names. Pinning alone should make this unnecessary — and the
    pin was wrong once already, shipping `>=1.0,<2` while every line here was written and
    tested against 2.8.1, so every deploy resolved a 1.x and died on
    "type object 'Sandbox' has no attribute 'create'". The shim means a version skew
    degrades to a working sandbox instead of an outage.
    """
    from e2b_code_interpreter import Sandbox  # type: ignore[import-untyped]

    kwargs = {
        "api_key": settings.e2b_api_key,
        "timeout": EXECUTION_TIMEOUT_SECONDS * 2,
        # Learner code has no reason to reach the network, and every reason not to.
        "allow_internet_access": False,
    }
    if hasattr(Sandbox, "create"):
        return Sandbox.create(**kwargs)

    try:
        return Sandbox(**kwargs)
    except TypeError:
        # 1.x did not accept allow_internet_access. Losing network isolation silently
        # would be worse than failing, so this is the last resort and it is logged.
        logger.warning(
            "e2b SDK predates allow_internet_access — sandbox will have network access. "
            "Upgrade to e2b-code-interpreter>=2.0."
        )
        return Sandbox(api_key=kwargs["api_key"], timeout=kwargs["timeout"])


@dataclass
class _Logs:
    stdout: list[str]
    stderr: list[str] = field(default_factory=list)


@dataclass
class _Execution:
    """The shape run_code returns, so both execution paths look identical downstream."""

    logs: _Logs
    error: object = None


def _run_harness(sandbox, harness: str):
    """Execute the harness in a FRESH INTERPRETER, not the sandbox's notebook kernel.

    `run_code` evaluates inside a Jupyter kernel that already has a running asyncio event
    loop, so any submission calling `asyncio.run()` dies with "cannot be called from a
    running event loop" — no matter how correct it is. That is a property of our harness
    being reported as a property of the candidate's code, which is the worst class of bug
    this system can have: it fails a correct submission and blames the person who wrote it.

    It was not hypothetical. The one async question in the bank marked its own verified
    reference solution as failing 7 of 8 tests, and every approach then dutifully scored
    the resulting wreckage — including in live sessions.

    Running via `commands.run` gives a clean process with no loop of its own. Untrusted
    code is still confined to the sandbox: this changes which interpreter inside it
    executes the harness, not what the harness is allowed to reach.
    """
    if hasattr(sandbox, "files") and hasattr(sandbox, "commands"):
        sandbox.files.write(_HARNESS_PATH, harness)
        result = sandbox.commands.run(
            f"python3 {_HARNESS_PATH}",
            timeout=EXECUTION_TIMEOUT_SECONDS,
        )
        stdout = (
            result.stdout if isinstance(result.stdout, list) else [result.stdout or ""]
        )
        stderr = (
            result.stderr if isinstance(result.stderr, list) else [result.stderr or ""]
        )
        return _Execution(logs=_Logs(stdout=stdout, stderr=stderr))

    # Older SDK without a filesystem API: the event-loop hazard remains, so say so rather
    # than letting async submissions fail silently and look like candidate errors.
    logger.warning(
        "e2b SDK exposes no files/commands API — falling back to the notebook kernel, "
        "where asyncio.run() submissions will fail spuriously."
    )
    return sandbox.run_code(harness, timeout=EXECUTION_TIMEOUT_SECONDS)


def _blank_results(
    tests: list[dict], failure_type: str, detail: str
) -> list[TestOutcome]:
    return [TestOutcome(t["test_id"], False, 0.0, failure_type, detail) for t in tests]


def run_submission(
    code: str, tests: list[dict], function_name: str
) -> ExecutionEvidence:
    """Execute one submission against its test cases inside a fresh sandbox.

    Never raises for learner-caused failures — a syntax error or an exception is evidence,
    and is returned as such. Raises nothing for infrastructure failures either; they set
    `infrastructure_error`, because the caller must be able to tell "the candidate failed"
    from "we failed" and an exception would erase that distinction at the call site.
    """
    import time

    started = time.time()
    harness = _HARNESS.format(
        submission=code, tests=json.dumps(tests), function_name=function_name
    )

    sandbox = None
    try:
        sandbox = _create_sandbox()
        execution = _run_harness(sandbox, harness)
    except Exception as exc:  # noqa: BLE001 - network/quota SDK failures are unscorable
        logger.error("sandbox unavailable: %s: %s", type(exc).__name__, exc)
        return ExecutionEvidence(
            compiled=None,
            execution_completed=False,
            infrastructure_error=True,
            total_tests=len(tests),
            error_message=f"{type(exc).__name__}: {exc}",
            test_results=_blank_results(tests, "not_run", "sandbox unavailable"),
        )
    finally:
        if sandbox is not None:
            try:
                sandbox.kill()
            except Exception:  # noqa: BLE001  # pragma: no cover - best effort cleanup
                logger.warning("sandbox cleanup failed")

    elapsed_ms = int((time.time() - started) * 1000)

    payload = None
    for line in "".join(execution.logs.stdout).splitlines():
        if line.startswith("__RESULT__"):
            payload = json.loads(line[len("__RESULT__") :])
            break

    if payload is None:
        # The harness produced no verdict. Most often a timeout — an infinite loop kills
        # the run before it can print. Attributed to the learner, because their code is
        # what did not terminate, but flagged so a genuine harness fault is visible.
        timed_out = bool(execution.error) or not execution.logs.stdout
        detail = execution.error.name if execution.error else "no result emitted"
        return ExecutionEvidence(
            compiled=True,
            execution_completed=False,
            timeout=timed_out,
            runtime_error=not timed_out,
            total_tests=len(tests),
            execution_time_ms=elapsed_ms,
            error_message=str(detail)[:300],
            test_results=_blank_results(
                tests, "timeout" if timed_out else "exception", str(detail)[:200]
            ),
        )

    if not payload["compiled"]:
        # A syntax error scores zero on functional correctness by definition. Diagnostic
        # analysis of the source is still permitted downstream — a learner with a typo has
        # not demonstrated nothing.
        return ExecutionEvidence(
            compiled=False,
            execution_completed=True,
            total_tests=len(tests),
            execution_time_ms=elapsed_ms,
            error_message=payload["compile_error"],
            test_results=_blank_results(tests, "not_run", payload["compile_error"]),
        )

    by_id = {t["test_id"]: t for t in tests}
    outcomes = [
        TestOutcome(
            test_id=r["test_id"],
            passed=r["passed"],
            score=float(by_id.get(r["test_id"], {}).get("weight", 1.0))
            if r["passed"]
            else 0.0,
            failure_type=r["failure_type"],
            detail=r["detail"],
        )
        for r in payload["results"]
    ]
    return ExecutionEvidence(
        compiled=True,
        execution_completed=True,
        runtime_error=any(o.failure_type == "exception" for o in outcomes),
        passed_tests=sum(1 for o in outcomes if o.passed),
        total_tests=len(outcomes),
        execution_time_ms=elapsed_ms,
        test_results=outcomes,
    )
