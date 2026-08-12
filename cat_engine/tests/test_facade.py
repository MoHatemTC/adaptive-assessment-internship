"""A whole assessment, driven through the surface a host calls.

WHAT THIS REPLACES

`test_assessment_orchestrator_service.py`, which drove the same lifecycle across four
services over ASGI, and before that `test_main_api.py`, which drove it against the
monolith's FastAPI app. The transport has changed twice; the assertions have not, because
what they check is the candidate boundary, the concurrency guard, the bank pinning and the
scope rules — none of which was ever about HTTP.

The eighty-line fixture that wired five services together is one line now. That is the
clearest measure of what the collapse bought, and it is why this file opens with it.

WHAT IS REAL AND WHAT IS NOT

The module is real. The SANDBOX and the MODEL are stubbed: one costs money and needs
network, the other is not deterministic, and neither is what a lifecycle test is about.
Every session here runs the deterministic selection path, which is also what makes an
identical seed produce an identical session.

STATUS CODES ARE STILL ASSERTED, ON EXCEPTIONS

`CatError` carries the `code` and `status_code` a host maps back onto responses. Asserting
them here is what keeps that mapping honest — a frontend written against the service-era
API branches on exactly these strings, and they are the contract now.
"""

from __future__ import annotations

import json

import pytest

from cat_engine.errors import (
    AnswerInvalid,
    AnswerTypeMismatch,
    AssessmentUnknown,
    CapacityReached,
    CatError,
    DiagnosticsDisabled,
    ScopeInvalid,
    ScopeUnassessable,
)

CODE_ANSWER = "def solve(x):\n    return x"
TYPED_ANSWER = (
    "I would profile the query first, add an index on the join column, and measure "
    "again before changing anything else."
)


@pytest.fixture()
def cat(stub_boundaries, module, tmp_path):
    """The module, with a bank store of its own.

    Any test that writes a bank needs the temporary store. Without it the write lands in
    the checked-in `engine/data/banks`, and the next run discovers a registered bank that
    no fixture created and no assertion expects — a failure in a different file, hours
    later, with nothing pointing back here.
    """
    from cat_engine.engine.services.orchestrator import registry
    from cat_engine.engine.services.orchestrator.bank_store import (
        BankStore,
        _seed_profiles,
    )

    previous = registry.STORE
    registry.use_store(BankStore(seeds=_seed_profiles(), store_dir=tmp_path / "banks"))
    module.wiring.reset()
    try:
        yield module
    finally:
        registry.use_store(previous)


async def begin(cat, **overrides):
    return await cat.begin(bank_id="DA", use_llm=False, seed=7, **overrides)


async def answer(cat, state):
    """Answer whatever is presented, correctly where that is knowable."""
    from cat_engine.engine.services.orchestrator import registry

    item = state.presenting.item
    if item.modality == "mcq":
        full = registry.get_bank(state.bank_id).get(item.item_id)
        kwargs = {"mcq": int(full.payload["answer_index"])}
    elif item.modality == "code":
        kwargs = {"code": CODE_ANSWER}
    else:
        kwargs = {"transcript": TYPED_ANSWER}
    return await cat.answer(state.session_id, use_llm=False, **kwargs)


async def run_to_report(cat, state):
    """Answer until the assessment stops, and return the report it produced."""
    while state.presenting is not None:
        state = await answer(cat, state)
    assert state.report is not None, state.stop_reason
    return state.report.model_dump()


def comparable(report: dict) -> dict:
    """A report minus what identifies the RUN rather than the measurement.

    THE SESSION ID, which is a uuid per run and is embedded in every evidence id —
    `{session}:{item}:{attempt}:{modality}:{node}` — because that is what makes an evidence
    id unique and a replay recognisable. Normalised rather than dropped: the SHAPE of those
    ids is worth comparing, and one naming a different item, attempt or node should still
    fail.

    THE CLOCK, which is measured rather than computed.

    THE SCOPE ANNOTATION, which is the one field the two runs are SUPPOSED to differ in.
    """
    import re

    stripped = {
        k: v
        for k, v in report.items()
        if k not in ("session_id", "seconds_by_item", "scope")
    }
    text = json.dumps(stripped, sort_keys=True, default=str)
    return json.loads(re.sub(r"asmt_[0-9a-f]{12}", "asmt_SESSION", text))


class TestTheLifecycle:
    async def test_beginning_an_assessment_presents_a_question(self, cat):
        state = await begin(cat)
        assert state.session_id.startswith("asmt_")
        assert state.stop is False
        assert state.presenting.item.item_id
        assert state.open_variables == ["DA"]

    async def test_the_bank_version_is_pinned_at_the_start(self, cat):
        """Replacing a bank mid-session must not change the pool underneath a candidate."""
        state = await begin(cat)
        assert state.bank_version
        assert cat.state(state.session_id).bank_version == state.bank_version

    async def test_polling_does_not_advance_anything(self, cat):
        state = await begin(cat)
        first = cat.state(state.session_id)
        second = cat.state(state.session_id)
        assert first.presenting.item.item_id == second.presenting.item.item_id
        assert second.items_administered == 0

    async def test_next_item_returns_the_presented_item(self, cat):
        state = await begin(cat)
        presented = cat.next_item(state.session_id)
        assert presented.item.item_id == state.presenting.item.item_id
        assert presented.criterion in ("KL", "E[Fisher]")

    async def test_answering_advances_and_acknowledges(self, cat):
        state = await begin(cat)
        after = await answer(cat, state)
        assert after.items_administered == 1
        assert after.last_graded.accepted is True
        assert after.last_graded.item_id == state.presenting.item.item_id

    async def test_a_session_runs_to_a_report(self, cat):
        state = await begin(cat)
        for _ in range(40):
            if state.stop:
                break
            state = await answer(cat, state)
        assert state.stop is True, "the session never stopped"
        assert state.stop_reason
        assert state.report.variables
        assert state.report.session_id == state.session_id

    async def test_the_report_is_refused_while_the_assessment_runs(self, cat):
        state = await begin(cat)
        with pytest.raises(CatError) as caught:
            cat.report(state.session_id)
        assert caught.value.code == "assessment_running"
        assert caught.value.status_code == 409

    async def test_discarding_an_assessment_forgets_it(self, cat):
        state = await begin(cat)
        assert cat.discard(state.session_id) is True
        with pytest.raises(AssessmentUnknown):
            cat.state(state.session_id)

    async def test_an_unknown_assessment_is_refused(self, cat):
        with pytest.raises(AssessmentUnknown) as caught:
            cat.state("asmt_nope")
        assert caught.value.status_code == 404


class TestTheCandidateBoundary:
    """What a caller may see while the assessment is still running."""

    async def test_the_presented_item_carries_no_answer(self, cat):
        state = await begin(cat)
        shown = state.presenting.item.model_dump()
        for forbidden in ("answer_index", "reference_solution", "tests", "rubric"):
            assert forbidden not in shown

    async def test_no_answer_key_appears_anywhere_in_the_response(self, cat):
        """Asserted on the serialised body, so a field that happens to embed a payload
        fails here rather than in a candidate's browser."""
        state = await begin(cat)
        body = state.model_dump_json()
        for forbidden in ("answer_index", "reference_solution"):
            assert forbidden not in body

    async def test_the_receipt_carries_no_grading_detail(self, cat):
        """Feedback after each answer changes what the assessment measures: a candidate
        told which hidden test failed learns something between question four and five, and
        the estimate that comes out is of a person who was taught mid-measurement."""
        state = await begin(cat)
        after = await answer(cat, state)
        assert set(after.last_graded.model_dump()) == {
            "item_id",
            "modality",
            "accepted",
            "flags",
        }

    async def test_diagnostics_are_refused_by_default(self, cat):
        state = await begin(cat)
        with pytest.raises(DiagnosticsDisabled) as caught:
            cat.diagnostics(state.session_id)
        assert caught.value.code == "diagnostics_disabled"

    async def test_raw_state_is_refused_by_default(self, cat):
        state = await begin(cat)
        with pytest.raises(DiagnosticsDisabled):
            cat.raw_state(state.session_id)


class TestAnswerValidation:
    async def test_answering_with_the_wrong_modality_is_refused(self, cat):
        state = await begin(cat)
        modality = state.presenting.item.modality
        wrong = {"code": CODE_ANSWER} if modality != "code" else {"mcq": 0}
        with pytest.raises(AnswerTypeMismatch) as caught:
            await cat.answer(state.session_id, use_llm=False, **wrong)
        assert caught.value.status_code == 422

    async def test_an_mcq_answer_without_an_index_is_refused(self, cat):
        state = await begin(cat)
        if state.presenting.item.modality != "mcq":
            pytest.skip("the first item is not an mcq for this seed")
        with pytest.raises(AnswerTypeMismatch):
            await cat.answer(state.session_id, use_llm=False)

    async def test_an_out_of_range_index_is_refused_and_records_nothing(self, cat):
        state = await begin(cat)
        if state.presenting.item.modality != "mcq":
            pytest.skip("the first item is not an mcq for this seed")
        with pytest.raises(AnswerInvalid):
            await cat.answer(state.session_id, mcq=99, use_llm=False)
        assert cat.state(state.session_id).items_administered == 0


class TestConcurrency:
    """The guard against a double-submit.

    WHY THIS IS NOT THE TEST IT USED TO BE.

    The service version fired two real HTTP requests with `asyncio.gather` and asserted one
    got a 409. It passed for a reason nobody intended: the picking agent's model call was a
    real network round trip, and its latency was what let the second request interleave
    with the first. Run without credentials — which is how CI runs — request one completed
    before request two started, both were accepted, and the assertion failed.

    A concurrency test whose outcome depends on whether a developer has a `.env` is not
    testing concurrency. So the interleaving is made explicit here: the first answer is
    held inside the lock until the second has read the state it is going to race against.
    """

    async def test_two_answers_in_flight_at_once_leave_exactly_one_recorded(self, cat):
        import asyncio

        state = await begin(cat)
        if state.presenting.item.modality != "mcq":
            pytest.skip("the first item is not an mcq for this seed")

        from cat_engine.engine.services.orchestrator import registry

        index = int(
            registry.get_bank("DA").get(state.presenting.item.item_id).payload[
                "answer_index"
            ]
        )
        orchestrator = cat.wiring.orchestrator_for(state.bank_id, state.bank_version, None)

        # Park the FIRST answer inside the lock, after it has graded and before it writes
        # the new state back. `after_response` is the async step there, so it is the one
        # place a second answer can be let in at exactly the moment that matters: the
        # session still shows the item both of them read.
        entered = asyncio.Event()
        release = asyncio.Event()
        original = orchestrator.after_response

        async def park(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        orchestrator.after_response = park
        try:
            first = asyncio.create_task(
                cat.answer(state.session_id, mcq=index, use_llm=False)
            )
            await entered.wait()

            # The second answer captures the presented item — the same one — and then
            # blocks on the lock. Nothing before the lock awaits, so one turn of the loop
            # is enough to get it there.
            second = asyncio.create_task(
                cat.answer(state.session_id, mcq=index, use_llm=False)
            )
            await asyncio.sleep(0.05)

            release.set()
            outcomes = await asyncio.gather(first, second, return_exceptions=True)
        finally:
            orchestrator.after_response = original

        accepted = [o for o in outcomes if not isinstance(o, Exception)]
        refused = [o for o in outcomes if isinstance(o, CatError)]
        assert len(accepted) == 1, f"both answers were accepted: {outcomes}"
        assert len(refused) == 1 and refused[0].code == "stale_answer", outcomes
        assert cat.state(state.session_id).items_administered == 1


class TestPropertiesCarriedOverFromTheReleaseAudit:
    """Each of these was a release-audit finding once, which is the strongest reason to
    carry them through two transport changes rather than let them go with the file."""

    async def test_a_code_item_shows_the_scaffold_and_never_the_reference_solution(
        self, cat
    ):
        """The scaffold is deliberately incomplete and belongs to the candidate. The
        reference solution is the answer, and shipping it turns every code item into an
        answer key."""
        from cat_engine.engine.services.orchestrator import registry
        from cat_engine.projection import presented_item

        item = next(
            i for i in registry.get_bank("DA").all_items() if i.modality == "code"
        )
        shown = presented_item(item).model_dump()
        assert shown["starter_code"] == item.payload.get("starter_code", "")
        assert item.payload["reference_solution"] not in json.dumps(shown)

    async def test_every_measured_band_in_a_finished_report_is_provisional(self, cat):
        """`converged` is an engine outcome; certification is a release decision. Until
        external exact-band calibration passes, even a converged estimate is provisional —
        and a report that said otherwise would claim an accuracy nobody has shown."""
        report = await run_to_report(cat, await begin(cat))
        measured = [v for v in report["variables"] if v["observations"] > 0]
        assert measured
        assert {v["decision_status"] for v in measured} == {"provisional"}

    async def test_capacity_fails_closed_rather_than_evicting_a_live_assessment(
        self, cat, monkeypatch
    ):
        """A candidate halfway through a test losing their session to a cache policy is not
        a trade-off anyone chose. At capacity a NEW session is refused."""
        from cat_engine.engine.config.settings import settings as engine_settings

        monkeypatch.setattr(engine_settings, "cat_max_retained_sessions", 1)
        first = await begin(cat)
        with pytest.raises(CapacityReached) as caught:
            await cat.begin(bank_id="DA", use_llm=False, seed=8)
        assert caught.value.status_code == 503

        cat.discard(first.session_id)
        assert await cat.begin(bank_id="DA", use_llm=False, seed=9)

    async def test_discarding_a_session_releases_everything_held_for_it(self, cat):
        """Not just the state: the lock and the exposure-control generator too. A leak here
        is unbounded — one entry per assessment ever begun."""
        state = await begin(cat)
        assert cat.sessions.get(state.session_id) is not None

        cat.discard(state.session_id)
        assert cat.sessions.get(state.session_id) is None
        assert cat.sessions.lock_for(state.session_id) is None

    async def test_a_session_keeps_one_persistent_generator(self, cat):
        """A fresh generator per request would make randomesque exposure control repeat its
        first draw on every question, which is not exposure control."""
        state = await begin(cat)
        held = cat.sessions.get(state.session_id).rng
        await answer(cat, state)
        assert cat.sessions.get(state.session_id).rng is held


class TestTheCatalogue:
    async def test_banks_are_listed_with_their_versions(self, cat):
        banks = cat.banks()
        assert {b.bank_id for b in banks} >= {"DA", "AIE"}
        assert all(b.version for b in banks)


class TestDiagnosticsWhenEnabled:
    @pytest.fixture()
    def author(self, cat, monkeypatch):
        monkeypatch.setattr(cat.settings, "author_diagnostics_enabled", True)
        return cat

    async def test_they_report_the_posterior_and_the_criterion(self, author):
        state = await begin(author)
        body = author.diagnostics(state.session_id)
        assert body.session_id == state.session_id
        variable = body.variables[0]
        assert variable.criterion in ("KL", "E[Fisher]")
        assert variable.credible_interval_95
        assert variable.standard_error > 0

    async def test_they_say_what_the_presented_item_is_worth(self, author):
        """The number that makes a selection auditable rather than merely logged."""
        state = await begin(author)
        body = author.diagnostics(state.session_id)
        row = next(
            v for v in body.variables if v.variable == state.presenting.variable
        )
        assert row.presenting_information is not None
        assert row.presenting_information > 0

    async def test_the_queue_records_why_each_item_was_chosen(self, author):
        """`presenting` is the engine's own queue entry, unprojected — which is the point
        of the author view. A wire copy would be a second definition of the thing being
        audited."""
        state = await begin(author)
        presenting = author.diagnostics(state.session_id).presenting
        assert presenting["engine_top_pick"]
        assert presenting["shortlist_ids"]
        assert "chosen_by_llm" in presenting

    async def test_raw_state_round_trips_as_the_engines_own_object(self, author):
        """It is the engine's schema on purpose. A projected copy here would be a second
        definition that drifts from the one the session is actually stored in."""
        from cat_engine.engine.schemas.orchestration import AssessmentState

        state = await begin(author)
        raw = author.raw_state(state.session_id)
        assert AssessmentState.model_validate(raw.model_dump()).session_id == (
            state.session_id
        )


class TestAScopedAssessment:
    """Beginning an assessment on some of a bank's competencies rather than all of it.

    THE FIRST TEST IS THE ONE THAT MATTERS. A scope naming every competency has to produce
    the same report as no scope at all. If it does not, a scoped session is not a
    restriction of the engine's behaviour but a second, slightly different instrument — and
    every number in `docs/evidence.md` was measured against the first one.
    """

    async def test_a_scope_over_everything_reports_exactly_what_no_scope_does(self, cat):
        unscoped = await run_to_report(cat, await begin(cat))
        scoped = await run_to_report(cat, await begin(cat, scope=["DA"]))
        assert comparable(scoped) == comparable(unscoped)

    async def test_the_report_names_the_scope_it_was_measured_under(self, cat):
        report = await run_to_report(cat, await begin(cat, scope=["DA"]))
        assert report["scope"]["scope_id"].startswith("scp_")
        assert report["scope"]["selected"] == ["DA"]
        # A whole main is not partial, so nothing here should claim it is.
        assert report["scope"]["partial_mains"] == []

    async def test_an_unscoped_report_carries_no_scope(self, cat):
        assert (await run_to_report(cat, await begin(cat)))["scope"] is None

    async def test_only_items_inside_the_scope_are_ever_presented(self, cat):
        """The property a candidate would notice. Asserted across a whole session rather
        than on the first item, because a narrowing that leaked later would still be a
        candidate answering a question nobody selected."""
        from cat_engine.engine.services.orchestrator import registry

        state = await begin(cat, scope=["DA.1", "DA.2"])
        allowed = {
            item.item_id
            for item in registry.get_bank("DA").all_items()
            if {m.variable for m in item.measures} & {"DA.1", "DA.2"}
        }
        seen = set()
        while state.presenting is not None:
            seen.add(state.presenting.item.item_id)
            state = await answer(cat, state)
        assert seen, "the session presented nothing at all"
        assert seen <= allowed

    async def test_a_partially_scoped_main_is_reported_as_partial(self, cat):
        report = await run_to_report(cat, await begin(cat, scope=["DA.1", "DA.2"]))
        assert report["scope"]["partial_mains"] == ["DA"]
        assert 0.0 < report["scope"]["retained_weight_by_main"]["DA"] < 1.0

    async def test_a_sub_competency_never_becomes_a_reported_variable(self, cat):
        """Ability is estimated per MAIN. `rollup_outcomes` folds every outcome to its main
        and DROPS it when that main is not in the session, so a session opened on `DA.1`
        would discard every outcome it computed and finish with the standard error exactly
        where it started — raising nothing and logging nothing."""
        report = await run_to_report(cat, await begin(cat, scope=["DA.1", "DA.2"]))
        assert [row["variable"] for row in report["variables"]] == ["DA"]
        assert report["variables"][0]["observations"] > 0

    async def test_scope_and_target_variables_together_are_refused(self, cat):
        with pytest.raises(ScopeInvalid) as caught:
            await cat.begin(
                bank_id="DA", use_llm=False, scope=["DA"], target_variables=["DA"]
            )
        assert caught.value.code == "scope_and_targets"

    async def test_an_unassessable_scope_is_refused_before_a_candidate_is_involved(
        self, cat
    ):
        """With the competency named. A scope thin enough to exhaust immediately otherwise
        produces a session that finalises everything as `bank_exhausted` — a shape
        indistinguishable afterwards from a candidate who stopped answering."""
        with pytest.raises(ScopeUnassessable) as caught:
            await cat.begin(bank_id="DA", use_llm=False, scope=["NOT-A-COMPETENCY"])
        assert caught.value.status_code == 422
        assert "NOT-A-COMPETENCY" in caught.value.detail

    async def test_the_scope_pins_the_bank_version_it_was_built_against(self, cat):
        from cat_engine.engine.services.orchestrator import registry

        state = await begin(cat, scope=["DA"])
        assert state.bank_version == registry.version("DA")
