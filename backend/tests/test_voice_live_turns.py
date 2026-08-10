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
