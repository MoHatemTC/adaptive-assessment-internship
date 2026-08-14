"""The adaptive state the host persists: the engine's own state, wrapped and pinned.

``engine_state`` is ``cat_engine``'s ``AssessmentState`` — the complete, proven runtime
record (posteriors, queue, presented question, graph evidence, timing) that the engine
loop reads and returns. It is carried whole rather than re-modelled: reducing it would
change what the engine can decide, and it is already JSON-round-trippable (the legacy
SQL store persisted exactly this object).

The wrapper adds what a STATELESS boundary needs and the engine state alone cannot say:

- which assessment (id + version) this state belongs to,
- a ``content_hash`` of the full definition, so edited content behind an unchanged
  version string is detected instead of silently continued,
- the exposure-control RNG state, so selection randomness resumes identically on any
  process,
- a schema version for the wrapper itself.

It still contains no user identity, no host session ids, and no database concerns —
those belong to the host.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from adaptive_engine.models import FrozenModel
from cat_engine.engine.schemas.orchestration import AssessmentState

STATE_SCHEMA_VERSION = 1


class AdaptiveState(FrozenModel):
    """One assessment run's complete memory. JSON-compatible by construction:

        payload = state.model_dump(mode="json")
        restored = AdaptiveState.model_validate(payload)

    Continuing from ``restored`` produces the same decisions as continuing from the
    original.
    """

    schema_version: int = STATE_SCHEMA_VERSION
    assessment_id: str = Field(min_length=1)
    assessment_version: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    engine_state: AssessmentState
    #: ``numpy`` bit-generator state for exposure control — plain ints, JSON-safe.
    rng_state: dict[str, Any]

    @property
    def questions_answered(self) -> int:
        return self.engine_state.items_administered

    @property
    def current_question_id(self) -> str | None:
        """The question awaiting an answer, or None when none is presented."""
        presenting = self.engine_state.presenting
        return presenting.item_id if presenting is not None else None
