"""Append a finished assessment state to a JSONL corpus.

The only durable record of a session this system keeps. `AssessmentState` otherwise lives
in a process dict (`app.main`) or in `st.session_state`, so every completed assessment is
lost at restart — which means the graph's central claim, that a prerequisite edge predicts
anything, has never been checkable against real candidates.

Deliberately narrow: append-only, one line per session, keyed by bank id, and it never
raises into the caller. A telemetry sink that can fail a candidate's assessment is worse
than no telemetry sink.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config.settings import settings
from app.schemas.orchestration import AssessmentState

logger = logging.getLogger(__name__)

# Distinguishes real candidates from simulated ones. `validate_prerequisite_edges.py`
# refuses to enable an edge on simulated data: simulated candidates have no dependency
# structure, so an edge validated against them would be validated against an assumption.
SOURCE_LIVE = "live"
SOURCE_SIMULATED = "simulated"


def dump_path(bank_id: str) -> Path | None:
    directory = (settings.cat_session_dump_dir or "").strip()
    if not directory:
        return None
    return Path(directory) / f"{bank_id}.jsonl"


def record_session(
    state: AssessmentState,
    bank_id: str,
    *,
    stop_reason: str = "",
    source: str = SOURCE_LIVE,
) -> Path | None:
    """Append one finished session. Returns the file written, or None when disabled."""
    path = dump_path(bank_id)
    if path is None:
        return None

    record = {
        "source": source,
        "bank_id": bank_id,
        "stop_reason": stop_reason,
        "state": state.model_dump(mode="json"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.error("could not append session %s to %s", state.session_id, path, exc_info=True)
        return None
    return path


def read_sessions(path: Path) -> list[dict]:
    """Every record in one dump file or directory of them. Bad lines are skipped, loudly."""
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    records: list[dict] = []
    for file in files:
        for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.error("%s:%d is not valid JSON — skipped", file, number)
    return records
