"""Adaptive competency assessment: 3PL IRT with LLM-assisted item selection.

Public surface. Import from here rather than from the submodules, so the internal
arrangement can change without breaking callers.

    from app.services.adaptive import AdaptiveSession, JsonItemRepository

    session = AdaptiveSession(JsonItemRepository())
    state = await session.begin("Python & Software Engineering", self_rating=3)

Who decides what:

    grading, ability estimation, stopping   code, deterministic
    which items are viable, and their rank  code, deterministic
    which of those viable items to ask      the model, from a code-computed shortlist
    the wording of the chosen stem          the model, then a code guard (opt-in)

The model can only choose among items the engine has already ranked as near-optimal, so
it cannot damage the measurement — and every choice is recorded against the engine's own,
so divergence is visible rather than silent.
"""

from app.services.adaptive.bank import (
    ItemRepository,
    JsonItemRepository,
    questions_needed,
)
from app.services.adaptive.convergence import StopDecision, certainty_pct
from app.services.adaptive.session import AdaptiveSession

__all__ = [
    "AdaptiveSession",
    "ItemRepository",
    "JsonItemRepository",
    "StopDecision",
    "certainty_pct",
    "questions_needed",
]
