"""How a simulee answers an item — the same way in every arm.

THE PAIRING RULE

A response is a PURE FUNCTION of (simulee, item). It draws from a generator seeded on
those two identifiers and nothing else — not the step number, not the session, not the
order. That is what makes the paired design work: Approach C administers a different
sequence by construction, so any generator consumed in administration order would give the
two arms different answers to the same question, and the difference between arms would
then contain sampling noise the pairing is supposed to remove.

It is also what makes the between-arm correlation worth reporting. The validation
document's B1 shows the RMSE sample-size requirement falls from 1,490 to 446 as that
correlation goes 0.60 to 0.90; a pure response function is how you buy it.

WHAT IS SIMULATED AND WHAT IS REAL

Simulated: whether the answer is right, how many tests pass, what the rubric scores are.
Real: every line of grading, projection, weighting, posterior update and selection. The
MCQ grader compares indices, the code engine runs its full scoring pipeline over a stubbed
sandbox result, and the voice path runs the real criterion->competency projection. Only the
two boundaries that need network — the sandbox and the model — are replaced.

THE CODE PATH'S ONE COMPROMISE

`run_submission` is patched to report a test-pass count drawn here, and the submitted
source is a fixed valid function. Static analysis therefore contributes a constant, so a
code score varies with the test outcome only. That understates the variance of a real code
score and it is stated rather than hidden: it makes the code modality slightly MORE
reliable in simulation than in production, which flatters both arms equally.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.schemas.orchestration import BankItem
from app.schemas.voice import (
    CriterionEvidence,
    GradedVoiceResponse,
    VoiceEvaluation,
)
from app.services.voice.evaluator import _rubric_from_payload, package_from_text
from app.services.voice.rubrics import load_rubric

from .dgp import Simulee, _seed_of

# How much a node's true mastery moves the response probability, in logits. Large enough
# that node structure is detectable, small enough that ability still dominates — a value
# that swamped theta would make the CAT irrelevant and the comparison meaningless.
NODE_SHIFT = 0.8

# A submission that parses, has a docstring and no obvious smells, so the static-analysis
# contribution to a code score is a constant across simulees and arms.
CODE_SUBMISSION = (
    "def solve(*args, **kwargs):\n"
    '    """Reference-shaped submission used by the simulation harness."""\n'
    "    result = None\n"
    "    for value in args:\n"
    "        result = value\n"
    "    return result\n"
)


@dataclass(frozen=True)
class ResponsePlan:
    """Everything a stubbed boundary needs to reproduce one simulated answer."""

    simulee_id: str
    item_id: str
    modality: str
    probability: float
    correct: bool
    passed_tests: int
    total_tests: int
    score: float


# The patched sandbox reads this. Set by `session_runner` immediately before the graded
# call and cleared after — a module global rather than a parameter because
# `code_session.run_submission` is called from inside the engine, three frames down, and
# threading a value through it would mean changing the engine to measure it.
CURRENT: ResponsePlan | None = None


def primary_node(item: BankItem) -> str:
    """The sub-competency this item is mostly about."""
    return max(item.measures, key=lambda m: m.weight).variable


def success_probability(simulee: Simulee, item: BankItem, main: str) -> float:
    """P(this simulee answers this item correctly), under the cohort's DGP.

    Under DGP-0 this is exactly the 3PL the engine scores with — the null arm has no
    structure the graph could exploit, which is the point of it. Under the structured arms
    the node's true mastery shifts ability by `NODE_SHIFT` logits either way.
    """
    theta = float(simulee.theta.get(main, 0.0))
    node = primary_node(item)
    if simulee.nodes:
        theta += NODE_SHIFT if simulee.nodes.get(node, 0) else -NODE_SHIFT

    a, b, c = float(item.cat.a), float(item.cat.b), float(item.cat.c)
    p = c + (1.0 - c) / (1.0 + np.exp(-a * (theta - b)))
    return float(np.clip(p * simulee.slip_ceiling, 0.0, 1.0))


def plan_for(simulee: Simulee, item: BankItem, main: str) -> ResponsePlan:
    """The deterministic response this simulee gives this item. Pure in (simulee, item)."""
    rng = np.random.default_rng(_seed_of("response", simulee.simulee_id, item.item_id))
    p = success_probability(simulee, item, main)

    correct = bool(rng.random() < p)
    total_tests = len(item.payload.get("tests", [])) if item.modality == "code" else 0
    passed = int(rng.binomial(total_tests, p)) if total_tests else 0

    # The continuous score a rubric-graded modality reports, before the grader's own error.
    score = float(np.clip(p + rng.normal(0.0, simulee.grader_error_sd), 0.0, 1.0))
    return ResponsePlan(
        simulee_id=simulee.simulee_id,
        item_id=item.item_id,
        modality=item.modality,
        probability=p,
        correct=correct,
        passed_tests=passed,
        total_tests=total_tests,
        score=score,
    )


# --- per-modality response objects ---------------------------------------------------


def mcq_response(item: BankItem, plan: ResponsePlan) -> int:
    """An option index. A wrong answer is a specific wrong option, not a sentinel."""
    answer = int(item.payload["answer_index"])
    options = item.payload.get("options") or []
    if plan.correct or len(options) < 2:
        return answer
    wrong = [i for i in range(len(options)) if i != answer]
    rng = np.random.default_rng(_seed_of("distractor", plan.simulee_id, plan.item_id))
    return int(wrong[int(rng.integers(len(wrong)))])


def voice_response(item: BankItem, plan: ResponsePlan, simulee: Simulee) -> GradedVoiceResponse:
    """A pre-evaluated voice/open answer, as the async evaluator would have produced.

    Built directly rather than by calling the heuristic evaluator, because the heuristic
    grades TRANSCRIPT LENGTH — it would make every simulee identical and measure nothing
    about ability. The criterion scores here come from the simulee's true probability; the
    projection, weighting and evidence-strength rungs downstream are the engine's own.
    """
    payload = item.payload or {}
    rubric_id = payload.get("rubric_id", "")
    rubric = load_rubric(rubric_id) if rubric_id else _rubric_from_payload(item)

    rng = np.random.default_rng(_seed_of("voice", plan.simulee_id, plan.item_id))
    evidence: list[CriterionEvidence] = []
    for crit in rubric.get("criteria", []):
        maximum = float(crit.get("maximum_score", 5))
        # Per-criterion jitter around the simulee's level: a real answer is not uniformly
        # strong across technical accuracy, completeness and communication.
        raw = float(np.clip(plan.score + rng.normal(0.0, 0.08), 0.0, 1.0)) * maximum
        confidence = float(
            np.clip(0.90 - abs(rng.normal(0.0, simulee.grader_error_sd)), 0.0, 1.0)
        )
        evidence.append(
            CriterionEvidence(
                criterion_id=crit["criterion_id"],
                competency_id=crit["competency_id"],
                raw_score=round(raw, 3),
                maximum_score=maximum,
                confidence=confidence,
                prompt_dependency="independent",
                quote="simulated transcript span",
                quote_turn_id="t0",
                quote_tier="exact",
                description="simulated",
            )
        )

    # 150 words of filler: long enough to clear `evidence_strength`'s short-answer
    # discount, so the weight a voice answer carries is the rubric's, not the transcript
    # length's. Length is not what this simulation varies.
    transcript = " ".join(["simulated"] * 150)
    package = package_from_text(item.item_id, transcript, speech_seconds=120.0)
    return GradedVoiceResponse(
        package=package,
        evaluation=VoiceEvaluation(
            item_id=item.item_id,
            rubric_id=rubric.get("rubric_id", ""),
            criterion_evidence=evidence,
            evaluation_confidence=0.9,
            overall_rationale="simulated",
        ),
        rubric=rubric,
    )


def response_for(item: BankItem, plan: ResponsePlan, simulee: Simulee):
    """The object this item's grader expects."""
    if item.modality == "mcq":
        return mcq_response(item, plan)
    if item.modality == "code":
        return CODE_SUBMISSION
    if item.modality in ("open", "voice"):
        return voice_response(item, plan, simulee)
    raise NotImplementedError(f"no simulated response for modality {item.modality!r}")


# --- boundary stubs -------------------------------------------------------------------


def install_stubs() -> None:
    """Replace the sandbox and the model with deterministic simulation boundaries.

    Module-level assignment rather than a pytest fixture, because the runner is a script.
    Both replacements are the same two the repository's own `stub_boundaries` fixture
    makes, so the simulation exercises exactly the path the test suite does.
    """
    from app.services.code_adaptive import session as code_session
    from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
    from app.services.code_adaptive.llm_evaluator import LLMEvaluation

    def simulated_run(code: str, tests: list[dict], function_name: str) -> ExecutionEvidence:
        plan = CURRENT
        passed = plan.passed_tests if plan is not None else len(tests)
        # Which tests pass is decided by index, not at random a second time: the same
        # simulee answering the same item must produce the same per-test record in both
        # arms, or a replay would not reproduce.
        results = [TestOutcome(t["test_id"], i < passed, 1.0) for i, t in enumerate(tests)]
        return ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=passed,
            total_tests=len(tests),
            test_results=results,
        )

    code_session.run_submission = simulated_run  # type: ignore[assignment]
    code_session.llm_evaluate = lambda *a, **k: LLMEvaluation(available=False)  # type: ignore[assignment]
