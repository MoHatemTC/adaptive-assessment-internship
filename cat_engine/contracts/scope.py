"""The scoping surface: a selection in, an induced sub-graph and an allowlist out.

WHAT A SCOPE IS, AND WHAT IT DELIBERATELY IS NOT

A scope narrows an assessment to some of a bank's competencies. It answers two questions:
which sub-competency nodes are in play, and which items may therefore be administered. It
answers neither by returning items — `item_ids` is a list of identifiers, never
`BankItemRef`s and certainly never payloads.

That is the same boundary `catalogue` draws across its two read paths. One place serves
items. A scope that returned questions would be a second one, with a second answer to "what
is in this bank" — and it is not needed: selection already holds the whole parameter pool
for the bank version, so an id allowlist costs a set intersection and nothing else.

A SCOPE CANNOT MOVE A POSTERIOR EITHER

There is no `score`, no `weight` and no theta anywhere in this module. A scope changes which
questions are asked and what coverage requires. As with the graph, the worst a buggy scope
can do is narrow a pool — which is a quality-of-measurement problem, visible in the report,
rather than a wrong number nobody can see.

WHY A MAIN CAN BE PARTIAL, AND WHY THAT HAS TO BE SAID OUT LOUD

Ability is estimated per MAIN competency; sub-competencies exist in the graph, in an item's
`measures`, and in the coverage gate, but they have no theta of their own. So selecting some
of a main's sub-competencies produces an estimate of that main built from a corner of itself
— exactly what the coverage gate exists to prevent when it happens by accident. It is
legitimate when it happens on purpose, and `ScopeMainDTO.partial` and `retained_weight` are
how a report says so. A partial main's estimate is not comparable with a whole-bank one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .envelopes import Modality


class ScopeSelection(BaseModel):
    """Which competencies an assessment is about. Carried on `CreateAssessmentRequest`.

    THE SELECTION TRAVELS, NOT A SCOPE ID

    `scope_id` is a hash, and a hash cannot be inverted, so a service holding no state
    cannot answer `GET /scopes/{id}`. The alternatives are both worse: caching manifests
    makes a deterministic service stateful and bound to one replica, and letting a client
    hand the orchestrator an id it cannot verify means trusting an allowlist nobody
    re-derived.

    So the orchestrator builds the scope itself, from the selection. A client that called
    `POST /scopes` first to preview or draw it gets the identical `scope_id` back on the
    session, because the manifest is a pure function of (bank version, selection) — which
    is the same property that lets this service scale horizontally and lose nothing on
    restart.
    """

    #: Node ids: `C1`, `C1.4`, or both, in one list. A caller thinks in competencies rather
    #: than in tiers, and two fields would make "C1 plus two of C3's sub-competencies"
    #: express itself as a shape rather than as a sentence. A selected main expands to its
    #: sub-competencies; a selected sub-competency stands alone.
    #:
    #: An id the bank does not declare lands in `rejected` rather than raising — a picker
    #: needs to say which of the five things the user chose is the problem.
    selected: list[str] = Field(min_length=1)
    #: None defers to the bank's own coverage policy, which is the rule an unscoped session
    #: already follows. True narrows the requirement to the critical set.
    critical_only: bool | None = None
    #: Pull in the PREREQUISITE ancestors of every selected node. Off by default: those
    #: edges ship inert precisely because their inference rate was measured and rejected,
    #: so enlarging an assessment on their authority is the one use they were found unfit
    #: for.
    include_prerequisites: bool = False


class ScopeRequest(ScopeSelection):
    """A selection, against a named bank. What `POST /scopes` takes."""

    bank_id: str


class ScopeNodeDTO(BaseModel):
    """One retained node, with what the scope knows about how measurable it is."""

    node_id: str
    title: str = ""
    node_type: str = "sub_competency"
    critical: bool = False
    main_competencies: list[str] = Field(default_factory=list)
    #: Active items measuring this node WITHIN the scope. Zero here is what makes a scope
    #: unreachable, and naming the node is what makes that fixable.
    item_count: int = 0


class ScopeEdgeDTO(BaseModel):
    """One retained edge. Both endpoints are in the scope, by construction.

    `weight_original` is kept beside `weight` because renormalising CONTRIBUTES_TO over a
    retained subset is a real change to what a main's declared mass means, and a change
    that cannot be inspected is a change nobody can review.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relation: str
    strength: float = 1.0
    weight: float = 1.0
    weight_original: float = 1.0


class DanglingEdgeDTO(BaseModel):
    """An edge with exactly one endpoint in the scope.

    Reported rather than dropped. A selection that severs a prerequisite chain is usually
    fine and occasionally the reason an assessment measures something other than what was
    intended, and the difference is only visible if the severing is written down.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relation: str
    #: `leaves` — the source is in scope and the target is not. `enters` — the reverse.
    direction: str


class ScopeMainDTO(BaseModel):
    """One main competency the scope opens, and how much of it survived."""

    main: str
    title: str = ""
    #: True when nobody selected this main — it was opened because a SHARED sub-competency
    #: in the scope also serves it. `C1.6` serves both C1 and C6, so selecting C6 whole
    #: necessarily measures something that evidences C1.
    #:
    #: The main has to be opened: `rollup_outcomes` folds every outcome to its main and
    #: DROPS it if that main is not in the session, so leaving C1 closed would discard part
    #: of what a C6 question just measured. But an implied main is usually served by one or
    #: two shared items, so its estimate is thin — a caller should be able to tell it apart
    #: from a main somebody actually asked for, and this is how.
    implied: bool = False
    #: False when every sub-competency the bank declares for this main is retained.
    partial: bool = False
    #: The share of this main's AUTHORED CONTRIBUTES_TO mass that the retained nodes carry.
    #: 1.0 for a whole main. Descriptive, not inferential: it does not enter any estimate,
    #: and it is the number a report cites when it says an estimate covers part of a main.
    retained_weight: float = 1.0
    #: What the coverage gate will require of this main under this scope.
    required: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)


class ScopeCoverageDTO(BaseModel):
    """Whether this scope can actually be assessed, and if not, why not.

    Answered here rather than discovered later. A scope whose required nodes outnumber the
    question budget, or whose required node has no active item, produces a session that ends
    on the budget escape with a competency that never converged — which is indistinguishable
    afterwards from a candidate who simply did not answer enough questions.
    """

    reachable: bool = True
    question_budget: int = 0
    critical_only_applied: bool = False
    required_by_main: dict[str, int] = Field(default_factory=dict)
    #: Required nodes that no active item in the scope measures. Named, because this is the
    #: one failure an author can fix by changing the selection.
    unserved: list[str] = Field(default_factory=list)
    #: Mains requiring more sub-competencies than the budget could ever cover.
    over_budget: list[str] = Field(default_factory=list)


class ScopeManifest(BaseModel):
    """Everything an assessment needs to run narrowed, and a hash that identifies it.

    DETERMINISTIC, WHICH IS WHY THIS SERVICE HOLDS NO STATE. `scope_id` is a hash over the
    bank version and the normalised request, so `GET /scopes/{id}` recomputes rather than
    looks up. The service scales horizontally for free and a restart loses nothing — the
    same property, for the same reason, as `competency-graph`.

    `bank_version` is carried so a session can refuse a scope built against a bank that has
    since been replaced. Without it the orchestrator would happily apply an allowlist naming
    items that no longer exist, and the symptom would be a competency that exhausts
    immediately for no stated reason.
    """

    scope_id: str
    scope_hash: str
    bank_id: str
    bank_version: str
    selected: list[str] = Field(default_factory=list)
    include_prerequisites: bool = False

    nodes: list[ScopeNodeDTO] = Field(default_factory=list)
    edges: list[ScopeEdgeDTO] = Field(default_factory=list)
    mains: list[ScopeMainDTO] = Field(default_factory=list)

    #: The allowlist. Ids only — see the module docstring.
    item_ids: list[str] = Field(default_factory=list)
    item_count_by_modality: dict[Modality, int] = Field(default_factory=dict)

    coverage: ScopeCoverageDTO = Field(default_factory=ScopeCoverageDTO)
    #: Selected ids the bank does not declare, or declares with no active item.
    rejected: list[str] = Field(default_factory=list)
    dangling_prerequisites: list[DanglingEdgeDTO] = Field(default_factory=list)

    @property
    def assessable(self) -> bool:
        """Whether an assessment may begin against this scope."""
        return self.coverage.reachable and bool(self.item_ids) and bool(self.mains)


class ScopeSummaryDTO(BaseModel):
    """What a REPORT says about the scope it was measured under.

    Small on purpose: a report carries the identity of the scope and the honesty flags, not
    the whole manifest. The manifest is reproducible from `scope_id` at any time, and a
    report that inlined it would grow by the size of a bank's taxonomy for no reader.
    """

    scope_id: str = ""
    scope_hash: str = ""
    selected: list[str] = Field(default_factory=list)
    partial_mains: list[str] = Field(default_factory=list)
    retained_weight_by_main: dict[str, float] = Field(default_factory=dict)
