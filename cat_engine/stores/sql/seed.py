"""Loading the checked-in banks into the database, without changing what they are.

THE ONE THING THIS HAS TO GET RIGHT

The version each seed lands with must be the version the file store already computes for it.
Every orchestrator cache is keyed on that number and every assessment pins it. If the seeder
produces a different one, every cache invalidates at once, the parity suite's pinned
expectations move, and five banks quietly become five different banks with the same names —
with nothing failing to say so.

So the seeder does not re-serialise anything. It reads the checked-in files as BYTES and
stores those bytes, and the hash is taken over them exactly as `BankStore.version` takes it.
`assert_versions_match` is the check, and it is meant to be run in CI rather than trusted.

IDEMPOTENT ON (bank_id, version)

Running it twice is a no-op, because the version IS the content. That matters more than it
sounds: this runs at service startup, and a seeder that had to be run exactly once would be
a deployment step somebody eventually forgets or repeats.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .sql import SqlBankStore, content_hash

logger = logging.getLogger(__name__)


def seed_from_profiles(store: SqlBankStore, profiles: dict) -> dict[str, str]:
    """Load every checked-in bank into the database. Returns bank id -> version.

    Takes `BankProfile`s rather than reading a directory, because each profile carries a
    claim the file cannot make about itself — which mains it is supposed to measure, and
    whether full coverage is reachable for it. A directory scan would lose both.
    """
    written: dict[str, str] = {}
    for bank_id, profile in sorted(profiles.items()):
        items_bytes = Path(profile.bank_path).read_bytes()
        graph_bytes = (
            Path(profile.graph_path).read_bytes()
            if profile.graph_path is not None
            else None
        )
        version = _write_verbatim(
            store,
            bank_id=bank_id,
            title=profile.title,
            items_bytes=items_bytes,
            graph_bytes=graph_bytes,
            mains=list(profile.mains),
            coverage_critical_only=profile.coverage_critical_only,
        )
        written[bank_id] = version
        logger.info("seeded %s at version %s", bank_id, version)
    return written


def _write_verbatim(
    store: SqlBankStore,
    *,
    bank_id: str,
    title: str,
    items_bytes: bytes,
    graph_bytes: bytes | None,
    mains: list[str],
    coverage_critical_only: bool | None,
) -> str:
    """Store the file's own bytes, and index the rows parsed out of them.

    `SqlBankStore.save` re-serialises what it is given, which is right for a bank arriving
    over HTTP — there is no original file to preserve. A SEED has one, and its bytes are its
    identity, so this path writes them through untouched and derives the rows from them.
    """
    parsed = json.loads(items_bytes)
    items = parsed["items"] if isinstance(parsed, dict) else parsed
    graph = json.loads(graph_bytes) if graph_bytes else None
    profile = {
        "mains": list(mains),
        "coverage_critical_only": coverage_critical_only,
        "title": title or bank_id,
    }
    version = content_hash(items_bytes, graph_bytes, profile)

    from psycopg.types.json import Jsonb

    with store.connect() as conn, conn.transaction():
        exists = conn.execute(
            "SELECT 1 FROM bank_version WHERE version = %s", (version,)
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE bank_version SET is_current = (version = %s) WHERE bank_id = %s",
                (version, bank_id),
            )
            return version

        conn.execute(
            """
            INSERT INTO bank (bank_id, title) VALUES (%s, %s)
            ON CONFLICT (bank_id) DO UPDATE SET title = EXCLUDED.title
            """,
            (bank_id, title or bank_id),
        )
        conn.execute(
            "UPDATE bank_version SET is_current = false WHERE bank_id = %s", (bank_id,)
        )
        conn.execute(
            """
            INSERT INTO bank_version (version, bank_id, title, source,
                                      coverage_critical_only, mains, items_bytes,
                                      graph_bytes, profile_bytes, is_current)
            VALUES (%s,%s,%s,'seed',%s,%s,%s,%s,%s,true)
            """,
            (
                version,
                bank_id,
                title or bank_id,
                coverage_critical_only,
                list(mains),
                items_bytes,
                graph_bytes,
                json.dumps(profile, sort_keys=True).encode(),
            ),
        )
        store._write_items(conn, version, items)
        if graph is not None:
            store._write_graph(conn, version, graph)
        _ = Jsonb  # imported for the writers above
    return version


def assert_versions_match(store: SqlBankStore, file_store) -> list[str]:
    """Every seeded bank's version equals the file store's. Returns the mismatches.

    Meant to run in CI and at startup rather than to be trusted. A mismatch here is the
    failure that has no other symptom: nothing errors, nothing logs, and every cached
    parameter set in the fleet silently belongs to a bank nobody registered.
    """
    mismatched = []
    for bank_id in file_store.profiles():
        expected = file_store.version(bank_id)
        actual = store.version(bank_id)
        if actual != expected:
            mismatched.append(f"{bank_id}: file={expected} sql={actual}")
    return mismatched
