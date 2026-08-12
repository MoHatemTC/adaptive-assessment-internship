"""A whole assessment, driven across four services over HTTP.

WHAT THIS REPLACES

`backend/tests/test_main_api.py` tested the same lifecycle against the monolith's FastAPI
app, which no longer exists. The assertions are the ones that were worth keeping — the
candidate boundary, the concurrency guard, the bank pinning — moved onto the new paths.

WHAT IS REAL AND WHAT IS NOT

All four services are real and talk to each other through real clients over ASGI, with no
socket open anywhere. The SANDBOX and the MODEL are stubbed: one costs money and needs
network, the other is not deterministic, and neither is what a lifecycle test is about.
Every session here therefore runs the deterministic selection path, which is also what
makes an identical seed produce an identical session.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import asgi_client, bank_store_at, load_services, stub_sandbox_and_model


@pytest.fixture()
def stack(monkeypatch, tmp_path):
    """bank-registry, grader, competency-graph and the orchestrator, wired together."""
    from adaptive_clients import (
        BankRegistryClient,
        CompetencyGraphClient,
        CompetencyScopeClient,
    )
    from adaptive_clients.engine import (
        HttpGrader,
        HttpGraphSource,
        HttpUnifiedBank,
        ScopedBank,
        scoped_graph_service,
    )

    stub_sandbox_and_model(monkeypatch)
    with bank_store_at(tmp_path / "banks"):
        with load_services(
            "bank-registry",
            "grader",
            "competency-graph",
            "competency-scope",
            "assessment-orchestrator",
        ) as modules:
            registry_main = modules["bank-registry"]
            grader_main = modules["grader"]
            graph_main = modules["competency-graph"]
            orchestrator_main = modules["assessment-orchestrator"]

            bank_http = asgi_client(registry_main.app)
            grader_main._items = grader_main.ItemSource(
                BankRegistryClient(http=bank_http)
            )
            graph_main._graphs = graph_main.HttpGraphSource(
                BankRegistryClient(http=bank_http)
            )

            # Rebuild the orchestrator's wiring against the in-process apps. Everything
            # else about it — selection, the posterior, stopping — is untouched.
            eng = orchestrator_main.engine
            eng._bank_client = BankRegistryClient(http=bank_http)
            eng._graph_source = HttpGraphSource(eng._bank_client)
            eng.reset()

            def build(bank_id: str, version: str, scope=None):
                # Mirrors `engine.orchestrator_for`, rewired to the in-process apps. The
                # SCOPING is not re-implemented — it calls the same two adapters the
                # service does, so a scope that behaved differently here would be a test
                # passing against code nobody ships.
                key = (bank_id, version, scope.scope_hash if scope else "")
                if key in eng._orchestrators:
                    return eng._orchestrators[key]
                from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator

                bank = HttpUnifiedBank(eng._bank_client, bank_id)
                bank.refresh()
                graph = eng._graph_source.service(bank_id)
                critical_only = eng._bank_client.bank(bank_id).coverage_critical_only
                if scope is not None:
                    bank = ScopedBank(
                        bank,
                        item_ids=set(scope.item_ids),
                        mains={row.main for row in scope.mains},
                    )
                    graph = scoped_graph_service(
                        graph, {node.node_id for node in scope.nodes}
                    )
                    critical_only = scope.coverage.critical_only_applied
                built = Orchestrator(
                    bank,
                    HttpGrader(http=asgi_client(grader_main.app), bank_id=bank_id),
                    graph=graph,
                    coverage_critical_only=critical_only,
                    bank_id=bank_id,
                    propagation=CompetencyGraphClient(
                        http=asgi_client(graph_main.app), bank_id=bank_id
                    ),
                )
                eng._orchestrators[key] = built
                return built

            # competency-scope, wired to the same in-process registry. The orchestrator
            # BUILDS the scope rather than being handed one, so this is the real path: a
            # scoped test that stubbed the manifest would never exercise the induction.
            scope_main = modules["competency-scope"]
            scope_main._bank = BankRegistryClient(http=bank_http)
            eng._scope_client = CompetencyScopeClient(
                http=asgi_client(scope_main.app)
            )

            monkeypatch.setattr(eng, "orchestrator_for", build)
            monkeypatch.setattr(orchestrator_main.engine, "orchestrator_for", build)
            monkeypatch.setattr(eng, "scope_client", lambda: eng._scope_client)

            with TestClient(orchestrator_main.app) as http:
                yield http, orchestrator_main


@pytest.fixture()
def api(stack):
    return stack[0]


def begin(api, **overrides) -> dict:
    body = {"bank_id": "DA", "use_llm": False, "seed": 7, **overrides}
    response = api.post("/assessments", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def answer(api, session: dict) -> dict:
    """Answer whatever is presented, correctly where that is knowable."""
    from cat_engine.engine.services.orchestrator import registry

    presenting = session["presenting"]
    item_id = presenting["item"]["item_id"]
    modality = presenting["item"]["modality"]
    if modality == "mcq":
        item = registry.get_bank(session["bank_id"]).get(item_id)
        payload = {"type": "mcq", "chosen_index": int(item.payload["answer_index"])}
    elif modality == "code":
        payload = {"type": "code", "code": "def solve(x):\n    return x"}
    else:
        payload = {
            "type": modality,
            "use_llm": False,
            "transcript": "I would profile the query first, add an index on the join "
            "column, and measure again before changing anything else.",
        }
    response = api.post(f"/assessments/{session['session_id']}/responses", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def comparable(report: dict) -> dict:
    """A report minus what identifies the RUN rather than the measurement.

    THE SESSION ID, which is a uuid per run and is embedded in every evidence id —
    `{session}:{item}:{attempt}:{modality}:{node}` — because that is what makes an evidence
    id unique and a replay recognisable. Normalised rather than dropped: the SHAPE of those
    ids is worth comparing, and one naming a different item, attempt or node should still
    fail.

    THE CLOCK, which is measured rather than computed.

    THE SCOPE ANNOTATION, which is the one field the two runs are SUPPOSED to differ in.

    The same three exclusions, for the same reasons, as
    `test_parity_inprocess_vs_services.comparable`.
    """
    import re

    stripped = {
        k: v
        for k, v in report.items()
        if k not in ("session_id", "seconds_by_item", "scope")
    }
    text = json.dumps(stripped, sort_keys=True)
    return json.loads(re.sub(r"asmt_[0-9a-f]{12}", "asmt_SESSION", text))


def run_to_report(api, session: dict) -> dict:
    """Answer until the assessment stops, and return the report it produced."""
    while session.get("presenting"):
        session = answer(api, session)
    assert session["report"] is not None, session.get("stop_reason")
    return session["report"]


class TestTheLifecycle:
    def test_beginning_an_assessment_presents_a_question(self, api):
        session = begin(api)
        assert session["session_id"].startswith("asmt_")
        assert session["stop"] is False
        assert session["presenting"]["item"]["item_id"]
        assert session["open_variables"] == ["DA"]

    def test_the_bank_version_is_pinned_at_the_start(self, api):
        """Replacing a bank mid-session must not change the pool underneath a candidate."""
        session = begin(api)
        assert session["bank_version"]
        assert api.get(f"/assessments/{session['session_id']}").json()[
            "bank_version"
        ] == session["bank_version"]

    def test_polling_does_not_advance_anything(self, api):
        session = begin(api)
        first = api.get(f"/assessments/{session['session_id']}").json()
        second = api.get(f"/assessments/{session['session_id']}").json()
        assert first["presenting"]["item"]["item_id"] == second["presenting"]["item"][
            "item_id"
        ]
        assert second["items_administered"] == 0

    def test_next_returns_the_presented_item(self, api):
        session = begin(api)
        presented = api.get(f"/assessments/{session['session_id']}/next").json()
        assert presented["item"]["item_id"] == session["presenting"]["item"]["item_id"]
        assert presented["criterion"] in ("KL", "E[Fisher]")

    def test_answering_advances_and_acknowledges(self, api):
        session = begin(api)
        after = answer(api, session)
        assert after["items_administered"] == 1
        assert after["last_graded"]["accepted"] is True
        assert after["last_graded"]["item_id"] == session["presenting"]["item"]["item_id"]

    def test_a_session_runs_to_a_report(self, api):
        session = begin(api)
        for _ in range(40):
            if session["stop"]:
                break
            session = answer(api, session)
        assert session["stop"] is True, "the session never stopped"
        assert session["stop_reason"]
        assert session["report"]["variables"]
        assert session["report"]["session_id"] == session["session_id"]

    def test_the_report_endpoint_refuses_while_the_assessment_runs(self, api):
        session = begin(api)
        response = api.get(f"/assessments/{session['session_id']}/report")
        assert response.status_code == 409
        assert response.json()["code"] == "assessment_running"

    def test_deleting_an_assessment_forgets_it(self, api):
        session = begin(api)
        assert api.delete(f"/assessments/{session['session_id']}").status_code == 200
        assert api.get(f"/assessments/{session['session_id']}").status_code == 404

    def test_an_unknown_assessment_is_a_404(self, api):
        assert api.get("/assessments/asmt_nope").status_code == 404


class TestTheCandidateBoundary:
    """What a client may see while the assessment is still running."""

    def test_the_presented_item_carries_no_answer(self, api):
        session = begin(api)
        item = session["presenting"]["item"]
        for forbidden in ("answer_index", "reference_solution", "tests", "rubric"):
            assert forbidden not in item

    def test_no_answer_key_appears_anywhere_in_the_response(self, api):
        """Asserted on the serialised body, so a field that happens to embed a payload
        fails here rather than in a candidate's browser."""
        body = api.post(
            "/assessments", json={"bank_id": "DA", "use_llm": False, "seed": 7}
        ).text
        for forbidden in ("answer_index", "reference_solution"):
            assert forbidden not in body

    def test_the_receipt_carries_no_grading_detail(self, api):
        """Feedback after each answer changes what the assessment measures: a candidate
        told which hidden test failed learns something between question four and five, and
        the estimate that comes out is of a person who was taught mid-measurement."""
        session = begin(api)
        after = answer(api, session)
        assert set(after["last_graded"]) == {"item_id", "modality", "accepted", "flags"}
        assert "correct" not in after["last_graded"]
        assert "score" not in after["last_graded"]

    def test_diagnostics_are_refused_by_default(self, api):
        session = begin(api)
        response = api.get(f"/assessments/{session['session_id']}/diagnostics")
        assert response.status_code == 404
        assert response.json()["code"] == "diagnostics_disabled"

    def test_raw_state_is_refused_by_default(self, api):
        session = begin(api)
        assert api.get(f"/assessments/{session['session_id']}/state").status_code == 404


class TestAnswerValidation:
    def test_answering_with_the_wrong_modality_is_refused(self, api):
        session = begin(api)
        modality = session["presenting"]["item"]["modality"]
        wrong = "code" if modality != "code" else "mcq"
        response = api.post(
            f"/assessments/{session['session_id']}/responses",
            json={"type": wrong, "chosen_index": 0, "code": "x"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "answer_type_mismatch"

    def test_an_mcq_answer_without_an_index_is_refused(self, api):
        session = begin(api)
        if session["presenting"]["item"]["modality"] != "mcq":
            pytest.skip("the first item is not an mcq for this seed")
        response = api.post(
            f"/assessments/{session['session_id']}/responses", json={"type": "mcq"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "answer_missing"

    def test_an_out_of_range_index_is_refused_and_records_nothing(self, api):
        session = begin(api)
        if session["presenting"]["item"]["modality"] != "mcq":
            pytest.skip("the first item is not an mcq for this seed")
        response = api.post(
            f"/assessments/{session['session_id']}/responses",
            json={"type": "mcq", "chosen_index": 99},
        )
        assert response.status_code == 422
        assert (
            api.get(f"/assessments/{session['session_id']}").json()["items_administered"]
            == 0
        )


class TestConcurrency:
    """The guard against a double-submit, which needs to be genuinely concurrent to test.

    A SEQUENTIAL second POST is not stale — the item has moved on and the client is
    answering the new one, which is correct and returns 200. What the guard exists for is
    two requests in flight at once: both read the same presentation, one commits, and the
    other must not wake up and apply its answer to a question nobody showed it.
    """

    @pytest.mark.asyncio
    async def test_two_answers_in_flight_at_once_leave_exactly_one_recorded(self, stack):
        import asyncio

        import httpx

        _http, orchestrator_main = stack
        session = begin(_http)
        if session["presenting"]["item"]["modality"] != "mcq":
            pytest.skip("the first item is not an mcq for this seed")

        from cat_engine.engine.services.orchestrator import registry

        item_id = session["presenting"]["item"]["item_id"]
        index = int(registry.get_bank("DA").get(item_id).payload["answer_index"])

        transport = httpx.ASGITransport(app=orchestrator_main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://in-process"
        ) as client:
            body = {"type": "mcq", "chosen_index": index}
            path = f"/assessments/{session['session_id']}/responses"
            first, second = await asyncio.gather(
                client.post(path, json=body),
                client.post(path, json=body),
                return_exceptions=True,
            )

        codes = sorted(
            r.status_code for r in (first, second) if isinstance(r, httpx.Response)
        )
        assert 200 in codes, (first, second)
        assert 409 in codes, (
            "both answers were accepted; the second applied a stale answer to whatever "
            f"was presented next — {codes}"
        )
        assert (
            _http.get(f"/assessments/{session['session_id']}").json()[
                "items_administered"
            ]
            == 1
        )


class TestPropertiesCarriedOverFromTheReleaseAudit:
    """Assertions that used to live in `backend/tests/test_main_api.py`.

    The monolith app they tested is gone; the properties are not. Each of these was a
    release-audit finding once, which is the strongest reason to move them rather than
    let them go with the file.
    """

    def test_a_code_item_shows_the_scaffold_and_never_the_reference_solution(self, api):
        """The scaffold is deliberately incomplete and belongs to the candidate. The
        reference solution is the answer, and shipping it turns every code item into an
        answer key."""
        from cat_engine.engine.services.orchestrator import registry

        item = next(
            i for i in registry.get_bank("DA").all_items() if i.modality == "code"
        )
        from service.presentation import presented_item  # noqa: PLC0415

        shown = presented_item(item).model_dump()
        assert shown["starter_code"] == item.payload.get("starter_code", "")
        assert item.payload["reference_solution"] not in json.dumps(shown)

    def test_every_measured_band_in_a_finished_report_is_provisional(self, api):
        """`converged` is an engine outcome; certification is a release decision. Until
        external exact-band calibration passes, even a converged estimate is provisional —
        and a report that said otherwise would be claiming an accuracy nobody has shown."""
        session = begin(api)
        for _ in range(40):
            if session["stop"]:
                break
            session = answer(api, session)
        assert session["stop"] is True
        measured = [v for v in session["report"]["variables"] if v["observations"] > 0]
        assert measured
        assert {v["decision_status"] for v in measured} == {"provisional"}

    def test_capacity_fails_closed_rather_than_evicting_a_live_assessment(
        self, api, monkeypatch
    ):
        """A candidate halfway through a test losing their session to a cache policy is
        not a trade-off anyone chose. At capacity the service refuses a NEW session."""
        from cat_engine.engine.config.settings import settings as engine_settings

        monkeypatch.setattr(engine_settings, "cat_max_retained_sessions", 1)
        first = api.post(
            "/assessments", json={"bank_id": "DA", "use_llm": False, "seed": 7}
        )
        assert first.status_code == 201
        second = api.post(
            "/assessments", json={"bank_id": "DA", "use_llm": False, "seed": 8}
        )
        assert second.status_code == 503
        assert second.json()["code"] == "capacity_reached"

        api.delete(f"/assessments/{first.json()['session_id']}")
        assert (
            api.post(
                "/assessments", json={"bank_id": "DA", "use_llm": False, "seed": 9}
            ).status_code
            == 201
        )

    def test_deleting_a_session_releases_everything_held_for_it(self, stack):
        """Not just the state: the lock and the exposure-control generator too. A leak
        here is unbounded — one entry per assessment ever begun."""
        http, orchestrator_main = stack
        session = begin(http)
        session_id = session["session_id"]
        assert session_id in orchestrator_main.SESSIONS

        http.delete(f"/assessments/{session_id}")
        assert session_id not in orchestrator_main.SESSIONS
        assert orchestrator_main.SESSIONS.lock_for(session_id) is None

    def test_a_session_keeps_one_persistent_generator(self, stack):
        """A fresh generator per request would make randomesque exposure control repeat
        its first draw on every question, which is not exposure control."""
        http, orchestrator_main = stack
        session = begin(http)
        held = orchestrator_main.SESSIONS.get(session["session_id"]).rng
        answer(http, session)
        assert orchestrator_main.SESSIONS.get(session["session_id"]).rng is held


class TestTheCatalogue:
    def test_banks_are_proxied_so_a_frontend_has_one_base_url(self, api):
        banks = api.get("/banks").json()
        assert {b["bank_id"] for b in banks} >= {"DA", "AIE"}
        assert all(b["version"] for b in banks)


class TestDiagnosticsWhenEnabled:
    @pytest.fixture()
    def author_api(self, stack, monkeypatch):
        _http, orchestrator_main = stack
        monkeypatch.setattr(
            orchestrator_main.settings, "author_diagnostics_enabled", True
        )
        return _http

    def test_they_report_the_posterior_and_the_criterion(self, author_api):
        session = begin(author_api)
        body = author_api.get(
            f"/assessments/{session['session_id']}/diagnostics"
        ).json()
        assert body["session_id"] == session["session_id"]
        variable = body["variables"][0]
        assert variable["criterion"] in ("KL", "E[Fisher]")
        assert variable["credible_interval_95"]
        assert variable["standard_error"] > 0

    def test_they_say_what_the_presented_item_is_worth(self, author_api):
        """The number that makes a selection auditable rather than merely logged."""
        session = begin(author_api)
        body = author_api.get(
            f"/assessments/{session['session_id']}/diagnostics"
        ).json()
        presenting_variable = session["presenting"]["variable"]
        row = next(v for v in body["variables"] if v["variable"] == presenting_variable)
        assert row["presenting_information"] is not None
        assert row["presenting_information"] > 0

    def test_the_queue_records_why_each_item_was_chosen(self, author_api):
        session = begin(author_api)
        body = author_api.get(
            f"/assessments/{session['session_id']}/diagnostics"
        ).json()
        presenting = body["presenting"]
        assert presenting["engine_top_pick"]
        assert "chosen_by_llm" in presenting
        assert presenting["shortlist_ids"]

    def test_raw_state_round_trips_as_the_engines_own_object(self, author_api):
        """It is the engine's schema on purpose. A wire copy here would be a second
        definition that drifts from the one the session is actually stored in."""
        from cat_engine.engine.schemas.orchestration import AssessmentState

        session = begin(author_api)
        raw = author_api.get(f"/assessments/{session['session_id']}/state").json()
        assert AssessmentState.model_validate(raw).session_id == session["session_id"]


class TestAScopedAssessment:
    """Beginning an assessment on some of a bank's competencies rather than all of it.

    THE FIRST TEST IS THE ONE THAT MATTERS. A scope naming every competency has to produce
    the same report as no scope at all. If it does not, a scoped session is not a
    restriction of the engine's behaviour but a second, slightly different instrument — and
    every number in `docs/evidence.md` was measured against the first one.
    """

    def test_a_scope_over_everything_reports_exactly_what_no_scope_does(self, api):
        unscoped = run_to_report(api, begin(api))
        scoped = run_to_report(api, begin(api, scope={"selected": ["DA"]}))
        assert comparable(scoped) == comparable(unscoped)

    def test_the_report_names_the_scope_it_was_measured_under(self, api):
        report = run_to_report(api, begin(api, scope={"selected": ["DA"]}))
        assert report["scope"]["scope_id"].startswith("scp_")
        assert report["scope"]["selected"] == ["DA"]
        # A whole main is not partial, so nothing here should claim it is.
        assert report["scope"]["partial_mains"] == []

    def test_an_unscoped_report_carries_no_scope(self, api):
        assert run_to_report(api, begin(api))["scope"] is None

    def test_only_items_inside_the_scope_are_ever_presented(self, api):
        """The property a candidate would notice. Asserted across a whole session rather
        than on the first item, because a narrowing that leaked later would still be a
        candidate answering a question nobody selected."""
        from cat_engine.engine.services.orchestrator import registry

        session = begin(api, scope={"selected": ["DA.1", "DA.2"]})
        allowed = {
            item.item_id
            for item in registry.get_bank("DA").all_items()
            if {m.variable for m in item.measures} & {"DA.1", "DA.2"}
        }
        seen = set()
        while session.get("presenting"):
            seen.add(session["presenting"]["item"]["item_id"])
            session = answer(api, session)
        assert seen, "the session presented nothing at all"
        assert seen <= allowed

    def test_a_partially_scoped_main_is_reported_as_partial(self, api):
        report = run_to_report(api, begin(api, scope={"selected": ["DA.1", "DA.2"]}))
        assert report["scope"]["partial_mains"] == ["DA"]
        assert 0.0 < report["scope"]["retained_weight_by_main"]["DA"] < 1.0

    def test_a_sub_competency_never_becomes_a_reported_variable(self, api):
        """Ability is estimated per MAIN. `rollup_outcomes` folds every outcome to its
        main and DROPS it when that main is not in the session, so a session opened on
        `DA.1` would discard every outcome it computed and finish with the standard error
        exactly where it started — raising nothing and logging nothing."""
        report = run_to_report(api, begin(api, scope={"selected": ["DA.1", "DA.2"]}))
        assert [row["variable"] for row in report["variables"]] == ["DA"]
        assert report["variables"][0]["observations"] > 0

    def test_scope_and_target_variables_together_are_refused(self, api):
        response = api.post(
            "/assessments",
            json={
                "bank_id": "DA",
                "use_llm": False,
                "scope": {"selected": ["DA"]},
                "target_variables": ["DA"],
            },
        )
        assert response.status_code == 400
        assert response.json()["code"] == "scope_and_targets"

    def test_an_unassessable_scope_is_refused_before_a_candidate_is_involved(self, api):
        """With the competency named. A scope thin enough to exhaust immediately otherwise
        produces a session that finalises everything as `bank_exhausted` — a shape
        indistinguishable afterwards from a candidate who stopped answering."""
        response = api.post(
            "/assessments",
            json={
                "bank_id": "DA",
                "use_llm": False,
                "scope": {"selected": ["NOT-A-COMPETENCY"]},
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "scope_unassessable"
        assert "NOT-A-COMPETENCY" in response.json()["detail"]

    def test_the_scope_pins_the_bank_version_it_was_built_against(self, api):
        session = begin(api, scope={"selected": ["DA"]})
        from cat_engine.engine.services.orchestrator import registry

        assert session["bank_version"] == registry.version("DA")
