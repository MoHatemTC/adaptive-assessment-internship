"""`BankStore`, with Postgres underneath it instead of two layers of files.

THE ONLY MODULE HERE THAT IMPORTS THE ENGINE

`stores.sql` is deliberately engine-free — psycopg and nothing else — so the schema
and the queries can be read, tested and reused without pulling in numpy and 600 KB of
question banks. This module is the adapter, and it follows the same rule
the in-process bank repository follows for the same reason.

WHY THIS SUBCLASSES RATHER THAN REIMPLEMENTS

`BankStore` already owns a great deal that has nothing to do with where bytes live: the
version-keyed caches, the `JsonUnifiedBank` construction, the graph loading, and
`validate()` — the rules about coverage, pairing, duplicate ids and parameter bounds that a
bank has to clear before it may measure anybody. Reimplementing any of that against SQL
would create a second set of answers to questions that already have one, and the drift
would show up as a bank accepted here and refused there.

So only one thing is overridden: **where the bytes come from.** SQL is the durable,
transactional source of truth; the bytes are materialised into a local cache directory and
the inherited machinery reads them exactly as it reads a checked-in bank.

THAT IS WHY THE VERSION STILL MATCHES

`BankStore.version` hashes the bank file, the graph file and the declared profile. The
materialised files are byte-identical to what the file store would hold — the seeder stores
the original bytes untouched — so the inherited hash produces the same number without this
class computing anything. A version that changed would invalidate every orchestrator cache
and move every pinned expectation in the suite, silently.

THE CACHE DIRECTORY IS A CACHE, NOT A STORE

Deleting it costs a re-read from the database. Nothing is lost, and nothing about a bank
lives only there — which is the distinction the current two-service arrangement cannot make,
because there the directory *is* the store and two processes have to agree about it.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from cat_engine.engine.services.orchestrator.bank_store import (
    BankProfile,
    BankStore,
    Validation,
    _seed_profiles,
)

from .seed import seed_from_profiles
from .sql import SqlBankStore

logger = logging.getLogger(__name__)


class SqlBackedBankStore(BankStore):
    """Banks resolved from Postgres, parsed by the engine's own loader."""

    def __init__(self, sql: SqlBankStore, cache_dir: Path | None = None) -> None:
        # No seeds and no store directory: both layers now come from the database, and a
        # residual file layer would be a third answer to "which bank is this".
        super().__init__(seeds={}, store_dir=Path(tempfile.mkdtemp(prefix="unused-")))
        self._sql = sql
        self._cache = Path(
            cache_dir or Path(tempfile.gettempdir()) / "adaptive-bank-cache"
        )
        self._cache.mkdir(parents=True, exist_ok=True)
        self._materialised: dict[str, BankProfile] = {}
        #: bank id -> current version, so `version()` costs no file read.
        self._versions_by_bank: dict[str, str] = {}

    # --- where the bytes come from ----------------------------------------
    def _materialise(self, bank_id: str) -> BankProfile | None:
        """Write this bank's stored bytes into the cache, and describe them as a profile.

        Keyed by version, so a bank that has not changed is written once. A bank that has
        lands under a new name and the old one is simply never read again — no in-place
        rewrite, so a reader holding the previous path keeps reading a coherent bank.
        """
        loaded = self._sql.load(bank_id)
        if loaded is None:
            return None

        cached = self._materialised.get(bank_id)
        if cached is not None and cached.bank_path.name.startswith(loaded.version):
            return cached

        bank_path = self._cache / f"{loaded.version}-{bank_id}.bank.json"
        if not bank_path.exists():
            bank_path.write_bytes(self._sql_bytes(loaded.version, "items_bytes"))

        graph_path = None
        if loaded.graph is not None:
            graph_path = self._cache / f"{loaded.version}-{bank_id}.graph.json"
            if not graph_path.exists():
                graph_path.write_bytes(self._sql_bytes(loaded.version, "graph_bytes"))

        profile = BankProfile(
            bank_id=loaded.bank_id,
            title=loaded.title,
            bank_path=bank_path,
            graph_path=graph_path,
            mains=tuple(loaded.mains),
            coverage_critical_only=loaded.coverage_critical_only,
            source=loaded.source,  # type: ignore[arg-type]
        )
        self._materialised[bank_id] = profile
        return profile

    def _sql_bytes(self, version: str, column: str) -> bytes:
        with self._sql.connect() as conn:
            row = conn.execute(
                f"SELECT {column} AS blob FROM bank_version WHERE version = %s",
                (version,),
            ).fetchone()
        return bytes(row["blob"]) if row and row["blob"] is not None else b""

    # --- BankStore's surface, re-sourced ----------------------------------
    def profiles(self) -> dict[str, BankProfile]:
        """Every bank, from metadata alone — NO content is read and nothing is written.

        The paths here name where a bank WOULD be materialised, not where it already is.
        `bank()` and `graph()` do that on first use, so listing the catalogue costs one
        query rather than five blob reads and five file writes.
        """
        resolved: dict[str, BankProfile] = {}
        for row in self._sql.catalogue():
            bank_id, version = row["bank_id"], row["version"]
            resolved[bank_id] = BankProfile(
                bank_id=bank_id,
                title=row["title"],
                bank_path=self._cache / f"{version}-{bank_id}.bank.json",
                graph_path=(
                    self._cache / f"{version}-{bank_id}.graph.json"
                    if row["has_graph"]
                    else None
                ),
                mains=tuple(row["mains"]),
                coverage_critical_only=row["coverage_critical_only"],
                source=row["source"],  # type: ignore[arg-type]
            )
            self._versions_by_bank[bank_id] = version
        return resolved

    def version(self, bank_id: str) -> str:
        """From the database, not by hashing files.

        The inherited implementation reads the bank and graph files and hashes their bytes,
        which would force a materialisation for every cache lookup. The stored version was
        computed from exactly those bytes, so this is the same number without the read.
        """
        cached = self._versions_by_bank.get(bank_id)
        if cached is not None:
            return cached
        version = self._sql.version(bank_id)
        if version is None:
            from cat_engine.engine.services.orchestrator.bank_store import (
                UnknownBankError,
            )

            raise UnknownBankError(bank_id, self._sql.bank_ids())
        self._versions_by_bank[bank_id] = version
        return version

    def profile(self, bank_id: str) -> BankProfile:
        """ONE bank, with its bytes on disk by the time the paths are handed out.

        The split with `profiles()` is the whole optimisation. The catalogue lists banks and
        must not read them; `profile()` is what callers use to GET A PATH, and several read
        that path directly rather than going through `bank()` or `graph()` —
        `registry._graph_cached` loads the graph file itself. Handing out a path to a file
        that does not exist yet is how that surfaces, as a FileNotFoundError from a caller
        that never asked this class for anything.
        """
        materialised = self._materialise(bank_id)
        if materialised is None:
            from cat_engine.engine.services.orchestrator.bank_store import (
                UnknownBankError,
            )

            raise UnknownBankError(bank_id, self._sql.bank_ids())
        return materialised

    def bank(self, bank_id: str):
        self._materialise(bank_id)
        return super().bank(bank_id)

    def graph(self, bank_id: str):
        self._materialise(bank_id)
        return super().graph(bank_id)

    def is_stored(self, bank_id: str) -> bool:
        """`stored` shadows `seed`, exactly as it did with two directories — the layer is
        now a column rather than a path, and deleting a shadow still restores the seed."""
        loaded = self._sql.load(bank_id)
        return loaded is not None and loaded.source == "stored"

    def save(
        self,
        *,
        bank_id: str,
        title: str,
        items: list[dict[str, Any]],
        graph: dict[str, Any] | None,
        coverage_critical_only: bool | None,
        validation: Validation,
    ) -> None:
        """Write an ALREADY-VALIDATED bank, in one transaction.

        The guard is the inherited one, kept verbatim: validation and writing are separate
        calls so a dry run can do the first without the second, and so this can never be the
        place where a caller forgets.

        What changes is what a failure leaves behind. The file store stages a directory and
        renames it, because a filesystem offers nothing better; here a failure rolls back.
        """
        if not validation.accepted:
            raise ValueError(
                f"refusing to write {bank_id}: {[f.message for f in validation.errors]}"
            )
        self._sql.save(
            bank_id=bank_id,
            title=title,
            items=items,
            graph=graph,
            coverage_critical_only=coverage_critical_only,
            mains=list(validation.mains),
            source="stored",
        )
        self._materialised.pop(bank_id, None)
        self._versions_by_bank.pop(bank_id, None)
        self.reset(bank_id)

    def delete(self, bank_id: str) -> bool:
        deleted = self._sql.delete(bank_id)
        self._materialised.pop(bank_id, None)
        self._versions_by_bank.pop(bank_id, None)
        self.reset(bank_id)
        return deleted

    def reset(self, bank_id: str | None = None) -> None:
        if bank_id is None:
            self._materialised.clear()
            self._versions_by_bank.clear()
        else:
            self._materialised.pop(bank_id, None)
            self._versions_by_bank.pop(bank_id, None)
        super().reset(bank_id)

    @property
    def store_dir(self) -> Path:
        """Reported on `/health`. Names the database rather than a path, because a path
        would be a cache directory and an operator reading it would draw the wrong
        conclusion about where a bank actually lives."""
        return self._cache


def _wait_for(sql: SqlBankStore, *, attempts: int = 30, delay: float = 1.0) -> None:
    """Block briefly until the database answers.

    `bank-db` sits behind a compose profile, and a profiled service cannot be a
    `depends_on` target of one that starts without it — so on a cold `--profile db` boot
    these services can reach the database before it is accepting connections. Restarting the
    container until it works would also converge, eventually, and would fill a log with
    stack traces that look like a misconfiguration rather than a race.
    """
    import time

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with sql.connect():
                return
        except Exception as exc:
            last = exc
            if attempt == 0:
                logger.info("waiting for the bank database to accept connections")
            time.sleep(delay)
    raise RuntimeError(f"the bank database never became reachable: {last}")


def install(
    dsn: str, *, seed_from=None, cache_dir: Path | None = None
) -> SqlBackedBankStore:
    """Point this process's bank store at Postgres. Returns the store now in force.

    Ensures the schema, loads the checked-in banks if they are not there yet, and rebinds
    `registry.STORE` through the seam the engine already provides for exactly this —
    `use_store` exists because a service resolves its store at startup rather than from a
    module-level constant.

    Seeding here rather than in a deployment step: a seeder that has to be run exactly once,
    by hand, before the first request is a step somebody eventually forgets or repeats. It
    is idempotent on the content hash, so running it on every boot costs one query per bank.
    """
    from cat_engine.engine.services.orchestrator import registry

    sql = SqlBankStore(dsn)
    _wait_for(sql)
    sql.ensure_schema()

    # THE CHECKED-IN BANKS, not whatever store happens to be installed. Reading them from
    # `registry.STORE` was circular: called twice in one process — a second replica's worth
    # of startup, or two tests — the second call would ask the SQL store for the seeds it is
    # about to be given, and get back cache paths for files it has not written yet.
    profiles = seed_from if seed_from is not None else _seed_profiles()
    if profiles:
        seeded = seed_from_profiles(sql, profiles)
        logger.info("seeded %d checked-in banks into the database", len(seeded))

    store = SqlBackedBankStore(sql, cache_dir=cache_dir)
    registry.use_store(store)
    logger.info("bank store is now Postgres, cache at %s", store.store_dir)
    return store
