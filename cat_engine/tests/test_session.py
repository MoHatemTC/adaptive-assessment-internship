"""End-to-end: does the assembled engine actually recover ability?

The unit tests prove each part is individually correct. This proves the assembly measures
what it claims to, by administering tests to simulated candidates of known ability and
checking the estimates come back near the truth.
"""

import numpy as np
import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.adaptive import AdaptiveSession, JsonItemRepository
from cat_engine.engine.services.adaptive.bank import questions_needed
from cat_engine.engine.services.adaptive.convergence import certainty_pct, evaluate
from cat_engine.engine.services.adaptive.irt import probability_correct

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
        chosen = (
            item.answer_index
            if rng.random() < p
            else (item.answer_index + 1) % len(item.options)
        )
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
    from cat_engine.engine.services.adaptive.convergence import measurement_certainty_pct

    assert measurement_certainty_pct(0.75) == measurement_certainty_pct(0.75)
    assert measurement_certainty_pct(settings.cat_se_target) == pytest.approx(90.0)
    assert measurement_certainty_pct(0.5) > measurement_certainty_pct(0.9)


def test_reported_certainty_stays_provisional_before_precision_floor():
    """A crushed SE after 2–3 high-a items must not display as ≥90% yet."""
    from cat_engine.engine.services.adaptive.convergence import measurement_certainty_pct

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
        band_probability=0.99,
    )
    assert ready.should_stop is True
    assert ready.reason == "band_probability"
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


# Pools that cannot reach `cat_se_target` even when the whole pool is administered to a
# candidate at theta=0. Measured, not aspirational: a pool listed here always stops on the
# question budget and never on precision, whatever the selection does. The test below fails
# when a pool NOT on this list becomes unreachable, so the bank can only get better.
#
# Recording the number rather than xfailing the test is deliberate. An xfail says "known
# broken" and hides the size; the size is the point. 16 of AIE's 33 variables is a finding
# about every study run against that bank, including the stop-reason distribution, because
# those variables can only ever terminate on the budget.
KNOWN_UNREACHABLE_POOLS: dict[str, set[str]] = {
    # The legacy MCQ bank: uniformly short, best attainable SE 0.598 against a 0.55 target
    # on every one of its five competencies. Not one weak pool — the whole bank.
    "legacy-item-bank": {
        "Agentic AI & Orchestration",
        "Data & ML Foundations",
        "Generative AI & LLM Applications",
        "MLOps & Productionization",
        "Python & Software Engineering",
    },
    # 16 of 33. Concentrated in C6 (13 of its 16 own variables), which is the main the
    # graph's PREREQUISITE edges mostly connect — so the competency propagation is meant
    # to help is also the one the bank measures worst.
    "AIE": {
        "C3.11", "C3.4", "C3.6", "C3.7", "C3.8",
        "C6.10", "C6.12", "C6.14", "C6.15", "C6.16",
        "C6.2", "C6.3", "C6.4", "C6.5", "C6.8", "C6.9",
    },
    "DA": {"DA.1", "DA.3", "DA.4", "DA.5", "DA.6"},
    "PY": set(),
}


def _unreachable(pools: dict[str, list]) -> set[str]:
    """Pool names that cannot reach the SE target at theta=0 with every item administered."""
    return {
        name
        for name, items in pools.items()
        if questions_needed(items, 0.0, prior_sd=1.7) is None
    }


@pytest.mark.asyncio
async def test_legacy_bank_precision_reachability_has_not_regressed(repository):
    """A bank whose items are individually weak cannot reach the target at any length.

    This is a property of the BANK, and it bounds every assessment run against it
    regardless of how good the selection is.
    """
    pools = {c: await repository.items_for_competency(c) for c in await repository.competencies()}
    assert _unreachable(pools) == KNOWN_UNREACHABLE_POOLS["legacy-item-bank"]


@pytest.mark.parametrize("bank_id", ["AIE", "DA", "PY"])
def test_shipped_bank_precision_reachability_has_not_regressed(bank_id):
    """The same bound, on the banks that actually ship.

    Measured per VARIABLE, because that is the unit the CAT stops on — `cat_se_target`
    is applied to a variable's posterior, not to a main competency's.
    """
    from cat_engine.engine.schemas.adaptive import Item
    from cat_engine.engine.services.orchestrator import registry

    pools: dict[str, list[Item]] = {}
    for unified in registry.get_bank(bank_id).all_items():
        for measure in unified.measures:
            # `questions_needed` reads only the IRT triple; the rest is filler to satisfy
            # the schema. Every item measuring a variable counts toward that variable's pool.
            pools.setdefault(measure.variable, []).append(
                Item(
                    id=unified.item_id,
                    competency=measure.variable,
                    stem="",
                    options=["a", "b"],
                    answer_index=0,
                    a=unified.cat.a,
                    b=unified.cat.b,
                    c=unified.cat.c,
                )
            )

    assert pools, f"{bank_id}: no measurable variables"
    assert _unreachable(pools) == KNOWN_UNREACHABLE_POOLS[bank_id]


class TestTheBankFloorArithmeticIsSound:
    """The prescription must actually close the gap it prescribes for.

    `bank_floor` says "N more items at b = theta fixes this variable". That is only useful
    if adding N items of the stated information really reaches the target — an off-by-one
    in the ceiling division, or a mismatch between the deficit and the per-item value,
    would produce a work order that does not work.
    """

    def test_adding_the_prescribed_items_reaches_the_target(self):
        from cat_engine.validation import analyse_bank

        report = analyse_bank("AIE", theta=0.0)
        required = report["rows"][0]["required_precision"]

        short = [r for r in report["rows"] if not r["reachable"]]
        assert short, "no unreachable variables; the test would be vacuous"

        for row in short:
            closed = row["attained_precision"] + (
                row["items_needed"] * row["replacement_item_information"]
            )
            assert closed >= required, (
                f"{row['variable']}: {row['items_needed']} items leaves it short "
                f"({closed:.4f} < {required:.4f})"
            )
            # And not wastefully over: one fewer item must NOT suffice, or the count is
            # inflated and the work order costs more than it needs to.
            if row["items_needed"] > 1:
                one_fewer = row["attained_precision"] + (
                    (row["items_needed"] - 1) * row["replacement_item_information"]
                )
                assert one_fewer < required, (
                    f"{row['variable']}: {row['items_needed'] - 1} items would already do"
                )

    def test_attained_precision_and_best_se_agree(self):
        """SE = 1/sqrt(precision). If these disagree, one of them is being computed wrong."""
        from cat_engine.validation import analyse_bank

        for row in analyse_bank("AIE", theta=0.0)["rows"]:
            assert row["best_attainable_se"] == pytest.approx(
                row["attained_precision"] ** -0.5, abs=1e-3
            )

    def test_it_agrees_with_the_reachability_test_above(self):
        """Two independent routes to 'which variables are short' must name the same set."""
        from cat_engine.validation import analyse_bank

        computed = {
            r["variable"] for r in analyse_bank("AIE", theta=0.0)["rows"] if not r["reachable"]
        }
        assert computed == KNOWN_UNREACHABLE_POOLS["AIE"]
