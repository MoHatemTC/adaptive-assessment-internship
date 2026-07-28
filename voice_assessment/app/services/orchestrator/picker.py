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
"""

from __future__ import annotations

import json
import logging

import numpy as np

from app.config.settings import settings
from app.schemas.orchestration import BankItem, QueuedCandidate, VariableState
from app.services import observability
from app.services.adaptive.irt import (
    THETA_GRID,
    expected_fisher_information,
    kullback_leibler_information,
)
from app.services.adaptive.llm import LLMUnavailable, chat_json
from app.services.orchestrator.prompts import PICKING_SYSTEM

logger = logging.getLogger(__name__)

# KL while the estimate is vague, expected Fisher once it is worth localising around.
# Three is where the MCQ engine found the standard error worth trusting.
KL_PHASE_OBSERVATIONS = 3

ALLOWED_REASON_CODES = {
    "MAX_INFORMATION",
    "RESOLVE_UNCERTAINTY",
    "IMPROVE_COVERAGE",
    "BALANCE_DIFFICULTY",
    "BALANCE_MODALITY",
}


def criterion_for(observations: int) -> str:
    return "KL" if observations < KL_PHASE_OBSERVATIONS else "E[Fisher]"


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
) -> tuple[list[tuple[BankItem, float]], str]:
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

    scored = [(item, information_for(item, state, variable)) for item in items]
    scored.sort(key=lambda pair: (pair[1], -abs(pair[0].cat.b - state.theta_hat)), reverse=True)

    k = settings.cat_exposure_top_k if top_k is None else top_k
    offset = 0
    if k > 1 and len(scored) > 1:
        generator = rng if rng is not None else np.random.default_rng()
        offset = int(generator.integers(min(k, len(scored))))

    return scored[offset : offset + settings.orchestrator_shortlist_size], criterion


def _deterministic(
    shortlist: list[tuple[BankItem, float]], variable: str, criterion: str
) -> QueuedCandidate:
    """The engine's own choice, and the yardstick the model's is measured against."""
    item, information = shortlist[0]
    return QueuedCandidate(
        variable=variable,
        item_id=item.item_id,
        modality=item.modality,
        criterion=criterion,
        information=round(information, 6),
        utility=round(information, 6),
        best_information=round(information, 6),
        normalized_regret=0.0,
        engine_top_pick=item.item_id,
        chosen_by_llm=False,
        reason_code="MAX_INFORMATION",
        reason="Highest information at the current estimate.",
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
) -> QueuedCandidate | None:
    """Choose one candidate for `variable`, or None when its pool is exhausted.

    The model may only return an id from the shortlist the engine ranked, so the worst case
    is bounded by the shortlist rather than by the model's behaviour. Any failure — an
    invented id, a malformed reply, an unreachable gateway — falls back to the engine's own
    choice, because there is always a correct next question and an infrastructure problem
    must not end a candidate's assessment.
    """
    shortlist, criterion = rank(items, state, variable, rng=rng, top_k=top_k)
    if not shortlist:
        return None

    engine_choice = _deterministic(shortlist, variable, criterion)
    if not use_llm or len(shortlist) == 1:
        return engine_choice

    by_id = {item.item_id: (item, information) for item, information in shortlist}
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
                "information": round(information, 6),
                "difficulty": round(item.cat.b, 3),
                "discrimination": round(item.cat.a, 3),
                "loading_on_variable": item.loading(variable),
                "sub_competency": item.sub_competency,
            }
            for index, (item, information) in enumerate(shortlist)
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
        logger.warning("picker unavailable for %s (%s) — using the engine's choice", variable, exc)
        return engine_choice

    selected_id = str(reply.get("selected_item_id", ""))
    if selected_id not in by_id:
        logger.warning(
            "picker returned %r which is not in the shortlist for %s — using the engine's choice",
            selected_id, variable,
        )
        return engine_choice

    reason_code = str(reply.get("reason_code", ""))
    if reason_code not in ALLOWED_REASON_CODES:
        reason_code = "MAX_INFORMATION"

    item, information = by_id[selected_id]
    best = engine_choice.best_information
    utility = information

    # Below the relative-utility floor the model's pick is overridden. The only thing
    # bounding how bad a constrained choice can be, and it costs nothing when the model
    # behaves.
    if best > 0 and utility / best < settings.orchestrator_minimum_relative_utility:
        logger.info(
            "picker chose %s at %.2f of best information for %s — overriding",
            selected_id, utility / best, variable,
        )
        return engine_choice

    return QueuedCandidate(
        variable=variable,
        item_id=item.item_id,
        modality=item.modality,
        criterion=criterion,
        information=round(utility, 6),
        utility=round(utility, 6),
        best_information=round(best, 6),
        normalized_regret=round(max(0.0, (best - utility) / best), 6) if best > 0 else 0.0,
        engine_top_pick=engine_choice.item_id,
        chosen_by_llm=True,
        reason_code=reason_code,
        reason=str(reply.get("reason", ""))[:300],
        shortlist_ids=[i.item_id for i, _ in shortlist],
    )
