"""Capture the engine's own logging so a tester can see it.

The engine logs the decisions that matter — a picker falling back because the model was
unavailable, a variable finalising, an item vanishing from the bank — and on a server those
go to stderr where nobody watching a session will ever read them.

A session that quietly ran entirely on deterministic fallbacks looks IDENTICAL to one that
ran on the model unless those records are visible, and for a tester that difference is
usually the whole question. So the handler is installed on the packages the engine actually
logs from, and nothing in the engine changes to accommodate it.
"""

from __future__ import annotations

import logging
import traceback
from collections import deque
from datetime import datetime

# Bounded: a long session with a chatty model would otherwise grow without limit inside a
# Streamlit session that is never garbage collected between reruns.
MAX_RECORDS = 400

WATCHED_LOGGERS = (
    "app.services.orchestrator",
    "app.services.adaptive",
    "app.services.code_adaptive",
    "app.services.competency_graph",
    "app.services.voice",
    "app.services.voice_live",
)

ERROR_LEVELS = frozenset({"WARNING", "ERROR", "CRITICAL"})


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: deque[dict] = deque(maxlen=MAX_RECORDS)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover — a broken format string must not kill the UI
            message = repr(record.msg)
        entry = {
            "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "source": record.name,
            "message": message,
            "exc_text": None,
        }
        if record.exc_info:
            entry["exc_text"] = "".join(traceback.format_exception(*record.exc_info))
        self.records.append(entry)


_COLLECTOR = _Collector()
_INSTALLED = False


def install() -> None:
    """Attach the collector once per process. Safe to call on every Streamlit rerun."""
    global _INSTALLED
    if _INSTALLED:
        return
    for name in WATCHED_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(_COLLECTOR)
        # Still propagate: a deployment's own logging should keep receiving these.
    _INSTALLED = True


def records() -> list[dict]:
    return list(_COLLECTOR.records)


def errors() -> list[dict]:
    """WARNING / ERROR / CRITICAL only — the Tracebook feed."""
    return [r for r in _COLLECTOR.records if r["level"] in ERROR_LEVELS]


def counts() -> dict[str, int]:
    tally: dict[str, int] = {}
    for record in _COLLECTOR.records:
        tally[record["level"]] = tally.get(record["level"], 0) + 1
    return tally


def clear() -> None:
    _COLLECTOR.records.clear()


def note_ui_error(source: str, message: str, *, exc: BaseException | None = None) -> None:
    """Append a synthetic Tracebook entry from Streamlit UI catch blocks."""
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": "ERROR",
        "source": source,
        "message": message,
        "exc_text": None,
    }
    if exc is not None:
        entry["exc_text"] = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
    _COLLECTOR.records.append(entry)
