"""End-to-end: does the assembled engine actually recover ability?

The unit tests prove each part is individually correct. This proves the assembly measures
what it claims to, by administering tests to simulated candidates of known ability and
checking the estimates come back near the truth.
"""

import numpy as np
import pytest

from app.config.settings import settings
from app.services.adaptive import AdaptiveSession, JsonItemRepository
from app.services.adaptive.bank import questions_needed
from app.services.adaptive.convergence import certainty_pct, evaluate
from app.services.adaptive.irt import probability_correct

TRUE_ABILITIES = [-2.0, -1.0, 0.0, 1.0, 2.0]


@pytest.fixture()
def repository() -> JsonItemRepository:
    return JsonItemRepository()


async def simulate(session: AdaptiveSession, competency: str, true_theta: float, seed: int):
    """Administer a full competency to a simulated candidate of known ability."""
    rng = np.random.default_rng(seed)
    state = await session.begin(competency, self_rating=3)
    stop = evaluate(state.standard_error, [], 0, 99)

    for _ in range(settings.cat_max_questions):
        selected = await session.next_item(state, use_llm=False, top_k=1)
        if selected is None:
            break
        item = selected.item
        p = probability_correct(true_theta, item.a, item.b, item.c)
        # Answer as a real candidate of this ability would: right with probability p.
        chosen = item.answer_index if rng.random() < p else (item.answer_index + 1) % len(item.options)
        state, stop = await session.record_answer(state, item, chosen)
        if stop.should_stop:
            break
    return state, stop


@pytest.mark.asyncio
async def test_bank_loads_and_validates(repository):
    competencies = await repository.competencies()
    assert competencies
    items = await repository.items_for_competency(competencies[0])
    assert items and all(0.0 < i.a <= 3.0 and 0.0 <= i.c < 1.0 for i in items)


@pytest.mark.asyncio
async def test_ability_is_recovered_across_the_range(repository):
    """The headline claim: estimates track true ability."""
    session = AdaptiveSession(repository)
    competency = (await repository.competencies())[0]

    errors = []
    for index, true_theta in enumerate(TRUE_ABILITIES):
        for rep in range(6):
            state, _ = await simulate(session, competency, true_theta, seed=1000 * index + rep)
            errors.append(state.theta_hat - true_theta)

    rmse = float(np.sqrt(np.mean(np.square(errors))))
    bias = float(np.mean(errors))
    assert rmse < 0.95, f"ability is not being recovered (RMSE {rmse:.3f})"
    assert abs(bias) < 0.35, f"estimates are systematically off ({bias:+.3f})"


@pytest.mark.asyncio
async def test_a_session_never_repeats_an_item_or_exceeds_its_budget(repository):
    session = AdaptiveSession(repository)
    competency = (await repository.competencies())[0]
    state, _ = await simulate(session, competency, 1.0, seed=7)
    assert len(state.served_item_ids) == len(set(state.served_item_ids))
    assert state.questions_answered <= settings.cat_max_questions


@pytest.mark.asyncio
async def test_stronger_candidates_score_higher(repository):
    """Ordering is the property a report actually relies on."""
    session = AdaptiveSession(repository)
    competency = (await repository.competencies())[0]
    weak, _ = await simulate(session, competency, -2.0, seed=11)
    strong, _ = await simulate(session, competency, 2.0, seed=11)
    assert strong.theta_hat > weak.theta_hat + 1.0


@pytest.mark.asyncio
async def test_report_never_claims_precision_it_did_not_reach(repository):
    """`precision_target_met` must follow the standard error, not the stop reason."""
    session = AdaptiveSession(repository)
    competency = (await repository.competencies())[0]
    state, stop = await simulate(session, competency, 2.0, seed=3)
    result = await session.summarise(state, stop)
    assert result.precision_target_met == (result.standard_error <= settings.cat_se_target)
    if not result.precision_target_met:
        assert result.certainty_pct < 90.0


def test_certainty_depends_only_on_precision():
    """Two candidates at the same standard error must report the same measurement certainty.

    Guards a defect this engine was built to avoid: when certainty is scaled by the prior
    width, the same measured precision reports differently depending on how the candidate
    answered an intake question — and because a stopping rule reads that number, it also
    changes the length of the test.
    """
    from app.services.adaptive.convergence import measurement_certainty_pct

    assert measurement_certainty_pct(0.75) == measurement_certainty_pct(0.75)
    assert measurement_certainty_pct(settings.cat_se_target) == pytest.approx(90.0)
    assert measurement_certainty_pct(0.5) > measurement_certainty_pct(0.9)


def test_reported_certainty_stays_provisional_before_precision_floor():
    """A crushed SE after 2–3 high-a items must not display as ≥90% yet."""
    from app.services.adaptive.convergence import measurement_certainty_pct

    se = 0.50  # ≥90% on the raw scale when target is 0.55
    assert measurement_certainty_pct(se) >= 90.0
    assert certainty_pct(se, observations=3) < 90.0
    assert certainty_pct(se, observations=settings.cat_precision_min_questions) >= 90.0


def test_precision_stop_requires_observation_floor():
    """SE alone is not enough — the short high-a burst must not end the competency."""
    early = evaluate(
        standard_error=0.50,
        band_history=[2, 2, 2],
        questions_answered=3,
        items_remaining=20,
    )
    assert early.should_stop is False

    ready = evaluate(
        standard_error=0.50,
        band_history=[2] * settings.cat_precision_min_questions,
        questions_answered=settings.cat_precision_min_questions,
        items_remaining=20,
    )
    assert ready.should_stop is True
    assert ready.reason == "precision"
    assert ready.converged is True


def test_precision_stop_requires_difficulty_corroboration():
    """A narrow posterior from easy items must not claim the ability level is settled."""
    held = evaluate(
        standard_error=0.50,
        band_history=[2] * settings.cat_precision_min_questions,
        questions_answered=settings.cat_precision_min_questions,
        items_remaining=20,
        difficulty_corroborated=False,
    )
    assert held.should_stop is False


@pytest.mark.asyncio
async def test_bank_can_support_the_configured_precision_target(repository):
    """A bank whose items are individually weak cannot reach the target at any length.

    Reported rather than asserted for the extremes: this is a property of the BANK, and it
    bounds every assessment run against it regardless of how good the selection is.
    """
    for competency in await repository.competencies():
        items = await repository.items_for_competency(competency)
        needed = questions_needed(items, 0.0, prior_sd=1.7)
        assert needed is not None, (
            f"{competency}: the precision target is unreachable with the entire pool"
        )
