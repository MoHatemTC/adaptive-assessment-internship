"""Optional Langfuse tracing for CAT approach comparisons.

The app must keep working without Langfuse installed or configured, so every
function in this module is best-effort and never raises into the assessment.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from config_env import load_runtime_config
from engine_log import get_logger

load_runtime_config()

APPROACH_ID = os.getenv("CAT_APPROACH_ID", "approach-1-code-math-llm-pick")
MATH_ACTOR = os.getenv("CAT_MATH_ACTOR", "code")
SELECTION_ACTOR = os.getenv("CAT_SELECTION_ACTOR", "llm")
TRACE_NAME = os.getenv("CAT_TRACE_NAME", f"masaar-cat-{APPROACH_ID}")


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
) -> bool:
    """Record one Langfuse observation if configured."""
    client = _get_langfuse_client()
    if client is None:
        return False

    meta = _base_metadata(metadata)
    try:
        with client.start_as_current_observation(
            name=name,
            as_type=as_type,
            input=_jsonable(input_data or {}),
            metadata=meta,
        ) as observation:
            try:
                client.update_current_trace(
                    name=TRACE_NAME,
                    tags=[APPROACH_ID, f"math:{MATH_ACTOR}", f"selection:{SELECTION_ACTOR}"],
                    metadata=_base_metadata(),
                )
            except Exception:
                pass
            if output_data is not None:
                observation.update(output=_jsonable(output_data))
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
            "rephrased_stem": getattr(selection, "rephrased_stem", ""),
            "original_stem": item.get("stem", ""),
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
    trace_event(
        name,
        input_data=input_data,
        output_data=output_data,
        metadata=metadata,
        as_type="generation",
    )
