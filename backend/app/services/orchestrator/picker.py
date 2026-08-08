"""The Picking Agent: choose the next item for ONE variable, across modalities.

Its whole job is "which question best measures this variable right now". It does not decide
WHICH variable to probe — that is the Orchestrator's, and keeping the two apart is what the
Queue between them exists for.

THE CRITERION IS THE MCQ ENGINE'S, UNCHANGED

    KL          first three observations, while the estimate is still vague. Fisher
                information is local: it asks which item is most informative exactly here,
                when "here" is barely known. KL integrates over a neighbourhood and so
                prefers items separating a whole region of ability.
    E[Fisher]   thereafter, averaged over the posterior rather than taken at its mean.

Both come from `services.adaptive.irt` and are applied to every modality identically. That
is the entire payoff of putting all items on one theta scale: an MCQ item and a code
question are ranked by the same number, computed the same way, and neither gets an
advantage from how it happens to be graded.

INFORMATION IS SCALED BY LOADING, and that is deliberate. A code question loading 0.2 on a
variable genuinely tells you less about it than one loading 0.8, so it should usually lose.
`UnifiedBank.information_parity` separates that expected outcome from the unexpected one —
an item losing because its parameters are wrong.

RANKING IS INFORMATION PER MINUTE, WEIGHTED BY THE EVIDENCE IT WILL ACTUALLY CARRY

Two corrections to raw information, and both change which item wins:

    E[w]        Fisher information is computed from the BINARY model, but realised
                information scales with the evidence weight a response actually earns.
                A partially-credited code answer at w = 0.74 delivers about a quarter
                less than the criterion promises. Without this the picker systematically
                over-selects the modalities that deliver least per unit of weight.
    time        A code item costs eight times an MCQ item. Optimising information per
                ITEM inside a session bounded by MINUTES picks the diet that cannot
                finish: eighteen code items is 147 minutes against a 90-minute limit.

`information` still means information — it is reported unchanged, and the SHORTLIST is
ranked on the derived key. Conflating the two would make the audit record say the engine
valued something it did not.

The ranking key has no meaningful absolute scale: `kullback_leibler_information` is an
unnormalised sum over grid points inside a neighbourhood, so it is roughly twenty times
Fisher's magnitude and shrinks across the KL phase purely because the neighbourhood
narrows. It is monotone WITHIN a phase, which is all ranking needs — but no absolute
threshold may ever be placed on it. The 0.75 utility floor is relative, and safe.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np

from app.config.settings import settings
from app.schemas.orchestration import BankItem, QueuedCandidate, VariableState
from app.services import observability
from app.services.adaptive.irt import (
    expected_fisher_information,
    kullback_leibler_information,
)
from app.services.adaptive.llm import LLMUnavailable, chat_json
from app.services.orchestrator import selection_calibration
from app.services.orchestrator.prompts import PICKING_SYSTEM

logger = logging.getLogger(__name__)

# KL while the estimate is vague, expected Fisher once it is worth localising around.
# Three is where the MCQ engine found the standard error worth trusting.
KL_PHASE_OBSERVATIONS = 3

# The smallest multiplier a utility modifier may produce. See `rank`.
GRAPH_UTILITY_FLOOR = 0.01

ALLOWED_REASON_CODES = {
    "MAX_INFORMATION",
    "RESOLVE_UNCERTAINTY",
    "IMPROVE_COVERAGE",
    "BALANCE_DIFFICULTY",
    "BALANCE_MODALITY",
}


def criterion_for(observations: int) -> Literal["KL", "E[Fisher]"]:
    return "KL" if observations < KL_PHASE_OBSERVATIONS else "E[Fisher]"


@dataclass(frozen=True, order=True)
class RankScore:
    """What the shortlist is ordered by, and everything that went into it.

    FIELD ORDER IS LOAD-BEARING. `order=True` compares fields in declaration order, so
    `rank_key` first is what makes `sorted(..., reverse=True)` order by the ranking key.
    Reordering these silently changes which question every candidate is asked next.
    """

    rank_key: float
    information: float
    expected_weight: float
    estimated_seconds: float
    modifier: float

    @property
    def information_per_minute(self) -> float:
        return self.information / max(self.estimated_seconds / 60.0, 1e-9)


def expected_weight_for(modality: str) -> float:
    """How much evidence a response in this modality typically carries.

    Measured from the session corpus where there is one, and from the documented default
    otherwise — `selection_calibration` decides which and logs it. It used to be a literal
    read off the graders' discount rungs, and it decides the modality mix and therefore
    session length, so it is worth measuring rather than asserting.
    """
    return selection_calibration.expected_weight_for(modality)


def information_for(item: BankItem, state: VariableState, variable: str) -> float:
    """The criterion score used to rank this item for this variable, in the current phase."""
    posterior = np.asarray(state.posterior, dtype=float)
    loading = item.loading(variable)
    if criterion_for(state.observations) == "KL":
        # Neighbourhood shrinks as the estimate sharpens: early on ask which item separates
        # a wide region of ability, later a narrow one.
        delta = 3.0 / np.sqrt(state.observations + 1)
        raw = kullback_leibler_information(
            state.theta_hat, item.cat.a, item.cat.b, item.cat.c, delta
        )
    else:
        raw = expected_fisher_information(posterior, item.cat.a, item.cat.b, item.cat.c)
    return float(raw) * loading


def rank(
    items: list[BankItem],
    state: VariableState,
    variable: str,
    *,
    rng: np.random.Generator | None = None,
    top_k: int | None = None,
    utility_modifiers_by_item_id: dict[str, float] | None = None,
) -> tuple[list[tuple[BankItem, RankScore]], Literal["KL", "E[Fisher]"]]:
    """Rank a variable's shortlist best-first, and say which criterion produced it.

    Exposure control is applied to the WINDOW, not to the final pick: the shortlist starts
    at a uniform random rank in [0, k), so whoever chooses its best entry administers
    rank ~ U[0, k). That is randomesque control, and it composes identically whether the
    chooser is code or a model — randomising after the model chooses would discard the
    decision this design exists to record.
    """
    criterion = criterion_for(state.observations)
    if not items:
        return [], criterion

    time_aware = settings.orchestrator_time_aware_selection_enabled

    scored: list[tuple[BankItem, RankScore]] = []
    for item in items:
        info = information_for(item, state, variable)
        modifier = (
            float(utility_modifiers_by_item_id.get(item.item_id, 0.0))
            if utility_modifiers_by_item_id
            else 0.0
        )
        # CLAMPED, and the clamp is load-bearing now that modifiers can be negative. At
        # exactly -1.0 the key is zero for every penalised item and the tie-break below
        # decides selection arbitrarily; past -1.0 the key goes negative and the ranking
        # INVERTS, so the least informative item wins.
        adjustment = max(GRAPH_UTILITY_FLOOR, 1.0 + modifier)

        seconds = item.authored_seconds or selection_calibration.seconds_for(
            item.modality
        )
        weight = expected_weight_for(item.modality)
        if time_aware:
            rank_key = info * weight * adjustment / max(seconds / 60.0, 1e-9)
        else:
            rank_key = info * adjustment

        scored.append(
            (
                item,
                RankScore(
                    rank_key=rank_key,
                    information=info,
                    expected_weight=weight,
                    estimated_seconds=seconds,
                    modifier=modifier,
                ),
            )
        )
    scored.sort(
        key=lambda pair: (pair[1].rank_key, -abs(pair[0].cat.b - state.theta_hat)),
        reverse=True,
    )

    k = settings.cat_exposure_top_k if top_k is None else top_k
    offset = 0
    if k > 1 and len(scored) > 1:
        generator = rng if rng is not None else np.random.default_rng()
        offset = int(generator.integers(min(k, len(scored))))

    return scored[offset : offset + settings.orchestrator_shortlist_size], criterion


def _deterministic(
    shortlist: list[tuple[BankItem, RankScore]],
    variable: str,
    criterion: Literal["KL", "E[Fisher]"],
) -> QueuedCandidate:
    """The engine's own choice, and the yardstick the model's is measured against."""
    item, score = shortlist[0]
    return QueuedCandidate(
        variable=variable,
        item_id=item.item_id,
        modality=item.modality,
        criterion=criterion,
        # `information` keeps meaning information. `utility` is the key the shortlist was
        # ordered by, which is what a regret figure has to be computed against.
        information=round(score.information, 6),
        utility=round(score.rank_key, 6),
        best_information=round(score.information, 6),
        best_utility=round(score.rank_key, 6),
        normalized_regret=0.0,
        expected_weight=round(score.expected_weight, 4),
        estimated_seconds=round(score.estimated_seconds, 1),
        information_per_minute=round(score.information_per_minute, 6),
        engine_top_pick=item.item_id,
        chosen_by_llm=False,
        reason_code="MAX_INFORMATION",
        reason="Best information per minute at the current estimate.",
        shortlist_ids=[i.item_id for i, _ in shortlist],
    )


async def pick(
    items: list[BankItem],
    state: VariableState,
    variable: str,
    *,
    use_llm: bool = True,
    rng: np.random.Generator | None = None,
    top_k: int | None = None,
    utility_modifiers_by_item_id: dict[str, float] | None = None,
) -> QueuedCandidate | None:
    """Choose one candidate for `variable`, or None when its pool is exhausted.

    The model may only return an id from the shortlist the engine ranked, so the worst case
    is bounded by the shortlist rather than by the model's behaviour. Any failure — an
    invented id, a malformed reply, an unreachable gateway — falls back to the engine's own
    choice, because there is always a correct next question and an infrastructure problem
    must not end a candidate's assessment.
    """
    shortlist, criterion = rank(
        items,
        state,
        variable,
        rng=rng,
        top_k=top_k,
        utility_modifiers_by_item_id=utility_modifiers_by_item_id,
    )
    if not shortlist:
        return None

    engine_choice = _deterministic(shortlist, variable, criterion)
    if not use_llm or len(shortlist) == 1:
        return engine_choice

    by_id = {item.item_id: (item, score) for item, score in shortlist}
    payload = {
        "variable_under_test": variable,
        "cat_parameters": {
            "ability_estimate": round(state.theta_hat, 4),
            "standard_error": round(state.standard_error, 4),
            "observations": state.observations,
            "selection_criterion": criterion,
        },
        "allowed_candidates": [
            {
                "item_id": item.item_id,
                "modality": item.modality,
                "rank": index + 1,
                "information": round(score.information, 6),
                "estimated_time_seconds": round(score.estimated_seconds, 1),
                "information_per_minute": round(score.information_per_minute, 6),
                "expected_evidence_weight": round(score.expected_weight, 4),
                "difficulty": round(item.cat.b, 3),
                "discrimination": round(item.cat.a, 3),
                "loading_on_variable": item.loading(variable),
                "sub_competency": item.sub_competency,
            }
            for index, (item, score) in enumerate(shortlist)
        ],
        "constraints": {
            "allowed_item_ids": [item.item_id for item, _ in shortlist],
            "must_not_generate_new_items": True,
        },
    }

    try:
        # json.dumps, not the dict. The SDK forwards a dict straight into the message
        # content, and the proxy answers 400 "'str' object has no attribute 'get'" —
        # which reached production and ended sessions, because a 400 is not
        # LLMUnavailable and so was never caught below. `chat_json` now refuses a
        # non-string payload outright so the same slip cannot recur silently.
        reply = await chat_json(
            PICKING_SYSTEM,
            json.dumps(payload, indent=2),
            require=("selected_item_id", "reason_code"),
            trace=observability.generation(
                "picker",
                variable=variable,
                criterion=criterion,
                shortlist_size=len(shortlist),
                engine_choice=engine_choice.item_id,
                observations=state.observations,
            ),
        )
    except (LLMUnavailable, ValueError, TypeError) as exc:
        logger.warning(
            "picker unavailable for %s (%s) — using the engine's choice", variable, exc
        )
        return engine_choice

    selected_id = str(reply.get("selected_item_id", ""))
    if selected_id not in by_id:
        logger.warning(
            "picker returned %r which is not in the shortlist for %s — using the engine's choice",
            selected_id,
            variable,
        )
        return engine_choice

    reason_code = str(reply.get("reason_code", ""))
    if reason_code not in ALLOWED_REASON_CODES:
        reason_code = "MAX_INFORMATION"

    item, score = by_id[selected_id]
    # The floor compares RANK KEYS, not raw information. On information alone a model
    # could take a code item at 95% of the best information and eight times the time,
    # pass the floor, and reintroduce the budget blowout through the one door the design
    # says must stay shut. This tightens the bound; it never loosens it.
    best = engine_choice.utility
    utility = score.rank_key

    # Below the relative-utility floor the model's pick is overridden. The only thing
    # bounding how bad a constrained choice can be, and it costs nothing when the model
    # behaves.
    if best > 0 and utility / best < settings.orchestrator_minimum_relative_utility:
        logger.info(
            "picker chose %s at %.2f of best information for %s — overriding",
            selected_id,
            utility / best,
            variable,
        )
        return engine_choice

    return QueuedCandidate(
        variable=variable,
        item_id=item.item_id,
        modality=item.modality,
        criterion=criterion,
        information=round(score.information, 6),
        utility=round(utility, 6),
        best_information=round(engine_choice.best_information, 6),
        best_utility=round(best, 6),
        normalized_regret=round(max(0.0, (best - utility) / best), 6)
        if best > 0
        else 0.0,
        expected_weight=round(score.expected_weight, 4),
        estimated_seconds=round(score.estimated_seconds, 1),
        information_per_minute=round(score.information_per_minute, 6),
        engine_top_pick=engine_choice.item_id,
        chosen_by_llm=True,
        reason_code=reason_code,
        reason=str(reply.get("reason", ""))[:300],
        shortlist_ids=[i.item_id for i, _ in shortlist],
    )
