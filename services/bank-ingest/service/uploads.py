"""What an upload was, kept long enough to say why it failed.

THE RAW BYTES ARE KEPT, AND THAT IS THE POINT

A bank that fails to parse is still the artefact somebody uploaded. A rejection that cannot
be reproduced from the original input is a support ticket with no evidence in it, and the
author's only recourse is to upload again and hope.

IN MEMORY, LIKE THE ORCHESTRATOR'S SESSIONS, AND FOR THE SAME REASON

Bounded and lost on restart. What a registered bank became IS durable — it is in the bank
store — so what is lost here is the receipt for an upload, not a bank. Making these durable
means deciding where uploaded content lives and under whose retention policy, which is the
same decision `sessions.py` declines to make inside a refactor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from adaptive_contracts import UploadReceipt


@dataclass
class Upload:
    """One uploaded file and everything decided about it."""

    upload_id: str
    filename: str
    raw: bytes
    receipt: UploadReceipt
    #: Kept so a receipt can be re-served without re-deriving. Derivation is deterministic,
    #: so this is a convenience rather than a source of truth.
    derived: dict = field(default_factory=dict)


class UploadStore:
    """Process-local uploads, bounded, oldest evicted first."""

    def __init__(self, maximum: int = 200) -> None:
        self._uploads: dict[str, Upload] = {}
        self._maximum = maximum

    def __len__(self) -> int:
        return len(self._uploads)

    def new(self, filename: str, raw: bytes) -> Upload:
        upload_id = f"upl_{uuid.uuid4().hex[:12]}"
        upload = Upload(
            upload_id=upload_id,
            filename=filename,
            raw=raw,
            receipt=UploadReceipt(
                upload_id=upload_id, status="received", filename=filename
            ),
        )
        self._uploads[upload_id] = upload
        self._prune()
        return upload

    def get(self, upload_id: str) -> Upload | None:
        return self._uploads.get(upload_id)

    def recent(self, limit: int = 50) -> list[Upload]:
        """Newest first. Insertion order is creation order — dicts preserve it."""
        return list(reversed(list(self._uploads.values())))[:limit]

    def _prune(self) -> None:
        while len(self._uploads) > self._maximum:
            self._uploads.pop(next(iter(self._uploads)))

    def clear(self) -> None:
        self._uploads.clear()
