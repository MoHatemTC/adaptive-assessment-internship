"""A bank store backed by Postgres, answering exactly what the file store answers.

WHAT THIS IS FOR

`BankStore` resolves a bank from two layers of files: the checked-in seeds and whatever has
been written to a volume. That works, and it is why `bank-ingest` and `bank-registry`
currently share a directory — one writing, one reading, agreeing through mtimes and a
content hash. Two processes agreeing about a directory is a weaker guarantee than one
transaction, and it is the reason a half-written bank has to be prevented by a staging
directory and a rename rather than by a rollback.

THE CLAIM THIS MODULE HAS TO EARN

Not "it works" — **it answers identically**. Every bank's version, its item set, its graph
and its resolved profile have to come back the same from here as from the file store, for
all five checked-in banks. `test_sql_store_parity.py` asserts exactly that, and it is the
only reason this is safe to point a service at.

WHY THE VERSION IS STILL A HASH OVER BYTES

Every orchestrator cache is keyed on the bank version and every assessment pins it.
Recomputing a hash from normalised rows would produce a different number for the same bank:
every cache would invalidate at once, every pinned expectation in the suite would move, and
five banks would quietly become five different banks with the same names. So the original
bytes are stored beside the rows and hashed exactly as `BankStore` hashes them. The bytes
are identity; the rows are the index.

NO ORM, DELIBERATELY

Eight tables and about a dozen statements. An ORM here would add a dependency and a second
description of a schema that is already written down in `schema.sql`, and would buy nothing
this module does not do in plain SQL. `psycopg` is installed only into the two images that
touch a bank, so the engine and the other five services stay free of a database driver.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

SCHEMA = Path(__file__).with_name("schema.sql")


class StoreIntegrityError(ValueError):
    """A write that cannot become rows. Distinct from a validation failure, which is data.

    `BankStore.validate` answers "should this bank be allowed to measure anybody" and
    returns findings a caller can show an author. This answers "can this even be stored",
    and there is nothing useful to say about it except which item is malformed.
    """


def content_hash(items_bytes: bytes, graph_bytes: bytes | None, profile: dict) -> str:
    """The bank version, computed exactly as `BankStore._hash_bytes` computes it.

    Length-prefixed per blob, sha256, truncated to sixteen hex characters. Copied rather
    than imported because the engine's copy lives in a module this package deliberately does
    not depend on — and `test_sql_store_parity` asserts the two agree on all five banks,
    which is the only thing that makes a copy safe.
    """
    blobs = [items_bytes]
    if graph_bytes is not None:
        blobs.append(graph_bytes)
    blobs.append(json.dumps(profile, sort_keys=True).encode())

    digest = hashlib.sha256()
    for blob in blobs:
        digest.update(len(blob).to_bytes(8, "big"))
        digest.update(blob)
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class StoredBank:
    """One bank version, as the store hands it back."""

    bank_id: str
    title: str
    version: str
    source: str
    coverage_critical_only: bool | None
    mains: tuple[str, ...]
    items: tuple[dict[str, Any], ...]
    graph: dict[str, Any] | None


class SqlBankStore:
    """Banks in Postgres. One writer, many readers, one transaction per write."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row)

    # --- schema ------------------------------------------------------------
    def ensure_schema(self) -> None:
        """Idempotent. Safe to run on every boot, and run on every boot on purpose: a
        service that starts against a database it has not checked is a service that fails
        on its first write instead of at startup."""
        with self.connect() as conn:
            conn.execute(SCHEMA.read_text(encoding="utf-8"))
            conn.commit()

    # --- writes ------------------------------------------------------------
    def save(
        self,
        *,
        bank_id: str,
        title: str,
        items: list[dict[str, Any]],
        graph: dict[str, Any] | None,
        coverage_critical_only: bool | None,
        mains: list[str],
        source: str = "stored",
    ) -> str:
        """Write one bank version, all or nothing, and make it the current one.

        ONE TRANSACTION. This is the whole reason to be here: the file store prevents a
        half-written bank with a staging directory and a rename, because a filesystem gives
        it nothing better. A partially applied bank is not a state this can reach.

        The bytes are serialised the same way the file store writes them, so the version
        computed here is the version that store would have computed.
        """
        items_bytes = json.dumps({"items": items}, indent=2, sort_keys=False).encode()
        graph_bytes = json.dumps(graph, indent=2).encode() if graph is not None else None
        profile = {
            "mains": list(mains),
            "coverage_critical_only": coverage_critical_only,
            "title": title or bank_id,
        }
        version = content_hash(items_bytes, graph_bytes, profile)

        with self.connect() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO bank (bank_id, title) VALUES (%s, %s)
                ON CONFLICT (bank_id) DO UPDATE SET title = EXCLUDED.title
                """,
                (bank_id, title or bank_id),
            )
            # Re-registering identical bytes is a no-op rather than an error: the version IS
            # the content, so "already there" and "just written" describe the same bank.
            conn.execute(
                "UPDATE bank_version SET is_current = false WHERE bank_id = %s", (bank_id,)
            )
            conn.execute("DELETE FROM bank_version WHERE version = %s", (version,))
            conn.execute(
                """
                INSERT INTO bank_version (version, bank_id, title, source,
                                          coverage_critical_only, mains, items_bytes,
                                          graph_bytes, profile_bytes, is_current)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true)
                """,
                (
                    version,
                    bank_id,
                    title or bank_id,
                    source,
                    coverage_critical_only,
                    list(mains),
                    items_bytes,
                    graph_bytes,
                    json.dumps(profile, sort_keys=True).encode(),
                ),
            )
            self._write_items(conn, version, items)
            if graph is not None:
                self._write_graph(conn, version, graph)

        logger.info(
            "stored bank %s version %s: %d items, mains %s", bank_id, version, len(items), mains
        )
        return version

    @staticmethod
    def _write_items(conn: psycopg.Connection, version: str, items: list[dict]) -> None:
        """Write the item rows, refusing anything that cannot be a question.

        STRICT ABOUT `cat`, AND THAT IS THE POINT. An earlier version of this defaulted a
        missing discrimination to 1.0 and a missing difficulty to 0.0, which meant an item
        with no calibration at all was stored looking exactly like a calibrated one — and
        then ranked, administered and scored against. Nothing would have failed; the item
        would simply have carried parameters nobody measured.

        This is a STORE-level integrity check, not the engine's semantic validation.
        `BankStore.validate` still runs first and still owns the rules about coverage,
        pairing and duplicate ids. This owns "these bytes cannot be a row".
        """
        rows, measures = [], []
        for index, item in enumerate(items):
            identifier = item.get("item_id")
            if not identifier or not item.get("modality"):
                raise StoreIntegrityError(
                    f"item {index} has no item_id or no modality: {item!r:.120}"
                )
            cat = item.get("cat")
            if not isinstance(cat, dict) or "a" not in cat or "b" not in cat:
                raise StoreIntegrityError(
                    f"{identifier} carries no CAT parameters; an item stored without them "
                    "would be ranked and administered against a calibration nobody measured"
                )
            if not item.get("measures"):
                raise StoreIntegrityError(
                    f"{identifier} measures no variable, so no response to it could move "
                    "any estimate"
                )

            payload = {
                key: value
                for key, value in item.items()
                if key in ("mcq", "code", "open", "voice") and value
            }
            # One payload, under its own modality key, exactly as the bank file nests it.
            flattened = next(iter(payload.values()), {}) if payload else {}
            rows.append(
                (
                    version,
                    identifier,
                    item["modality"],
                    item.get("status", "active"),
                    item.get("competency", ""),
                    item.get("sub_competency", ""),
                    float(cat["a"]),
                    float(cat["b"]),
                    float(cat.get("c", 0.0)),
                    item.get("estimated_time_seconds"),
                    item.get("minimum_success_confidence"),
                    Jsonb(flattened),
                )
            )
            for measure in item["measures"]:
                measures.append(
                    (version, identifier, measure["variable"], float(measure["weight"]))
                )

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO item (version, item_id, modality, status, competency,
                                  sub_competency, cat_a, cat_b, cat_c,
                                  estimated_time_seconds, minimum_success_confidence, payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )
            cur.executemany(
                """
                INSERT INTO item_measure (version, item_id, node_id, weight)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                measures,
            )

    @staticmethod
    def _write_graph(conn: psycopg.Connection, version: str, graph: dict) -> None:
        nodes = [
            (
                version,
                node["competency_id"],
                node.get("title", ""),
                node.get("node_type", "sub_competency"),
                bool(node.get("critical", False)),
                bool(node.get("context_specific", False)),
                list(node.get("main_competencies", []) or []),
                Jsonb(node.get("metadata", {}) or {}),
            )
            for node in graph.get("nodes", [])
        ]
        edges = [
            (
                version,
                edge["from"],
                edge["to"],
                edge["relation"],
                float(edge.get("strength", 1.0)),
                float(edge.get("weight", 1.0)),
                bool(edge.get("allow_upward_inference", True)),
                bool(edge.get("allow_downward_blocking", True)),
                Jsonb(edge.get("metadata", {}) or {}),
            )
            for edge in graph.get("edges", [])
        ]
        policy = graph.get("policy", {}) or {}

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO competency_node (version, node_id, title, node_type, critical,
                                             context_specific, main_competencies, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                nodes,
            )
            cur.executemany(
                """
                INSERT INTO competency_edge (version, from_id, to_id, relation, strength,
                                             weight, allow_upward_inference,
                                             allow_downward_blocking, metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
                """,
                edges,
            )
            cur.execute(
                """
                INSERT INTO bank_policy (version, upward_inference, descendant_blocking,
                                         minimum_failures_to_block,
                                         accepted_validation_statuses, notes)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (version) DO NOTHING
                """,
                (
                    version,
                    bool(policy.get("upward_inference", False)),
                    bool(policy.get("descendant_blocking", False)),
                    policy.get("minimum_failures_to_block"),
                    list(policy.get("accepted_validation_statuses", []) or []),
                    str(policy.get("notes", "")),
                ),
            )

    def delete(self, bank_id: str) -> bool:
        """Remove the STORED layer only. A seed underneath reappears — that is the point.

        Deleting the bank row would cascade the seed's versions away with the shadow, and
        an accidental overwrite would then need a redeploy to undo. So this removes the
        stored versions and makes the newest surviving seed version current again.
        """
        with self.connect() as conn, conn.transaction():
            deleted = conn.execute(
                "DELETE FROM bank_version WHERE bank_id = %s AND source = 'stored'",
                (bank_id,),
            ).rowcount
            if not deleted:
                return False
            restored = conn.execute(
                """
                UPDATE bank_version SET is_current = true
                 WHERE version = (
                    SELECT version FROM bank_version
                     WHERE bank_id = %s AND source = 'seed'
                     ORDER BY registered_at DESC LIMIT 1)
                RETURNING version
                """,
                (bank_id,),
            ).fetchone()
            if restored is None:
                # Nothing underneath: the bank existed only as a posted one.
                conn.execute("DELETE FROM bank WHERE bank_id = %s", (bank_id,))
        return True

    # --- reads -------------------------------------------------------------
    def bank_ids(self) -> list[str]:
        with self.connect() as conn:
            return [
                row["bank_id"]
                for row in conn.execute(
                    "SELECT bank_id FROM bank ORDER BY bank_id"
                ).fetchall()
            ]

    def catalogue(self) -> list[dict[str, Any]]:
        """Every bank's METADATA, without a byte of its content.

        Listing banks must not read them. An earlier version of the store had `profiles()`
        materialise all five to build a catalogue — pulling 1.8 MB blobs out of Postgres and
        writing them to disk — which took longer than the orchestrator's 10 s bank timeout
        and made `GET /banks` fail on a cold cache. Found by the container smoke test, which
        is the only thing that exercises a cold start.
        """
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT version, bank_id, title, source, coverage_critical_only, mains,
                       graph_bytes IS NOT NULL AS has_graph
                  FROM bank_version WHERE is_current ORDER BY bank_id
                """
            ).fetchall()

    def version(self, bank_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT version FROM bank_version WHERE bank_id = %s AND is_current",
                (bank_id,),
            ).fetchone()
        return row["version"] if row else None

    def load(self, bank_id: str) -> StoredBank | None:
        """One bank version, rebuilt into the shapes the rest of the system already speaks.

        Items come back in the bank file's nested form — the payload under its modality key
        — because that is what `BankItem` validates, and returning a second shape would make
        this store's output need a translation nothing else needs.
        """
        with self.connect() as conn:
            head = conn.execute(
                """
                SELECT v.version, v.bank_id, v.title, v.coverage_critical_only, v.mains,
                       v.graph_bytes, v.source
                  FROM bank_version v
                 WHERE v.bank_id = %s AND v.is_current
                """,
                (bank_id,),
            ).fetchone()
            if head is None:
                return None
            version = head["version"]

            item_rows = conn.execute(
                """
                SELECT item_id, modality, status, competency, sub_competency,
                       cat_a, cat_b, cat_c, estimated_time_seconds,
                       minimum_success_confidence, payload
                  FROM item WHERE version = %s ORDER BY item_id
                """,
                (version,),
            ).fetchall()
            measure_rows = conn.execute(
                """
                SELECT item_id, node_id, weight FROM item_measure
                 WHERE version = %s ORDER BY item_id, node_id
                """,
                (version,),
            ).fetchall()

        measures: dict[str, list[dict]] = {}
        for row in measure_rows:
            measures.setdefault(row["item_id"], []).append(
                {"variable": row["node_id"], "weight": row["weight"]}
            )

        items = []
        for row in item_rows:
            item = {
                "item_id": row["item_id"],
                "modality": row["modality"],
                "status": row["status"],
                "competency": row["competency"],
                "sub_competency": row["sub_competency"],
                "measures": measures.get(row["item_id"], []),
                "cat": {"a": row["cat_a"], "b": row["cat_b"], "c": row["cat_c"]},
                row["modality"]: row["payload"],
            }
            if row["estimated_time_seconds"] is not None:
                item["estimated_time_seconds"] = row["estimated_time_seconds"]
            if row["minimum_success_confidence"] is not None:
                item["minimum_success_confidence"] = row["minimum_success_confidence"]
            items.append(item)

        graph = json.loads(head["graph_bytes"]) if head["graph_bytes"] else None
        return StoredBank(
            bank_id=head["bank_id"],
            title=head["title"],
            version=version,
            source=head["source"],
            coverage_critical_only=head["coverage_critical_only"],
            mains=tuple(head["mains"]),
            items=tuple(items),
            graph=graph,
        )

    # --- the query the whole schema exists for ------------------------------
    def items_measuring(self, bank_id: str, nodes: list[str]) -> list[str]:
        """Every active item measuring any of `nodes`, as ids.

        This is what a scan of 600 items becomes once `item_measure` exists. Ids, not items:
        `bank-registry` serves items through two endpoints with deliberately different
        authorisation, and a second path returning them would give one caller's
        authorisation to another.
        """
        version = self.version(bank_id)
        if version is None:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT m.item_id
                  FROM item_measure m JOIN item i USING (version, item_id)
                 WHERE m.version = %s AND i.status = 'active' AND m.node_id = ANY(%s)
                 ORDER BY m.item_id
                """,
                (version, list(nodes)),
            ).fetchall()
        return [row["item_id"] for row in rows]
