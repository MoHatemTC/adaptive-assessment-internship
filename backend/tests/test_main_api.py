"""Candidate-facing CAT API contracts and end-to-end session flow."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import main
from app.config.settings import settings
from app.config.voice_settings import voice_settings
from app.services.orchestrator import registry


@pytest.fixture
async def client(stub_boundaries, monkeypatch):
    main._cat_sessions.clear()
    main._cat_rngs.clear()
    main._cat_locks.clear()
    main._finished_sessions.clear()
    main._cat_last_access.clear()
    main._cat_finished_at.clear()
    # API tests are deliberately offline. Real Langfuse credentials in a developer .env
    # must not make TestClient startup depend on a collector or its background workers.
    monkeypatch.setattr(main.observability, "configure", lambda: False)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as test_client:
        yield test_client
    main._cat_sessions.clear()
    main._cat_rngs.clear()
    main._cat_locks.clear()
    main._finished_sessions.clear()
    main._cat_last_access.clear()
    main._cat_finished_at.clear()


def _answer_for(presenting: dict) -> dict:
    item = presenting["item"]
    modality = item["modality"]
    if modality == "mcq":
        return {"type": modality, "chosen_index": 0, "use_llm": False, "seed": 999}
    if modality == "code":
        return {
            "type": modality,
            "code": item.get("starter_code") or "def solve(*args):\n    return None\n",
            "use_llm": False,
            "seed": 999,
        }
    return {
        "type": modality,
        "transcript": "A concrete answer with an example and an explicit tradeoff.",
        "use_llm": False,
        "seed": 999,
    }


class TestCandidatePayloadBoundary:
    def test_code_item_exposes_scaffold_but_never_reference_solution(self):
        item = next(
            i for i in registry.get_bank("DA").all_items() if i.modality == "code"
        )

        payload = main._ui_item(item)

        assert payload["starter_code"] == item.payload.get("starter_code", "")
        assert "reference_solution" not in payload
        assert item.payload["reference_solution"] not in json.dumps(payload)


class TestCatSessionApi:
    @pytest.mark.asyncio
    async def test_unknown_bank_and_session_are_explicit_client_errors(self, client):
        assert (
            await client.post("/api/cat/sessions", json={"bank_id": "missing"})
        ).status_code == 400
        assert (await client.get("/api/cat/sessions/missing")).status_code == 404

    @pytest.mark.asyncio
    async def test_create_get_answer_and_persistent_rng(self, client):
        created = await client.post(
            "/api/cat/sessions",
            json={
                "bank_id": "DA",
                "target_variables": ["DA"],
                "use_llm": False,
                "seed": 17,
            },
        )
        assert created.status_code == 200
        body = created.json()
        session_id = body["session_id"]
        assert body["bank_id"] == "DA"
        assert body["presenting"] is not None

        rng = main._cat_rngs[session_id]
        fetched = await client.get(f"/api/cat/sessions/{session_id}")
        assert fetched.status_code == 200
        assert fetched.json()["presenting"] == body["presenting"]

        mismatch = await client.post(
            f"/api/cat/sessions/{session_id}/answer",
            json={"type": "not-the-presented-modality", "use_llm": False},
        )
        assert mismatch.status_code == 400

        answered = await client.post(
            f"/api/cat/sessions/{session_id}/answer",
            json=_answer_for(body["presenting"]),
        )
        assert answered.status_code == 200
        assert answered.json()["items_administered"] == 1
        receipt = answered.json()["last_graded"]
        assert receipt["item_id"] == body["presenting"]["item"]["item_id"]
        assert receipt["accepted"] is True
        serialized_receipt = json.dumps(receipt)
        assert "answer_index" not in serialized_receipt
        assert "reference_solution" not in serialized_receipt
        assert "criterion_scores" not in serialized_receipt
        assert "detail" not in receipt
        assert "open_debug" not in answered.json()
        assert main._cat_rngs[session_id] is rng

    @pytest.mark.asyncio
    async def test_completed_report_keeps_every_band_provisional(self, client):
        response = await client.post(
            "/api/cat/sessions",
            json={
                "bank_id": "DA",
                "target_variables": ["DA"],
                "use_llm": False,
                "seed": 23,
            },
        )
        assert response.status_code == 200
        body = response.json()
        session_id = body["session_id"]

        for _ in range(80):
            if body["stop"]:
                break
            assert body["presenting"] is not None
            response = await client.post(
                f"/api/cat/sessions/{session_id}/answer",
                json=_answer_for(body["presenting"]),
            )
            assert response.status_code == 200
            body = response.json()

        assert body["stop"] is True
        assert body["report"] is not None
        measured = [v for v in body["report"]["variables"] if v["observations"] > 0]
        assert measured
        assert {v["decision_status"] for v in measured} == {"provisional"}

    @pytest.mark.asyncio
    async def test_session_delete_releases_all_process_local_state(self, client):
        created = (
            await client.post(
                "/api/cat/sessions",
                json={"bank_id": "DA", "target_variables": ["DA"], "use_llm": False},
            )
        ).json()
        session_id = created["session_id"]

        deleted = await client.delete(f"/api/cat/sessions/{session_id}")

        assert deleted.json() == {"ok": True}
        assert session_id not in main._cat_sessions
        assert session_id not in main._cat_rngs
        assert session_id not in main._cat_locks
        assert (await client.get(f"/api/cat/sessions/{session_id}")).status_code == 404

    @pytest.mark.asyncio
    async def test_active_session_capacity_fails_closed(self, client, monkeypatch):
        monkeypatch.setattr(settings, "cat_max_retained_sessions", 1)
        payload = {"bank_id": "DA", "target_variables": ["DA"], "use_llm": False}
        first = await client.post("/api/cat/sessions", json=payload)

        second = await client.post("/api/cat/sessions", json=payload)

        assert first.status_code == 200
        assert second.status_code == 503
        await client.delete(f"/api/cat/sessions/{first.json()['session_id']}")
        assert (await client.post("/api/cat/sessions", json=payload)).status_code == 200

    @pytest.mark.asyncio
    async def test_concurrent_answers_are_serialized_and_the_stale_one_is_rejected(
        self, monkeypatch
    ) -> None:
        from starlette.requests import Request

        orchestrator = main._orchestrator_for("DA")
        state = orchestrator.begin(["DA"])
        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=main.np.random.default_rng(31)
        )
        state = orchestrator.ensure_presenting(state)
        session_id = state.session_id
        main._cat_sessions[session_id] = ("DA", state)
        main._cat_rngs[session_id] = main.np.random.default_rng(31)
        main._cat_locks[session_id] = asyncio.Lock()

        active = 0
        maximum_active = 0

        async def fake_submit(**_kwargs):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.03)
            _bank, saved = main._cat_sessions[session_id]
            # Simulate the first request advancing to a different presentation.
            candidate = saved.presenting
            assert candidate is not None
            main._cat_sessions[session_id] = (
                "DA",
                saved.model_copy(
                    update={
                        "presenting": candidate.model_copy(
                            update={"item_id": f"{candidate.item_id}-next"}
                        )
                    }
                ),
            )
            active -= 1
            return {"ok": True}

        monkeypatch.setattr(main, "_submit_cat_answer_unlocked", fake_submit)

        async def answer_once():
            request = Request({"type": "http", "headers": []})
            return await main.submit_cat_answer(
                session_id,
                request,
                type_form=None,
                chosen_index_form=None,
                code_form=None,
                transcript_form=None,
                use_llm_form=False,
                seed_form=None,
                audio=None,
            )

        results = await asyncio.gather(
            answer_once(), answer_once(), return_exceptions=True
        )

        assert maximum_active == 1
        assert sum(result == {"ok": True} for result in results) == 1
        conflicts = [
            result for result in results if isinstance(result, main.HTTPException)
        ]
        assert len(conflicts) == 1
        assert conflicts[0].status_code == 409


class TestLiveHelperApi:
    @pytest.mark.asyncio
    async def test_health_config_and_bank_discovery(self, client):
        health = await client.get(
            "/health", headers={"Origin": "https://candidate.test"}
        )
        assert health.json() == {"ok": True}
        assert health.headers["access-control-allow-origin"] == "*"
        assert "access-control-allow-credentials" not in health.headers
        config = await client.get("/api/live/config")
        assert config.status_code == 200
        assert config.json()["gating_mode"] == "manual"
        banks = (await client.get("/api/banks")).json()
        assert banks["active"]
        assert {row["bank_id"] for row in banks["banks"]} >= {"AIE", "DA"}

    @pytest.mark.asyncio
    async def test_debug_ring_is_private_by_default(self, client, monkeypatch):
        monkeypatch.setattr(voice_settings, "live_debug_api_enabled", False)
        assert (await client.get("/api/live/debug")).status_code == 404
        assert (await client.delete("/api/live/debug")).status_code == 404

        monkeypatch.setattr(voice_settings, "live_debug_api_enabled", True)
        assert (await client.get("/api/live/debug")).status_code == 200

    @pytest.mark.asyncio
    async def test_missing_live_room_has_http_and_websocket_errors(self, client):
        room_id = "definitely-not-a-room"
        response = await client.get(f"/api/live/rooms/{room_id}")
        assert response.status_code == 404
        assert response.json() == {"error": "not_found"}

        class FakeWebSocket:
            def __init__(self) -> None:
                self.accepted = False
                self.messages: list[str] = []
                self.close_code: int | None = None

            async def accept(self) -> None:
                self.accepted = True

            async def send_text(self, message: str) -> None:
                self.messages.append(message)

            async def close(self, code: int) -> None:
                self.close_code = code

        websocket = FakeWebSocket()
        await main.live_ws(websocket, room_id)
        message = json.loads(websocket.messages[0])
        assert websocket.accepted is True
        assert websocket.close_code == 4404
        assert message["type"] == "error"
        assert room_id in message["error"]
