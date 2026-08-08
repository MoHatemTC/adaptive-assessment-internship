"""Typed boundary for the adaptive engine.

`Item` and `AbilityState` are the two objects that cross module lines, so they are
validated here rather than passed as dicts. The engine's correctness depends on `a`, `b`
and `c` being sane; a bank with `c = 1.0` or a negative `a` produces silently meaningless
information scores, so the bounds are enforced at the door.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Difficulty = Literal["very_easy", "easy", "medium", "hard", "very_hard"]


class Item(BaseModel):
    """One calibrated multiple-choice item."""

    id: str
    competency: str
    sub_competency: str = ""
    stem: str
    options: list[str] = Field(min_length=2)
    answer_index: int = Field(ge=0)
    difficulty: Difficulty = "medium"

    # IRT parameters. Bounds are deliberately tight enough to catch a mis-calibrated bank
    # and loose enough not to reject a legitimate one.
    a: float = Field(gt=0.0, le=3.0, description="discrimination")
    b: float = Field(ge=-4.0, le=4.0, description="difficulty on the ability scale")
    c: float = Field(ge=0.0, lt=1.0, description="guessing floor")

    @model_validator(mode="after")
    def _answer_index_in_range(self) -> Item:
        if self.answer_index >= len(self.options):
            raise ValueError(
                f"item {self.id}: answer_index {self.answer_index} is out of range for "
                f"{len(self.options)} options"
            )
        return self


class AbilityState(BaseModel):
    """Everything needed to resume a competency mid-assessment.

    The posterior is carried as a plain list so the state round-trips through JSON — a
    session may be persisted between requests. Summarising it to (mean, sd) instead and
    rebuilding a normal curve would be lossy: a 3PL posterior is genuinely skewed near the
    guessing floor, and the selection criterion integrates over its actual shape.
    """

    model_config = {"arbitrary_types_allowed": True}

    competency: str
    posterior: list[float]
    theta_hat: float = 0.0
    standard_error: float = 2.0
    questions_answered: int = 0
    served_item_ids: list[str] = Field(default_factory=list)
    band_history: list[int] = Field(default_factory=list)


class SelectedItem(BaseModel):
    """An item chosen for administration, with the rationale for choosing it.

    The audit fields exist because selection is the one decision delegated to a model:
    `chosen_by_llm` and `matched_procedure` are what let a run be checked afterwards
    rather than trusted.
    """

    item: Item
    criterion: Literal["KL", "E[Fisher]"]
    information: float
    fisher_information: float
    chosen_by_llm: bool
    matched_procedure: bool = True
    rule_applied: str = ""
    rationale: str = ""
    shortlist_ids: list[str] = Field(default_factory=list)
    presented_stem: str = ""
    rephrase_rejected_reason: str = ""


class CompetencyResult(BaseModel):
    """Final report for one competency."""

    competency: str
    theta_hat: float
    standard_error: float
    percentile: float
    level: int = Field(ge=1, le=5)
    band: str
    certainty_pct: float
    questions_answered: int
    stop_reason: str
    converged: bool
    # False whenever the test ended without meeting the precision target. A report that
    # omits this reads as more authoritative than the measurement supports.
    precision_target_met: bool
    sub_competencies_covered: int
    audit: dict[str, Any] = Field(default_factory=dict)
