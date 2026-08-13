"""One uploaded file becomes a registered bank.

ONE FILE: THE QUESTIONS

An author uploads questions. They do not author a competency graph and are never asked for
one, so the graph is DERIVED from the items — nodes from `measures[].variable`, titles from
each item's own `sub_competency` label, weights from item co-measurement, and every
prerequisite edge inert. See `derive.py`, which is where that modelling decision lives and
the only file that has to change to replace it.

The optional `competencies` block states the two things a file of questions cannot imply:
which sub-competencies are critical, and which serve more than one main. It has no edges, so
it is still not a graph.

ONE ENTRY POINT, AND IT IS IDEMPOTENT

Registering and replacing were separate verbs, which made the difference between them
something a caller had to know before it could act. A bank is identified by its id and its
content, so uploading one is idempotent by nature: the same bytes under the same id produce
the same version, whether that is the first upload or the fifth. `write=False` is the
identical path with the last step omitted rather than a second implementation that could
drift from the one that actually registers a bank.

VALIDATION IS REUSED, NOT REIMPLEMENTED

`BankStore.validate` is the same function the checked-in banks are asserted against. A bank
arriving as an upload clears exactly the bar the seeds clear — otherwise the engine's own
suite is testing the seeds rather than the system.

Sessions already in flight are unaffected. Each pins the bank version it began under, so a
replacement changes what the NEXT assessment sees and nothing about one already running.
"""

from __future__ import annotations

import json
import logging

from cat_engine.contracts import (
    BankValidationReport,
    DerivedGraphDTO,
    UploadReceipt,
    ValidationFinding,
)
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.services.orchestrator import registry
from cat_engine.errors import BankInvalid, WritesDisabled
from cat_engine.ingest.derive import derive_graph, parse_declaration, summarise
from cat_engine.ingest.uploads import Upload, UploadStore

logger = logging.getLogger(__name__)

__all__ = ["Ingest", "Upload", "UploadStore", "derive_graph", "parse_declaration"]


def _report(validation, *, version: str = "") -> BankValidationReport:
    return BankValidationReport(
        bank_id=validation.bank_id,
        accepted=validation.accepted,
        version=version,
        items=validation.items,
        mains=list(validation.mains),
        modalities=list(validation.modalities),
        findings=[
            ValidationFinding(
                severity=f.severity, code=f.code, message=f.message, subject=f.subject
            )
            for f in validation.findings
        ],
    )


def parse_bank(raw: bytes) -> tuple[list[dict], str, dict]:
    """The uploaded bytes into items, a title, and whatever the bank declares about itself.

    Both shapes the engine's own parser accepts: `{schema_version, competency, items}` and a
    bare list. Nothing else — a bank is JSON, and guessing at a spreadsheet would mean
    inventing a second bank schema and maintaining it forever.
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("the file is not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"the file is not valid JSON: {exc}") from exc

    declared: dict = {}
    if isinstance(parsed, dict):
        items = parsed.get("items")
        title = str(parsed.get("competency") or "").strip()
        declared = parse_declaration(parsed.get("competencies"))
    elif isinstance(parsed, list):
        items, title = parsed, ""
    else:
        # ValueError, not TypeError, and deliberately: `register` catches ValueError to
        # turn a bad upload into a REJECTION RECEIPT naming what is wrong. A TypeError
        # would escape that catch and reach the caller as a crash, which is the opposite
        # of what an authoring tool needs.
        raise ValueError(  # noqa: TRY004
            "expected a bank object with an `items` list, or a bare list of items"
        )

    if not isinstance(items, list) or not items:
        raise ValueError("this file declares no items")
    if not all(isinstance(i, dict) for i in items):
        raise ValueError("every item must be an object")
    return items, title, declared


class Ingest:
    """The write path, and the receipts for what went through it."""

    def __init__(self, settings=None) -> None:
        from cat_engine.settings import ModuleSettings

        self.settings = settings if settings is not None else ModuleSettings()
        self.uploads = UploadStore(maximum=self.settings.max_retained_uploads)

    def register(
        self,
        bank_id: str,
        raw: bytes,
        *,
        filename: str = "bank.json",
        write: bool = True,
    ) -> UploadReceipt:
        """Parse, derive, validate, and — when asked — write. In that order.

        A rejected upload comes back as a receipt with `status="rejected"` and the findings
        that caused it, not as an exception: the caller is usually an authoring tool, and
        the useful answer is which item is at fault. `raise_if_rejected` is there for a
        caller that wants the other behaviour.
        """
        if not self.settings.ingest_api_enabled:
            raise WritesDisabled(
                "bank ingest is disabled on this deployment (INGEST_API_ENABLED)"
            )
        if not raw:
            raise BankInvalid("the uploaded file is empty", code="upload_empty")
        if len(raw) > self.settings.max_upload_bytes:
            raise BankInvalid(
                f"{len(raw)} bytes exceeds the {self.settings.max_upload_bytes}-byte ceiling",
                code="upload_too_large",
                status_code=413,
            )
        upload = self.uploads.new(filename, raw)
        receipt = upload.receipt
        receipt.bank_id = bank_id
        receipt.status = "parsing"

        try:
            items, title, declared = parse_bank(upload.raw)
        except ValueError as exc:
            receipt.status = "rejected"
            receipt.error = str(exc)
            return receipt

        receipt.items = len(items)
        receipt.title = title or bank_id
        receipt.status = "deriving"
        graph = derive_graph(
            bank_id,
            items,
            declared=declared,
            relation_threshold=self.settings.relation_threshold,
            edge_floor=self.settings.edge_floor,
        )
        upload.derived = graph
        receipt.derived_graph = DerivedGraphDTO.model_validate(summarise(graph))

        # WHETHER THE CRITICAL SET MEANS ANYTHING FOR THIS BANK.
        #
        # With no declaration every derived node is critical, so critical-only and full
        # coverage are the same requirement and the flag is inert. When the bank narrows the
        # set, the flag is what makes the narrowing take effect — without it the gate would
        # still require every node and the declaration would be decoration.
        #
        # Derived rather than configured, and stated on the profile, so the bank is
        # validated against exactly the rule it will be gated by.
        critical_only = any(
            not node.get("critical", True)
            for node in graph["nodes"]
            if node["node_type"] == "sub_competency"
        )

        receipt.status = "validating"
        validation = registry.STORE.validate(
            bank_id=bank_id,
            items=items,
            graph=graph,
            coverage_critical_only=critical_only,
            question_budget=engine_settings.cat_max_questions,
            deployment_critical_only=engine_settings.graph_coverage_critical_only,
        )
        receipt.validation = _report(validation)

        if not validation.accepted:
            receipt.status = "rejected"
            receipt.error = "; ".join(f"{f.code}: {f.message}" for f in validation.errors)
            return receipt

        if not write:
            receipt.status = "validating"
            return receipt

        registry.STORE.save(
            bank_id=bank_id,
            title=receipt.title,
            items=items,
            graph=graph,
            coverage_critical_only=critical_only,
            validation=validation,
        )
        registry.reset_caches()
        receipt.version = registry.version(bank_id)
        receipt.validation = _report(validation, version=receipt.version)
        receipt.status = "registered"
        logger.info(
            "registered bank %s from upload %s: %d items, mains %s, version %s",
            bank_id,
            upload.upload_id,
            validation.items,
            validation.mains,
            receipt.version,
        )
        return receipt

    def delete(self, bank_id: str) -> bool:
        """Remove a stored bank. A checked-in one of the same id reappears underneath it."""
        removed = registry.STORE.delete(bank_id)
        if removed:
            registry.reset_caches()
            logger.info("deleted stored bank %s", bank_id)
        return removed

    @staticmethod
    def raise_if_rejected(receipt: UploadReceipt) -> UploadReceipt:
        """For a caller that wants an exception rather than a receipt to inspect."""
        if receipt.status == "rejected":
            raise BankInvalid(receipt.error or "the bank failed validation")
        return receipt
