"""live-voice: the operator surface, and the two things that must fail closed.

WHAT IS NOT TESTED HERE, AND WHY

Not the realtime bridge itself. Driving it needs a live model connection and real audio,
and a test that mocked both would be testing the mock. The room lifecycle is covered by
`backend/tests/test_realtime_room.py` and `test_voice_live_turns.py`, which run against the
engine directly — moving the HTTP wrapper into a service did not change any of it.

What only exists at THIS layer is the wrapper: a missing room answering honestly, and the
debug feed — which carries candidate transcripts — being off unless someone asked for it.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def live(client_factory):
    with client_factory("live-voice") as client:
        yield client


class TestTheOperatorSurface:
    def test_health_identifies_the_service_and_the_live_model(self, live):
        body = live.get("/health").json()
        assert body["service"] == "live-voice"
        assert body["status"] == "ok"
        assert "live_model" in body["detail"]

    def test_the_turn_taking_configuration_is_served(self, live):
        """The browser reads gating thresholds from here rather than hardcoding them, so a
        change to turn-taking is a deployment setting rather than a client release."""
        body = live.get("/api/live/config").json()
        assert body["gating_mode"] == "manual"
        assert body["via"] == "litellm"
        assert body["turn_end_silence_ms"] > 0

    def test_cors_is_open_without_credentials(self, live):
        """A frontend embeds `/interview` from its own origin. Wildcard plus credentials
        is invalid browser policy and would risk reflecting ambient credentials if auth
        were added later — public non-credentialed access is the actual contract."""
        response = live.get("/health", headers={"Origin": "https://frontend.test"})
        assert response.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in response.headers


class TestTheDebugFeedIsPrivateByDefault:
    """It carries candidate transcripts. An operator tool, not an open endpoint."""

    def test_it_is_refused_when_disabled(self, live):
        assert live.get("/api/live/debug").status_code == 404
        assert live.delete("/api/live/debug").status_code == 404

    def test_it_answers_when_a_deployment_turns_it_on(self, client_factory, monkeypatch):
        from conftest import load_service

        with load_service("live-voice") as main:
            monkeypatch.setattr(main.settings, "live_debug_api_enabled", True)
            from fastapi.testclient import TestClient

            with TestClient(main.app) as client:
                assert client.get("/api/live/debug").status_code == 200


class TestAMissingRoomAnswersHonestly:
    def test_the_http_route_is_a_404_that_names_the_room(self, live):
        response = live.get("/api/live/rooms/definitely-not-a-room")
        assert response.status_code == 404
        assert response.json()["code"] == "room_unknown"

    def test_the_websocket_accepts_and_then_explains(self, live):
        """Accept first, close after. A bare handshake rejection reaches the browser as
        'connection closed', which tells a candidate nothing and an operator less."""
        import json as json_module

        with live.websocket_connect("/ws/live/definitely-not-a-room") as ws:
            message = json_module.loads(ws.receive_text())
        assert message["type"] == "error"
        assert "not found" in message["error"]


class TestThePagesAreServed:
    def test_the_interview_page_and_its_worklet_are_reachable(self, live):
        """`/interview` is embedded in an iframe and loads its own audio worklet from a
        relative path, so both must come from THIS service."""
        assert live.get("/interview").status_code == 200
        assert live.get("/static/mic-worklet.js").status_code == 200
        assert live.get("/static/interview.js").status_code == 200
