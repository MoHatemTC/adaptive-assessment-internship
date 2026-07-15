"""Optional Langfuse tracing for CAT approach comparisons.

The app must keep working without Langfuse installed or configured, so every
function in this module is best-effort and never raises into the assessment.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from config_env import load_runtime_config
from engine_log import get_logger

load_runtime_config()

APPROACH_ID = os.getenv("CAT_APPROACH_ID", "approach-2-llm-math-code-pick")
MATH_ACTOR = os.getenv("CAT_MATH_ACTOR", "llm")
SELECTION_ACTOR = os.getenv("CAT_SELECTION_ACTOR", "code")
TRACE_NAME = os.getenv("CAT_TRACE_NAME", f"masaar-cat-{APPROACH_ID}")
BANK_ID = os.getenv("CAT_BANK_ID", "enriched_bank_cat")
# Free-form extras, comma-separated: CAT_TRACE_TAGS="run:pilot-3,cohort:2026-summer"
EXTRA_TAGS = [t.strip() for t in os.getenv("CAT_TRACE_TAGS", "").split(",") if t.strip()]


def trace_tags() -> list[str]:
    """Trace-level tags for filtering and grouping in the Langfuse UI.

    Namespaced `key:value` rather than bare words so the three approaches stay
    comparable: `math:llm` groups approaches 2 and 3 regardless of how they select,
    and `model:...` keeps a pricing change from silently pooling with older runs.
    """
    tags = [
        APPROACH_ID,
        f"math:{MATH_ACTOR}",
        f"selection:{SELECTION_ACTOR}",
        f"bank:{BANK_ID}",
    ]
    try:
        from llm_client import get_model

        tags.append(f"model:{get_model()}")
    except Exception:  # pragma: no cover — tracing must never break the assessment
        pass
    return tags + EXTRA_TAGS


def _jsonable(value: Any) -> Any:
    """Convert numpy/pandas-ish values into JSON-compatible structures."""
    try:
        json.dumps(value)
        return value
    except TypeError:
        pass

    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return str(value)


def _base_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "app": "masaar-mcq-cat-test",
        "approach_id": APPROACH_ID,
        "math_actor": MATH_ACTOR,
        "selection_actor": SELECTION_ACTOR,
        "bank_id": BANK_ID,
    }
    if extra:
        metadata.update(_jsonable(extra))
    return metadata


def langfuse_configured() -> bool:
    return bool(
        os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        and os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        and os.getenv("LANGFUSE_BASE_URL", "").strip()
    )


@contextmanager
def _noop_context():
    yield


def propagate_attributes(**kwargs):
    """Trace-level attributes, via whatever the installed SDK actually supports.

    Resolved at call time rather than import time so a missing/older langfuse degrades
    to a no-op instead of taking the assessment down.
    """
    try:
        from langfuse import propagate_attributes as _propagate

        return _propagate(**kwargs)
    except Exception as exc:  # pragma: no cover
        get_logger().warning("TRACE | propagate_attributes unavailable: %s", exc)
        return _noop_context()


@lru_cache(maxsize=1)
def _get_langfuse_client():
    if not langfuse_configured():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception as exc:  # pragma: no cover - depends on optional package/config
        get_logger().warning("TRACE | langfuse unavailable: %s", exc)
        return None


def flush_traces() -> None:
    client = _get_langfuse_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # pragma: no cover
        get_logger().warning("TRACE | flush failed: %s", exc)


def trace_event(
    name: str,
    *,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    as_type: str = "span",
    model: str | None = None,
    usage_details: dict[str, int] | None = None,
    cost_details: dict[str, float] | None = None,
) -> bool:
    """Record one Langfuse observation if configured.

    model/usage_details/cost_details only apply to generations. Without them Langfuse
    shows a generation with no tokens and no cost, so the dashboard cannot answer "what
    did this approach cost" — which is most of the point of tracing three approaches
    side by side.
    """
    client = _get_langfuse_client()
    if client is None:
        return False

    meta = _base_metadata(metadata)
    try:
        # propagate_attributes is how the v4 SDK sets trace-level name/tags. The previous
        # code called client.update_current_trace(), which does not exist on this client:
        # it raised AttributeError into a bare `except: pass`, so every trace was written
        # with tags=[] and named after its observation instead of TRACE_NAME. The tags
        # were never reaching Langfuse and nothing said so.
        with propagate_attributes(trace_name=TRACE_NAME, tags=trace_tags(), metadata=meta):
            with client.start_as_current_observation(
                name=name,
                as_type=as_type,
                input=_jsonable(input_data or {}),
                metadata=meta,
            ) as observation:
                update: dict[str, Any] = {}
                if output_data is not None:
                    update["output"] = _jsonable(output_data)
                if model:
                    update["model"] = model
                if usage_details:
                    update["usage_details"] = usage_details
                if cost_details:
                    update["cost_details"] = cost_details
                if update:
                    observation.update(**update)
        return True
    except Exception as exc:  # pragma: no cover
        get_logger().warning("TRACE | %s failed: %s", name, exc)
        return False


def trace_session_start(
    competencies: list[str],
    pool_sizes: dict[str, int],
    *,
    llm_enabled: bool,
) -> None:
    trace_event(
        "cat.session.start",
        output_data={
            "competencies": competencies,
            "pool_sizes": pool_sizes,
            "llm_enabled": llm_enabled,
        },
    )


def trace_selection(
    competency: str,
    q_count: int,
    theta_hat: float,
    se: float,
    selection,
) -> None:
    item = selection.item
    trace_event(
        "cat.selection",
        input_data={
            "competency": competency,
            "q_count": q_count,
            "theta_hat": theta_hat,
            "se": se,
            "shortlist_ids": selection.shortlist_ids,
        },
        output_data={
            "selected_id": item["id"],
            "difficulty": item.get("difficulty", "medium"),
            "discrimination": item.get("discrimination", "medium"),
            "criterion": selection.criterion,
            "info_score": selection.info_score,
            "fisher_i": selection.fisher_i,
            "llm_used": selection.llm_used,
            "rule_applied": selection.rule_applied,
            "adaptation_note": selection.adaptation_note,
        },
        metadata={"competency": competency, "phase": "selection"},
    )


def trace_update(
    competency: str,
    q_count: int,
    item: dict,
    *,
    correct: bool,
    theta_hat: float,
    se: float,
    certainty_pct: float,
    converged: bool,
) -> None:
    trace_event(
        "cat.update",
        input_data={
            "competency": competency,
            "q_count": q_count,
            "item_id": item["id"],
            "difficulty": item.get("difficulty", "medium"),
            "discrimination": item.get("discrimination", "medium"),
            "correct": correct,
        },
        output_data={
            "theta_hat": theta_hat,
            "se": se,
            "certainty_pct": certainty_pct,
            "converged": converged,
        },
        metadata={"competency": competency, "phase": "update"},
    )


def trace_final(
    competency: str,
    state: dict,
    *,
    bank_exhausted: bool,
) -> None:
    trace_event(
        "cat.competency.final",
        output_data={
            "competency": competency,
            "theta_hat": state["theta_hat"],
            "se": state["se"],
            "certainty_pct": state.get("certainty_pct"),
            "level": state.get("level"),
            "pct": state.get("pct"),
            "band": state.get("band"),
            "low_confidence": state.get("low_confidence"),
            "q_count": state.get("q_count"),
            "bank_exhausted": bank_exhausted,
        },
        metadata={"competency": competency, "phase": "final"},
    )
    flush_traces()


def trace_llm_response(
    name: str,
    *,
    input_data: dict[str, Any],
    output_data: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record an LLM call as a costed Langfuse generation.

    Model and token usage are pulled from the call that just happened rather than
    passed in by every call site, so a new call site cannot forget them and silently
    contribute a zero-cost generation. Callers that trace a *failed* call get no usage,
    which is correct — there was no billed response.
    """
    model = None
    usage_details = None
    cost_details = None
    try:
        from llm_client import consume_last_usage, get_model, model_pricing

        model = get_model()
        last = consume_last_usage()
        if last is not None and last.calls:
            usage_details = {"input": last.input_tokens, "output": last.output_tokens}
            price = model_pricing(model)
            if price:
                cost_details = {
                    "input": last.input_tokens * price["input"] / 1e6,
                    "output": last.output_tokens * price["output"] / 1e6,
                }
    except Exception:  # pragma: no cover — tracing must never break the assessment
        pass

    trace_event(
        name,
        input_data=input_data,
        output_data=output_data,
        metadata=metadata,
        as_type="generation",
        model=model,
        usage_details=usage_details,
        cost_details=cost_details,
    )
