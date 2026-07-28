"""Process-wide debug ring for the realtime Live server.

Streamlit cannot see FastAPI exceptions unless we expose them here.
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.Lock()
_LOGS: deque[dict[str, Any]] = deque(maxlen=200)


def live_debug(event: str, **fields: Any) -> None:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "live_server",
        "event": event,
        **fields,
    }
    with _LOCK:
        _LOGS.append(line)


def live_debug_exc(event: str, exc: BaseException, **fields: Any) -> None:
    live_debug(
        event,
        error_type=type(exc).__name__,
        error=str(exc),
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2500:],
        **fields,
    )


def snapshot(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        items = list(_LOGS)
    return items[-limit:]


def clear() -> None:
    with _LOCK:
        _LOGS.clear()
