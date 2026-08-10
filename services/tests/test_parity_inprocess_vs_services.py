"""The claim the whole migration rests on: the split changed nothing.

WHAT THIS DOES

Runs one identical, seeded assessment twice — once through the engine in this process,
exactly as `backend/evaluation/` drives it, and once through all four services over real
clients — and asserts the two produce the same report. Same theta, same standard error,
same band, same stopping reason, same items, in the same order.

WHY IT IS THE TEST THAT MATTERS

Every other test in this suite checks that a service does what its route says. None of them
would notice the failure that actually matters here: that selection, the posterior or the
stopping rule behave DIFFERENTLY once the bank is behind HTTP and grading is behind a hop.
A drift of one item in a shortlist, or one bank item whose parameters round differently
through JSON, produces a session that is internally consistent, plausible, and not the one
the evaluation harness measured — and the harness is where every claim about this engine's
accuracy comes from.

If this ever fails, the number to distrust is not the one in the report. It is every number
in `docs/evidence.md`.

WHAT IS EXCLUDED FROM THE COMPARISON, AND WHY THAT IS SAFE

Wall clock. `started_at`, `elapsed_minutes` and `seconds_by_item` are measured, not
computed, and two runs of anything differ in them. Nothing downstream of them enters an
estimate — they inform the time-aware selection budget, which is why the sessions are run
back to back rather than compared across a suite.
"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import asgi_client, bank_store_at, load_services, stub_sandbox_and_model

BANK = "DA"
SEED = 4242
#: A ceiling, not an expectation. Both runs stop on their own rule; this only stops a
#: broken one spinning.
MAXIMUM_ITEMS = 60

TYPED_ANSWER = (
    "I would profile the query first, add an index on the join column, and measure "
    "again before changing anything else rather than guessing at the cause."
)
CODE_ANSWER = "def solve(x):\n    return x\n"


def answer_for(item, bank):
    """The same answer for the same item, whichever path is asking.

    Deterministic and correct where correctness is knowable, because two runs that answered
    differently would prove nothing about the split.
    """
    if item.modality == "mcq":
        return int(bank.get(item.item_id).payload["answer_index"])
    if item.modality == "code":
        return CODE_ANSWER
    return TYPED_ANSWER


def comparable(report: dict) -> dict:
    """The report minus the two things that identify a RUN rather than a measurement.

    THE CLOCK. `seconds_by_item` is measured, not computed, and two runs of anything differ
    in it. Nothing downstream of it enters an estimate.

    THE SESSION ID. It is a uuid per run, and it is embedded in every evidence id —
    `{session}:{item}:{attempt}:{modality}:{node}:{criterion}` — because that is what makes
    an evidence id unique and a replay recognisable. Two runs are two sessions, so those
    ids differ by construction and by design.

    Normalising it rather than dropping the ids: their SHAPE is the thing worth comparing.
    An evidence id that named a different item, a different attempt number or a different
    node would still fail this, which is the failure that would matter.
    """
    import json
    import re

    stripped = {k: v for k, v in report.items() if k != "seconds_by_item"}
    text = json.dumps(stripped, sort_keys=True)
    text = re.sub(r"asmt_[0-9a-f]{12}", "asmt_SESSION", text)
    return json.loads(text)


@pytest.fixture()
def in_process_report(monkeypatch):
    """One assessment, driven exactly as `backend/evaluation/` drives it."""
    import asyncio

    from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
    from app.services.orchestrator import registry
    from app.services.orchestrator.grader import GraderAgent
    from app.services.orchestrator.orchestrator import Orchestrator
    from app.services.voice.evaluator import evaluate as evaluate_voice

    stub_sandbox_and_model(monkeypatch)
    bank = registry.get_bank(BANK)
    orchestrator = Orchestrator(
        bank,
        GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository())),
        graph=registry.get_graph_service(BANK),
        coverage_critical_only=registry.coverage_policy(BANK),
        bank_id=BANK,
    )

    async def run() -> dict:
        rng = np.random.default_rng(SEED)
        state = orchestrator.begin(bank.variables())
        state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
        state = orchestrator.ensure_presenting(state)

        for _ in range(MAXIMUM_ITEMS):
            stop, reason = orchestrator.should_stop(state)
            if stop:
                return orchestrator.summarise(state, reason).model_dump()
            pair = orchestrator.next_item(state)
            if pair is None:
                break
            item, _candidate = pair
            response = answer_for(item, bank)
            if item.modality in ("open", "voice"):
                # The service grades from the package; in-process the evaluation runs here,
                # which is where `app.main` used to do it. Same evaluator, same rubric,
                # `use_llm=False` on both sides.
                from app.schemas.voice import VoiceResponsePackage

                package = VoiceResponsePackage.from_text(item.item_id, response)
                response = await evaluate_voice(item, package, use_llm=False)
            state, _graded = orchestrator.record_response(state, item, response)
            state = await orchestrator.after_response(
                state, item, use_llm=False, rng=rng
            )
            state = orchestrator.ensure_presenting(state)

        stop, reason = orchestrator.should_stop(state)
        return orchestrator.summarise(state, reason).model_dump()

    return asyncio.run(run())


@pytest.fixture()
def services_report(monkeypatch, tmp_path):
    """The same assessment, through bank-registry, grader, competency-graph and the API."""
    from fastapi.testclient import TestClient

    from adaptive_clients import BankRegistryClient, CompetencyGraphClient
    from adaptive_clients.engine import HttpGrader, HttpGraphSource, HttpUnifiedBank
    from app.services.orchestrator import registry

    stub_sandbox_and_model(monkeypatch)
    bank = registry.get_bank(BANK)

    with bank_store_at(tmp_path / "banks"):
        with load_services(
            "bank-registry", "grader", "competency-graph", "assessment-orchestrator"
        ) as modules:
            bank_http = asgi_client(modules["bank-registry"].app)
            modules["grader"]._items = modules["grader"].ItemSource(
                BankRegistryClient(http=bank_http)
            )
            modules["competency-graph"]._graphs = modules[
                "competency-graph"
            ].HttpGraphSource(BankRegistryClient(http=bank_http))

            eng = modules["assessment-orchestrator"].engine
            eng._bank_client = BankRegistryClient(http=bank_http)
            eng._graph_source = HttpGraphSource(eng._bank_client)
            eng.reset()

            def build(bank_id: str, version: str):
                from app.services.orchestrator.orchestrator import Orchestrator

                key = (bank_id, version)
                if key in eng._orchestrators:
                    return eng._orchestrators[key]
                remote_bank = HttpUnifiedBank(eng._bank_client, bank_id)
                remote_bank.refresh()
                built = Orchestrator(
                    remote_bank,
                    HttpGrader(
                        http=asgi_client(modules["grader"].app), bank_id=bank_id
                    ),
                    graph=eng._graph_source.service(bank_id),
                    coverage_critical_only=eng._bank_client.bank(
                        bank_id
                    ).coverage_critical_only,
                    bank_id=bank_id,
                    propagation=CompetencyGraphClient(
                        http=asgi_client(modules["competency-graph"].app),
                        bank_id=bank_id,
                    ),
                )
                eng._orchestrators[key] = built
                return built

            monkeypatch.setattr(eng, "orchestrator_for", build)

            with TestClient(modules["assessment-orchestrator"].app) as api:
                session = api.post(
                    "/assessments",
                    json={"bank_id": BANK, "use_llm": False, "seed": SEED},
                ).json()

                for _ in range(MAXIMUM_ITEMS):
                    if session["stop"]:
                        break
                    presenting = session["presenting"]["item"]
                    item = bank.get(presenting["item_id"])
                    response = answer_for(item, bank)
                    body: dict = {"type": item.modality, "use_llm": False}
                    if item.modality == "mcq":
                        body["chosen_index"] = response
                    elif item.modality == "code":
                        body["code"] = response
                    else:
                        body["transcript"] = response
                    session = api.post(
                        f"/assessments/{session['session_id']}/responses", json=body
                    ).json()

                assert session["stop"], "the services run never stopped"
                return session["report"]


class TestOneAssessmentRunBothWays:
    def test_the_two_reports_are_identical(self, in_process_report, services_report):
        """Field for field, competency for competency.

        Not "close enough". A posterior is a 41-point grid of floats and the update is
        arithmetic; if the two paths agree at all they agree exactly, and a tolerance here
        would hide the one class of bug this test exists to find.
        """
        assert comparable(services_report) == comparable(in_process_report)

    def test_the_same_items_were_administered_in_the_same_order(
        self, in_process_report, services_report
    ):
        """Stated separately because it is the first thing to look at when the above fails.

        Selection diverging is a different bug from the posterior diverging, and the
        difference decides where to look — a shortlist that ranked differently, or an
        update that arrived differently.
        """
        assert list(services_report["seconds_by_item"]) == list(
            in_process_report["seconds_by_item"]
        )

    def test_every_competency_reaches_the_same_estimate_and_stop(
        self, in_process_report, services_report
    ):
        """The measurement itself, spelled out — so a failure names theta, the standard
        error or the reason, rather than pointing at a dict."""
        expected = {v["variable"]: v for v in in_process_report["variables"]}
        actual = {v["variable"]: v for v in services_report["variables"]}
        assert set(actual) == set(expected)
        for variable, want in expected.items():
            got = actual[variable]
            assert got["theta_hat"] == want["theta_hat"], variable
            assert got["standard_error"] == want["standard_error"], variable
            assert got["level"] == want["level"], variable
            assert got["band"] == want["band"], variable
            assert got["stop_reason"] == want["stop_reason"], variable
            assert got["observations"] == want["observations"], variable
            assert got["converged"] == want["converged"], variable

    def test_the_graph_reached_the_same_conclusions(
        self, in_process_report, services_report
    ):
        """Propagation ran in this process for one and over a wire for the other.

        Node status, mastery, the evidence ledger and the unvalidated-edge forecast, all
        the way down.
        """
        assert comparable(services_report)["graph"] == comparable(in_process_report)[
            "graph"
        ]
