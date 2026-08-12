"""Live assessments, in this process's memory.

WHY MEMORY AND NOT A DATABASE

This is exactly what the monolith did, with the same retention and capacity rules, and the
migration is not the place to change it. `AssessmentState` is serialisable by construction
— the 41-point posterior is carried as a `list[float]` specifically so it round-trips
through JSON — so the seam for a real store exists and nothing here has to move when one
arrives. What does NOT exist yet is a decision about where candidate response data lives,
how long, and under whose retention policy, and inventing one inside a refactor would be
the wrong way to make it.

The consequence is stated rather than hidden: sessions are bound to one replica. Run one,
or put sticky sessions in front of several, until there is a store.

THE TWO RULES THAT MATTER

An assessment in progress is NEVER evicted. Capacity pressure and retention both only ever
remove sessions that have finished — a candidate halfway through a test losing their
session to a cache policy is not a trade-off anyone chose.

A session is bound to the bank VERSION it began under. Replacing a bank mid-session would
otherwise change the item pool underneath a live candidate: items vanishing from a queue
that already ranked them, and a session's own record of what it asked no longer matching
what the bank says exists.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np
from cat_engine.contracts import ScopeManifest
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.schemas.orchestration import AssessmentState

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depends on whether this image installs the store
    from adaptive_store import SessionConflict
except ImportError:  # the in-process deployment, which cannot raise one

    class SessionConflict(RuntimeError):
        """Never raised without persistence — nothing else can move a session."""



@dataclass
class Session:
    """One live assessment and everything the service holds beside it."""

    bank_id: str
    #: The content hash of the bank this session began under. Pinned, never refreshed.
    bank_version: str
    state: AssessmentState
    #: The one exposure-control stream this assessment owns. Persisted rather than
    #: recreated, because a fresh generator per request would make randomesque selection
    #: repeat its first draw on every question.
    rng: np.random.Generator
    #: The scope this assessment was narrowed to, or None for the whole bank. Pinned
    #: exactly as `bank_version` is: the allowlist and the coverage requirement a candidate
    #: began under must not change under them, and a scope rebuilt mid-session against a
    #: replaced bank would name items the queue has already ranked and lost.
    scope: ScopeManifest | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access: float = field(default_factory=time.time)
    finished_at: float | None = None
    #: Set once, when the session is first observed to have stopped. A poll of the session
    #: must not append a second record of the same assessment.
    recorded: bool = False


class SessionStore:
    """Sessions, in this process and — when a DSN is configured — in Postgres beside it.

    THE DICT IS NOW A CACHE, NOT THE STORE.

    With `SESSION_DATABASE_URL` set, every mutation is written through to the database and a
    miss reads back from it, so a restart resumes and a second replica can serve a candidate
    the first one started. Without it, this is exactly what it was: a bounded dict, which is
    a supported single-replica deployment rather than a fallback, and `/health` says which
    one is in force.

    WHY WRITE-THROUGH AND NOT WRITE-BEHIND. The thing being persisted is the record of what
    a candidate answered. Losing the last write because a process died between the answer and
    the flush is the exact failure this exists to prevent.
    """

    def __init__(self, persistence=None) -> None:
        self._sessions: dict[str, Session] = {}
        #: `SqlSessionStore`, or None for the in-process deployment.
        self._db = persistence
        #: session id -> the revision this process last saw. What a compare-and-swap needs,
        #: and the reason two replicas answering one assessment cannot both win.
        self._revisions: dict[str, int] = {}

    @property
    def persistent(self) -> bool:
        return self._db is not None

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    def get(self, session_id: str) -> Session | None:
        self.prune()
        session = self._sessions.get(session_id)
        if session is None and self._db is not None:
            session = self._rehydrate(session_id)
        if session is not None:
            session.last_access = time.time()
        return session

    def _rehydrate(self, session_id: str) -> Session | None:
        """Rebuild a session this process has never seen, from the database.

        The path a restart and a second replica both take. Everything a session needs is a
        row: the state, the pinned bank and version, the scope, and the generator state —
        which is restored rather than recreated, so exposure control continues its stream
        instead of redrawing its first item.
        """
        from adaptive_store import restore_rng

        row = self._db.load(session_id)
        if row is None:
            return None
        session = Session(
            bank_id=row.bank_id,
            bank_version=row.bank_version,
            state=AssessmentState.model_validate(row.state),
            rng=restore_rng(row.rng_state),
            scope=ScopeManifest.model_validate(row.scope) if row.scope else None,
            finished_at=time.time() if row.finished else None,
            recorded=row.finished,
        )
        self._sessions[session_id] = session
        self._revisions[session_id] = row.revision
        logger.info("resumed session %s from the store", session_id)
        return session

    def save(self, session: Session, *, finished: bool = False) -> None:
        """Write a session's new state through. A no-op without persistence configured.

        Raises `SessionConflict` when another writer moved this session first — the
        distributed form of the `stale_answer` 409, and the reason no lock is held across a
        grading call that can take thirty seconds.
        """
        if self._db is None:
            return
        from adaptive_store import PersistedSession, rng_state_of

        session_id = session.state.session_id
        row = PersistedSession(
            session_id=session_id,
            bank_id=session.bank_id,
            bank_version=session.bank_version,
            state=session.state.model_dump(mode="json"),
            rng_state=rng_state_of(session.rng),
            scope=session.scope.model_dump(mode="json") if session.scope else None,
            revision=0,
            finished=finished,
        )
        seen = self._revisions.get(session_id)
        if seen is None:
            self._db.create(row)
            self._revisions[session_id] = 1
            return
        self._revisions[session_id] = self._db.update(
            row, seen_revision=seen, finished=finished
        )

    def lock_for(self, session_id: str) -> asyncio.Lock | None:
        session = self._sessions.get(session_id)
        return session.lock if session is not None else None

    def add(self, session: Session) -> None:
        self._sessions[session.state.session_id] = session

    def drop(self, session_id: str) -> bool:
        dropped = self._sessions.pop(session_id, None) is not None
        self._revisions.pop(session_id, None)
        if self._db is not None:
            # A DELETE is a candidate or an operator saying this assessment is over. Removing
            # it from the cache and leaving the row would make it come back on the next read.
            dropped = self._db.drop(session_id) or dropped
        return dropped

    def mark_finished(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None and session.finished_at is None:
            session.finished_at = time.time()
            # Starts the retention clock. Until this, the row is a live assessment and is
            # never removed by any amount of pruning.
            self.save(session, finished=True)

    def prune(self, *, now: float | None = None, reserve_slot: bool = False) -> int:
        """Bound process-local state without ever evicting an assessment in progress.

        With persistence on, this still only bounds the CACHE. Deleting the row is a
        retention decision with a deadline attached, and it belongs to
        `SqlSessionStore.prune`, which is called on a schedule rather than incidentally by
        whichever request happened to arrive.
        """
        now = time.time() if now is None else now
        removed = 0

        for session_id, session in list(self._sessions.items()):
            if (
                session.finished_at is not None
                and now - session.finished_at
                >= engine_settings.cat_session_retention_seconds
            ):
                removed += self.drop(session_id)

        finished = sorted(
            (s for s in self._sessions.values() if s.finished_at is not None),
            key=lambda s: s.last_access,
        )

        def over_capacity() -> bool:
            count = len(self._sessions)
            maximum = engine_settings.cat_max_retained_sessions
            return count >= maximum if reserve_slot else count > maximum

        while over_capacity() and finished:
            removed += self.drop(finished.pop(0).state.session_id)
        return removed

    def at_capacity(self) -> bool:
        """True when a new session cannot be admitted without evicting a live one.

        Counted against the DATABASE when there is one, because the cap is about how many
        assessments exist rather than how many this replica happens to be holding — three
        replicas each admitting up to the cap would be three times the cap.
        """
        self.prune(reserve_slot=True)
        count = self._db.count() if self._db is not None else len(self._sessions)
        return count >= engine_settings.cat_max_retained_sessions
