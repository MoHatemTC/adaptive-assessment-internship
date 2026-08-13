"""Tracing must be incapable of affecting the assessment.

Monitoring is the layer most likely to be misconfigured, because it is the one nobody
notices working — and here it wraps the exact call a candidate is waiting on. So the tests
that matter are not "does a span arrive"; they are "what happens when none of this works".
"""

from __future__ import annotations

import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services import observability


@pytest.fixture(autouse=True)
def _fresh():
    observability.reset()
    yield
    observability.reset()


# --- off by default ----------------------------------------------------------
def test_tracing_is_off_without_both_keys(monkeypatch):
    """A public key alone cannot authenticate, so it must not read as configured."""
    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "", raising=False)
    assert settings.langfuse_enabled is False
    assert observability.enabled() is False


def test_generation_labels_are_empty_when_tracing_is_off(monkeypatch):
    """`{}` splats into a completion call as nothing at all.

    This is why the call site needs no branch: there is one code path through the gateway
    whether tracing is on or off, so a traced call cannot behave differently from an
    untraced one.
    """
    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "", raising=False)
    assert observability.generation("picker", variable="T1.1") == {}


def test_the_session_context_yields_even_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    ran = False
    with observability.session("asmt_1", track="T1"):
        ran = True
    assert ran


# --- failure is contained ----------------------------------------------------
def test_a_broken_langfuse_disables_tracing_instead_of_raising(monkeypatch):
    """Import errors, bad hosts, moved APIs — all mean "no tracing", never "no engine"."""
    monkeypatch.setattr(settings, "langfuse_public_key", "pk", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk", raising=False)

    import builtins

    real_import = builtins.__import__

    def _explode(name, *args, **kwargs):
        if name.startswith("langfuse"):
            raise RuntimeError("collector on fire")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _explode)
    assert observability.configure() is False
    assert observability.generation("picker") == {}


def test_the_session_context_still_runs_its_body_when_propagation_fails(monkeypatch):
    """The body does the WORK. Losing a trace is acceptable; skipping the work is not."""
    monkeypatch.setattr(observability, "_state", True)

    def _explode(**kwargs):
        raise RuntimeError("propagation failed")

    monkeypatch.setattr(observability, "_propagate", _explode)

    ran = False
    with observability.session("asmt_1"):
        ran = True
    assert ran


def test_flush_never_raises(monkeypatch):
    monkeypatch.setattr(observability, "_state", True)
    observability.flush()  # no client was ever constructed


def test_configure_is_resolved_once(monkeypatch):
    """Not a micro-optimisation: without it, a misconfigured collector is re-imported and
    re-logged on every model call, which turns one warning into a flooded log."""
    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    calls = []
    monkeypatch.setattr(
        settings.__class__, "langfuse_enabled",
        property(lambda self: calls.append(1) or False),
    )
    observability.configure()
    observability.configure()
    observability.configure()
    assert len(calls) == 1


# --- what is not sent --------------------------------------------------------
def test_submitted_source_is_never_put_in_a_trace():
    """A submission is a person's work and tracing has no need of it.

    The item id, the score and the test counts answer every question monitoring exists to
    answer; the source answers none of them and leaves the system if included.
    """
    source = "def solve(n):\n    return n * 2\n"
    redacted = observability.redact_code(source)
    assert "return n * 2" not in redacted
    assert "def solve" not in redacted
    assert "2 lines" in redacted


def test_live_speech_is_never_put_in_a_trace():
    text = "I would use a dict for O(1) lookup and explain aliasing carefully."
    redacted = observability.redact_speech(text)
    assert set(redacted) == {"chars", "words"}
    assert redacted["chars"] == len(text)
    assert redacted["words"] == len(text.split())
    assert text not in str(redacted)


def test_live_start_end_are_noops_when_tracing_is_off(monkeypatch):
    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    handle = observability.start_live(
        model="gemini/x",
        room_id="r1",
        item_id="open_1",
        assessment_session_id="asmt_1",
        question="secret question text",
    )
    assert handle.observation is None
    observability.end_live(
        handle,
        outcome_status="complete",
        transcript="secret answer text",
        candidate_turns=2,
    )
    assert handle.ended is True


def test_live_end_tolerates_broken_observation(monkeypatch):
    monkeypatch.setattr(observability, "_state", True)

    class _Boom:
        def update(self, **kwargs):
            raise RuntimeError("collector down")

        def end(self):
            raise RuntimeError("collector down")

    handle = observability.LiveHandle(observation=_Boom(), started_at=0.0)
    observability.end_live(handle, outcome_status="error", error="boom")
    assert handle.ended is True


def test_unset_attributes_are_dropped(monkeypatch):
    """A trace should show what was known, not a wall of nulls."""
    monkeypatch.setattr(observability, "_state", True)
    labels = observability.generation("picker", variable="T1.1", reason=None)
    assert labels["metadata"] == {"variable": "T1.1"}


# --- the traced path must reach the gateway unchanged ------------------------
@pytest.mark.asyncio
async def test_trace_labels_never_reach_the_gateway(monkeypatch):
    """The bug class this whole change was about, in the other direction.

    Langfuse's `name` and `metadata` are consumed by its argument extractor and stripped
    before the request is built. If a version ever stopped stripping them, they would be
    forwarded as chat-completion parameters and the proxy would answer 400 — the same
    failure mode, arriving from the monitoring layer instead of the picker, on the same
    call a candidate is waiting on.

    So this asserts on the bytes the gateway receives, with instrumentation genuinely
    installed, rather than trusting the integration's documented behaviour.
    """
    pytest.importorskip("langfuse")

    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", 0))
            received["body"] = _json.loads(self.rfile.read(length) or b"{}")
            body = _json.dumps(
                {
                    "id": "1", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{
                        "index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"selected_id": "x"}'},
                    }],
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the test output readable
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    monkeypatch.setattr(settings, "langfuse_public_key", "pk-lf-test", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "sk-lf-test", raising=False)
    # A closed port, so the suite makes no outbound call. The export failing is the point
    # being demonstrated as harmless — instrumentation stays installed and the completion
    # still succeeds, which is exactly the behaviour a broken collector must have.
    monkeypatch.setattr(settings, "langfuse_host", "http://127.0.0.1:1", raising=False)
    monkeypatch.setattr(settings, "litellm_base_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "litellm_api_key", "sk-test")

    from cat_engine.engine.services.adaptive import llm as llm_module

    llm_module.reset_client()
    try:
        assert observability.configure() is True
        labels = observability.generation("picker", variable="T1.1")
        assert labels["name"] == "picker"  # instrumentation really is on

        reply = await llm_module.chat_json(
            "system", "user", require=("selected_id",), trace=labels
        )
        assert reply == {"selected_id": "x"}
    finally:
        llm_module.reset_client()
        server.shutdown()
        # Langfuse installs a process-global client and patches the OpenAI resource
        # classes. Left running it keeps retrying a dead collector for the rest of the
        # suite, so this test cleans up after itself rather than leaking into the others.
        try:
            from langfuse import get_client

            get_client().shutdown()
        # pragma: no cover - cleanup is best effort, and a failure here must not fail a
        # test that has already made its assertion.
        except Exception:  # noqa: BLE001, S110
            pass

    assert "name" not in received["body"], "langfuse label leaked into the request"
    assert "metadata" not in received["body"], "langfuse metadata leaked into the request"
    assert received["body"]["messages"][1]["content"] == "user"
