"""Which evidence has already been applied, across the whole session.

The ledger is what stops one response counting twice. It used to be constructed fresh
inside the propagation entry point — per response, not per session — so it deduplicated
within a single item and nothing else, and a resumed or replayed session re-applied every
event it had already applied.

It is now seeded from and drained back into the persisted session state, so
`record_response` is idempotent on `(session, item, attempt, modality, node, criterion)`
across process restarts and across workers.
"""

from __future__ import annotations

from collections.abc import Iterable


class DuplicateEvidenceError(ValueError):
    pass


class EvidenceLedger:
    """Processed evidence ids for one session."""

    def __init__(self, processed: Iterable[str] = ()) -> None:
        self._processed: set[str] = set(processed)

    @classmethod
    def from_ids(cls, processed: Iterable[str]) -> EvidenceLedger:
        return cls(processed)

    def assert_new(self, evidence_id: str) -> None:
        if evidence_id in self._processed:
            raise DuplicateEvidenceError(evidence_id)

    def commit(self, evidence_id: str) -> None:
        self._processed.add(evidence_id)

    def contains(self, evidence_id: str) -> bool:
        return evidence_id in self._processed

    def processed_ids(self) -> list[str]:
        """Sorted, for persistence: state that round-trips must round-trip identically."""
        return sorted(self._processed)

    def __len__(self) -> int:
        return len(self._processed)
