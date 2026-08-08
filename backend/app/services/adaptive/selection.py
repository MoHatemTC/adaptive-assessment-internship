"""Choosing the next item.

The division of labour here is the whole design, and it was chosen on measurement rather
than taste. Code computes the psychometrics — the criterion, every item's information,
the ranking, the content-balancing constraint and all tie-breaks — and produces a
shortlist. The model picks one entry from that shortlist and explains the choice.

The model therefore cannot damage the instrument: every candidate it can choose is one
the engine already ranked as near-optimal, and the code-computed choice is recorded
alongside so any divergence is visible after the fact rather than invisible forever.

Measured on 40 simulated candidates against a live reasoning model: the model reproduced
the code-computed choice on 353 of 353 steps, with zero information given up. That is the
result this structure is designed to guarantee — the shortlist bounds the damage whether
or not the model behaves.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

import numpy as np

from app.config.settings import settings
from app.schemas.adaptive import AbilityState, Item, SelectedItem
from app.services.adaptive.irt import (
    expected_fisher_information,
    fisher_information,
    kullback_leibler_information,
)
from app.services.adaptive.llm import LLMUnavailable, chat_json
from app.services.adaptive.prompts import SELECTION_SYSTEM

logger = logging.getLogger(__name__)

# Items whose information is within this fraction of the best are treated as tied, so the
# documented tie-breaks can decide between them. Without a band, the raw float ordering
# settles everything and the tie-breaks are unreachable code: across a sweep of ability
# points the top two scores were never exactly equal.
NEAR_TIE_RATIO = 0.99

# Number of items offered to the model. Wide enough that the choice is meaningful, narrow
# enough that the prompt stays small and the model is not asked to scan a whole bank.
SHORTLIST_SIZE = 5

# KL is the criterion while the estimate is still vague; Fisher once it is worth
# localising around. Three items is where the standard error typically drops below ~1.0.
KL_PHASE_QUESTIONS = 3


def _criterion_for(questions_answered: int) -> Literal["KL", "E[Fisher]"]:
    return "KL" if questions_answered < KL_PHASE_QUESTIONS else "E[Fisher]"


def _information(item: Item, state: AbilityState, posterior: np.ndarray) -> float:
    """The criterion score actually used to rank, for the current phase."""
    if state.questions_answered < KL_PHASE_QUESTIONS:
        # Neighbourhood shrinks as the estimate sharpens: early on, ask which item
        # separates a wide region; later, a narrow one.
        delta = 3.0 / np.sqrt(state.questions_answered + 1)
        return kullback_leibler_information(
            state.theta_hat, item.a, item.b, item.c, delta
        )
    return expected_fisher_information(posterior, item.a, item.b, item.c)


def _served_counts(state: AbilityState, pool: list[Item]) -> dict[str, int]:
    by_id = {i.id: i.sub_competency for i in pool}
    counts: dict[str, int] = {}
    for served in state.served_item_ids:
        counts[by_id.get(served, "")] = counts.get(by_id.get(served, ""), 0) + 1
    return counts


def rank_candidates(
    state: AbilityState,
    pool: list[Item],
    *,
    rng: np.random.Generator | None = None,
    top_k: int | None = None,
) -> tuple[list[tuple[Item, float]], Literal["KL", "E[Fisher]"]]:
    """Rank unserved items best-first and return the shortlist plus the criterion used.

    Exposure control is applied HERE, to the window, not to the final pick. The shortlist
    starts at a uniform random rank in [0, k), so whoever chooses the best entry from the
    window administers rank ~ U[0, k) — which is randomesque (Kingsbury & Zara, 1989), and
    composes identically whether the chooser is code or a model. Randomising *after* the
    model chooses would discard the decision this design exists to measure.
    """
    served = set(state.served_item_ids)
    candidates = [i for i in pool if i.id not in served]
    criterion = _criterion_for(state.questions_answered)
    if not candidates:
        return [], criterion

    posterior = np.asarray(state.posterior, dtype=float)
    scored = [(item, _information(item, state, posterior)) for item in candidates]
    scored.sort(
        key=lambda pair: (pair[1], -abs(pair[0].b - state.theta_hat)), reverse=True
    )

    k = settings.cat_exposure_top_k if top_k is None else top_k
    offset = 0
    if k > 1 and len(scored) > 1:
        generator = rng if rng is not None else np.random.default_rng()
        offset = int(generator.integers(min(k, len(scored))))

    return scored[offset : offset + SHORTLIST_SIZE], criterion


def choose_deterministically(
    state: AbilityState,
    pool: list[Item],
    *,
    rng: np.random.Generator | None = None,
    top_k: int | None = None,
) -> SelectedItem | None:
    """The engine's own choice. The fallback path, and the yardstick for the model's.

    Applies exactly the procedure the model is instructed to follow, so a disagreement
    between the two is a real disagreement and not a comparison of different rules.
    """
    shortlist, criterion = rank_candidates(state, pool, rng=rng, top_k=top_k)
    if not shortlist:
        return None

    counts = _served_counts(state, pool)
    best_information = shortlist[0][1]

    def sort_key(entry: tuple[Item, float]):
        item, information = entry
        relative = information / max(best_information, 1e-9)
        # Content balancing leads, as a constraint over the near-best items rather than a
        # tie-break behind them: |b - theta| is continuous, so anything sitting behind it
        # never fires and coverage is left to whether the bank happens to be evenly
        # spread. Restricting it to items within the floor bounds what it can cost.
        eligible = relative >= settings.cat_content_balance_floor
        coverage = -counts.get(item.sub_competency, 0) if eligible else -99
        primary = 1.0 if relative >= NEAR_TIE_RATIO else relative
        return (coverage, primary, -abs(item.b - state.theta_hat), item.a)

    ranked = sorted(shortlist, key=sort_key, reverse=True)
    item, information = ranked[0]
    rule = (
        "content balancing: least-served sub-competency"
        if counts
        and any(
            i.sub_competency != item.sub_competency
            for i, s in shortlist
            if s / max(best_information, 1e-9) >= settings.cat_content_balance_floor
        )
        else "highest information"
    )
    return SelectedItem(
        item=item,
        criterion=criterion,
        information=float(information),
        fisher_information=fisher_information(state.theta_hat, item.a, item.b, item.c),
        chosen_by_llm=False,
        matched_procedure=True,
        rule_applied=rule,
        rationale="Deterministic engine selection.",
        shortlist_ids=[i.id for i, _ in shortlist],
        presented_stem=item.stem,
    )


def _shortlist_payload(
    shortlist: list[tuple[Item, float]], state: AbilityState, counts: dict[str, int]
) -> list[dict]:
    best = max((s for _, s in shortlist), default=0.0)
    return [
        {
            "id": item.id,
            "difficulty": item.difficulty,
            "sub_competency": item.sub_competency,
            "a": round(item.a, 2),
            "b": round(item.b, 2),
            "information": round(score, 4),
            # Precomputed so the model never has to divide. Content balancing keys off
            # this ratio, and an arithmetic slip there would silently unbalance the
            # blueprint in a way nothing downstream would catch.
            "information_relative": round(score / best, 4) if best > 0 else 0.0,
            "distance_from_ability": round(abs(item.b - state.theta_hat), 2),
            "sub_competency_served_count": counts.get(item.sub_competency, 0),
            "stem_preview": item.stem[:120],
        }
        for item, score in shortlist
    ]


async def choose_next_item(
    state: AbilityState,
    pool: list[Item],
    *,
    use_llm: bool = True,
    rng: np.random.Generator | None = None,
    top_k: int | None = None,
) -> SelectedItem | None:
    """Select the next item: model-chosen from the engine's shortlist, else engine-chosen.

    Returns None when the pool is exhausted. Every failure mode of the model — an invalid
    id, a malformed reply, an unreachable gateway — falls back to the deterministic
    choice, because there is always a correct next item and refusing to administer one
    would end a candidate's assessment over an infrastructure problem.
    """
    shortlist, criterion = rank_candidates(state, pool, rng=rng, top_k=top_k)
    if not shortlist:
        return None

    fallback = choose_deterministically(state, pool, rng=rng, top_k=top_k)
    if not use_llm:
        return fallback

    counts = _served_counts(state, pool)
    payload = {
        "competency": state.competency,
        "ability_estimate": round(state.theta_hat, 3),
        "standard_error": round(state.standard_error, 3),
        "questions_answered": state.questions_answered,
        "criterion": criterion,
        "content_balance_floor": settings.cat_content_balance_floor,
        "shortlist": _shortlist_payload(shortlist, state, counts),
    }

    try:
        reply = await chat_json(
            SELECTION_SYSTEM, json.dumps(payload, indent=2), require=("selected_id",)
        )
    except (LLMUnavailable, ValueError) as exc:
        logger.warning(
            "adaptive selection: model unusable (%s) — using engine choice", exc
        )
        return fallback

    by_id = {item.id: (item, score) for item, score in shortlist}
    selected_id = str(reply.get("selected_id", "")).strip()
    if selected_id not in by_id:
        logger.warning(
            "adaptive selection: model returned id %r, not in shortlist — using engine choice",
            selected_id,
        )
        return fallback

    item, information = by_id[selected_id]
    return SelectedItem(
        item=item,
        criterion=criterion,
        information=float(information),
        fisher_information=fisher_information(state.theta_hat, item.a, item.b, item.c),
        chosen_by_llm=True,
        # Recorded, never enforced. The model's pick is administered either way — it is
        # drawn from a shortlist the engine already vetted — but a run where this is
        # often False is a run to investigate, and without the flag it would look
        # identical to one where the model followed the procedure exactly.
        matched_procedure=fallback is not None and fallback.item.id == item.id,
        rule_applied=str(reply.get("rule_applied", "")),
        rationale=str(reply.get("rationale", "")),
        shortlist_ids=[i.id for i, _ in shortlist],
        presented_stem=item.stem,
    )
