"""competency-graph over HTTP, and the claim that it changed nothing.

THE TEST THAT MATTERS HERE IS THE LAST ONE

Everything else checks a route. `TestTheServiceAndTheInProcessPathAgree` runs the same
evidence through the engine directly and through the service, and asserts the resulting
state updates are identical — field for field, node for node. If propagation over a wire
and propagation in a process ever disagree, every session run against the service is
measuring something subtly different from every session in the evaluation harness, and no
route test would notice.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import asgi_client, load_services


@pytest.fixture()
def stack():
    """competency-graph with a real bank-registry behind it."""
    from adaptive_clients import BankRegistryClient

    with load_services("bank-registry", "competency-graph") as modules:
        registry_main = modules["bank-registry"]
        graph_main = modules["competency-graph"]
        graph_main._graphs = graph_main.HttpGraphSource(
            BankRegistryClient(http=asgi_client(registry_main.app))
        )
        with TestClient(graph_main.app) as http:
            yield http, graph_main


@pytest.fixture()
def graph_service(stack):
    return stack[0]


def da_nodes() -> list[str]:
    from cat_engine.engine.services.orchestrator import registry

    graph = registry.get_graph_service("DA")
    return sorted(
        nid for nid, node in graph.graph.nodes.items() if node.node_type != "main"
    )


def propagate_payload(node: str, *, score: float = 1.0, session: str = "s1") -> dict:
    return {
        "bank_id": "DA",
        "session_id": session,
        "item": {"item_id": "q1", "modality": "mcq"},
        "outcomes": [
            {
                "variable": node,
                "score": score,
                "weight": 1.0,
                "confidence": 1.0,
                "modality": "mcq",
            }
        ],
        "graph_state": {},
        "session_variables": ["DA"],
        "attempt_no": 1,
    }


class TestPropagation:
    def test_a_direct_success_marks_the_node_measured_and_mastered(self, graph_service):
        node = da_nodes()[0]
        body = graph_service.post("/propagate", json=propagate_payload(node)).json()
        assert body["applied"] is True
        assert node in body["state_update"]["graph_direct_measured_nodes"]
        assert node in body["state_update"]["graph_direct_mastered_nodes"]

    def test_a_direct_failure_is_measured_but_not_mastered(self, graph_service):
        """Coverage asks whether a node was MEASURED. Mastery is a separate question, and
        conflating them would let a competency go uncovered because the candidate got it
        wrong."""
        node = da_nodes()[0]
        body = graph_service.post(
            "/propagate", json=propagate_payload(node, score=0.0)
        ).json()
        update = body["state_update"]
        assert node in update["graph_direct_measured_nodes"]
        assert node in update["graph_direct_not_mastered_nodes"]
        assert node not in update["graph_direct_mastered_nodes"]

    def test_replaying_the_same_response_changes_nothing(self, graph_service):
        """Evidence ids are deterministic, which is what makes a retry safe. Without it a
        network retry would count one answer twice, and the damage would land on the
        standard error — the thing the assessment stops on."""
        node = da_nodes()[0]
        first = graph_service.post("/propagate", json=propagate_payload(node)).json()

        replay = propagate_payload(node)
        replay["graph_state"] = first["state_update"]
        second = graph_service.post("/propagate", json=replay).json()

        assert second["state_update"]["graph_node_states"] == first["state_update"][
            "graph_node_states"
        ]
        assert (
            second["state_update"]["graph_processed_evidence_ids"]
            == first["state_update"]["graph_processed_evidence_ids"]
        )

    def test_two_outcomes_for_one_variable_are_refused(self, graph_service):
        """They would collide on the evidence id and the second would be dropped as a
        duplicate. The grader aggregates per competency before this point; a silent drop
        would make a grader bug look like a propagation one."""
        node = da_nodes()[0]
        payload = propagate_payload(node)
        payload["outcomes"].append(dict(payload["outcomes"][0]))
        response = graph_service.post("/propagate", json=payload)
        assert response.status_code == 422
        assert response.json()["code"] == "outcomes_invalid"

    def test_an_unknown_bank_is_a_404(self, graph_service):
        payload = propagate_payload(da_nodes()[0])
        payload["bank_id"] = "nope"
        assert graph_service.post("/propagate", json=payload).status_code == 404


class TestNothingComingBackCanMoveAPosterior:
    """The invariant the whole decomposition rests on, checked on the wire."""

    def test_the_response_carries_no_score_and_no_weight_at_the_top_level(
        self, graph_service
    ):
        body = graph_service.post("/propagate", json=propagate_payload(da_nodes()[0]))
        payload = body.json()
        assert "score" not in payload
        assert "weight" not in payload

    def test_inferred_signals_have_no_score_or_weight_field(self, graph_service):
        payload = graph_service.post(
            "/propagate", json=propagate_payload(da_nodes()[0])
        ).json()
        for signal in payload["inferred_signals"]:
            assert "score" not in signal
            assert "weight" not in signal

    def test_propagation_ships_inert_so_nothing_is_inferred_at_all(self, graph_service):
        """On this deployment every prerequisite edge is unvalidated and therefore inert.
        A completed screening study measured the wrong-inference rate at 7-17% against a
        3% gate, which is why. If this ever returns a signal, an edge has been enabled and
        that is a release decision with its own evidence requirement."""
        payload = graph_service.post(
            "/propagate", json=propagate_payload(da_nodes()[0])
        ).json()
        assert payload["inferred_signals"] == []
        assert payload["state_update"]["graph_inferred_mastered_nodes"] == []


class TestCoverage:
    def test_it_reports_what_is_still_unmeasured(self, graph_service):
        body = graph_service.post(
            "/coverage",
            json={"bank_id": "DA", "main": "DA", "directly_measured": [], "critical_only": False},
        ).json()
        assert body["required"]
        assert body["unmeasured"] == body["required"]
        assert body["satisfied"] is False

    def test_measuring_every_required_node_satisfies_it(self, graph_service):
        required = graph_service.post(
            "/coverage",
            json={"bank_id": "DA", "main": "DA", "directly_measured": [], "critical_only": False},
        ).json()["required"]
        body = graph_service.post(
            "/coverage",
            json={
                "bank_id": "DA",
                "main": "DA",
                "directly_measured": required,
                "critical_only": False,
            },
        ).json()
        assert body["unmeasured"] == []
        assert body["satisfied"] is True


class TestTheManifest:
    def test_it_names_the_configuration_in_force(self, graph_service):
        body = graph_service.get("/manifest/DA").json()
        assert body["bank_id"] == "DA"
        assert body["manifest_hash"]
        assert body["manifest"]

    def test_it_is_stable_across_calls(self, graph_service):
        first = graph_service.get("/manifest/DA").json()["manifest_hash"]
        assert graph_service.get("/manifest/DA").json()["manifest_hash"] == first


class TestTheServiceAndTheInProcessPathAgree:
    """The claim the whole seam rests on, tested rather than asserted.

    If propagation over a wire and propagation in a process disagree, every session run
    against the service measures something subtly different from every session in the
    evaluation harness — and no route test above would notice.
    """

    def _in_process(self, node: str, score: float) -> dict:
        from cat_engine.engine.schemas.orchestration import AssessmentState, BankItem, GradedResponse
        from cat_engine.engine.services.orchestrator import registry
        from cat_engine.engine.services.orchestrator.propagation_port import propagate

        state = AssessmentState.model_validate({"session_id": "s1"})
        item = BankItem.model_validate(
            {
                "item_id": "q1",
                "modality": "mcq",
                "measures": [{"variable": node, "weight": 1.0}],
                "cat": {"a": 1.0, "b": 0.0, "c": 0.0},
                "mcq": {},
            }
        )
        graded = GradedResponse(
            item_id="q1",
            modality="mcq",
            outcomes=[
                {
                    "variable": node,
                    "score": score,
                    "weight": 1.0,
                    "confidence": 1.0,
                    "source_item_id": "q1",
                    "modality": "mcq",
                }
            ],
        )
        result = propagate(
            graph=registry.get_graph_service("DA"),
            state=state,
            item=item,
            graded=graded,
            attempt_no=1,
            minimum_failures_to_block=None,
            session_variables={"DA"},
        )
        return result.state_update

    @staticmethod
    def _comparable(update: dict) -> dict:
        """Everything except the timestamps, which are wall-clock by construction."""

        def strip(node_state: dict) -> dict:
            return {
                k: v
                for k, v in node_state.items()
                if not k.endswith("_at") and k != "last_updated"
            }

        return {
            **{k: v for k, v in update.items() if k != "graph_node_states"},
            "graph_node_states": {
                nid: strip(value)
                for nid, value in (update.get("graph_node_states") or {}).items()
            },
        }

    @pytest.mark.parametrize("score", [1.0, 0.0, 0.5])
    def test_the_two_paths_produce_identical_state_updates(self, graph_service, score):
        node = da_nodes()[0]
        over_http = graph_service.post(
            "/propagate", json=propagate_payload(node, score=score)
        ).json()["state_update"]
        in_process = self._in_process(node, score)
        assert self._comparable(over_http) == self._comparable(in_process)

    def test_they_agree_on_every_node_in_the_bank(self, graph_service):
        """Not one node — all of them. A disagreement that only shows up on a
        context-specific or shared node is exactly the kind that survives a smoke test."""
        for node in da_nodes():
            over_http = graph_service.post(
                "/propagate", json=propagate_payload(node)
            ).json()["state_update"]
            assert self._comparable(over_http) == self._comparable(
                self._in_process(node, 1.0)
            ), node
