"""Langfuse tracing, as an option that can never take the engine down.

WHY THIS IS A SEAM AND NOT A DEPENDENCY

Every rule in this codebase about the model applies to monitoring it: the engine's
correctness must not depend on it. A missing package, an unset key, an unreachable
collector, a version that moved an API — none of those may change a single score or end a
candidate's session. So every entry point here is a no-op when tracing is off, catches its
own exceptions, and returns something the caller can use unconditionally.

That is not defensive habit. Observability is the layer most likely to be misconfigured in
a deployment, because it is the one nobody notices working, and it wraps the exact call
path a candidate is waiting on.

WHAT GETS TRACED

    generations   every LiteLLM call, via the `langfuse.openai` drop-in. Importing that
                  module patches the OpenAI resource classes in place, so clients built
                  before configure() ran are instrumented too — which matters, because the
                  engine caches its client.
    gemini-live   Live interviews over LiteLLM `/v1/realtime` (raw WebSocket). The OpenAI
                  drop-in cannot see those frames, so `start_live` / `end_live` open and
                  close a manual generation with duration, turn counts and outcome — never
                  audio bytes or full transcripts.
    traces        one per orchestrated operation, carrying the assessment's session id, so
                  a session's picks and gradings group together rather than arriving as
                  loose calls nobody can attribute.

WHAT IS DELIBERATELY NOT SENT

Candidate source code. A submission is a person's work, it goes to an external service
under this configuration, and nothing in tracing needs it — the score, the test counts and
the item id answer every question monitoring exists to answer. `redact_code` is applied at
the one call site that has any. Live speech is the same class of data: only counts and
status, via `redact_speech`.

VERSION TOLERANCE

Only two surfaces are used, both stable across Langfuse generations: the `langfuse.openai`
drop-in, and `name` / `metadata` keyword arguments on a completion call (the arg extractor
has stripped those before forwarding to OpenAI since v2). Session grouping uses
`propagate_attributes`, which is v3+; where it is absent, tracing still works and only the
session grouping is lost. A monitoring feature must degrade one feature at a time, not all
at once.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Resolved once. None means "configure() has not run"; False means it ran and tracing is
# off, which is a normal outcome and must not be retried on every call.
_state: bool | None = None
_propagate: Any = None


@dataclass
class LiveHandle:
    """Opaque handle for a Live WebSocket generation. May wrap nothing when tracing is off."""

    observation: Any = None
    started_at: float = 0.0
    ended: bool = False


def configure() -> bool:
    """Turn tracing on if it is both wanted and possible. Idempotent, never raises."""
    global _state, _propagate
    if _state is not None:
        return _state

    _state = False
    if not settings.langfuse_enabled:
        return _state

    try:
        from langfuse import Langfuse

        Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            environment=settings.langfuse_environment,
        )
        # The import IS the instrumentation: it patches the OpenAI resource classes, so
        # this must happen even though the symbol it exports is never referenced.
        import langfuse.openai  # noqa: F401

        try:
            from langfuse import propagate_attributes

            _propagate = propagate_attributes
        except ImportError:  # pragma: no cover — older Langfuse
            logger.info(
                "langfuse has no propagate_attributes — generations will be traced, "
                "but not grouped into sessions"
            )
    except Exception as exc:  # noqa: BLE001 — any failure here means "no tracing", not "no engine"
        logger.warning("langfuse tracing unavailable (%s: %s)", type(exc).__name__, exc)
        return _state

    _state = True
    logger.info("langfuse tracing enabled -> %s", settings.langfuse_host)
    return _state


def enabled() -> bool:
    return configure()


def reset() -> None:
    """Forget the resolved state. For tests, and after a settings change."""
    global _state, _propagate
    _state, _propagate = None, None


@contextmanager
def session(session_id: str, **attributes: Any) -> Iterator[None]:
    """Group everything traced inside this block under one assessment session.

    Yields unconditionally: a collector problem must not skip the work the block does.

    IT ALSO MUST NOT SWALLOW OR DISGUISE THE BLOCK'S OWN FAILURE. This used to wrap the
    `yield` in `try/except Exception` and then yield a second time — so any exception from
    the body was thrown into the generator, caught here, and answered with another yield,
    which `contextlib` reports as `RuntimeError: generator didn't stop after throw()`.
    The real error was replaced by a message about generators, and a NameError on the
    grading path read as a bug in the tracer.

    The context is therefore entered and exited by hand: a failure to START or FINISH
    tracing is swallowed and logged, and anything the body raises passes straight
    through untouched.
    """
    if not configure() or _propagate is None:
        yield
        return

    try:
        context = _propagate(session_id=session_id, metadata=_clean(attributes))
        context.__enter__()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "langfuse session could not start (%s) — continuing untraced", exc
        )
        yield
        return

    try:
        yield
    finally:
        try:
            # The in-flight exception, so the collector records that the block failed —
            # and the return value is ignored, because a tracer may not suppress it.
            context.__exit__(*sys.exc_info())
        except Exception as exc:  # noqa: BLE001
            logger.warning("langfuse session could not close (%s)", exc)


def generation(name: str, **metadata: Any) -> dict[str, Any]:
    """Keyword arguments to splat into a completion call, or `{}` when tracing is off.

    Returning a dict rather than wrapping the call keeps the call site honest: it reads as
    one request with some labels, which is what it is, and there is no second code path
    through which a traced call could behave differently from an untraced one.
    """
    if not configure():
        return {}
    return {"name": name, "metadata": _clean(metadata)}


def redact_code(source: str) -> str:
    """What may be said about a submission without sending it anywhere."""
    lines = source.splitlines()
    return f"<{len(lines)} lines, {len(source)} characters — source not sent>"


def redact_speech(text: str) -> dict[str, int]:
    """Counts only — Live transcripts are spoken answers, not monitoring payload."""
    cleaned = (text or "").strip()
    return {
        "chars": len(cleaned),
        "words": len(cleaned.split()) if cleaned else 0,
    }


# Paid Gemini 3.1 Flash Live audio rates (USD / minute). Used when the realtime WS
# path has no token usage to report — Langfuse still gets a cost estimate.
_LIVE_AUDIO_INPUT_USD_PER_MIN = 0.005
_LIVE_AUDIO_OUTPUT_USD_PER_MIN = 0.018
# Rough audio-token density so the registered TOKENS model prices can also apply.
_LIVE_AUDIO_TOKENS_PER_SEC = 25.0


def _live_usage_and_cost(
    *, speech_seconds: float, duration_ms: int
) -> tuple[dict[str, int], dict[str, float]]:
    duration_s = max(duration_ms / 1000.0, 0.0)
    input_s = max(float(speech_seconds), 0.0)
    output_s = max(duration_s - input_s, 0.0)
    usage = {
        "input": round(input_s * _LIVE_AUDIO_TOKENS_PER_SEC),
        "output": round(output_s * _LIVE_AUDIO_TOKENS_PER_SEC),
    }
    cost = {
        "input": round((input_s / 60.0) * _LIVE_AUDIO_INPUT_USD_PER_MIN, 6),
        "output": round((output_s / 60.0) * _LIVE_AUDIO_OUTPUT_USD_PER_MIN, 6),
    }
    cost["total"] = round(cost["input"] + cost["output"], 6)
    return usage, cost


def start_live(
    *,
    name: str = "gemini-live",
    model: str,
    room_id: str,
    item_id: str,
    assessment_session_id: str | None = None,
    mode: str = "interview",
    question: str = "",
    **metadata: Any,
) -> LiveHandle:
    """Open a manual generation for a LiteLLM realtime Live session. Never raises."""
    handle = LiveHandle(started_at=time.time())
    if not configure():
        return handle
    try:
        from langfuse import get_client

        meta = _clean(
            {
                "room_id": room_id,
                "item_id": item_id,
                "mode": mode,
                "via": "litellm_realtime",
                "modality": "open",
                "question_chars": len(question or ""),
                **metadata,
            }
        )
        session_ctx = (
            _propagate(session_id=assessment_session_id)
            if assessment_session_id and _propagate is not None
            else nullcontext()
        )
        with session_ctx:
            handle.observation = get_client().start_observation(
                name=name,
                as_type="generation",
                model=model,
                input={
                    "item_id": item_id,
                    "mode": mode,
                    "question_chars": len(question or ""),
                },
                metadata=meta,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse live start failed (%s) — continuing untraced", exc)
        handle.observation = None
    return handle


def end_live(
    handle: LiveHandle | None,
    *,
    outcome_status: str = "",
    reason_code: str = "",
    error: str = "",
    candidate_turns: int = 0,
    interviewer_turns: int = 0,
    speech_seconds: float = 0.0,
    transcript: str = "",
    level: str | None = None,
) -> None:
    """Close a Live generation. Safe on None / already-ended / tracing-off handles."""
    if handle is None or handle.ended:
        return
    handle.ended = True
    observation = handle.observation
    if observation is None:
        return
    try:
        duration_ms = int(max(time.time() - handle.started_at, 0.0) * 1000)
        status = (error and "ERROR") or level or "DEFAULT"
        usage, cost = _live_usage_and_cost(
            speech_seconds=speech_seconds, duration_ms=duration_ms
        )
        observation.update(
            output={
                "outcome_status": outcome_status or ("error" if error else "unknown"),
                "reason_code": reason_code or None,
                "candidate_turns": candidate_turns,
                "interviewer_turns": interviewer_turns,
                "speech_seconds": round(float(speech_seconds), 2),
                "duration_ms": duration_ms,
                "transcript": redact_speech(transcript),
                "error": (error[:300] if error else None),
                "pricing": {
                    "basis": "gemini-3.1-flash-live audio USD/min",
                    "input_usd_per_min": _LIVE_AUDIO_INPUT_USD_PER_MIN,
                    "output_usd_per_min": _LIVE_AUDIO_OUTPUT_USD_PER_MIN,
                },
            },
            metadata=_clean(
                {
                    "duration_ms": duration_ms,
                    "outcome_status": outcome_status or None,
                    "reason_code": reason_code or None,
                }
            ),
            usage_details=usage,
            cost_details=cost,
            level=status
            if status in {"DEBUG", "DEFAULT", "WARNING", "ERROR"}
            else "DEFAULT",
            status_message=(error[:300] if error else None),
        )
        observation.end()
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse live end failed (%s)", exc)


def flush() -> None:
    """Push buffered spans. Worth calling when a session ends; never required."""
    if not configure():
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse flush failed (%s)", exc)


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    """Drop unset values, so a trace shows what was known rather than a wall of nulls."""
    return {k: v for k, v in attributes.items() if v is not None}
