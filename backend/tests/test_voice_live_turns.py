"""Live-interview failures found in a real session, none of which had a test.

Four bugs, and the reason each survived:

1. `_call_grader` read `item.modality` while taking `item_id` — a NameError on the only
   path that reaches the grader, which needs a live gateway.
2. `observability.session` yielded a second time after an exception was thrown into it,
   so `contextlib` raised `RuntimeError: generator didn't stop after throw()` and the
   real error above was never shown.
3. A question delivered as audio with a late transcript recorded NO turn, and its text
   then attached to the next collection — so the transcript read
   `(audio) / answer / question / answer`.
4. A server-side websocket close was reported as an interview error rather than an
   infrastructure event, burying a routine disconnect under a traceback.
"""

from __future__ import annotations

import asyncio

import pytest

from app.services import observability


class TestObservabilitySession:
    def test_the_body_s_exception_reaches_the_caller_unchanged(self) -> None:
        """The bug that hid the NameError.

        Wrapping the `yield` in `except Exception` and yielding again turns any failure
        inside the block into `RuntimeError: generator didn't stop after throw()`.
        """
        with pytest.raises(ValueError, match="the real error"):
            with observability.session("s", stage="test"):
                raise ValueError("the real error")

    def test_a_broken_collector_does_not_skip_the_work(self, monkeypatch) -> None:
        def explode(**_kwargs):
            raise RuntimeError("collector unreachable")

        monkeypatch.setattr(observability, "_state", True)
        monkeypatch.setattr(observability, "_propagate", explode)

        ran = False
        with observability.session("s", stage="test"):
            ran = True
        assert ran

    def test_a_broken_collector_still_lets_the_body_s_exception_through(
        self, monkeypatch
    ) -> None:
        def explode(**_kwargs):
            raise RuntimeError("collector unreachable")

        monkeypatch.setattr(observability, "_state", True)
        monkeypatch.setattr(observability, "_propagate", explode)

        with pytest.raises(ValueError, match="the real error"):
            with observability.session("s", stage="test"):
                raise ValueError("the real error")

    def test_a_collector_that_fails_on_exit_cannot_suppress_the_body(
        self, monkeypatch
    ) -> None:
        """A tracer may not swallow the application's exception on the way out."""

        class Failing:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                raise RuntimeError("collector died flushing")

        monkeypatch.setattr(observability, "_state", True)
        monkeypatch.setattr(observability, "_propagate", lambda **kw: Failing())

        with pytest.raises(ValueError, match="the real error"):
            with observability.session("s", stage="test"):
                raise ValueError("the real error")


class TestGraderSignature:
    def test_the_grader_is_called_with_a_modality_not_an_item(self, aie_bank) -> None:
        """`_call_grader` takes `item_id`; reading `item.modality` inside it is a
        NameError that only fires with a reachable gateway."""
        import inspect

        from app.services.voice import evaluator

        parameters = inspect.signature(evaluator._call_grader).parameters
        assert "modality" in parameters
        assert "item" not in parameters

        # Code lines only: the fix's own comment names the bug it removed.
        code = "\n".join(
            line
            for line in inspect.getsource(evaluator._call_grader).splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "item.modality" not in code

    @pytest.mark.asyncio
    async def test_the_grading_path_tags_the_real_modality(
        self, aie_bank, monkeypatch
    ) -> None:
        """End to end through `evaluate`, with the gateway stubbed — the shape of call
        that crashed in production."""
        from app.services.voice import evaluator

        seen: dict = {}

        async def fake_chat_json(system, user, *, require=(), trace=None):
            seen.update(trace or {})
            return {
                "item_id": "x",
                "criterion_evidence": [],
                "evaluation_confidence": 0.5,
            }

        monkeypatch.setattr(evaluator, "chat_json", fake_chat_json)
        monkeypatch.setattr(
            evaluator.observability,
            "generation",
            lambda name, **metadata: {"name": name, **metadata},
        )

        item = next(i for i in aie_bank.all_items() if i.modality == "voice")
        package = evaluator.package_from_text(
            item.item_id,
            "Gradient boosted trees usually win on tabular accuracy but need more tuning; "
            "a random forest is the safer baseline when time is short.",
        )

        await evaluator.evaluate(item, package, use_llm=True)

        assert seen.get("modality") == "voice"


class _FakeSession:
    """A Live session that replays a scripted event stream.

    Backed by a real `asyncio.Queue` that is NEVER closed, exactly like the production
    one. That detail is the whole point: a fake which runs dry and stops cannot catch a
    collector that waits forever for an event the stream will not send.
    """

    def __init__(self, events: list[dict]) -> None:
        self._events: asyncio.Queue = asyncio.Queue()
        for event in events:
            self._events.put_nowait(event)
        self.sent: list[str] = []

    async def events(self):
        while True:
            yield await self._events.get()

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def append_audio(self, pcm: bytes) -> None:
        pass

    async def commit_audio(self) -> None:
        pass


class TestTurnOrdering:
    @staticmethod
    def _bridge(events: list[dict]):
        from app.services.voice_live.streamlit_live import StreamlitLiteLLMLiveBridge

        bridge = StreamlitLiteLLMLiveBridge.__new__(StreamlitLiteLLMLiveBridge)
        bridge.turns = []
        bridge.interviewer_wavs = []
        bridge._turn_counter = 0
        bridge.session_id = "s"
        bridge.item_id = "voice_x"
        bridge._session = _FakeSession(events)
        return bridge

    @pytest.mark.asyncio
    async def test_a_question_whose_transcript_arrives_late_is_still_in_order(
        self,
    ) -> None:
        """The reported bug: `(audio) / answer / question / answer`.

        Audio, then `done`, then the transcript. Breaking on `done` recorded the turn
        with no text and let the text attach to the next collection.
        """
        bridge = self._bridge(
            [
                {"type": "audio", "data": b"\x00\x00" * 8},
                {"type": "done"},
                {"type": "text", "text": "A colleague asks whether to use a random forest."},
            ]
        )

        audio = await bridge._collect_model_turn(role_label="interviewer")

        assert "random forest" in audio.transcript
        assert [t["role"] for t in bridge.turns] == ["interviewer"]
        assert bridge.turns[0]["audio_only"] is False

    @pytest.mark.asyncio
    async def test_an_audio_only_turn_is_recorded_rather_than_dropped(
        self, monkeypatch
    ) -> None:
        """The candidate HEARD it, so the record must contain it.

        A turn silently missing from the transcript is what allowed a later text event to
        be attributed to the wrong place in the conversation.
        """
        from app.services.voice_live import streamlit_live

        monkeypatch.setattr(streamlit_live, "TEXT_AFTER_AUDIO_GRACE_SECONDS", 0.01)
        bridge = self._bridge(
            [{"type": "audio", "data": b"\x00\x00" * 8}, {"type": "done"}]
        )

        await bridge._collect_model_turn(role_label="interviewer")

        assert len(bridge.turns) == 1
        assert bridge.turns[0]["role"] == "interviewer"
        assert bridge.turns[0]["audio_only"] is True
        assert bridge.turns[0]["text"] == ""

    @pytest.mark.asyncio
    async def test_a_question_split_across_two_chunks_is_one_turn(self, monkeypatch) -> None:
        """The reported transcript: `question-fragment / answer / question-remainder`.

            Interviewer: You're facing
            You: For an intermittent production bug, the first step is…
            Interviewer: an intermittent bug that only shows up in production under load…

        The transcript does not arrive as one message. Closing the turn on the first chunk
        records "You're facing" as the entire question and leaves the rest of the sentence
        queued, where the NEXT collection picks it up — after the answer to it.
        """
        from app.services.voice_live import streamlit_live

        monkeypatch.setattr(streamlit_live, "TEXT_CHUNK_QUIET_SECONDS", 0.3)
        bridge = self._bridge(
            [
                {"type": "audio", "data": b"\x00\x00" * 8},
                {"type": "done"},
                {"type": "text", "text": "You're facing"},
                {
                    "type": "text",
                    "text": "an intermittent bug that only shows up in production.",
                },
            ]
        )

        result = await asyncio.wait_for(
            bridge._collect_model_turn(role_label="interviewer", timeout=10.0), timeout=5.0
        )

        assert result.transcript == (
            "You're facing an intermittent bug that only shows up in production."
        )
        assert len(bridge.turns) == 1

    @pytest.mark.asyncio
    async def test_text_arriving_before_done_is_not_closed_by_done(
        self, monkeypatch
    ) -> None:
        """`done` is not the last word on the transcript, whichever order they arrive in."""
        from app.services.voice_live import streamlit_live

        monkeypatch.setattr(streamlit_live, "TEXT_CHUNK_QUIET_SECONDS", 0.3)
        bridge = self._bridge(
            [
                {"type": "audio", "data": b"\x00\x00" * 8},
                {"type": "text", "text": "Explain aloud"},
                {"type": "done"},
                {"type": "text", "text": "the principles you follow."},
            ]
        )

        result = await asyncio.wait_for(
            bridge._collect_model_turn(role_label="interviewer", timeout=10.0), timeout=5.0
        )

        assert result.transcript == "Explain aloud the principles you follow."

    @pytest.mark.asyncio
    async def test_a_turn_with_neither_audio_nor_text_records_nothing(self) -> None:
        bridge = self._bridge([{"type": "done"}, {"type": "closed"}])

        await bridge._collect_model_turn(role_label="interviewer")

        assert bridge.turns == []


class TestBoundedWaits:
    """A collector that can block forever presents as "loading" and never recovers.

    `events()` reads an unbounded queue, so a deadline checked only when an event
    arrives is not a deadline. The caller sees a spinner until its own timeout fires
    minutes later — which is exactly what a candidate reported.
    """

    @pytest.mark.asyncio
    async def test_a_silent_stream_ends_the_turn_at_the_timeout(self) -> None:
        bridge = TestTurnOrdering._bridge([])

        result = await asyncio.wait_for(
            bridge._collect_model_turn(role_label="interviewer", timeout=0.2),
            timeout=3.0,
        )

        assert result.transcript == ""
        assert bridge.turns == []

    @pytest.mark.asyncio
    async def test_audio_with_no_following_text_still_returns(self, monkeypatch) -> None:
        """The exact shape that hung: `done` with audio, then nothing ever again."""
        from app.services.voice_live import streamlit_live

        monkeypatch.setattr(streamlit_live, "TEXT_AFTER_AUDIO_GRACE_SECONDS", 0.15)
        bridge = TestTurnOrdering._bridge(
            [{"type": "audio", "data": b"\x00\x00" * 8}, {"type": "done"}]
        )

        result = await asyncio.wait_for(
            bridge._collect_model_turn(role_label="interviewer", timeout=5.0),
            timeout=3.0,
        )

        # Returned on the grace deadline, not the turn timeout, and kept the audio.
        assert result.wav_bytes
        assert bridge.turns[0]["audio_only"] is True

    @pytest.mark.asyncio
    async def test_a_late_transcript_still_closes_the_turn_early(self, monkeypatch) -> None:
        """Waiting for late text must not cost the full grace when the text arrives."""
        from app.services.voice_live import streamlit_live

        monkeypatch.setattr(streamlit_live, "TEXT_AFTER_AUDIO_GRACE_SECONDS", 5.0)
        bridge = TestTurnOrdering._bridge(
            [
                {"type": "audio", "data": b"\x00\x00" * 8},
                {"type": "done"},
                {"type": "text", "text": "the question"},
            ]
        )

        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await asyncio.wait_for(
            bridge._collect_model_turn(role_label="interviewer", timeout=10.0),
            timeout=3.0,
        )

        assert result.transcript == "the question"
        assert loop.time() - started < 2.0, "waited out the grace despite the text arriving"


class TestConnectionClose:
    def test_a_server_close_is_not_reported_as_an_interview_error(self) -> None:
        """A gateway keepalive expiring while the candidate thinks is infrastructure.

        Raising for it loses the transcript already captured; the caller needs to stop
        collecting and grade what it has.
        """
        import inspect

        from app.services.voice_live import litellm_realtime

        source = inspect.getsource(litellm_realtime.AsyncLiteLLMLiveSession._read_loop)
        assert "ConnectionClosed" in source
        # The close path must not enqueue an `error` event — only the generic handler may.
        close_branch = source.split("except ConnectionClosed")[1].split("except Exception")[0]
        close_code = "\n".join(
            line for line in close_branch.splitlines() if not line.lstrip().startswith("#")
        )
        assert '"type": "error"' not in close_code
        assert '{"type": "closed"}' in source
