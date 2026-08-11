"""A session survives the process that started it.

WHAT THIS HAS TO PROVE

Not that rows appear. That a candidate mid-assessment can be served by a process which has
never heard of them, and get the same assessment — same posterior, same pinned bank version,
same scope, and an exposure-control stream that continues rather than restarts.

The last one is the easiest to get wrong and the hardest to notice: a resumed session whose
generator was recreated rather than restored draws the same "random" item every time a
replica comes up, and nothing anywhere reports it.

SKIPPED WITHOUT A DATABASE, like the other SQL suites, so the default run needs no
infrastructure. `SESSION_DATABASE_URL` also has to be read at import by the service, so this
runs as its own session:

    cd services && SESSION_DATABASE_URL=... python -m pytest tests/test_session_persistence.py
"""

from __future__ import annotations

import os

import numpy as np
import pytest

DSN = os.environ.get("SESSION_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not DSN, reason="SESSION_DATABASE_URL is unset; this file needs a live Postgres"
)


@pytest.fixture()
def store():
    from adaptive_store import SqlSessionStore

    sql = SqlSessionStore(DSN)
    sql.ensure_schema()
    with sql.connect() as conn:
        conn.execute("DELETE FROM assessment_session")
        conn.commit()
    return sql


def a_session(session_id: str = "asmt_test000001", *, seed: int = 7):
    from app.schemas.orchestration import AssessmentState

    from service.sessions import Session

    return Session(
        bank_id="DA",
        bank_version="deadbeefdeadbeef",
        state=AssessmentState(session_id=session_id),
        rng=np.random.default_rng(seed),
    )


@pytest.fixture()
def sessions(store):
    """A `SessionStore` writing through to the database, as the service builds one."""
    from conftest import load_service

    with load_service("assessment-orchestrator"):
        from service.sessions import SessionStore

        yield SessionStore(store)


class TestASessionOutlivesItsProcess:
    def test_a_store_that_never_saw_it_can_still_serve_it(self, store, sessions):
        from conftest import load_service  # noqa: F401 - keeps the service on the path

        from service.sessions import SessionStore

        original = a_session()
        sessions.add(original)
        sessions.save(original)

        # A second store, holding nothing — the restart, and the second replica.
        fresh = SessionStore(store)
        assert len(fresh) == 0
        resumed = fresh.get("asmt_test000001")

        assert resumed is not None
        assert resumed.bank_id == "DA"
        assert resumed.bank_version == "deadbeefdeadbeef"
        assert resumed.state.session_id == "asmt_test000001"

    def test_the_pinned_bank_version_survives(self, store, sessions):
        """Replacing a bank must not change the pool underneath a live candidate, and a
        resumed session that forgot its version would be assessed against whatever is
        current now."""
        from service.sessions import SessionStore

        session = a_session()
        sessions.add(session)
        sessions.save(session)
        assert SessionStore(store).get(session.state.session_id).bank_version == (
            "deadbeefdeadbeef"
        )

    def test_exposure_control_continues_rather_than_restarting(self, store, sessions):
        """THE ONE THAT WOULD GO UNNOTICED.

        A generator recreated instead of restored makes the first item after every restart
        predictable — selection would still look random, and be the same draw every time.
        """
        session = a_session(seed=1234)
        sessions.add(session)
        # Burn some draws, exactly as a running assessment would.
        [session.rng.integers(0, 1000) for _ in range(5)]
        sessions.save(session)
        expected = [int(session.rng.integers(0, 1000)) for _ in range(5)]

        from service.sessions import SessionStore

        resumed = SessionStore(store).get(session.state.session_id)
        assert [int(resumed.rng.integers(0, 1000)) for _ in range(5)] == expected

    def test_a_scope_survives_the_round_trip(self, store, sessions):
        """The allowlist a candidate began under. A resumed session that lost it would widen
        silently to the whole bank — an assessment covering more than the one that started."""
        from adaptive_contracts import ScopeManifest, ScopeMainDTO

        from service.sessions import SessionStore

        session = a_session()
        session.scope = ScopeManifest(
            scope_id="scp_abc",
            scope_hash="abc",
            bank_id="DA",
            bank_version="deadbeefdeadbeef",
            selected=["DA.1"],
            item_ids=["q1", "q2"],
            mains=[ScopeMainDTO(main="DA", partial=True, retained_weight=0.25)],
        )
        sessions.add(session)
        sessions.save(session)

        resumed = SessionStore(store).get(session.state.session_id)
        assert resumed.scope is not None
        assert resumed.scope.scope_id == "scp_abc"
        assert resumed.scope.item_ids == ["q1", "q2"]
        assert resumed.scope.mains[0].partial is True


class TestTwoWritersCannotBothWin:
    """What replaced the lock. Two replicas answering one assessment is the case an
    `asyncio.Lock` never covered, and a lock held across a thirty-second grading call is one
    nobody should hold anyway."""

    def test_the_second_writer_is_refused(self, store, sessions):
        from adaptive_store import SessionConflict

        from service.sessions import SessionStore

        session = a_session()
        sessions.add(session)
        sessions.save(session)

        # Another replica loads the same session, so both hold revision 1.
        other = SessionStore(store)
        other.get(session.state.session_id)

        sessions.save(session)  # this one lands, revision -> 2
        with pytest.raises(SessionConflict):
            other.save(other.get(session.state.session_id))

    def test_the_winner_is_the_one_that_persisted(self, store, sessions):
        from service.sessions import SessionStore

        session = a_session()
        sessions.add(session)
        sessions.save(session)
        session.state = session.state.model_copy(update={"items_administered": 3})
        sessions.save(session)

        resumed = SessionStore(store).get(session.state.session_id)
        assert resumed.state.items_administered == 3


class TestRetentionIsADeadlineRatherThanAHint:
    def test_a_session_in_progress_is_never_removed(self, store, sessions):
        session = a_session()
        sessions.add(session)
        sessions.save(session)
        assert store.prune(retention_seconds=0, keep_responses=True) == 0
        assert store.load(session.state.session_id) is not None

    def test_a_finished_session_is_deleted_once_it_is_past_the_deadline(
        self, store, sessions
    ):
        """DELETED, not evicted. The in-process store dropped finished sessions to bound
        memory and the data went with the process anyway; a row outlives the process, so the
        same setting has to mean removal or the retention policy is a comment."""
        session = a_session()
        sessions.add(session)
        sessions.save(session)
        sessions.mark_finished(session.state.session_id)

        assert store.prune(retention_seconds=0, keep_responses=True) == 1
        assert store.load(session.state.session_id) is None

    def test_an_abandoned_session_does_not_live_forever(self, store, sessions):
        """A candidate who closes the tab leaves a row that is neither finished nor coming
        back. Without this the table only grows, and "we keep responses for a day" is false
        for exactly the sessions nobody consented to leaving behind."""
        session = a_session()
        sessions.add(session)
        sessions.save(session)
        assert store.expire_stale_in_progress(maximum_age_seconds=0) == 1
        assert store.load(session.state.session_id) is None

    def test_dropping_a_session_removes_the_row_too(self, store, sessions):
        session = a_session()
        sessions.add(session)
        sessions.save(session)
        assert sessions.drop(session.state.session_id) is True
        assert store.load(session.state.session_id) is None
