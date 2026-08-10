"""Load and project voice rubrics."""

from __future__ import annotations

import json
from functools import lru_cache

from app.config.paths import DATA_DIR

RUBRIC_DIR = DATA_DIR / "voice_rubrics"


@lru_cache(maxsize=256)
def load_rubric(rubric_id: str) -> dict:
    path = RUBRIC_DIR / f"{rubric_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"rubric not found: {rubric_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def measures_from_rubric(rubric: dict) -> list[dict]:
    """Derive BankItem.measures from rubric criteria, max-normalised to 1.0."""
    weights: dict[str, float] = {}
    for crit in rubric.get("criteria", []):
        cid = crit["competency_id"]
        weights[cid] = weights.get(cid, 0.0) + float(crit.get("weight", 0.0))
    if not weights:
        return []
    peak = max(weights.values()) or 1.0
    return [
        {"variable": cid, "weight": round(w / peak, 4)}
        for cid, w in sorted(weights.items())
    ]


def criterion_to_competency_weights(rubric: dict) -> dict[str, dict[str, float]]:
    """criterion_id -> {competency_id: projection weight}, rows sum to 1.0."""
    out: dict[str, dict[str, float]] = {}
    for crit in rubric.get("criteria", []):
        cid = crit["criterion_id"]
        out[cid] = {crit["competency_id"]: 1.0}
    return out


def normalize_criterion_score(raw: float, maximum: float) -> float:
    if maximum <= 0:
        return 0.0
    return min(max(float(raw) / float(maximum), 0.0), 1.0)


def ordinal_level(score: float) -> int:
    """Reporting transform only — not used in the posterior update."""
    s = min(max(float(score), 0.0), 1.0)
    if s < 0.2:
        return 1
    if s < 0.4:
        return 2
    if s < 0.6:
        return 3
    if s < 0.8:
        return 4
    return 5
