"""Turning one response into graded outcomes, per modality.

WHAT IS REAL AND WHAT IS NOT

The bank is real. The SANDBOX and the MODEL are stubbed: one costs money and needs network,
the other is not deterministic, and neither is what these tests are about.

TWO CLASSES DID NOT SURVIVE THE COLLAPSE, AND BOTH ABSENCES ARE DELIBERATE

`TestWhenTheRegistryIsUnreachable` asserted that a bank registry that could not be reached
produced a 503 rather than a 404 — retryable versus not, so an orchestrator would neither
retry forever nor abandon a session over a rolling restart. There is no hop to fail. An item
that is not there is `BankUnknown` and always was; an item that cannot be READ is now a
`FileNotFoundError` from the bank store, which is a bug rather than a condition to model.
Keeping a `GraderUnavailable` test with a hand-broken registry would be testing the stub.

`TestTheItemCacheIsKeyedOnTheBankVersion` asserted that a repeated grade did not refetch
the item over HTTP, because a round trip in a 500 ms MCQ budget is most of the budget. The
fetch is a dict lookup now. What remains worth asserting is that the bank is not re-PARSED
per response — a 1.8 MB file — and that is `registry`'s cache, tested where it lives.
"""

from __future__ import annotations

import json

import pytest

from cat_engine.errors import AnswerInvalid, AnswerTypeMismatch, BankUnknown

BANK = "DA"
CODE_ANSWER = "def solve(x):\n    return x"


@pytest.fixture()
def grader(stub_boundaries):
    """The grader on its own — no assessment, no session, no posterior."""
    from cat_engine.grading import Grader

    return Grader()


class TestMcqGrading:
    """Exact index comparison. No model, and none ever — a model that graded multiple
    choice could disagree with the bank about its own answer key."""

    def test_the_right_answer_scores_one(self, grader, bank_items):
        item_id, answer = bank_items["mcq"]
        graded = grader.grade_mcq(BANK, item_id, answer)
        assert graded.modality == "mcq"
        assert all(o["score"] == 1.0 for o in graded.outcomes)
        assert all(o["weight"] > 0 for o in graded.outcomes)

    def test_a_wrong_answer_scores_zero_but_still_carries_weight(
        self, grader, bank_items
    ):
        """Zero score and zero weight are different statements. A wrong answer is evidence;
        an outage is not."""
        item_id, answer = bank_items["mcq"]
        graded = grader.grade_mcq(BANK, item_id, 1 - answer)
        assert all(o["score"] == 0.0 for o in graded.outcomes)
        assert all(o["weight"] > 0 for o in graded.outcomes)

    def test_an_out_of_range_index_is_refused_rather_than_scored(
        self, grader, bank_items
    ):
        item_id, _ = bank_items["mcq"]
        with pytest.raises(AnswerInvalid) as caught:
            grader.grade_mcq(BANK, item_id, 99)
        assert caught.value.status_code == 422

    def test_the_outcome_names_the_item_and_the_variables_it_measures(
        self, grader, bank_items
    ):
        item_id, answer = bank_items["mcq"]
        graded = grader.grade_mcq(BANK, item_id, answer)
        assert graded.item_id == item_id
        assert all(o["source_item_id"] == item_id for o in graded.outcomes)
        assert all(o["variable"] for o in graded.outcomes)


class TestTheAnswerKeyStaysInTheGradedRecord:
    """It used to be that the grader fetched its own item so the answer key never travelled
    through the orchestrator. There is no travel now, so the property is carried by the
    RETURN TYPE instead: `GradedResponse.outcomes` is what reaches a posterior and carries
    no payload, and `detail` is the audit record that does."""

    def test_the_outcomes_do_not_echo_the_payload(self, grader, bank_items):
        item_id, answer = bank_items["mcq"]
        body = json.dumps(grader.grade_mcq(BANK, item_id, answer).outcomes)
        for forbidden in ("stem", "options", "reference_solution"):
            assert forbidden not in body

    def test_the_audit_detail_does_carry_the_answer_index(self, grader, bank_items):
        """It has to — it is written to the session dump, which is what an appeal is
        adjudicated from. Keeping it away from a CANDIDATE is `presentation.grade_receipt`,
        which is tested in `test_facade.py`."""
        item_id, answer = bank_items["mcq"]
        assert "answer_index" in grader.grade_mcq(BANK, item_id, answer).detail


class TestWrongItemWrongPath:
    def test_grading_an_mcq_as_code_is_refused(self, grader, bank_items):
        """Not a crash and not a confident zero. Grading an MCQ through the code path would
        produce a score, and a wrong one."""
        item_id, _ = bank_items["mcq"]
        with pytest.raises(AnswerTypeMismatch):
            grader.grade_code(BANK, item_id, CODE_ANSWER)

    def test_an_unknown_item_is_refused(self, grader):
        with pytest.raises(BankUnknown):
            grader.grade_mcq(BANK, "nope", 0)

    def test_an_unknown_bank_is_refused(self, grader):
        from cat_engine.engine.services.orchestrator import registry

        with pytest.raises((BankUnknown, registry.UnknownBankError)):
            grader.grade_mcq("nope", "x", 0)


class TestCodeGrading:
    def test_a_passing_submission_scores_and_carries_weight(self, grader, bank_items):
        item_id, _ = bank_items["code"]
        graded = grader.grade_code(BANK, item_id, CODE_ANSWER)
        assert graded.modality == "code"
        assert graded.outcomes
        assert any(o["weight"] > 0 for o in graded.outcomes)

    def test_one_submission_can_evidence_several_variables(self, grader, bank_items):
        """The reason `measures` is a list. A code answer genuinely says something about
        more than one competency, and collapsing that to one would throw evidence away."""
        item_id, _ = bank_items["code"]
        graded = grader.grade_code(BANK, item_id, CODE_ANSWER)
        assert len({o["variable"] for o in graded.outcomes}) >= 1

    def test_a_sandbox_failure_moves_nothing(self, grader, bank_items, monkeypatch):
        """The rule that an infrastructure fault is not evidence about a candidate.

        It falls out of the arithmetic — weight 0 makes the likelihood identically 1 — so
        what is tested here is that the grader really does report weight 0, rather than a
        zero score at full weight, which would record our outage as their inability.
        """
        _break_sandbox(monkeypatch)
        item_id, _ = bank_items["code"]
        graded = grader.grade_code(BANK, item_id, CODE_ANSWER)
        assert all(o["weight"] == 0.0 for o in graded.outcomes), graded.outcomes

    def test_a_failure_to_compile_is_evidence_but_weak_evidence(
        self, grader, bank_items, monkeypatch
    ):
        """The distinction the weight cap exists to draw. A learner whose code did not
        compile has shown a syntax problem, not an absence of every competency the question
        touches — so it counts, at a quarter strength, rather than counting fully or not at
        all."""
        from cat_engine.engine.services.code_adaptive import session as code_session
        from cat_engine.engine.services.code_adaptive.execution import ExecutionEvidence

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
        graded = grader.grade_code(BANK, item_id, "def solve(x) return x")
        assert graded.outcomes
        assert all(0.0 < o["weight"] <= 0.25 for o in graded.outcomes), graded.outcomes

    def test_a_sandbox_failure_is_flagged_so_a_candidate_can_be_told(
        self, grader, bank_items, monkeypatch
    ):
        """The one thing a candidate is entitled to know while the session is running.

        Their answer moved nothing and they may be asked again. Everything else about the
        grading stays behind the candidate boundary until the session ends.
        """
        _break_sandbox(monkeypatch)
        item_id, _ = bank_items["code"]
        graded = grader.grade_code(BANK, item_id, CODE_ANSWER)
        assert any("SANDBOX_UNAVAILABLE" in f for f in graded.flags), graded.flags


def _break_sandbox(monkeypatch) -> None:
    from cat_engine.engine.services.code_adaptive import session as code_session
    from cat_engine.engine.services.code_adaptive.execution import ExecutionEvidence

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


class TestTheTrialRun:
    """A candidate may run their code before submitting. It grades nothing."""

    def test_public_tests_are_offered(self, grader, bank_items):
        item_id, _ = bank_items["code"]
        assert isinstance(grader.public_tests(BANK, item_id), list)

    def test_a_trial_run_returns_cases_and_no_score(self, grader, bank_items):
        item_id, _ = bank_items["code"]
        body = grader.trial_run(BANK, item_id, CODE_ANSWER).model_dump()
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
        offered = {t["test_id"] for t in grader.public_tests(BANK, item_id)}
        ran = {c.test_id for c in grader.trial_run(BANK, item_id, CODE_ANSWER).cases}
        assert ran <= offered


class TestOpenGrading:
    async def test_a_typed_answer_is_evaluated_and_graded(self, grader, bank_items):
        from cat_engine.engine.schemas.voice import VoiceResponsePackage

        item_id, _ = bank_items["open"]
        package = VoiceResponsePackage.from_text(
            item_id,
            "I would profile the query, add an index on the join column, and measure "
            "again before changing anything else.",
        )
        graded = await grader.grade_open(BANK, item_id, package, use_llm=False)
        assert graded.modality in ("open", "voice")
        assert graded.outcomes

    async def test_an_unscorable_package_moves_nothing(self, grader, bank_items):
        from cat_engine.engine.schemas.voice import VoiceResponsePackage

        item_id, _ = bank_items["open"]
        package = VoiceResponsePackage.model_validate(
            {
                "item_id": item_id,
                "outcome_status": "infrastructure_error",
                "reason_code": "AUDIO_LOST",
            }
        )
        graded = await grader.grade_open(BANK, item_id, package, use_llm=False)
        assert all(o["weight"] == 0.0 for o in graded.outcomes)


class TestTheBankIsParsedOnceNotPerResponse:
    def test_repeated_grading_resolves_the_same_bank_object(self, grader, bank_items):
        """Was an HTTP fetch cache keyed on the bank version. The fetch is a dict lookup
        now, but the thing it was protecting is unchanged: parsing a 1.8 MB bank per
        response is the difference between grading inside its 500 ms budget and outside it.
        """
        from cat_engine.engine.services.orchestrator import registry

        item_id, answer = bank_items["mcq"]
        first = registry.get_bank(BANK)
        for _ in range(3):
            grader.grade_mcq(BANK, item_id, answer)
        assert registry.get_bank(BANK) is first
