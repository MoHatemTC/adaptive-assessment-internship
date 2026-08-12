"""Where live assessments and registered banks are kept.

TWO STORES, TWO LIFETIMES, DELIBERATELY NOT ONE

A bank is content that is published and kept. A session is a record of what a person
answered and has a deletion deadline. They take separate DSNs even when both point at one
server, because one url for both makes the deadline somebody's afterthought.

PLUGGABLE, WITH THE IN-MEMORY ONE AS THE DEFAULT

`SessionStore` is a Protocol. `InMemorySessionStore` is what the monolith did and what the
services did without a database configured; `SqlSessionStore` persists so a restart resumes
rather than loses. A host with its own storage — its own retention policy, its own
encryption at rest — implements the Protocol and passes it in, which is the seam that did
not exist while this was seven services each holding their own assumption.

The Postgres implementations live under `stores.sql` and import psycopg. That import is
lazy at every call site, so a host that never configures a database never installs a driver.
"""

from __future__ import annotations

from cat_engine.stores.sessions import (
    InMemorySessionStore,
    Session,
    SessionConflict,
    SessionStore,
)

__all__ = [
    "InMemorySessionStore",
    "Session",
    "SessionConflict",
    "SessionStore",
    "open_session_store",
]


def open_session_store(dsn: str = "") -> SessionStore:
    """The session store this configuration asks for.

    An empty DSN keeps sessions in this process, which is a supported single-replica
    arrangement rather than a fallback: a restart loses every assessment mid-answer, and a
    second worker cannot serve one this one started. Stated here rather than discovered.
    """
    import logging

    logger = logging.getLogger(__name__)

    if not dsn.strip():
        logger.info("no SESSION_DATABASE_URL — sessions live in this process only")
        return InMemorySessionStore()

    from cat_engine.stores.sql import SqlSessionStore

    backing = SqlSessionStore(dsn)
    backing.ensure_schema()
    logger.info("sessions are persisted; a restart resumes rather than loses them")
    return InMemorySessionStore(backing)
