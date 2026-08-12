"""Selection: that the engine ranks correctly, and that the model cannot damage it.

The second half is the point of the architecture. A model that returns nonsense, invents
an id, or is unreachable must never produce a worse assessment than one that behaves —
it can only ever cost the tokens spent asking.
"""

import numpy as np
import pytest

from cat_engine.engine.schemas.adaptive import AbilityState, Item
from cat_engine.engine.services.adaptive import selection
from cat_engine.engine.services.adaptive.irt import uniform_prior
from cat_engine.engine.services.adaptive.llm import LLMUnavailable
from cat_engine.engine.services.adaptive.selection import (
    choose_deterministically,
    choose_next_item,
    rank_candidates,
)


def make_item(item_id: str, a: float, b: float, sub: str = "s1") -> Item:
    return Item(
        id=item_id,
        competency="Test",
        sub_competency=sub,
        stem=f"Stem for {item_id}",
        options=["a", "b", "c", "d"],
        answer_index=0,
        a=a,
        b=b,
        c=0.25,
    )


@pytest.fixture()
def pool() -> list[Item]:
    return [
        make_item("sharp-here", 1.6, 0.0, "s1"),
        make_item("sharp-high", 1.6, 2.0, "s2"),
        make_item("blunt-here", 0.6, 0.0, "s3"),
        make_item("sharp-low", 1.6, -2.0, "s4"),
        make_item("mid", 1.0, 0.5, "s5"),
        make_item("spare", 1.0, 1.0, "s6"),
    ]


def fresh_state(theta: float = 0.0, answered: int = 5) -> AbilityState:
    return AbilityState(
        competency="Test",
        posterior=list(map(float, uniform_prior())),
        theta_hat=theta,
        standard_error=0.9,
        questions_answered=answered,
    )


def test_ranking_prefers_the_most_informative_item(pool):
    shortlist, criterion = rank_candidates(fresh_state(), pool, top_k=1)
    assert criterion == "E[Fisher]"
    assert shortlist[0][0].id == "sharp-here"


def test_criterion_switches_from_kl_to_fisher(pool):
    assert rank_candidates(fresh_state(answered=0), pool, top_k=1)[1] == "KL"
    assert rank_candidates(fresh_state(answered=3), pool, top_k=1)[1] == "E[Fisher]"


def test_served_items_are_never_reoffered(pool):
    state = fresh_state()
    state.served_item_ids = ["sharp-here"]
    shortlist, _ = rank_candidates(state, pool, top_k=1)
    assert "sharp-here" not in [i.id for i, _ in shortlist]


def test_exposure_control_varies_the_window(pool):
    """k>1 must not always administer the argmax, or a cohort receives one fixed form."""
    picks = {
        choose_deterministically(
            fresh_state(), pool, rng=np.random.default_rng(seed), top_k=3
        ).item.id
        for seed in range(30)
    }
    assert len(picks) > 1
    only = {
        choose_deterministically(
            fresh_state(), pool, rng=np.random.default_rng(seed), top_k=1
        ).item.id
        for seed in range(10)
    }
    assert only == {"sharp-here"}, "top_k=1 must be deterministic, for reproducible runs"


def test_content_balancing_prefers_an_unserved_sub_competency():
    """Among near-equally informative items, coverage decides."""
    twins = [make_item("served-sub", 1.5, 0.0, "s1"), make_item("fresh-sub", 1.5, 0.0, "s2")]
    state = fresh_state()
    state.served_item_ids = []
    # Pretend s1 has already been sampled by adding a served item from it.
    pool = [*twins, make_item("prior-s1", 1.5, 3.9, "s1")]
    state.served_item_ids = ["prior-s1"]
    chosen = choose_deterministically(state, pool, top_k=1)
    assert chosen.item.sub_competency == "s2"


@pytest.mark.asyncio
async def test_model_choice_from_the_shortlist_is_administered(pool, monkeypatch):
    async def fake(system, user, **kwargs):
        return {"selected_id": "mid", "rule_applied": "test", "rationale": "because"}

    monkeypatch.setattr(selection, "chat_json", fake)
    chosen = await choose_next_item(fresh_state(), pool, top_k=1)
    assert chosen.item.id == "mid"
    assert chosen.chosen_by_llm is True
    # It differs from the engine's own pick, and that is recorded rather than hidden.
    assert chosen.matched_procedure is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        {"selected_id": "does-not-exist"},  # invented id
        {"selected_id": ""},                # no answer
        {"unrelated": "payload"},           # well-formed JSON that is not an answer
    ],
)
async def test_unusable_model_replies_fall_back_to_the_engine(pool, monkeypatch, reply):
    async def fake(system, user, **kwargs):
        return reply

    monkeypatch.setattr(selection, "chat_json", fake)
    chosen = await choose_next_item(fresh_state(), pool, top_k=1)
    assert chosen.item.id == "sharp-here"
    assert chosen.chosen_by_llm is False


@pytest.mark.asyncio
async def test_unreachable_gateway_falls_back_to_the_engine(pool, monkeypatch):
    """An infrastructure failure must not end a candidate's assessment."""

    async def boom(system, user, **kwargs):
        raise LLMUnavailable("gateway unreachable: APITimeoutError")

    monkeypatch.setattr(selection, "chat_json", boom)
    chosen = await choose_next_item(fresh_state(), pool, top_k=1)
    assert chosen.item.id == "sharp-here"
    assert chosen.chosen_by_llm is False


@pytest.mark.asyncio
async def test_exhausted_pool_returns_none(pool):
    state = fresh_state()
    state.served_item_ids = [i.id for i in pool]
    assert await choose_next_item(state, pool, use_llm=False) is None


@pytest.mark.asyncio
async def test_model_can_only_choose_from_the_shortlist(pool, monkeypatch):
    """The guarantee the design rests on: the shortlist bounds the worst case.

    Whatever the model returns, the administered item is one the engine ranked into the
    shortlist — so its information is within a bounded factor of the best available.
    """
    seen: dict = {}

    async def capture(system, user, **kwargs):
        import json

        seen.update(json.loads(user))
        return {"selected_id": seen["shortlist"][-1]["id"]}

    monkeypatch.setattr(selection, "chat_json", capture)
    chosen = await choose_next_item(fresh_state(), pool, top_k=1)
    assert chosen.item.id in [entry["id"] for entry in seen["shortlist"]]
    worst_offered = min(entry["information_relative"] for entry in seen["shortlist"])
    assert worst_offered > 0.0
