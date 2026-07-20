"""Capture engine log records for display in the UI.

The pipeline already logs every decision that matters — a rejected model reply, a
fallback, a flagged conflict, a sandbox failure. Those records go to stderr, where a
Streamlit user never sees them, so a session that quietly ran entirely on fallbacks looks
identical to one that ran on the model.

This buffers them in memory so the app can show what actually happened during the run.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime

# Bounded: a long session with a chatty logger should not grow without limit inside a
# Streamlit process that may live for hours.
MAX_RECORDS = 500

_buffer: deque[dict] = deque(maxlen=MAX_RECORDS)
_installed = False


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append(
                {
                    "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                    "level": record.levelname,
                    "source": record.name.split(".")[-1],
                    "message": record.getMessage(),
                }
            )
        except Exception:  # pragma: no cover — logging must never break the assessment
            pass


def install() -> None:
    """Attach the buffer to this package's loggers. Idempotent.

    Attached to the `cat` and `code_evaluation` loggers rather than the root, so a noisy
    dependency cannot flood the panel and bury the engine's own records.
    """
    global _installed
    if _installed:
        return
    handler = _BufferHandler()
    handler.setLevel(logging.INFO)
    for name in ("cat", "code_evaluation"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
    _installed = True


def records(levels: set[str] | None = None) -> list[dict]:
    """Buffered records, newest last. Optionally filtered by level."""
    if levels is None:
        return list(_buffer)
    return [r for r in _buffer if r["level"] in levels]


def clear() -> None:
    _buffer.clear()


def counts() -> dict[str, int]:
    tally: dict[str, int] = {}
    for record in _buffer:
        tally[record["level"]] = tally.get(record["level"], 0) + 1
    return tally
