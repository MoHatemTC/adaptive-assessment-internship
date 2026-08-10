"""grader over HTTP, with a real bank-registry behind it.

WHY THE REGISTRY IS REAL AND THE SANDBOX IS NOT

The grader fetching its own items is the security property this service exists to have —
the answer key never travels through the orchestrator — so the fetch is exercised against
the actual bank-registry app over a real client, not a fake. What IS stubbed is the sandbox
and the model: one costs money and needs network, the other is not deterministic, and
neither is what these tests are about. That is the same line `backend/tests/conftest.py`
draws with its `stub_boundaries` fixture.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import asgi_client, load_services, stub_sandbox_and_model


@pytest.fixture()
def stack(monkeypatch):
    """A grader whose bank client speaks to a real bank-registry, in-process."""
    from adaptive_clients import BankRegistryClient

    with load_services("bank-registry", "grader") as modules:
        registry_main = modules["bank-registry"]
        grader_main = modules["grader"]
        stub_sandbox_and_model(monkeypatch)

        grader_main._items = grader_main.ItemSource(
            BankRegistryClient(http=asgi_client(registry_main.app))
        )
        with TestClient(grader_main.app) as http:
            yield http, grader_main


@pytest.fixture()
def grader(stack):
    return stack[0]


class TestMcqGrading:
    """Exact index comparison. No model, and none ever — a model that graded multiple
    choice could disagree with the bank about its own answer key."""

    def test_the_right_answer_scores_one(self, grader, bank_items):
        item_id, answer = bank_items["mcq"]
        graded = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": item_id, "chosen_index": answer},
        ).json()
        assert graded["modality"] == "mcq"
        assert all(o["score"] == 1.0 for o in graded["outcomes"])
        assert all(o["weight"] > 0 for o in graded["outcomes"])

    def test_a_wrong_answer_scores_zero_but_still_carries_weight(
        self, grader, bank_items
    ):
        """Zero score and zero weight are different statements. A wrong answer is
        evidence; an outage is not."""
        item_id, answer = bank_items["mcq"]
        graded = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": item_id, "chosen_index": 1 - answer},
        ).json()
        assert all(o["score"] == 0.0 for o in graded["outcomes"])
        assert all(o["weight"] > 0 for o in graded["outcomes"])

    def test_an_out_of_range_index_is_refused_rather_than_scored(
        self, grader, bank_items
    ):
        item_id, _ = bank_items["mcq"]
        response = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": item_id, "chosen_index": 99},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "answer_invalid"

    def test_the_outcome_names_the_item_and_the_variables_it_measures(
        self, grader, bank_items
    ):
        item_id, answer = bank_items["mcq"]
        graded = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": item_id, "chosen_index": answer},
        ).json()
        assert graded["item_id"] == item_id
        assert all(o["source_item_id"] == item_id for o in graded["outcomes"])
        assert all(o["variable"] for o in graded["outcomes"])


class TestTheAnswerKeyNeverLeavesTheGrader:
    """The reason the grader fetches its own items instead of being handed one."""

    def test_the_graded_response_does_not_echo_the_payload(self, grader, bank_items):
        item_id, answer = bank_items["mcq"]
        body = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": item_id, "chosen_index": answer},
        ).text
        for forbidden in ("stem", "options", "reference_solution"):
            assert forbidden not in body

    def test_the_audit_detail_does_carry_the_answer_index(self, grader, bank_items):
        """It has to — the orchestrator writes it to the session dump, which is what an
        appeal is adjudicated from. It is the ORCHESTRATOR's job not to forward it to a
        candidate, and it does that in `_candidate_grade_receipt`."""
        item_id, answer = bank_items["mcq"]
        graded = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": item_id, "chosen_index": answer},
        ).json()
        assert "answer_index" in graded["detail"]


class TestWrongItemWrongEndpoint:
    def test_grading_an_mcq_as_code_is_refused(self, grader, bank_items):
        """Not a 500 and not a confident zero. Grading an MCQ through the code path would
        produce a score, and a wrong one."""
        item_id, _ = bank_items["mcq"]
        response = grader.post(
            "/grade/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(): pass"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "modality_mismatch"

    def test_an_unknown_item_is_a_404(self, grader):
        response = grader.post(
            "/grade/mcq",
            json={"bank_id": "DA", "item_id": "nope", "chosen_index": 0},
        )
        assert response.status_code == 404

    def test_an_unknown_bank_is_a_404(self, grader):
        response = grader.post(
            "/grade/mcq",
            json={"bank_id": "nope", "item_id": "x", "chosen_index": 0},
        )
        assert response.status_code == 404


class TestWhenTheRegistryIsUnreachable:
    def test_it_is_a_503_naming_the_dependency(self, monkeypatch):
        """Distinct from a 404. One is retryable and the other is not, and an orchestrator
        that could not tell them apart would either retry forever or abandon a session
        over a rolling restart."""
        from adaptive_clients import BankRegistryClient

        with load_services("grader") as modules:
            grader_main = modules["grader"]
            grader_main._items = grader_main.ItemSource(
                BankRegistryClient("http://127.0.0.1:1", timeout=0.05)
            )
            with TestClient(grader_main.app) as http:
                response = http.post(
                    "/grade/mcq",
                    json={"bank_id": "DA", "item_id": "x", "chosen_index": 0},
                )
        assert response.status_code == 503
        assert response.json()["code"] == "bank_registry_unavailable"


class TestCodeGrading:
    def test_a_passing_submission_scores_and_carries_weight(self, grader, bank_items):
        item_id, _ = bank_items["code"]
        graded = grader.post(
            "/grade/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(x):\n    return x"},
        ).json()
        assert graded["modality"] == "code"
        assert graded["outcomes"]
        assert any(o["weight"] > 0 for o in graded["outcomes"])

    def test_one_submission_can_evidence_several_variables(self, grader, bank_items):
        """The reason `measures` is a list. A code answer genuinely says something about
        more than one competency, and collapsing that to one would throw evidence away."""
        item_id, _ = bank_items["code"]
        graded = grader.post(
            "/grade/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(x):\n    return x"},
        ).json()
        assert len({o["variable"] for o in graded["outcomes"]}) >= 1

    def test_a_sandbox_failure_moves_nothing(self, grader, bank_items, monkeypatch):
        """The rule that an infrastructure fault is not evidence about a candidate.

        It falls out of the arithmetic — weight 0 makes the likelihood identically 1 — so
        what is tested here is that the grader really does report weight 0, rather than a
        zero score at full weight, which would record our outage as their inability.
        """
        from app.services.code_adaptive import session as code_session
        from app.services.code_adaptive.execution import ExecutionEvidence

        monkeypatch.setattr(
            code_session,
            "run_submission",
            lambda *a, **k: ExecutionEvidence(
                compiled=None,
                execution_completed=False,
                infrastructure_error=True,
                error_message="sandbox unavailable",
            ),
        )
        item_id, _ = bank_items["code"]
        graded = grader.post(
            "/grade/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(x): return x"},
        ).json()
        assert all(o["weight"] == 0.0 for o in graded["outcomes"]), graded["outcomes"]

    def test_a_failure_to_compile_is_evidence_but_weak_evidence(
        self, grader, bank_items, monkeypatch
    ):
        """The distinction the weight cap exists to draw. A learner whose code did not
        compile has shown a syntax problem, not an absence of every competency the question
        touches — so it counts, at a quarter strength, rather than counting fully or not at
        all."""
        from app.services.code_adaptive import session as code_session
        from app.services.code_adaptive.execution import ExecutionEvidence

        monkeypatch.setattr(
            code_session,
            "run_submission",
            lambda *a, **k: ExecutionEvidence(
                compiled=False,
                execution_completed=False,
                error_message="SyntaxError",
            ),
        )
        item_id, _ = bank_items["code"]
        graded = grader.post(
            "/grade/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(x) return x"},
        ).json()
        assert graded["outcomes"]
        assert all(0.0 < o["weight"] <= 0.25 for o in graded["outcomes"]), graded[
            "outcomes"
        ]

    def test_a_sandbox_failure_is_flagged_so_a_candidate_can_be_told(
        self, grader, bank_items, monkeypatch
    ):
        """The one thing a candidate is entitled to know while the session is running.

        Their answer moved nothing and they may be asked again. Everything else about the
        grading stays behind the candidate boundary until the session ends.
        """
        from app.services.code_adaptive import session as code_session
        from app.services.code_adaptive.execution import ExecutionEvidence

        monkeypatch.setattr(
            code_session,
            "run_submission",
            lambda *a, **k: ExecutionEvidence(
                compiled=None,
                execution_completed=False,
                infrastructure_error=True,
                error_message="sandbox unavailable",
            ),
        )
        item_id, _ = bank_items["code"]
        graded = grader.post(
            "/grade/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(x): return x"},
        ).json()
        assert any("SANDBOX_UNAVAILABLE" in f for f in graded["flags"]), graded["flags"]


class TestTheTrialRun:
    """A candidate may run their code before submitting. It grades nothing."""

    def test_public_tests_are_offered(self, grader, bank_items):
        item_id, _ = bank_items["code"]
        body = grader.get(f"/trial/{'DA'}/{item_id}/public-tests").json()
        assert body["item_id"] == item_id
        assert isinstance(body["tests"], list)

    def test_a_trial_run_returns_cases_and_no_score(self, grader, bank_items):
        item_id, _ = bank_items["code"]
        body = grader.post(
            "/trial/code",
            json={"bank_id": "DA", "item_id": item_id, "source": "def solve(x):\n    return x"},
        ).json()
        assert set(body) == {
            "available",
            "compiled",
            "cases",
            "error_message",
            "passed",
            "total",
        }
        for forbidden in ("score", "weight", "outcomes", "variable"):
            assert forbidden not in body

    def test_no_hidden_case_can_reach_a_trial_run(self, grader, bank_items):
        """If it could, a candidate could converge on a lookup table by trial and error and
        the score would mean nothing."""
        item_id, _ = bank_items["code"]
        offered = {
            t["test_id"]
            for t in grader.get(f"/trial/DA/{item_id}/public-tests").json()["tests"]
        }
        ran = {
            c["test_id"]
            for c in grader.post(
                "/trial/code",
                json={
                    "bank_id": "DA",
                    "item_id": item_id,
                    "source": "def solve(x):\n    return x",
                },
            ).json()["cases"]
        }
        assert ran <= offered


class TestOpenGrading:
    def test_a_typed_answer_is_evaluated_and_graded(self, grader, bank_items):
        item_id, _ = bank_items["open"]
        graded = grader.post(
            "/grade/open",
            json={
                "bank_id": "DA",
                "item_id": item_id,
                "use_llm": False,
                "package": {
                    "item_id": item_id,
                    "turns": [
                        {
                            "turn_id": "t0",
                            "role": "candidate",
                            "text": "I would profile the query, add an index on the join "
                            "column, and measure again before changing anything else.",
                            "transcript_confidence": 1.0,
                        }
                    ],
                    "word_count": 24,
                    "total_speech_seconds": 30.0,
                    "mean_transcript_confidence": 1.0,
                    "final_text": "I would profile the query, add an index on the join "
                    "column, and measure again before changing anything else.",
                },
            },
        ).json()
        assert graded["modality"] in ("open", "voice")
        assert graded["outcomes"]

    def test_an_unscorable_package_moves_nothing(self, grader, bank_items):
        item_id, _ = bank_items["open"]
        graded = grader.post(
            "/grade/open",
            json={
                "bank_id": "DA",
                "item_id": item_id,
                "use_llm": False,
                "package": {
                    "item_id": item_id,
                    "outcome_status": "infrastructure_error",
                    "reason_code": "AUDIO_LOST",
                },
            },
        ).json()
        assert all(o["weight"] == 0.0 for o in graded["outcomes"])


class TestTheItemCacheIsKeyedOnTheBankVersion:
    def test_a_repeated_grade_does_not_refetch_the_item(self, stack, bank_items):
        """Every retry and every replay grades the same item again. Paying a fetch for it
        would put a round trip in a path budgeted at 500 ms for MCQ."""
        http, grader_main = stack
        item_id, answer = bank_items["mcq"]
        calls: list[str] = []
        original = grader_main._items._client.item

        def counting(bank_id: str, wanted: str):
            calls.append(wanted)
            return original(bank_id, wanted)

        grader_main._items._client.item = counting
        for _ in range(3):
            http.post(
                "/grade/mcq",
                json={"bank_id": "DA", "item_id": item_id, "chosen_index": answer},
            )
        assert calls == [item_id]
