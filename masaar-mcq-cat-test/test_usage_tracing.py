"""Tests for token metering and Langfuse cost attribution.

Cost reporting is the kind of thing that looks right and is quietly wrong: a stale
token count still renders as a plausible number. These tests pin the parts that would
misreport silently.

No API key or Langfuse account needed — the OpenAI client and the Langfuse client are
both stubbed.

Run: python test_usage_tracing.py
"""

from __future__ import annotations

import sys
import types

import llm_client
import tracing

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok   ' if cond else 'FAIL '} {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


class FakeResp:
    def __init__(self, content: str, prompt_tokens: int, completion_tokens: int):
        msg = types.SimpleNamespace(content=content)
        self.choices = [types.SimpleNamespace(message=msg)]
        self.usage = types.SimpleNamespace(prompt_tokens=prompt_tokens,
                                           completion_tokens=completion_tokens)


class FakeClient:
    def __init__(self, resp=None, raises=None):
        self._resp, self._raises = resp, raises
        outer = self

        class _Completions:
            def create(self, **kw):
                if outer._raises:
                    raise outer._raises
                return outer._resp

        self.chat = types.SimpleNamespace(completions=_Completions())


def use_client(resp=None, raises=None):
    llm_client.get_client = lambda: FakeClient(resp, raises)


def main() -> int:
    print("Token metering & Langfuse cost attribution\n")

    # --- pricing table matches the configured model ---------------------------
    price = llm_client.model_pricing("gpt-4o-mini")
    expect(price == {"input": 0.15, "output": 0.60},
           "gpt-4o-mini pricing is $0.15/$0.60 per 1M", str(price))
    expect(llm_client.model_pricing("some-unpriced-model") is None,
           "unknown model has no pricing rather than a wrong one")

    # --- usage accumulates over calls -----------------------------------------
    llm_client.reset_usage()
    use_client(FakeResp('{"ok": true}', 1000, 100))
    llm_client.chat_json("sys", "user")
    llm_client.chat_json("sys", "user")
    u = llm_client.get_usage()
    expect(u.calls == 2 and u.input_tokens == 2000 and u.output_tokens == 200,
           "usage accumulates across calls", f"{u}")
    # 2000 in @ $0.15/1M + 200 out @ $0.60/1M = 0.0003 + 0.00012
    expect(abs(u.cost_usd("gpt-4o-mini") - 0.00042) < 1e-9,
           "cost is computed from metered tokens", f"{u.cost_usd('gpt-4o-mini')}")

    # --- a billed-but-unparseable response is still counted --------------------
    llm_client.reset_usage()
    use_client(FakeResp("not json at all", 500, 50))
    try:
        llm_client.chat_json("sys", "user")
    except ValueError:
        pass
    u = llm_client.get_usage()
    expect(u.calls == 1 and u.input_tokens == 500,
           "a response that fails to parse is still billed and counted")

    # --- consume-once semantics ------------------------------------------------
    llm_client.reset_usage()
    use_client(FakeResp('{"ok": true}', 800, 80))
    llm_client.chat_json("sys", "user")
    first = llm_client.consume_last_usage()
    second = llm_client.consume_last_usage()
    expect(first is not None and first.input_tokens == 800, "first read returns the call's usage")
    expect(second is None,
           "second read returns None — a follow-up event cannot re-bill the same call")

    # --- a raising call leaves no stale usage behind ---------------------------
    llm_client.reset_usage()
    use_client(FakeResp('{"ok": true}', 900, 90))
    llm_client.chat_json("sys", "user")          # succeeds, sets last usage
    use_client(raises=RuntimeError("gateway down"))
    try:
        llm_client.chat_json("sys", "user")      # raises
    except RuntimeError:
        pass
    expect(llm_client.consume_last_usage() is None,
           "a failed call does not leave the previous call's tokens for an error trace")

    # --- tracing attaches model + usage + cost to a generation ----------------
    captured: list[dict] = []

    class FakeObs:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def update(self, **kw):
            captured.append(kw)

    class FakeLF:
        def start_as_current_observation(self, **kw):
            captured.append({"_start": kw})
            return FakeObs()

        def flush(self):
            pass

    import contextlib

    @contextlib.contextmanager
    def fake_propagate(**kw):
        captured.append({"_trace": kw})
        yield

    tracing._get_langfuse_client.cache_clear()
    tracing._get_langfuse_client = lambda: FakeLF()
    tracing.propagate_attributes = fake_propagate

    llm_client.reset_usage()
    use_client(FakeResp('{"ok": true}', 1200, 300))
    llm_client.chat_json("sys", "user")
    tracing.trace_llm_response("cat.llm.selection", input_data={"a": 1},
                               output_data={"b": 2}, metadata={"phase": "test"})

    upd = next((c for c in captured if "usage_details" in c), None)
    expect(upd is not None, "generation carries usage_details")
    expect(upd and upd.get("usage_details") == {"input": 1200, "output": 300},
           "usage_details matches the metered call", str(upd and upd.get("usage_details")))
    expect(upd and upd.get("model") == llm_client.get_model(),
           "generation carries the model name")
    cost = upd.get("cost_details") if upd else None
    expect(cost is not None and abs(cost["input"] - 1200 * 0.15 / 1e6) < 1e-12
           and abs(cost["output"] - 300 * 0.60 / 1e6) < 1e-12,
           "cost_details is priced per token type", str(cost))

    # --- a follow-up guard event does not re-bill -----------------------------
    captured.clear()
    tracing.trace_llm_response("cat.llm.rephrase.rejected", input_data={}, output_data={})
    upd2 = next((c for c in captured if "usage_details" in c), None)
    expect(upd2 is None,
           "a follow-up event after the same call reports no usage (no double billing)")

    # --- trace tags ------------------------------------------------------------
    tags = tracing.trace_tags()
    expect(tracing.APPROACH_ID in tags, "tags include the approach id")
    expect(any(t.startswith("math:") for t in tags), "tags include math actor")
    expect(any(t.startswith("selection:") for t in tags), "tags include selection actor")
    expect(any(t.startswith("model:") for t in tags), "tags include the model")
    expect(any(t.startswith("bank:") for t in tags), "tags include the bank id")
    captured.clear()
    tracing.trace_event("x", input_data={}, output_data={})
    trace_call = next((c["_trace"] for c in captured if "_trace" in c), None)
    expect(trace_call is not None, "trace-level attributes are propagated")
    expect(trace_call is not None and set(tracing.trace_tags()) <= set(trace_call.get("tags", [])),
           "tags reach the trace", str(trace_call))
    expect(trace_call is not None and trace_call.get("trace_name") == tracing.TRACE_NAME,
           "trace is named TRACE_NAME, not the observation name",
           str(trace_call and trace_call.get("trace_name")))

    # The real SDK must actually expose what we call — this is the bug that hid before:
    # client.update_current_trace() does not exist on langfuse v4 and the AttributeError
    # was swallowed, so tags silently never landed.
    import langfuse as _lf
    expect(hasattr(_lf, "propagate_attributes"),
           "the installed langfuse SDK exposes propagate_attributes")

    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)}): ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
