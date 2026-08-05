"""G-01..G-07 as DeepEval metrics, plus the deterministic pre-filters that come first.

SCOPE DISCIPLINE (section 6.1). An LLM judge evaluates subjective quality only. Anything
expressible as a field check is a Tier-1 test, and asking a judge to do it converts a
deterministic assertion into a probabilistic one — strictly worse, at a cost per call.

So each metric here that CAN be partly checked deterministically is, and the judge only
sees what is left. G-01's characteristic failure is a report citing an item that was never
served or a node with no evidence; both are set operations. A judge that agrees 95% of the
time is worse than a set operation that agrees always.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Pinned by version, not by range. DeepEval has moved its parameter enum
#: (`LLMTestCaseParams` -> `SingleTurnParams`) between releases, and a metric that silently
#: changes shape between runs makes two runs incomparable without saying so.
DEEPEVAL_PIN = "4.1.5"

#: The judge is pinned by fingerprint, not alias. "the latest model" is not a
#: reproducible instrument, and section 6.4's canary set exists to detect drift in it.
JUDGE_MODEL_ENV = "EVAL_JUDGE_MODEL"


@dataclass(frozen=True)
class DeterministicVerdict:
    """What can be decided without a model. Runs before any judged metric."""

    passed: bool
    failures: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"passed": self.passed, "failures": list(self.failures)}


def g01_prefilter(report_text: str, evidence: dict) -> DeterministicVerdict:
    """G-01's deterministic half: does the report cite anything that did not happen?

    `evidence` carries `served_item_ids` and the DIRECT/INFERRED node labels from the
    session record. Two failures are decidable here and neither needs a judge:

      - an item id in the prose that was never administered;
      - a competency claimed as demonstrated whose evidence is INFERRED, where the report
        does not mark it inferred.

    The second is propagation's characteristic failure — a deduction presented as an
    observation — and it is the reason this metric exists at all.
    """
    failures: list[str] = []
    served = set(evidence.get("served_item_ids") or [])
    for token in evidence.get("cited_item_ids") or []:
        if token not in served:
            failures.append(f"cites item {token!r}, which was never administered")

    inferred = set(evidence.get("inferred_nodes") or [])
    labelled = set(evidence.get("nodes_labelled_inferred") or [])
    for node in sorted(inferred - labelled):
        if node in (evidence.get("nodes_claimed_demonstrated") or []):
            failures.append(
                f"claims {node} as demonstrated; its evidence is INFERRED and unlabelled"
            )
    return DeterministicVerdict(passed=not failures, failures=tuple(failures))


def build_metrics(judge_model):
    """The judged metrics. Imported lazily so the module is importable without deepeval."""
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCaseParams

    evidence_integrity = GEval(
        name="EvidenceIntegrity",
        criteria=(
            "Determine whether the candidate report claims any competency was "
            "demonstrated when the underlying evidence for it was inferred rather than "
            "directly observed."
        ),
        evaluation_steps=[
            "Read 'context', which lists every sub-competency with a DIRECT or INFERRED "
            "evidence label taken from the session record.",
            "For each competency claim in 'actual_output', identify whether its "
            "supporting evidence is DIRECT, INFERRED, or absent.",
            "Heavily penalise any claim of demonstrated skill whose evidence is INFERRED "
            "but which the report does not mark as inferred.",
            "Heavily penalise any claim whose evidence is absent entirely.",
            "Do not penalise hedged language, brevity, or tone.",
            "A report that labels every inferred claim as inferred scores 1.0 regardless "
            "of how many inferred claims it contains.",
        ],
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.CONTEXT],
        threshold=0.95,
        strict_mode=True,
        model=judge_model,
    )

    blocked_language = GEval(
        name="BlockedNotFailed",
        criteria=(
            "Determine whether the report describes competencies that were BLOCKED as "
            "untested, rather than as failed, weak, or deficient."
        ),
        evaluation_steps=[
            "Find every competency the context marks as BLOCKED.",
            "For each, read how 'actual_output' describes it.",
            "Penalise any description implying the candidate lacks the skill.",
            "Reward descriptions saying it was not assessed, or not reached.",
            "Ignore tone and length.",
        ],
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.CONTEXT],
        threshold=0.90,
        model=judge_model,
    )

    contestability = GEval(
        name="InferenceContestability",
        criteria=(
            "Determine whether a candidate reading this report could identify which "
            "specific answer led to an inferred claim, and contest it."
        ),
        evaluation_steps=[
            "Find every claim the context marks as INFERRED.",
            "Check whether 'actual_output' names the observation the inference came from.",
            "Reward reports that state the source competency and the reasoning.",
            "Penalise inferred claims presented with no traceable origin.",
        ],
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.CONTEXT],
        threshold=0.80,
        model=judge_model,
    )

    return {
        # The only metric with a hard threshold, because propagation's characteristic
        # failure is a report presenting a deduction as an observation.
        "G-01_evidence_integrity": evidence_integrity,
        "G-02_blocked_not_failed": blocked_language,
        "G-04_inference_contestability": contestability,
    }
