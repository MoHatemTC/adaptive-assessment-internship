"""Projecting a rubric onto competencies.

WHERE A RUBRIC COMES FROM

From the item. Every bank carries `rubric_criteria` inside the payload of each open or voice
item, so a rubric travels with the question it grades and a bank is one self-contained file.

There used to be a second route: an item could name a `rubric_id`, and this module loaded
`data/voice_rubrics/<id>.json`. That route is gone. No registered bank ever used it — 107
rubric files sat there keyed `open_cN_MMM` against items keyed `voice_cN_MMM`, orphaned from
a bank that was replaced — and an UPLOADED bank could never have used it either, because the
write path stores a bank file and has nowhere to put a side-car rubric. A second way to
answer "what grades this item" that one of the two could never populate is worse than one.

WHAT IS LEFT IS THE PROJECTION

A rubric scores CRITERIA. A posterior is over COMPETENCIES. These two functions are the map
between them, and they are the reason a spoken answer can move the same estimate an MCQ
moves.
"""

from __future__ import annotations


def criterion_to_competency_weights(rubric: dict) -> dict[str, dict[str, float]]:
    """criterion_id -> {competency_id: projection weight}, rows sum to 1.0."""
    out: dict[str, dict[str, float]] = {}
    for crit in rubric.get("criteria", []):
        cid = crit["criterion_id"]
        out[cid] = {crit["competency_id"]: 1.0}
    return out


def normalize_criterion_score(raw: float, maximum: float) -> float:
    """A criterion's raw score onto [0, 1].

    Clamped rather than trusted: a model asked for a score out of 8 will occasionally return
    9, and an out-of-range score would enter the likelihood as evidence stronger than any
    answer can be.
    """
    if maximum <= 0:
        return 0.0
    return min(max(float(raw) / float(maximum), 0.0), 1.0)
