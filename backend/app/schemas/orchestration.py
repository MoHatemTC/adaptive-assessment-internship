"""The typed boundary of the orchestrated, multi-modality assessment.

One envelope for every item, whatever grades it. The envelope carries what the ENGINE
needs — which variables the item measures and its CAT parameters on theta — and the
modality payload carries what the GRADER needs. That split is what lets a new modality be
added without touching selection, and what stops selection from having to know what a test
case or an answer index is.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Modality = Literal["mcq", "code", "open", "voice"]

# Wall-clock an item is expected to consume when the bank does not say. Selection divides
# information by this, so it decides the modality mix and therefore session length.
#
# `code` and `open`/`voice` are medians measured over the AI Engineer bank (code 480 s,
# voice answer duration 240 s plus prompt-read, ASR settle and probe overhead). `mcq` has
# NO measurement behind it anywhere — no bank carries a time for a multiple-choice item —
# and it multiplies most of the items in a session. Treat it as a policy constant to be
# replaced by observed `AssessmentState.item_seconds` at the first pilot, not as a fact.
DEFAULT_SECONDS_BY_MODALITY: dict[str, float] = {
    "mcq": 75.0,
    "code": 480.0,
    "open": 300.0,
    "voice": 300.0,
}


class CatParameters(BaseModel):
    """Item parameters ON THETA, for every modality.

    The invariant the whole design rests on. MCQ items arrive calibrated; code items are
    mapped here by `orchestrator.calibration`, which is provisional. Bounds match the MCQ
    engine's Item schema so a mis-calibrated bank is caught at the door rather than
    producing silently meaningless information scores.
    """

    a: float = Field(gt=0.0, le=3.0, description="discrimination")
    b: float = Field(ge=-4.0, le=4.0, description="difficulty on the ability scale")
    c: float = Field(ge=0.0, lt=1.0, default=0.0, description="guessing floor")


class MeasuredVariable(BaseModel):
    """A variable this item measures, and how much of it the item carries.

    The architecture's "required variables". An MCQ item names one; a code question names
    several with weights, because one submission genuinely evidences several competencies.
    """

    variable: str
    weight: float = Field(gt=0.0, le=1.0, default=1.0)


class BankItem(BaseModel):
    """One item of any modality."""

    item_id: str
    modality: Modality
    status: str = "active"

    # Human-facing taxonomy, shared by both source banks already.
    competency: str = ""
    sub_competency: str = ""

    measures: list[MeasuredVariable] = Field(min_length=1)
    cat: CatParameters

    # How long this item is expected to take. On the envelope rather than the payload
    # because SELECTION reads it — information per minute is a ranking quantity, and the
    # engine is not allowed to open a modality payload.
    estimated_time_seconds: float | None = Field(default=None, gt=0.0)

    # The grader's own confidence floor for this item, if it is stricter than the global
    # one. Propagation takes the MOST RESTRICTIVE of the two: a per-item floor exists to
    # tighten, never to loosen. No bank sets it today.
    minimum_success_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    # Exactly one of these is populated, matching `modality`. Kept as open dicts because
    # the grader owns their shape: forcing them through a union here would make every new
    # modality a change to this file and to everything that imports it.
    mcq: dict[str, Any] | None = None
    code: dict[str, Any] | None = None
    open: dict[str, Any] | None = None
    # `voice` grades exactly like `open` — same evaluator, same rubric criteria. It is a
    # separate modality so a report can distinguish a spoken interview from a typed essay,
    # and so the live-interview path is selected by the item rather than by a UI toggle.
    voice: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _payload_matches_modality(self) -> "BankItem":
        payload = getattr(self, self.modality, None)
        if payload is None:
            raise ValueError(f"item {self.item_id}: modality {self.modality!r} has no payload")
        return self

    @property
    def payload(self) -> dict[str, Any]:
        return getattr(self, self.modality)

    @property
    def authored_seconds(self) -> float | None:
        """What the BANK says this item takes, or None when it says nothing.

        None rather than a default, so a per-modality figure measured from real sessions
        is not shadowed by a guess baked into the item. `selection_calibration` supplies
        the fallback, and knows whether it is measured or assumed.
        """
        if self.estimated_time_seconds is not None:
            return float(self.estimated_time_seconds)
        authored = (self.payload or {}).get("estimated_time_seconds")
        if authored is None:
            return None
        try:
            value = float(authored)
        except (TypeError, ValueError):
            return None
        return value if value > 0.0 else None

    @property
    def expected_seconds(self) -> float:
        """Wall clock to budget for this item. Never None: a missing time must not make
        an item look free to a criterion that divides by it."""
        authored = self.authored_seconds
        if authored is not None:
            return authored
        return DEFAULT_SECONDS_BY_MODALITY[self.modality]

    def loading(self, variable: str) -> float:
        """How much of this item measures `variable`. 0.0 when it does not at all.

        Accepts a main competency (T1) or a sub-competency (T1.1). For a main competency
        the loading is the strongest measure among its sub-competencies on this item.
        """
        if "." not in variable:
            prefix = f"{variable}."
            loadings = [
                entry.weight
                for entry in self.measures
                if entry.variable == variable or entry.variable.startswith(prefix)
            ]
            return max(loadings) if loadings else 0.0
        for entry in self.measures:
            if entry.variable == variable:
                return entry.weight
        return 0.0


class VariableState(BaseModel):
    """The live estimate of one variable — the architecture's Examinee Variables, per entry.

    The posterior is carried as a plain list so the state round-trips through JSON. A
    session may be persisted between requests, and summarising to (mean, sd) instead would
    be lossy: a 3PL posterior is genuinely skewed near the guessing floor and the selection
    criterion integrates over its real shape.
    """

    variable: str
    posterior: list[float]
    theta_hat: float = 0.0
    standard_error: float = 2.0
    observations: int = 0
    served_item_ids: list[str] = Field(default_factory=list)
    band_history: list[int] = Field(default_factory=list)
    # Retained for adaptive challenge selection. Fractional open/code outcomes are kept
    # as-is; this is evidence history, not a binary-correctness reconstruction.
    score_history: list[float] = Field(default_factory=list)

    # Set once a stopping rule fires. A finalised variable is never picked for again.
    finalised: bool = False
    stop_reason: str = ""
    converged: bool = False


class QueuedCandidate(BaseModel):
    """One item waiting in the queue, with why it was chosen."""

    variable: str
    item_id: str
    modality: Modality
    criterion: Literal["KL", "E[Fisher]"]
    # `information` is the criterion score, unchanged in meaning. `utility` is what the
    # shortlist was actually ordered by — information per minute, weighted by the evidence
    # a response in this modality typically carries. Keeping them separate is what lets
    # the audit record say what the engine valued rather than what it ranked on.
    information: float
    utility: float
    best_information: float
    # Defaulted so an existing construction site needs no change.
    best_utility: float = 0.0
    expected_weight: float = 1.0
    estimated_seconds: float = 0.0
    information_per_minute: float = 0.0
    normalized_regret: float
    engine_top_pick: str
    chosen_by_llm: bool
    reason_code: str = ""
    reason: str = ""
    shortlist_ids: list[str] = Field(default_factory=list)


class GradedResponse(BaseModel):
    """What the grader returns for one administered item, before it touches any estimate."""

    item_id: str
    modality: Modality
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # Modality-specific detail for the audit record: the code path carries a full
    # SubmissionResult, MCQ carries the chosen index.
    detail: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)


class AssessmentState(BaseModel):
    """Everything one orchestrated assessment needs to resume on a different worker."""

    session_id: str
    variables: dict[str, VariableState] = Field(default_factory=dict)
    queue: dict[str, QueuedCandidate] = Field(default_factory=dict)
    # The question currently shown to the candidate. While set, that competency's slot is
    # empty so the queue holds only other competencies' next questions.
    presenting: QueuedCandidate | None = None
    served_item_ids: list[str] = Field(default_factory=list)
    items_administered: int = 0
    started_at: float = 0.0
    elapsed_minutes: float = 0.0

    # --- graph-augmented CAT state ------------------------------------------
    # Persisted so eligibility decisions are stable across HTTP requests and so a resumed
    # session does not re-apply evidence it has already applied.
    #
    # `graph_node_states` is the source of truth: full node state, including how each node
    # reached its status. The flat lists below are derived projections, kept because the
    # DAG view and the eligibility filter read them directly.
    graph_node_states: dict[str, dict] = Field(default_factory=dict)
    graph_processed_evidence_ids: list[str] = Field(default_factory=list)
    graph_blocked_nodes: list[str] = Field(default_factory=list)
    # Any direct scorable result, including partial credit. This is the coverage gate;
    # mastery/not-mastery remain separate status classifications.
    graph_direct_measured_nodes: list[str] = Field(default_factory=list)
    graph_direct_mastered_nodes: list[str] = Field(default_factory=list)
    graph_direct_not_mastered_nodes: list[str] = Field(default_factory=list)
    # Separate from `graph_direct_mastered_nodes` ON PURPOSE. Inferred mastery used to be
    # written into the direct set, where it satisfied the coverage gate — letting a
    # deduction stand in for the measurement the gate exists to require.
    graph_inferred_mastered_nodes: list[str] = Field(default_factory=list)
    # WHERE each of those came from, keyed by the same node ids. A sibling of the flat
    # list rather than a widening of it: that list is consumed as a set by the UI, by the
    # preview tests and by every session dump already written, and none of them read the
    # provenance.
    #
    # node -> {source_node, source_item_id, distance, strength, modality, evidence_id,
    #          edge_path: list[str], enforced: bool}
    #
    # `edge_path` is what makes a wrong inference attributable to an EDGE rather than only
    # to a depth, and removing the edges that are wrong is the only remedy that does not
    # also remove the edges that are right.
    graph_inferred_mastery_records: dict[str, dict] = Field(default_factory=dict)
    graph_contradicted_nodes: list[str] = Field(default_factory=list)
    # Every contradiction detected, as an event. A set membership that quietly disappears
    # when the contradiction is resolved is not a record that it happened.
    graph_contradictions: list[dict] = Field(default_factory=list)
    # Required nodes a main was finalised without, when the budget forced the issue.
    graph_waived_nodes: dict[str, list[str]] = Field(default_factory=dict)
    # Items served specifically to re-test a blocked node.
    graph_unblocking_probes: list[str] = Field(default_factory=list)
    # Audit mirrors: identical algorithm, recorded but never enforced.
    graph_shadow_direct_mastered_nodes: list[str] = Field(default_factory=list)
    graph_shadow_inferred_mastered_nodes: list[str] = Field(default_factory=list)
    graph_shadow_direct_not_mastered_nodes: list[str] = Field(default_factory=list)
    graph_shadow_blocked_nodes: list[str] = Field(default_factory=list)
    graph_shadow_contradicted_nodes: list[str] = Field(default_factory=list)
    # The unvalidated-edge forecast: what the PREREQUISITE edges would have concluded had
    # anyone trusted them. Distinct from the shadow mirrors above, which record what the
    # engine actually computed under the edges as they ship — with every edge inert those
    # are empty for every session, which reads identically to inference being broken.
    #
    # node -> {source_node, distance, strength, modality, evidence_id}
    graph_preview_inferred_nodes: dict[str, dict] = Field(default_factory=dict)
    graph_preview_blocked_nodes: list[str] = Field(default_factory=list)
    # Preview beliefs the session went on to measure and disprove — the evidence that an
    # authored edge is wrong, and the reason recording the forecast is worth anything.
    graph_preview_refuted_nodes: list[dict] = Field(default_factory=list)

    # WHAT CONFIGURATION THIS SESSION RAN UNDER. Written once at `begin`; INV-P8 reads it.
    # Without it a sweep cell cannot say which of nine factor values produced its numbers,
    # and a result that cannot name its configuration cannot be reproduced or believed.
    propagation_manifest: dict = Field(default_factory=dict)
    propagation_manifest_hash: str = ""
    # Every mid-session change to that configuration. Normally empty — and the assertion
    # is that it is empty, because the manifest describes the whole session only if
    # nothing moved underneath it. Each entry: {at_evidence, from_hash, to_hash, changed}.
    propagation_manifest_drift: list[dict] = Field(default_factory=list)
    # Set by record_response; after_response refills affected variables.
    graph_last_affected_mains: list[str] = Field(default_factory=list)

    # Which items were ADMINISTERED for which main, whatever the response was worth.
    # `VariableState.served_item_ids` records only what moved the estimate, which is the
    # right record for corroboration but the wrong one for "has this competency been asked
    # in every modality" — an unscorable modality would never clear its deficit and the
    # blueprint would re-serve it for the rest of the session, measuring nothing.
    administered_by_variable: dict[str, list[str]] = Field(default_factory=dict)

    # Responses the posterior did not expect, kept as a record rather than a count.
    # Report-only: nothing reads these and changes an estimate.
    aberrant_responses: list[dict] = Field(default_factory=list)
    # Per-item wall clock and per-item realised evidence weight, so both constants that
    # decide the modality mix can be MEASURED instead of asserted. Without them
    # `DEFAULT_SECONDS_BY_MODALITY["mcq"]` and the expected-weight table stay guesses.
    presenting_since: float = 0.0
    item_seconds: dict[str, float] = Field(default_factory=dict)
    realized_weight_by_item: dict[str, float] = Field(default_factory=dict)

    @property
    def open_variables(self) -> list[str]:
        """Variables still being measured. The only ones the picker is invoked for."""
        return sorted(v for v, s in self.variables.items() if not s.finalised)


class VariableReport(BaseModel):
    variable: str
    theta_hat: float
    # The REPORTED standard error, widened by `interval_widening_factor` when the
    # deployment applies a calibration correction. `standard_error_raw` is the posterior's
    # own value, which is what the stopping rule read.
    #
    # Both are present so the correction is auditable. A single widened number cannot be
    # checked against the rule that produced the session, and a single raw number is the
    # overconfident one that was measured at 33-45% too narrow.
    standard_error: float
    standard_error_raw: float = 0.0
    interval_widening_factor: float = 1.0
    # A monotone remap of the posterior SD. NOT the probability that the reported level is
    # correct — at SE 0.55 this reads 90 while P(correct band) is about 0.64 at a band
    # centre and 0.47 near a boundary. `certainty_pct` is retained as a deprecated alias
    # so nothing downstream breaks; `precision_index_pct` is the honest name and
    # `p_reported_band` is the number a reader assumes they are being given.
    precision_index_pct: float = 0.0
    certainty_pct: float
    level: int | None
    band: str
    # P(theta in the band being reported), and the probability of the most likely band.
    # They differ when the EAP mean sits near a cut point — which is exactly when a
    # single certainty figure is most misleading.
    p_reported_band: float = 0.0
    band_probability: float = 0.0
    most_probable_band: int | None = None
    credible_interval_95: tuple[float, float] | None = None
    aberrant_response_count: int = 0
    graph_coverage_satisfied: bool = True
    graph_unmeasured_nodes: list[str] = Field(default_factory=list)
    observations: int
    finalised: bool
    converged: bool
    stop_reason: str
    modalities_used: list[str] = Field(default_factory=list)


class AssessmentReport(BaseModel):
    session_id: str
    items_administered: int
    variables: list[VariableReport] = Field(default_factory=list)
    all_finalised: bool = False
    stop_reason: str = ""
    # Node-level provenance: what each competency's state is and how it got there.
    # Populated whenever a graph loads, whatever the enforcement flags say — a report that
    # only appears once a feature is on cannot inform the decision to turn it on.
    graph: dict[str, Any] = Field(default_factory=dict)
    aberrant_responses: list[dict] = Field(default_factory=list)
    seconds_by_item: dict[str, float] = Field(default_factory=dict)
