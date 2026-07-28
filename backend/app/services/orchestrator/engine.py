"""What the two engines have in common, expressed as a contract.

THIS FILE DELIBERATELY CONTAINS NO ORCHESTRATION POLICY.

The MCQ and code engines were built separately and measured separately. Before anything
can sequence them, there has to be an answer to "what is the same about them" — and that
answer is smaller than it looks, which is the useful finding to record here rather than
paper over.

WHAT IS GENUINELY SHARED

Both are stateless between calls, both take a serialisable state and return a new one, and
both follow begin -> next -> record -> summarise. `AssessmentEngine` captures exactly that
much. Any orchestrator can drive either engine through this protocol without knowing which
modality it is talking to.

WHAT IS NOT SHARED, AND MUST NOT BE FLATTENED

    ability scale     MCQ estimates theta on [-4, 4] with a 3PL model and an EAP
                      posterior over a fixed grid. Code estimates mastery on [0, 1] with a
                      Beta posterior. A standard error of 0.65 is a reasonable stop for one
                      and unreachable nonsense for the other.
    what an item is   an MCQ item has calibrated a, b, c parameters from real response
                      data. A code question has an authored difficulty and discrimination
                      and no calibration behind them.
    what a response   MCQ: one index, graded by exact comparison. Code: a program, executed
    is                in a sandbox, scored across four criteria by three evidence sources.
    evidence strength MCQ evidence is uniform — every answer counts the same. Code evidence
                      is weighted, and an infrastructure failure carries zero.

A combined REPORT therefore cannot simply average the two estimates, and an orchestrator
that treats a competency as measured because either engine touched it will overstate what
is known. Converting between the scales is possible — both are monotone in ability — but it
is a modelling decision with consequences for every downstream band, and it needs to be
made deliberately rather than implied by a helper function.

The architecture that resolves these is pending. This module exists so that when it
arrives, the seam it plugs into is already defined and both engines already satisfy it.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Modality(str, Enum):
    """How a competency is assessed. The value is stable and safe to persist."""

    MCQ = "mcq"
    CODE = "code"


@runtime_checkable
class AssessmentEngine(Protocol):
    """The behaviour both engines already have.

    Intentionally typed loosely: pinning the state and item types to a union of the two
    modalities would make every caller import both, and would break the moment a third is
    added. An orchestrator holds one engine per modality and keeps their states apart, so
    it always knows which concrete types it is handling.
    """

    def begin(self, target: str, self_rating: int | None = None) -> Any:
        """Open a session scoped to one competency, returning serialisable state."""
        ...

    def summarise(self, state: Any, stop: Any) -> Any:
        """The end-of-session report for one competency."""
        ...


class EngineRegistry:
    """Which engine handles which modality.

    A registry rather than a conditional, so adding a modality is a registration and not a
    branch in every caller. It holds no policy about WHICH modality a competency should be
    assessed with — that is the orchestrator's decision, and it is not yet specified.
    """

    def __init__(self) -> None:
        self._engines: dict[Modality, Any] = {}

    def register(self, modality: Modality, engine: Any) -> None:
        if modality in self._engines:
            raise ValueError(f"{modality.value} already registered")
        self._engines[modality] = engine

    def get(self, modality: Modality) -> Any:
        if modality not in self._engines:
            raise KeyError(
                f"no engine registered for {modality.value} — "
                f"registered: {sorted(m.value for m in self._engines)}"
            )
        return self._engines[modality]

    def available(self) -> list[Modality]:
        return sorted(self._engines, key=lambda m: m.value)

    def __contains__(self, modality: object) -> bool:
        return modality in self._engines
