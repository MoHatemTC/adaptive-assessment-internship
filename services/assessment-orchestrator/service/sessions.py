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
import time
from dataclasses import dataclass, field

import numpy as np
from app.config.settings import settings as engine_settings
from app.schemas.orchestration import AssessmentState


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
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_access: float = field(default_factory=time.time)
    finished_at: float | None = None
    #: Set once, when the session is first observed to have stopped. A poll of the session
    #: must not append a second record of the same assessment.
    recorded: bool = False


class SessionStore:
    """Process-local sessions, bounded without ever evicting one in progress."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    def get(self, session_id: str) -> Session | None:
        self.prune()
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_access = time.time()
        return session

    def lock_for(self, session_id: str) -> asyncio.Lock | None:
        session = self._sessions.get(session_id)
        return session.lock if session is not None else None

    def add(self, session: Session) -> None:
        self._sessions[session.state.session_id] = session

    def drop(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def mark_finished(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None and session.finished_at is None:
            session.finished_at = time.time()

    def prune(self, *, now: float | None = None, reserve_slot: bool = False) -> int:
        """Bound process-local state without ever evicting an assessment in progress."""
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
        """True when a new session cannot be admitted without evicting a live one."""
        self.prune(reserve_slot=True)
        return len(self._sessions) >= engine_settings.cat_max_retained_sessions
