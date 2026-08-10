"""System prompts and rubric rendering: everything the model is ever told.

Kept in one module so the instructions the model receives can be reviewed as a whole. The
two prompts have deliberately different shapes because the model is trusted with
different amounts in each:

    EVALUATION_SYSTEM   diagnosis. The model interprets evidence it may not contradict,
                        and its reply is validated field by field before any of it counts.
    SELECTION_SYSTEM    choice from a shortlist the engine has already ranked. The model
                        cannot reach a question the engine did not offer.

A RUBRIC is the instruction set the model grades against. Three strictness levels ship
because the difference is not obvious in advance: `loose` names the criteria and leaves
judgement to the grader, `mid` adds evaluation criteria and quality checks, `tight`
decomposes each criterion into binary points with worked examples and negative mistake
patterns. `mid` is the default — it is roughly the specificity a careful team reaches
without deliberately hardening the rubric.

The rubric only ever reaches the model. Nothing in the deterministic path reads it, so
the objective half of every score is rubric-invariant by construction.
"""

from __future__ import annotations

import json
from functools import lru_cache

from app.config.paths import DATA_DIR
from app.config.settings import CodeRubric as Rubric
from app.config.settings import settings

RUBRIC_DIR = DATA_DIR / "rubrics"


EVALUATION_SYSTEM = """You diagnose a learner's programming competencies from their code
and the objective evidence of running it.

You are given the problem, the learner's code, which tests passed and failed, compiler and
runtime output, and structural analysis. Your job is to explain WHAT THE EVIDENCE MEANS
about the learner's competencies. It is not to decide whether the code works — that has
already been measured.

HARD RULES. A response breaking any of these is discarded entirely:
- Never contradict a test result. If tests failed, the code failed.
- Never claim full functional correctness when any test failed.
- Never invent a test, a test id, or an execution result.
- Only score the criterion ids and competency ids given to you.
- Every score must cite evidence: a specific failed test, or a specific span of the code.

Score each criterion from 0.0 to 1.0, where 0 is no evidence of the competency and 1 is
fully demonstrated.

Return JSON only:
{
  "evaluation_confidence": 0.0-1.0,
  "criterion_evidence": [
    {
      "criterion_id": "<from the given list>",
      "competency_id": "<from the given list>",
      "score": 0.0-1.0,
      "confidence": 0.0-1.0,
      "evidence": [{"type": "failed_test|code_span|test_summary",
                    "reference": "<test id or line range>",
                    "description": "<what it shows>"}],
      "misconception_code": "<code or null>"
    }
  ],
  "overall_diagnostic": "<one or two sentences>"
}
"""


SELECTION_SYSTEM = """You choose the next coding question for an adaptive assessment.

The assessment engine has already filtered and ranked the candidates. You may ONLY return
an id from `allowed_question_ids`. You may not invent a question, and you may not ask for
one outside the list.

You are given the CAT parameters for the competency under test: the current ability
estimate, its standard error, and how much evidence it rests on. `expected_information`
is how much each candidate would tell you AT THAT ESTIMATE — it is highest where the
candidate could plausibly pass or fail, and low for questions far above or below them.

Prefer, in this order:
1. A question that directly verifies an unresolved misconception.
2. The highest `expected_information`, which is what shrinks the standard error fastest.
3. A question that improves competency coverage.

Do not simply pick the hardest or the easiest question. A question the candidate is
almost certain to pass, or almost certain to fail, moves the estimate very little
whatever its difficulty.

Return JSON only:
{"selected_question_id": "<id>", "reason_code": "<one of the allowed codes>",
 "reason": "<one sentence>", "confidence": 0.0-1.0}

Allowed reason codes: MAX_INFORMATION, VERIFY_MISCONCEPTION,
RESOLVE_COMPETENCY_UNCERTAINTY, IMPROVE_CONTENT_COVERAGE, BALANCE_DIFFICULTY,
REDUCE_REPETITION, BALANCE_EXPOSURE
"""


@lru_cache(maxsize=8)
def load(rubric_id: Rubric | None = None) -> dict:
    """Load a rubric by id. Cached: the file never changes within a process."""
    rubric_id = rubric_id or settings.code_rubric
    return json.loads((RUBRIC_DIR / f"{rubric_id}.json").read_text(encoding="utf-8"))


def criteria_for(rubric: dict, allowed_criterion_ids: list[str]) -> list[dict]:
    """The rubric's entries for the criteria this question actually uses.

    Filtered to the question's own rubric_criteria: instructing the model on a criterion
    the question does not assess invites it to return a score for something nothing will
    consume, and those entries are then dropped by validation and look like model error.
    """
    allowed = set(allowed_criterion_ids)
    return [c for c in rubric["criteria"] if c["criterion_id"] in allowed]


def render(rubric: dict, allowed_criterion_ids: list[str]) -> str:
    """Render the rubric as the instruction block for the evaluation prompt.

    Rendered as prose rather than embedded as JSON so the difference between strictness
    levels is visible in the prompt as something the model reads, not as a structure it
    must navigate. Only the fields present at that level are emitted, so a loose rubric
    produces a short block and a tight one a long, specific block.
    """
    lines: list[str] = [f"RUBRIC: {rubric['name']}", ""]

    for criterion in criteria_for(rubric, allowed_criterion_ids):
        lines.append(f"### {criterion['criterion_id']}")
        lines.append(criterion["description"])

        if criterion.get("evaluation_criteria"):
            lines.append(f"How to judge: {criterion['evaluation_criteria']}")

        for check in criterion.get("quality_checks", []):
            lines.append(f"  - check: {check}")

        for point in criterion.get("binary_points", []):
            lines.append(f"  - point: {point}")

        lines.append("")

    for pattern in rubric.get("mistake_patterns", []):
        lines.append(f"MISTAKE PATTERN: {pattern}")

    return "\n".join(lines).strip()
