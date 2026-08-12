"""Reading banks: what is registered, what it measures, and what it declares.

WAS A SERVICE, IS NOW A READ PATH

`bank-registry` served this over eight GET routes and owned nothing else. The routes are
gone; the projections are not, because the distinction they encode still matters in one
process:

    item_refs()  is what SELECTION sees — parameters, no payload. The orchestrator ranks
                 the entire eligible pool on every step and must not be able to read a
                 question.
    item_full()  is what RENDERING and GRADING see — the payload too, including the answer
                 key and the hidden tests.

Those were two endpoints with deliberately different authorisation. In one process they are
two functions returning two types, and the type is what keeps them apart: `BankItemRef` has
no payload field to populate, so a caller holding one cannot leak a question by mistake.
That is a stronger boundary than the HTTP one it replaces, not a weaker one — the wrong
call is now a `AttributeError` at development time rather than a 200 in production.
"""

from __future__ import annotations

import logging

from cat_engine.contracts import (
    BankItemFull,
    BankItemRef,
    BankSummary,
    CompetencyGraphDTO,
    PolicyDTO,
)
from cat_engine.engine.services.orchestrator import registry
from cat_engine.errors import BankUnknown
from cat_engine.mapping import (
    bank_summary,
    graph_dto,
    item_full,
    item_ref,
    parity_rows,
    policy_dto,
)

logger = logging.getLogger(__name__)

__all__ = [
    "banks",
    "graph",
    "item",
    "item_refs",
    "items",
    "parity",
    "policy",
    "summary",
    "version",
]


def _resolve(bank_id: str | None) -> str:
    """The bank id in force, or a `BankUnknown` naming what is registered.

    `registry.UnknownBankError` already lists the registered ids; it is re-raised as a
    module error so a host has one exception type to catch and does not have to import an
    engine-internal one to handle a bad request.
    """
    try:
        return registry.resolve_bank_id(bank_id)
    except registry.UnknownBankError as exc:
        raise BankUnknown(str(exc)) from exc


def banks() -> list[BankSummary]:
    """Every registered bank, for a picker.

    A bank that failed to load keeps its row, carrying its error. Silently dropping it
    would make "the bank is missing" and "the bank is broken" the same observation, and
    only one of those has a fix.
    """
    return [bank_summary(row) for row in registry.describe()]


def summary(bank_id: str | None = None) -> BankSummary:
    resolved = _resolve(bank_id)
    for row in registry.describe():
        if row["bank_id"] == resolved:
            return bank_summary(row)
    raise BankUnknown(f"bank {resolved} is registered but could not be described")


def version(bank_id: str | None = None) -> str:
    """The content hash over this bank, its graph and its declared profile.

    Pinned into an assessment at `begin`, so replacing a bank cannot change the item pool
    underneath a candidate half way through a session.
    """
    return registry.version(_resolve(bank_id))


def item_refs(bank_id: str | None = None) -> tuple[str, list[BankItemRef]]:
    """(version, parameters) for every ACTIVE item. What selection ranks over.

    The version travels with the list because the two are only meaningful together: a
    caller caching these has to know which bank state they describe.
    """
    resolved = _resolve(bank_id)
    bank = registry.get_bank(resolved)
    return registry.version(resolved), [
        item_ref(i) for i in bank.all_items() if i.status == "active"
    ]


def items(bank_id: str | None = None) -> list[BankItemFull]:
    """Every item WITH its payload. Rendering and grading only."""
    return [item_full(i) for i in registry.get_bank(_resolve(bank_id)).all_items()]


def item(bank_id: str | None, item_id: str) -> BankItemFull:
    """One item with its payload — the answer key, the hidden tests, the rubric."""
    found = registry.get_bank(_resolve(bank_id)).get(item_id)
    if found is None:
        raise BankUnknown(f"no item {item_id} in bank {_resolve(bank_id)}")
    return item_full(found)


def graph(bank_id: str | None = None) -> CompetencyGraphDTO | None:
    """The competency graph paired with this bank, or None when it declares none.

    None rather than an error: a bank without a graph is legal, and the coverage gate
    simply does not run for it.
    """
    service = registry.get_graph_service(_resolve(bank_id))
    return None if service is None else graph_dto(service.graph)


def policy(bank_id: str | None = None) -> PolicyDTO | None:
    """How deployment, bank and edge configuration resolved for this bank.

    Exposed so an operator can ask "why is this edge inert?" without reading three files
    and doing the AND in their head.
    """
    resolved = registry.get_propagation_policy(_resolve(bank_id))
    return None if resolved is None else policy_dto(resolved)


def parity(bank_id: str | None = None):
    """The bank's own parity report — whether each variable is measurable as declared."""
    return parity_rows(registry.get_bank(_resolve(bank_id)).parity_report())
