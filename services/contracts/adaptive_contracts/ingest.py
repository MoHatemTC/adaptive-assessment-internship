"""The bank-ingest surface: one uploaded file becomes a registered bank.

ONE FILE, AND IT IS THE BANK

An author uploads questions. They do not author a competency graph and are never asked for
one, so the graph is DERIVED from the items — see `services/bank-ingest/service/derive.py`.
That is the difference between this and `bank-registry`'s write path, where items and graph
arrive together and a bank without its graph is refused.

THE RECEIPT SAYS WHAT WAS INVENTED ON THE AUTHOR'S BEHALF

`DerivedGraphDTO` exists because an author who never wrote a graph is entitled to see the one
that was derived, and to see it BEFORE the bank measures anybody. A derivation nobody can
inspect is a modelling decision applied to candidates in the dark.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .bank import BankValidationReport

#: `registered` and `rejected` are terminal. The rest are what a poller sees.
UploadStatus = Literal["received", "parsing", "deriving", "validating", "rejected", "registered"]


class DerivedGraphDTO(BaseModel):
    """What was derived from the items, summarised for a receipt.

    Not the graph itself — that is served by `bank-registry` once the bank is registered,
    from the one place a graph is ever read. Two endpoints returning a bank's graph would be
    two answers to one question.
    """

    mains: list[str] = Field(default_factory=list)
    sub_competencies: list[str] = Field(default_factory=list)
    edges_by_relation: dict[str, int] = Field(default_factory=dict)
    #: Always true for a derived graph, and asserted rather than assumed. An edge derived
    #: from co-measurement has strictly less authority than one an expert authored, and the
    #: authored ones were themselves measured wrong 22.4% of the time against a 3% gate.
    prerequisite_edges_are_inert: bool = True


class UploadReceipt(BaseModel):
    """What an upload became, or why it did not become anything."""

    upload_id: str
    status: UploadStatus
    filename: str = ""
    bank_id: str = ""
    title: str = ""
    #: The content hash, once registered. Empty until then.
    version: str = ""
    items: int = 0
    #: Present from `validating` onwards. The findings name the item, node or main at fault,
    #: because a rejection nobody can act on is a rejection that gets retried unchanged.
    validation: BankValidationReport | None = None
    derived_graph: DerivedGraphDTO | None = None
    #: Set when the file could not be read as a bank at all — which is a different failure
    #: from a bank that parsed and then failed a rule, and the author fixes them differently.
    error: str = ""
