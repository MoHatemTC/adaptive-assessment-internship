from __future__ import annotations


class DuplicateEvidenceError(ValueError):
    pass


class EvidenceLedger:
    """In-memory ledger of processed evidence ids for one session."""

    def __init__(self) -> None:
        self._processed: set[str] = set()

    def assert_new(self, evidence_id: str) -> None:
        if evidence_id in self._processed:
            raise DuplicateEvidenceError(evidence_id)

    def commit(self, evidence_id: str) -> None:
        self._processed.add(evidence_id)

    def contains(self, evidence_id: str) -> bool:
        return evidence_id in self._processed

