"""Rubric loading and rendering (the second factor in the study).

A rubric here is the instruction set the model grades against. Three strictness levels
ship, and they differ only in how much is spelled out:

    loose   criteria named, judgement left to the grader
    mid     + evaluation criteria and quality checks
    tight   + binary points with worked examples, + negative mistake patterns

The rubric only ever reaches the MODEL. Nothing in the deterministic path reads it, so
approach A is rubric-invariant by construction — which is what makes A's three cells the
control arm of the 3x3.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from cat.config import Rubric, settings

RUBRIC_DIR = Path(__file__).resolve().parent.parent / "rubrics"


@lru_cache(maxsize=8)
def load(rubric_id: Rubric | None = None) -> dict:
    rubric_id = rubric_id or settings.code_cat_rubric
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

    Rendered rather than embedded as JSON so the difference between strictness levels is
    visible in the prompt as prose the model reads, not as a structure it must navigate.
    Only the fields present at that level are emitted — a loose rubric produces a short
    block, a tight one a long and specific block, and that difference IS the treatment.
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
            lines.append(f"  [{point['id']}] {point['description']}")
            lines.append(f"      when: {point['evaluation_criteria']}")
            for example in point.get("examples", []):
                lines.append(f"      e.g.  {example}")
        lines.append("")

    patterns = rubric.get("mistake_patterns") or []
    if patterns:
        lines.append("MISTAKE PATTERNS — each fires when the defect IS PRESENT.")
        lines.append(
            "Set misconception_code on the affected criterion when one applies, and "
            "leave it null when none does. Do not invent codes outside this list."
        )
        for pattern in patterns:
            lines.append(f"  [{pattern['id']}] {pattern['misconception_code']} — "
                         f"{pattern['description']}")
            lines.append(f"      when: {pattern['evaluation_criteria']}")
            for example in pattern.get("examples", []):
                lines.append(f"      e.g.  {example}")
        lines.append("")

    return "\n".join(lines)


def allowed_misconception_codes(rubric: dict) -> set[str]:
    """Codes this rubric authorises. Empty for loose and mid, which name none.

    Deliberately NOT used to reject codes on the looser rubrics: a model that diagnoses
    MISSING_EMPTY_INPUT_GUARD without being handed the vocabulary has done something
    worth measuring, and discarding it would hide the effect the study is looking for.
    """
    return {p["misconception_code"] for p in (rubric.get("mistake_patterns") or [])}
