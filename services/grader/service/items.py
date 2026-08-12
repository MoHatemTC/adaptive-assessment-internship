"""Fetching the item a response is about.

WHY THE GRADER FETCHES RATHER THAN BEING HANDED ONE

The grading payload holds the answer index for MCQ, the hidden test cases and the reference
solution for code, and the full rubric for open. The caller is the orchestrator, whose own
responses reach a candidate's browser. Naming the item and fetching it here means none of
those is ever in a message somebody has to remember not to forward.

The cost is one same-cluster read per response, against a 500 ms budget for MCQ grading and
30 s for code. The item is cached per bank version, so a repeated grade of the same item —
which happens on every retry and every replay — costs nothing.
"""

from __future__ import annotations

import logging

from adaptive_clients import BankRegistryClient
from cat_engine.contracts import BankItemFull
from cat_engine.engine.schemas.orchestration import BankItem

logger = logging.getLogger(__name__)


def to_bank_item(full: BankItemFull) -> BankItem:
    """The wire item into the engine's own envelope.

    The wire form carries one `payload` field whatever the modality; the engine nests it
    under the modality name, which is what lets `BankItem`'s validator say "this claims to
    be code and has no code payload". The translation is two lines and belongs at the
    boundary rather than in either definition.
    """
    data = full.model_dump()
    payload = data.pop("payload", {}) or {}
    data[full.modality] = payload
    return BankItem.model_validate(data)


class ItemSource:
    """Items by (bank, id), cached against the bank version.

    Keyed on the version rather than time-boxed: a bank that has not changed can be cached
    forever, and one that has must not be served from cache at all. `PUT /banks/{id}`
    changes the version, so the next fetch misses and everything after it is correct —
    without a TTL to tune or a cache to invalidate across services.
    """

    def __init__(self, client: BankRegistryClient) -> None:
        self._client = client
        self._versions: dict[str, str] = {}
        self._items: dict[tuple[str, str, str], BankItem] = {}

    def _version(self, bank_id: str) -> str:
        version = self._client.bank(bank_id).version
        if self._versions.get(bank_id) not in (None, version):
            logger.info(
                "bank %s moved to version %s; dropping %d cached items",
                bank_id,
                version,
                sum(1 for key in self._items if key[0] == bank_id),
            )
            self._items = {k: v for k, v in self._items.items() if k[0] != bank_id}
        self._versions[bank_id] = version
        return version

    def get(self, bank_id: str, item_id: str) -> BankItem:
        version = self._version(bank_id)
        key = (bank_id, version, item_id)
        if key not in self._items:
            self._items[key] = to_bank_item(self._client.item(bank_id, item_id))
        return self._items[key]
