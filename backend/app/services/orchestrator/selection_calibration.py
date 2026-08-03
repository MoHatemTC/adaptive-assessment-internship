"""Measured replacements for the two constants that decide the modality mix.

Selection ranks on information per minute, weighted by the evidence a response is expected
to carry. Both terms shipped as opinions:

    seconds   `mcq: 75` had no measurement behind it anywhere — no bank carries a time for
              a multiple-choice item — and it multiplies most of the items in a session.
    E[w]      `code: 0.85`, `voice: 0.65` were read off the graders' discount rungs, not
              off data.

Between them they set which modality wins a ranking, and therefore how long a session runs
and what it is made of. That is too much to leave as a guess once the sessions exist to
answer it.

`scripts/calibrate_selection_constants.py` writes `selection_calibration.json` from a real
session corpus; this module loads it. A measured value is used only when the corpus has
enough observations of that modality to be worth trusting — below the floor the documented
default stands, and which one is in force is logged rather than left to be inferred.

Order of precedence, most specific first:

    1. the environment override (an operator deliberately pinning a value)
    2. the measured calibration file, per modality, above the observation floor
    3. the documented default
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config.settings import settings
from app.schemas.orchestration import DEFAULT_SECONDS_BY_MODALITY

logger = logging.getLogger(__name__)

CALIBRATION_PATH = Path(__file__).resolve().parents[2] / "data" / "selection_calibration.json"

# Below this many observations of a modality, a measured mean is noise wearing a decimal
# point. Sessions accumulate slowly, so this deliberately errs toward the documented
# default rather than toward an early number that happens to exist.
MINIMUM_OBSERVATIONS = 30

DEFAULT_EXPECTED_WEIGHT: dict[str, float] = {
    "mcq": 1.00,
    "code": 0.85,
    "open": 0.65,
    "voice": 0.65,
}


@dataclass(frozen=True)
class Calibration:
    """What was measured, and how much of it there was."""

    seconds_by_modality: dict[str, float] = field(default_factory=dict)
    weight_by_modality: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    sessions: int = 0
    generated: str = ""

    def measured_seconds(self, modality: str) -> float | None:
        if self.counts.get(modality, 0) < MINIMUM_OBSERVATIONS:
            return None
        return self.seconds_by_modality.get(modality)

    def measured_weight(self, modality: str) -> float | None:
        if self.counts.get(modality, 0) < MINIMUM_OBSERVATIONS:
            return None
        return self.weight_by_modality.get(modality)


@lru_cache(maxsize=1)
def load(path: Path | None = None) -> Calibration:
    """The measured constants, or an empty calibration when none has been produced."""
    source = Path(path) if path is not None else CALIBRATION_PATH
    if not source.exists():
        return Calibration()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.error("selection calibration at %s is unreadable — using defaults", source)
        return Calibration()

    calibration = Calibration(
        seconds_by_modality={
            str(k): float(v) for k, v in (raw.get("seconds_by_modality") or {}).items()
        },
        weight_by_modality={
            str(k): float(v) for k, v in (raw.get("expected_weight_by_modality") or {}).items()
        },
        counts={str(k): int(v) for k, v in (raw.get("observations") or {}).items()},
        sessions=int(raw.get("sessions", 0)),
        generated=str(raw.get("generated", "")),
    )
    logger.info(
        "selection calibration loaded from %s sessions (%s); modalities above the "
        "%d-observation floor: %s",
        calibration.sessions,
        calibration.generated or "undated",
        MINIMUM_OBSERVATIONS,
        sorted(m for m, n in calibration.counts.items() if n >= MINIMUM_OBSERVATIONS),
    )
    return calibration


def reset_cache() -> None:
    load.cache_clear()


def seconds_for(modality: str) -> float:
    """Expected wall clock for a modality, measured where the data allows."""
    key = (modality or "").strip().lower()
    override = settings.item_seconds_by_modality().get(key)
    if override is not None:
        return override
    measured = load().measured_seconds(key)
    if measured is not None:
        return measured
    return DEFAULT_SECONDS_BY_MODALITY.get(key, 120.0)


def expected_weight_for(modality: str) -> float:
    """Expected evidence weight for a modality, measured where the data allows.

    Multiple choice stays exact rather than measured: the grader sets its weight from the
    item's own loading at confidence 1.0 and has no degradation path, so a mean over
    responses can only reproduce the loading distribution of whatever items happened to
    be administered. There is nothing to learn.
    """
    key = (modality or "").strip().lower()
    override = settings.expected_weight_override().get(key)
    if override is not None:
        return override
    if key == "mcq":
        return DEFAULT_EXPECTED_WEIGHT["mcq"]
    measured = load().measured_weight(key)
    if measured is not None:
        return measured
    return DEFAULT_EXPECTED_WEIGHT.get(key, 1.0)


def provenance() -> dict[str, str]:
    """Where each constant currently comes from. For the diagnostics panel."""
    calibration = load()
    report: dict[str, str] = {}
    for modality in sorted(set(DEFAULT_SECONDS_BY_MODALITY) | set(DEFAULT_EXPECTED_WEIGHT)):
        if settings.item_seconds_by_modality().get(modality) is not None:
            seconds = "override"
        elif calibration.measured_seconds(modality) is not None:
            seconds = f"measured (n={calibration.counts.get(modality, 0)})"
        else:
            seconds = "default"
        if settings.expected_weight_override().get(modality) is not None:
            weight = "override"
        elif modality == "mcq":
            weight = "exact"
        elif calibration.measured_weight(modality) is not None:
            weight = f"measured (n={calibration.counts.get(modality, 0)})"
        else:
            weight = "default"
        report[modality] = f"seconds={seconds}, weight={weight}"
    return report
