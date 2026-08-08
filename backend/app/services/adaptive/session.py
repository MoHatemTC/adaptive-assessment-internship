"""The public entry point: run one competency, question by question.

`AdaptiveSession` is stateless between calls — it takes an `AbilityState`, returns a new
one, and never holds a session in memory. That is what lets an assessment span HTTP
requests, be persisted between them, and resume on a different worker.

Typical use:

    session = AdaptiveSession(JsonItemRepository())
    state = await session.begin("Python & Software Engineering", self_rating=3)

    while True:
        selected = await session.next_item(state)
        if selected is None:
            break
        state, stop = await session.record_answer(state, selected.item, chosen_index)
        if stop.should_stop:
            break

    result = session.summarise(state, stop)
"""

from __future__ import annotations

import logging

import numpy as np

from app.config.settings import settings
from app.schemas.adaptive import AbilityState, CompetencyResult, Item, SelectedItem
from app.services.adaptive import convergence
from app.services.adaptive.bank import ItemRepository
from app.services.adaptive.irt import (
    ability_band,
    ability_percentile,
    band_probabilities,
    posterior_update,
    prior_from_self_rating,
    uniform_prior,
)
from app.services.adaptive.llm import LLMUnavailable, chat_json
from app.services.adaptive.prompts import REPHRASE_SYSTEM
from app.services.adaptive.rephrase import check_rephrase
from app.services.adaptive.selection import choose_next_item

logger = logging.getLogger(__name__)

# Prior width by how much the self-rating is worth believing. A rating given with
# confidence is narrower, but never so narrow that a handful of responses cannot override
# it — the rating sets where the search starts, not where it ends.
PRIOR_SD_CONFIDENT = 1.1
PRIOR_SD_TENTATIVE = 1.7
PRIOR_SD_NO_RATING = 2.0


class AdaptiveSession:
    """Drives one competency of an adaptive assessment."""

    def __init__(self, repository: ItemRepository) -> None:
        self._repository = repository

    async def begin(
        self,
        competency: str,
        *,
        self_rating: int | None = None,
        rating_confident: bool = False,
    ) -> AbilityState:
        """Open a competency, seeding the prior from the candidate's self-rating."""
        if self_rating is None:
            posterior = uniform_prior()
            standard_error = PRIOR_SD_NO_RATING
        else:
            sd = PRIOR_SD_CONFIDENT if rating_confident else PRIOR_SD_TENTATIVE
            posterior = prior_from_self_rating(self_rating, sd)
            standard_error = sd

        grid_mean = float(np.sum(np.asarray(posterior) * _grid()))
        return AbilityState(
            competency=competency,
            posterior=list(map(float, posterior)),
            theta_hat=grid_mean,
            standard_error=standard_error,
        )

    async def next_item(
        self,
        state: AbilityState,
        *,
        use_llm: bool = True,
        rng: np.random.Generator | None = None,
        top_k: int | None = None,
    ) -> SelectedItem | None:
        """Choose and prepare the next item, or None when the pool is exhausted."""
        pool = await self._repository.items_for_competency(state.competency)
        selected = await choose_next_item(
            state, pool, use_llm=use_llm, rng=rng, top_k=top_k
        )
        if selected is None:
            return None
        if use_llm and settings.cat_rephrasing_enabled:
            selected = await self._apply_rephrase(selected)
        return selected

    async def _apply_rephrase(self, selected: SelectedItem) -> SelectedItem:
        """Ask for a clearer stem; administer the original unless the rewrite is clean."""
        item = selected.item
        try:
            reply = await chat_json(
                REPHRASE_SYSTEM,
                f"Stem:\n{item.stem}\n\nOptions:\n"
                + "\n".join(f"- {o}" for o in item.options),
                require=("rephrased_stem",),
            )
        except (LLMUnavailable, ValueError) as exc:
            logger.warning(
                "rephrase unavailable (%s) — administering calibrated stem", exc
            )
            return selected

        checked = check_rephrase(
            item.stem,
            str(reply.get("rephrased_stem", "")),
            item.options,
            item.answer_index,
        )
        if not checked.ok:
            logger.warning("rephrase rejected for %s: %s", item.id, checked.reason)
        return selected.model_copy(
            update={
                "presented_stem": checked.stem,
                "rephrase_rejected_reason": "" if checked.ok else checked.reason,
            }
        )

    async def record_answer(
        self, state: AbilityState, item: Item, chosen_index: int
    ) -> tuple[AbilityState, convergence.StopDecision]:
        """Grade one response, update the estimate, and decide whether to continue.

        Grading is an exact index comparison and is never delegated: it is the one step
        with a definitively correct answer, and nothing is gained by asking a model to
        perform it.
        """
        is_correct = chosen_index == item.answer_index
        posterior, theta_hat, standard_error = posterior_update(
            np.asarray(state.posterior, dtype=float), item.a, item.b, item.c, is_correct
        )

        level, _ = ability_band(theta_hat)
        updated = state.model_copy(
            update={
                "posterior": list(map(float, posterior)),
                "theta_hat": theta_hat,
                "standard_error": standard_error,
                "questions_answered": state.questions_answered + 1,
                "served_item_ids": [*state.served_item_ids, item.id],
                "band_history": [*state.band_history, level],
            }
        )

        pool = await self._repository.items_for_competency(state.competency)
        remaining = sum(1 for i in pool if i.id not in set(updated.served_item_ids))
        # P(the reported level is the true level). Omitting it left
        # `cat_band_probability_stop_enabled` unreachable from this engine too.
        bands = band_probabilities(np.asarray(updated.posterior, dtype=float))
        stop = convergence.evaluate(
            updated.standard_error,
            updated.band_history,
            updated.questions_answered,
            remaining,
            band_probability=bands.get(ability_band(updated.theta_hat)[0], 0.0),
        )
        return updated, stop

    async def summarise(
        self, state: AbilityState, stop: convergence.StopDecision
    ) -> CompetencyResult:
        """Final report for one competency."""
        level, band = ability_band(state.theta_hat)
        pool = await self._repository.items_for_competency(state.competency)
        by_id = {i.id: i.sub_competency for i in pool}
        covered = {by_id.get(i, "") for i in state.served_item_ids} - {""}

        return CompetencyResult(
            competency=state.competency,
            theta_hat=state.theta_hat,
            standard_error=state.standard_error,
            percentile=ability_percentile(state.theta_hat),
            level=level,
            band=band,
            certainty_pct=convergence.certainty_pct(
                state.standard_error, observations=state.questions_answered
            ),
            questions_answered=state.questions_answered,
            stop_reason=stop.reason,
            converged=stop.converged,
            # Distinct from `converged`, and the difference matters in a report: a test
            # can end on a settled band without ever reaching the precision target, and
            # presenting that as a confident result overstates what was measured.
            precision_target_met=state.standard_error <= settings.cat_se_target,
            sub_competencies_covered=len(covered),
            audit={
                "se_target": settings.cat_se_target,
                "max_questions": settings.cat_max_questions,
                "exposure_top_k": settings.cat_exposure_top_k,
            },
        )


def _grid():
    from app.services.adaptive.irt import THETA_GRID

    return THETA_GRID
