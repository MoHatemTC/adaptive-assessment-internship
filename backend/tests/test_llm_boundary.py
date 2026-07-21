"""The boundary between the engine and the gateway.

Everything here protects one property: NO MODEL PROBLEM ENDS AN ASSESSMENT. Every caller
of `chat_json` has a deterministic path and catches `LLMUnavailable`, so that contract
holds exactly as long as `LLMUnavailable` is the only exception that escapes.

It did not hold. The Picking Agent passed its payload dict where text was expected; the
SDK forwarded the dict as message content; the proxy answered 400 "'str' object has no
attribute 'get'"; a 400 is an `APIStatusError`, not an `LLMUnavailable`, so it sailed past
the fallback, out of the async queue fill, and terminated the session on the candidate's
screen. Two independent faults, each of which alone would have been survivable.
"""

from __future__ import annotations

import json

import httpx
import pytest
from openai import APIStatusError, AuthenticationError, BadRequestError

from app.services.adaptive import llm as llm_module
from app.services.adaptive.llm import LLMUnavailable, chat_json


def _status_error(status: int, message: str) -> APIStatusError:
    request = httpx.Request("POST", "http://proxy.invalid/chat/completions")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    cls = {400: BadRequestError, 401: AuthenticationError}.get(status, APIStatusError)
    return cls(message, response=response, body=None)


# --- the payload must be text ------------------------------------------------
@pytest.mark.asyncio
async def test_a_non_string_payload_is_refused_at_the_boundary():
    """The exact slip that produced the 400, caught where it is diagnosable.

    A TypeError naming the call site beats a gateway error about the proxy's own parser,
    which is what the deployment actually reported and which pointed nowhere useful.
    """
    with pytest.raises(TypeError, match="json.dumps"):
        await chat_json("system", {"variable": "T1.1"}, require=("selected_id",))


@pytest.mark.asyncio
async def test_the_refusal_happens_before_any_network_call(monkeypatch):
    """A bad payload must cost nothing — not a request, not a retry, not a timeout."""
    calls = []

    async def _never(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(llm_module, "_one_call", _never)
    with pytest.raises(TypeError):
        await chat_json("system", ["not", "text"])
    assert calls == []


# --- a refused request degrades, it does not escape --------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404, 422])
async def test_a_rejected_request_becomes_llm_unavailable(monkeypatch, status):
    """The gateway answered and refused. Every caller's fallback must be able to see it.

    Parameterised over the ways a proxy says no — an unparseable body, a revoked key, a
    model name that does not exist, an unsupported response_format. All are configuration
    faults, and none is a reason a candidate cannot be asked another question.
    """
    async def _refuse(*args, **kwargs):
        raise _status_error(status, "nope")

    monkeypatch.setattr(llm_module, "_one_call", _refuse)
    with pytest.raises(LLMUnavailable, match=f"HTTP {status}"):
        await chat_json("system", "user", require=("selected_id",))


@pytest.mark.asyncio
async def test_a_rejected_request_is_not_retried(monkeypatch):
    """A 400 is refused identically the second time. Retrying spends a candidate's wait."""
    attempts = []

    async def _refuse(*args, **kwargs):
        attempts.append(1)
        raise _status_error(400, "invalid request format")

    monkeypatch.setattr(llm_module, "_one_call", _refuse)
    with pytest.raises(LLMUnavailable):
        await chat_json("system", "user")
    assert len(attempts) == 1


# --- the picker survives it end to end ---------------------------------------
@pytest.mark.asyncio
async def test_the_picker_falls_back_when_the_gateway_refuses(monkeypatch):
    """The property the deployment lost: a refused pick still returns a question."""
    from app.services.orchestrator import picker as picker_module

    async def _refuse(*args, **kwargs):
        raise _status_error(400, "Invalid request format: 'str' object has no attribute 'get'")

    monkeypatch.setattr(llm_module, "_one_call", _refuse)

    from app.services.orchestrator.bank import JsonUnifiedBank
    from app.services.orchestrator.variables import seed_variable

    pool = JsonUnifiedBank().shortlist("T1.1", exclude=set())
    state = seed_variable("T1.1", self_rating=3)

    candidate = await picker_module.pick(pool, state, "T1.1", use_llm=True)
    assert candidate is not None
    assert candidate.chosen_by_llm is False
    assert candidate.item_id in {item.item_id for item in pool}


@pytest.mark.asyncio
async def test_the_picker_sends_serialised_text(monkeypatch):
    """Not merely 'it does not crash': the payload must arrive as parseable JSON.

    Asserting on the type alone would pass for `str(payload)`, which is Python repr —
    single-quoted, `True`/`None` — and not JSON any model should be asked to read.
    """
    from app.services.orchestrator import picker as picker_module

    seen: dict = {}

    async def _capture(system, user, temperature, require, trace=None):
        seen["user"] = user
        parsed = json.loads(user)
        return {
            "selected_item_id": parsed["constraints"]["allowed_item_ids"][0],
            "reason_code": "MAX_INFORMATION",
        }

    monkeypatch.setattr(llm_module, "_one_call", _capture)

    from app.services.orchestrator.bank import JsonUnifiedBank
    from app.services.orchestrator.variables import seed_variable

    pool = JsonUnifiedBank().shortlist("T1.1", exclude=set())
    state = seed_variable("T1.1", self_rating=3)

    await picker_module.pick(pool, state, "T1.1", use_llm=True)
    assert isinstance(seen["user"], str)
    assert json.loads(seen["user"])["variable_under_test"] == "T1.1"
