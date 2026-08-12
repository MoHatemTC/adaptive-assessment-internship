"""Interview rooms as a Python API, and the two things that must fail closed.

WHAT IS NOT TESTED HERE, AND WHY

Not the realtime bridge itself. Driving it needs a live model connection and real audio, and
a test that mocked both would be testing the mock. The room lifecycle is covered by
`test_realtime_room.py` and `test_voice_live_turns.py`, which run against the engine
directly — moving the wrapper out of a service did not change any of it.

WHAT THE COLLAPSE REMOVED FROM THIS FILE

Three classes, and each absence is a thing the host now owns rather than a thing that
stopped mattering.

`TestTheOperatorSurface` asserted `/health` and CORS. There is no server to be healthy and
no origin to allow; `Live.config()` still exists and is tested below, because the browser
reads gating thresholds from it and that has to stay a setting rather than a client release.

`TestAMissingRoomAnswersHonestly::test_the_websocket_accepts_and_then_explains` asserted
that a socket for a missing room accepted first and then sent a JSON error, because a bare
handshake rejection reaches a browser as "connection closed". That is the host's pump now.
`Live.get` raises `RoomUnknown` with the room named; turning that into a graceful socket
close is transport, and the module cannot do it on the host's behalf.

`TestThePagesAreServed` asserted `/interview` and its worklet were reachable. The module
ships them and points at them — see `STATIC_DIR` below — but serving them is the host's.
"""

from __future__ import annotations

import pytest

from cat_engine.live import STATIC_DIR, Live, RoomUnknown, realtime_room_class


@pytest.fixture()
def live():
    from cat_engine.settings import ModuleSettings

    return Live(ModuleSettings())


class TestTheTurnTakingConfiguration:
    def test_it_is_served_so_the_browser_does_not_hardcode_it(self, live):
        """A change to turn-taking should be a setting rather than a client release."""
        body = live.config()
        assert body["gating_mode"] == "manual"
        assert body["via"] == "litellm"
        assert body["turn_end_silence_ms"] > 0

    def test_it_names_the_model_the_room_will_open(self, live):
        assert "model" in live.config()


class TestTheDebugFeedIsPrivateByDefault:
    """It carries candidate transcripts. An operator tool, not something a host should be
    able to expose by forgetting to check a flag — so it raises rather than returning
    empty."""

    def test_it_is_refused_when_disabled(self, live):
        with pytest.raises(RoomUnknown):
            live.debug_feed()
        with pytest.raises(RoomUnknown):
            live.clear_debug_feed()

    def test_it_answers_when_a_deployment_turns_it_on(self, live, monkeypatch):
        monkeypatch.setattr(live.settings, "live_debug_api_enabled", True)
        assert isinstance(live.debug_feed(), list)


class TestAMissingRoomAnswersHonestly:
    def test_it_raises_with_the_room_named(self, live):
        """A refusal nobody can act on is a refusal that gets retried unchanged."""
        with pytest.raises(RoomUnknown) as caught:
            live.get("definitely-not-a-room")
        assert "definitely-not-a-room" in caught.value.detail
        assert caught.value.status_code == 404

    def test_dropping_a_room_that_does_not_exist_is_not_an_error(self, live):
        """Idempotent on purpose: a host cleaning up after a dropped socket should not have
        to know whether the room outlived it."""
        live.drop("definitely-not-a-room")


class TestTheBrowserClientShipsWithTheModule:
    """The mic worklet must be served from the same origin as the page, so a host serves
    these files rather than reimplementing them. They are packaged for that reason, and
    `STATIC_DIR` is how a host finds them."""

    def test_the_interview_page_and_its_worklet_are_present(self):
        assert (STATIC_DIR / "interview.html").is_file()
        assert (STATIC_DIR / "interview.js").is_file()
        assert (STATIC_DIR / "mic-worklet.js").is_file()

    def test_the_page_loads_its_worklet_from_a_relative_path(self):
        """Which is what makes same-origin serving a requirement rather than a preference.
        A host that proxies the page from one origin and the worklet from another gets an
        interview that opens and records nothing."""
        source = (STATIC_DIR / "interview.js").read_text(encoding="utf-8")
        assert "mic-worklet.js" in source
        assert "http://" not in source.split("mic-worklet.js")[0][-200:]


class TestARoomHoldsNoAssessment:
    """The property that makes losing a room cost a candidate the interview they were in
    rather than the session: a room holds audio, turns and a transcript, and cannot end an
    assessment or move a posterior."""

    def test_the_room_type_exposes_no_scoring_surface(self):
        room_class = realtime_room_class()
        for forbidden in ("grade", "score", "theta", "posterior", "stop"):
            assert not hasattr(room_class, forbidden), forbidden

    def test_a_finished_room_produces_a_transcript_and_not_an_outcome(self):
        """`finish()` returns a `LiveInterviewResult`. Turning it into a score is the
        grader's job, reached by submitting the transcript as a normal answer — which is
        why an audio failure can be reported as an audio failure rather than as a candidate
        who said nothing."""
        from cat_engine.engine.services.voice_live.gemini_live import LiveInterviewResult

        fields = set(getattr(LiveInterviewResult, "__dataclass_fields__", {})) or set(
            getattr(LiveInterviewResult, "model_fields", {})
        )
        assert fields, "LiveInterviewResult has neither dataclass nor pydantic fields"
        for forbidden in ("score", "weight", "theta", "outcomes"):
            assert forbidden not in fields, forbidden
