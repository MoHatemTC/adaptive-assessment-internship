"""A bank, over HTTP, that satisfies the engine's own repository seam.

`UnifiedBankRepository` was already a Protocol — `all_items`, `get`, `shortlist`,
`variables`. So this is not a new abstraction over the engine; it is a second
implementation of a boundary the engine already had, which is why the orchestrator can be
pointed at a service without one line of the selection loop changing.

TWO THINGS MAKE THIS VIABLE IN THE HOT PATH

Selection needs a, b and c for every candidate item on every step. A fetch per decision
would put a round trip inside a loop budgeted at 100 ms, so the whole parameter set is
fetched once and held against the bank VERSION. That is what ADR-0001 meant by a
read-through cache keyed by bank version, and the version now exists to key it on.

Payloads are fetched one at a time, for the item about to be presented or graded. They are
the large part of a bank — 1.8 MB of stems for 600 items — and selection is not allowed to
read them anyway.

WHAT AN OUTAGE COSTS

Nothing, until the bank changes. A cached snapshot keeps serving, because stale item
parameters are a far better failure than an abandoned assessment. `refresh()` is the only
call that can fail once a snapshot exists, and it is called at session start rather than
per question.
"""

from __future__ import annotations

import logging
from typing import Any

from adaptive_contracts import BankItemFull, BankItemRef, BankSummary, CompetencyGraphDTO

from .transport import BaseClient, ServiceRefused

logger = logging.getLogger(__name__)


class BankRegistryClient(BaseClient):
    """The raw surface. `HttpUnifiedBank` is what the engine talks to."""

    def banks(self) -> list[BankSummary]:
        return [BankSummary.model_validate(row) for row in self.get("/banks").json()]

    def bank(self, bank_id: str) -> BankSummary:
        return BankSummary.model_validate(self.get(f"/banks/{bank_id}").json())

    def items(self, bank_id: str) -> tuple[str, list[BankItemRef]]:
        """Every item's PARAMETERS, and the bank version they came from.

        The version comes back with them so a caller can cache the two together and never
        hold parameters whose provenance it cannot name.
        """
        response = self.get(f"/banks/{bank_id}/items")
        version = response.headers.get("etag", "").strip('"')
        return version, [BankItemRef.model_validate(row) for row in response.json()]

    def item(self, bank_id: str, item_id: str) -> BankItemFull:
        """One item INCLUDING its payload. For rendering and for grading."""
        return BankItemFull.model_validate(
            self.get(f"/banks/{bank_id}/items/{item_id}").json()
        )

    def graph(self, bank_id: str) -> CompetencyGraphDTO | None:
        """The graph paired with this bank, or None when it declares one.

        A bank without a graph is a legitimate configuration — the coverage gate simply
        does not run — so a 404 here is an answer rather than a failure. Any other refusal
        still raises: "this bank does not exist" and "this bank has no graph" must not
        collapse into the same silence.
        """
        try:
            return CompetencyGraphDTO.model_validate(
                self.get(f"/banks/{bank_id}/graph").json()
            )
        except ServiceRefused as exc:
            if exc.status_code == 404 and exc.code == "graph_absent":
                return None
            raise

    def policy(self, bank_id: str) -> dict[str, Any]:
        return self.get(f"/banks/{bank_id}/policy").json()
