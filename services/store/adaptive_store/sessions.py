"""Assessment sessions, persisted — so losing a process does not lose a candidate.

WHAT WAS WRONG WITH A DICT

`SessionStore` held every live assessment in one process. A restart lost them all, mid
answer, with no way to resume; a second replica could not serve a candidate the first one
had started. The engine never required that — `Orchestrator` takes a state and returns one,
holding nothing between calls — so this was a property of the service, not of the
measurement.

WHY IT TOOK A DECISION RATHER THAN CODE

A session holds every answer a person gave. Persisting it turns "ephemeral by accident" into
"stored on purpose", and stored personal data needs a retention answer before it needs a
schema. Three are now written down rather than implied:

  RETENTION      `cat_session_retention_seconds` stops being an eviction hint and becomes a
                 deletion deadline. Expired rows are deleted, not merely dropped from a
                 cache.
  WHAT IS KEPT   the full state while a session runs, because it IS the session. After it
                 finishes, `SESSION_KEEP_RESPONSES=false` blanks the response detail and
                 keeps the report — the thing anybody actually reads afterwards.
  WHERE          a DSN of its own, `SESSION_DATABASE_URL`. It may point at the same Postgres
                 as the bank store, and it is a separate setting because "content we publish"
                 and "personal data with a deletion clock" have different lifetimes, and
                 sharing one URL makes the clock somebody's afterthought.

THE TWO THINGS THAT LOOKED UNSERIALISABLE, AND ARE NOT

`rng` is a `np.random.Generator`, which JSON cannot hold — but `rng.bit_generator.state` is
a plain dict, and restoring it reproduces the stream exactly. Exposure control stays
unpredictable across a restart, rather than every resumed session drawing the same first
item.

`lock` is an `asyncio.Lock`, which is process-local by construction and cannot travel. It
did not have to: the guard it protects was already optimistic. `record_response` snapshots
the presented item BEFORE taking the lock and re-checks it after, returning 409 when it
moved. Across replicas that becomes a compare-and-swap on `revision`, and the lock stays
only as a same-process optimisation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

SCHEMA = Path(__file__).with_name("sessions.sql")


class SessionConflict(RuntimeError):
    """Another writer advanced this session first.

    The distributed form of the `stale_answer` 409 the single-process path already returns:
    two answers to one assessment, and the second one arrived against a state that has since
    moved. Surfaced rather than merged, because merging them would apply an answer to a
    question the candidate was not looking at.
    """


@dataclass(frozen=True)
class PersistedSession:
    """One session as a row. `revision` is what makes a write safe without a lock."""

    session_id: str
    bank_id: str
    bank_version: str
    state: dict[str, Any]
    rng_state: dict[str, Any]
    scope: dict[str, Any] | None
    revision: int
    finished: bool


class SqlSessionStore:
    """Sessions in Postgres, guarded by a revision rather than by a lock."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(SCHEMA.read_text(encoding="utf-8"))
            conn.commit()

    # --- reads -------------------------------------------------------------
    def load(self, session_id: str) -> PersistedSession | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, bank_id, bank_version, state, rng_state, scope,
                       revision, finished_at IS NOT NULL AS finished
                  FROM assessment_session WHERE session_id = %s
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return PersistedSession(
            session_id=row["session_id"],
            bank_id=row["bank_id"],
            bank_version=row["bank_version"],
            state=row["state"],
            rng_state=row["rng_state"],
            scope=row["scope"],
            revision=row["revision"],
            finished=row["finished"],
        )

    def count(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT count(*) AS n FROM assessment_session"
            ).fetchone()["n"]

    # --- writes ------------------------------------------------------------
    def create(self, session: PersistedSession) -> None:
        with self.connect() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO assessment_session
                    (session_id, bank_id, bank_version, state, rng_state, scope, revision)
                VALUES (%s, %s, %s, %s, %s, %s, 1)
                """,
                (
                    session.session_id,
                    session.bank_id,
                    session.bank_version,
                    Jsonb(session.state),
                    Jsonb(session.rng_state),
                    Jsonb(session.scope) if session.scope is not None else None,
                ),
            )

    def update(
        self,
        session: PersistedSession,
        *,
        seen_revision: int,
        finished: bool = False,
    ) -> int:
        """Write a new state, only if nobody moved it since `seen_revision`.

        COMPARE AND SWAP, not a lock. Two answers to one assessment can be in flight on two
        replicas; the first to land bumps the revision and the second matches nothing, which
        is exactly the `stale_answer` case — and the alternative, a lock held across a
        grading call that can take thirty seconds, is a lock nobody should hold.
        """
        with self.connect() as conn, conn.transaction():
            row = conn.execute(
                """
                UPDATE assessment_session
                   SET state = %s, rng_state = %s, revision = revision + 1,
                       last_access = now(),
                       finished_at = CASE WHEN %s THEN coalesce(finished_at, now())
                                          ELSE finished_at END
                 WHERE session_id = %s AND revision = %s
                RETURNING revision
                """,
                (
                    Jsonb(session.state),
                    Jsonb(session.rng_state),
                    finished,
                    session.session_id,
                    seen_revision,
                ),
            ).fetchone()
        if row is None:
            raise SessionConflict(
                f"session {session.session_id} moved past revision {seen_revision}"
            )
        return row["revision"]

    def drop(self, session_id: str) -> bool:
        with self.connect() as conn, conn.transaction():
            return bool(
                conn.execute(
                    "DELETE FROM assessment_session WHERE session_id = %s", (session_id,)
                ).rowcount
            )

    # --- retention ---------------------------------------------------------
    def prune(self, *, retention_seconds: int, keep_responses: bool) -> int:
        """Delete what is past its deadline. Returns how many rows went.

        DELETION, not eviction. The in-process store dropped finished sessions to bound
        memory and the data went with the process anyway; a row outlives the process, so the
        same setting has to mean "remove it" or the retention policy is a comment.

        A session IN PROGRESS is never removed, exactly as the in-process store never evicted
        one. The clock starts when it finishes.
        """
        with self.connect() as conn, conn.transaction():
            removed = conn.execute(
                """
                DELETE FROM assessment_session
                 WHERE finished_at IS NOT NULL
                   AND finished_at < now() - make_interval(secs => %s)
                """,
                (retention_seconds,),
            ).rowcount

            if not keep_responses:
                # The report is what anybody reads afterwards. The per-response detail is
                # what makes the row personal data, so it goes as soon as the session ends
                # rather than at the end of the retention window.
                conn.execute(
                    """
                    UPDATE assessment_session
                       SET state = state - 'responses' - 'served_item_ids'
                                        - 'administered_by_variable'
                     WHERE finished_at IS NOT NULL
                       AND state ? 'responses'
                    """
                )
        return removed

    def expire_stale_in_progress(self, *, maximum_age_seconds: int) -> int:
        """Remove sessions nobody has touched in a very long time and which never finished.

        A candidate who closes the tab leaves a row that is neither finished nor coming
        back. Without this the table only grows, and "we keep responses for a day" would be
        false for exactly the sessions nobody consented to leaving behind.
        """
        with self.connect() as conn, conn.transaction():
            return conn.execute(
                """
                DELETE FROM assessment_session
                 WHERE finished_at IS NULL
                   AND last_access < now() - make_interval(secs => %s)
                """,
                (maximum_age_seconds,),
            ).rowcount


def rng_state_of(rng) -> dict[str, Any]:
    """A numpy generator's state, as something JSON can hold.

    Round-trips exactly, so a resumed session continues its exposure-control stream instead
    of restarting it — which would make the first item after every restart predictable.
    """
    return json.loads(json.dumps(rng.bit_generator.state, default=_coerce))


def restore_rng(state: dict[str, Any]):
    import numpy as np

    rng = np.random.default_rng()
    rng.bit_generator.state = state
    return rng


def _coerce(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"cannot serialise {type(value)!r} into an rng state")
