"""The Queue: exactly one pending candidate per open variable.

The hand-off between the Picking Agent, which decides WHICH ITEM best measures a variable,
and the Orchestrator, which decides WHICH VARIABLE to probe. Capping each variable at a
single pending candidate is what makes that split work: the orchestrator always has a ready
move for whichever variable it picks, without anything having committed to a long fixed
sequence in advance.

TWO INVARIANTS, BOTH ENFORCED HERE RATHER THAN TRUSTED

    ONE SLOT PER VARIABLE. A second candidate for the same variable would mean the picker
    ran against a stale estimate for one of them — the queue is refilled after every update
    precisely so a candidate is never chosen against an estimate that has since moved.

    NOTHING QUEUED FOR A FINALISED VARIABLE. A finalised variable is measured; queuing for
    it would spend a candidate's time re-confirming a result the engine has already
    committed to reporting. `release` is called on finalisation, and `refill` skips
    finalised variables entirely.
"""

from __future__ import annotations

import logging

from app.schemas.orchestration import QueuedCandidate

logger = logging.getLogger(__name__)


class CandidateQueue:
    """One pending candidate per variable, keyed by variable."""

    def __init__(self, initial: dict[str, QueuedCandidate] | None = None) -> None:
        self._slots: dict[str, QueuedCandidate] = dict(initial or {})

    def __contains__(self, variable: object) -> bool:
        return variable in self._slots

    def __len__(self) -> int:
        return len(self._slots)

    def put(self, candidate: QueuedCandidate) -> None:
        """Fill a variable's slot, replacing whatever was there.

        Replacement is correct rather than an error: after an estimate moves, the previous
        candidate was chosen against a belief that no longer holds, and keeping it would
        administer an item selected for a candidate who has since been measured further.
        """
        if candidate.variable in self._slots:
            logger.debug("replacing queued candidate for %s", candidate.variable)
        self._slots[candidate.variable] = candidate

    def get(self, variable: str) -> QueuedCandidate | None:
        return self._slots.get(variable)

    def take(self, variable: str) -> QueuedCandidate | None:
        """Remove and return a variable's candidate, for administration."""
        return self._slots.pop(variable, None)

    def release(self, variable: str) -> None:
        """Drop a variable's slot. Called when it finalises."""
        if self._slots.pop(variable, None) is not None:
            logger.debug("released queue slot for finalised variable %s", variable)

    def pending(self) -> dict[str, QueuedCandidate]:
        return dict(self._slots)

    def variables(self) -> list[str]:
        return sorted(self._slots)

    def queued_item_ids(self) -> set[str]:
        """Items already promised to some variable.

        Excluded when picking for another variable, so the same item is never queued twice
        — one item, administered once, must not be counted as evidence for two variables
        through two separate administrations.
        """
        return {c.item_id for c in self._slots.values()}
