"""Who is allowed to turn prerequisite propagation on, and at which level.

THREE LEVELS, MOST RESTRICTIVE WINS

    1. DEPLOYMENT   `.env` / environment. `GRAPH_UPWARD_INFERENCE_ENABLED`,
                    `GRAPH_DESCENDANT_BLOCKING_ENABLED`. Owned by whoever runs the
                    service; the switch an operator reaches for during an incident.
    2. BANK         a `policy` block inside the competency graph JSON. Owned by whoever
                    authors the bank, and it TRAVELS WITH the bank — which matters because
                    a bank and its graph are already selected together, and an edge set
                    validated for one bank says nothing about another.
    3. EDGE         `allow_upward_inference` / `allow_downward_blocking` and
                    `metadata.validation_status` on each PREREQUISITE edge. Owned by
                    whoever validated that specific edge against data.

An edge may act only if ALL THREE permit it. That direction is deliberate and it matches
the convention `prerequisite_rules` already states for the confidence floors: a narrower
scope exists to TIGHTEN a claim, never to loosen the one above it. A bank cannot switch on
what the deployment has switched off, and an edge cannot switch on what its bank has not.

WHY THE DEFAULT IS STILL OFF

Measured on this bank, with edges correct by construction, upward inference was wrong 22.4%
of the time against a 3% gate and blocking produced 8.7% false blocks against a 2% gate —
with a structural floor of 14.6% that no amount of evidence about the parent can beat,
because a prerequisite relation is probabilistic. So this module makes propagation
CONFIGURABLE, not RECOMMENDED. See `docs/propagation-policy.md` for the evidence and
`docs/operations.md` for the gates to clear before turning any of it on.

WHY THE ANSWER IS ALWAYS EXPLAINED

`EdgeDecision` carries the reason an edge is inert, not just the fact. An edge that ships
disabled, in a bank that disables it, under a deployment that disables it, is inert for
three different reasons — and an operator who flips one flag and sees nothing change needs
to be told which of the other two is still holding.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from .models import CompetencyEdge, CompetencyGraph

# Validation statuses an edge may carry. Only `validated` licenses action by default; a
# bank may widen this, and a deployment may not.
VALIDATION_STATUSES = ("validated", "provisional", "unvalidated", "refuted")
DEFAULT_ACCEPTED_STATUSES: tuple[str, ...] = ("validated",)


@dataclass(frozen=True)
class GraphPolicy:
    """The `policy` block of a competency graph file. Every field is optional.

    `None` means "no opinion — defer to the deployment". `False` means "this bank forbids
    it", which the deployment cannot override. There is deliberately no value meaning
    "this bank forces it on".
    """

    upward_inference: bool | None = None
    descendant_blocking: bool | None = None
    # Statuses this bank accepts as licensing an edge. Widening this is how a bank ships
    # provisional edges deliberately, with the widening visible in the file rather than in
    # someone's deployment.
    accepted_validation_statuses: tuple[str, ...] = DEFAULT_ACCEPTED_STATUSES
    # Per-bank override of how many consistent parent failures buy a block. None defers.
    minimum_failures_to_block: int | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict | None) -> GraphPolicy:
        raw = raw or {}
        statuses = raw.get("accepted_validation_statuses")
        if statuses is None:
            accepted = DEFAULT_ACCEPTED_STATUSES
        else:
            accepted = tuple(str(s).strip().lower() for s in statuses)
            unknown = sorted(set(accepted) - set(VALIDATION_STATUSES))
            if unknown:
                raise ValueError(
                    f"unknown validation status {unknown} in graph policy; "
                    f"expected any of {list(VALIDATION_STATUSES)}"
                )
            if "refuted" in accepted:
                raise ValueError(
                    "a graph policy may not accept 'refuted' edges: refuted means the data "
                    "disproved the dependency, and enabling one is not a configuration "
                    "choice a bank gets to make"
                )
        minimum = raw.get("minimum_failures_to_block")
        return cls(
            upward_inference=_tri_state(raw.get("upward_inference")),
            descendant_blocking=_tri_state(raw.get("descendant_blocking")),
            accepted_validation_statuses=accepted,
            minimum_failures_to_block=int(minimum) if minimum is not None else None,
            notes=str(raw.get("notes") or ""),
        )

    def as_dict(self) -> dict:
        return {
            "upward_inference": self.upward_inference,
            "descendant_blocking": self.descendant_blocking,
            "accepted_validation_statuses": list(self.accepted_validation_statuses),
            "minimum_failures_to_block": self.minimum_failures_to_block,
            "notes": self.notes,
        }


def _tri_state(value: object) -> bool | None:
    """`True` / `False` / `None`, where None means 'defer to the deployment'."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "yes", "on", "1"):
        return True
    if text in ("false", "no", "off", "0"):
        return False
    if text in ("", "default", "defer", "inherit", "none"):
        return None
    raise ValueError(f"cannot read {value!r} as a policy switch")


@dataclass(frozen=True)
class EdgeDecision:
    """What one edge is permitted to do, and — when it is not — why."""

    parent: str
    child: str
    validation_status: str
    # All three levels. What the edge will actually DO.
    inference_allowed: bool
    blocking_allowed: bool
    # Bank and edge only, deployment excluded. What the graph BELIEVES is a valid
    # dependency, which is a different question from whether this deployment acts on it.
    #
    # The split matters because the deployment switch governs ENFORCEMENT while the bank
    # and edge levels govern VALIDITY, and the audit mirror exists to record what would
    # have happened had enforcement been on. Baking the deployment switch into the edges
    # would silence the computation as well as the action, and the mirror would report
    # nothing for exactly the deployment that most needs to read it.
    inference_authorised: bool
    blocking_authorised: bool
    inference_blocked_by: str = ""
    blocking_blocked_by: str = ""

    def as_dict(self) -> dict:
        return {
            "parent": self.parent,
            "child": self.child,
            "validation_status": self.validation_status,
            "inference_allowed": self.inference_allowed,
            "blocking_allowed": self.blocking_allowed,
            "inference_authorised_by_bank": self.inference_authorised,
            "blocking_authorised_by_bank": self.blocking_authorised,
            "inference_blocked_by": self.inference_blocked_by,
            "blocking_blocked_by": self.blocking_blocked_by,
        }


@dataclass(frozen=True)
class ResolvedPolicy:
    """The whole edge set's decisions, plus the inputs that produced them."""

    deployment_inference: bool
    deployment_blocking: bool
    bank_policy: GraphPolicy
    decisions: tuple[EdgeDecision, ...] = ()
    minimum_failures_to_block: int = 2

    def by_pair(self) -> dict[tuple[str, str], EdgeDecision]:
        return {(d.parent, d.child): d for d in self.decisions}

    @property
    def inference_enabled_edges(self) -> tuple[EdgeDecision, ...]:
        return tuple(d for d in self.decisions if d.inference_allowed)

    @property
    def blocking_enabled_edges(self) -> tuple[EdgeDecision, ...]:
        return tuple(d for d in self.decisions if d.blocking_allowed)

    def summary(self) -> dict:
        """The shape a diagnostic endpoint or a startup log line wants."""
        reasons: dict[str, int] = {}
        for decision in self.decisions:
            for reason in (decision.inference_blocked_by, decision.blocking_blocked_by):
                if reason:
                    reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "prerequisite_edges": len(self.decisions),
            "inference_enabled": len(self.inference_enabled_edges),
            "blocking_enabled": len(self.blocking_enabled_edges),
            "minimum_failures_to_block": self.minimum_failures_to_block,
            "deployment": {
                "upward_inference": self.deployment_inference,
                "descendant_blocking": self.deployment_blocking,
            },
            "bank_policy": self.bank_policy.as_dict(),
            "inert_because": dict(sorted(reasons.items())),
        }


# An edge that carries no `validation_status` at all was authored before the concept
# existed. Its `allow_*` flags ARE its author's stated intent, and there is nothing to
# enforce against. An edge that declares `unvalidated` is saying something different and
# stronger — "nobody has checked this" — and that one is held inert whatever its flags say.
#
# The distinction is what keeps this change additive: the AI Engineer graph declares
# `unvalidated` on all thirty of its edges and stays inert, while a graph authored before
# the policy block existed keeps behaving exactly as it did.
UNSTATED = ""


def _status_of(edge: CompetencyEdge) -> str:
    raw = (edge.metadata or {}).get("validation_status")
    return UNSTATED if raw is None else str(raw).strip().lower()


def _status_vetoes(status: str, accepted: set[str]) -> bool:
    """True when a DECLARED status is not one this bank accepts."""
    return status != UNSTATED and status not in accepted


def resolve_policy(
    graph: CompetencyGraph,
    *,
    deployment_inference: bool,
    deployment_blocking: bool,
    deployment_minimum_failures_to_block: int = 2,
    deployment_accepted_validation_statuses: tuple[str, ...] | None = None,
) -> ResolvedPolicy:
    """Fold deployment, bank and edge configuration into one decision per edge.

    Pure: takes the three inputs, returns the resolution. Nothing here reads settings or
    the filesystem, so a caller can ask "what would this bank do under that deployment"
    without arranging for that deployment to exist — which is what makes the policy
    testable and what `scripts/show_propagation_policy.py` uses.
    """
    bank = graph.policy
    accepted = set(bank.accepted_validation_statuses)

    # A deployment may narrow the accepted statuses; it may never widen them. Intersection,
    # never union — a union would let a permissive deployment override a bank that
    # deliberately restricted itself, which inverts the most-restrictive-wins rule the rest
    # of this function exists to implement.
    #
    # None means "no opinion", NOT ("validated",). The difference decides whether a bank
    # that widened its own list keeps that choice: with a default of ("validated",) the
    # intersection would silently cut it back, and the symptom would be fewer inferences —
    # which reads as a quiet system rather than a misconfigured one.
    if deployment_accepted_validation_statuses is not None:
        accepted &= {s.strip().lower() for s in deployment_accepted_validation_statuses}

    # A bank may tighten the failure requirement, never loosen it: blocking is the
    # strongest claim the system makes, and this is the knob that prices it.
    minimum_failures = max(
        deployment_minimum_failures_to_block,
        bank.minimum_failures_to_block or 0,
    )

    decisions: list[EdgeDecision] = []
    for edge in graph.edges:
        if edge.relation != "PREREQUISITE":
            continue
        status = _status_of(edge)

        inference_authorised = not _first_veto(
            (bank.upward_inference is False, "bank"),
            (not edge.allow_upward_inference, "edge"),
            (_status_vetoes(status, accepted), "status"),
        )
        blocking_authorised = not _first_veto(
            (bank.descendant_blocking is False, "bank"),
            (not edge.allow_downward_blocking, "edge"),
            (_status_vetoes(status, accepted), "status"),
        )

        inference_blocked_by = _first_veto(
            (
                not deployment_inference,
                "deployment: GRAPH_UPWARD_INFERENCE_ENABLED=false",
            ),
            (bank.upward_inference is False, "bank policy: upward_inference=false"),
            (not edge.allow_upward_inference, "edge: allow_upward_inference=false"),
            (
                _status_vetoes(status, accepted),
                f"edge: validation_status={status!r} not accepted by this bank",
            ),
        )
        blocking_blocked_by = _first_veto(
            (
                not deployment_blocking,
                "deployment: GRAPH_DESCENDANT_BLOCKING_ENABLED=false",
            ),
            (
                bank.descendant_blocking is False,
                "bank policy: descendant_blocking=false",
            ),
            (not edge.allow_downward_blocking, "edge: allow_downward_blocking=false"),
            (
                _status_vetoes(status, accepted),
                f"edge: validation_status={status!r} not accepted by this bank",
            ),
        )

        decisions.append(
            EdgeDecision(
                parent=edge.from_id,
                child=edge.to_id,
                validation_status=status or "unstated",
                inference_allowed=not inference_blocked_by,
                blocking_allowed=not blocking_blocked_by,
                inference_authorised=inference_authorised,
                blocking_authorised=blocking_authorised,
                inference_blocked_by=inference_blocked_by,
                blocking_blocked_by=blocking_blocked_by,
            )
        )

    return ResolvedPolicy(
        deployment_inference=deployment_inference,
        deployment_blocking=deployment_blocking,
        bank_policy=bank,
        decisions=tuple(decisions),
        minimum_failures_to_block=minimum_failures,
    )


def _first_veto(*checks: tuple[bool, str]) -> str:
    """The first reason this is not permitted, or "" if it is.

    FIRST, not all of them, and the order is deployment -> bank -> edge on purpose. An
    operator reads the widest scope still saying no, which is the one they can act on;
    telling them an edge is disabled when the whole feature is off sends them to the wrong
    file.
    """
    for failed, reason in checks:
        if failed:
            return reason
    return ""


def apply_policy(graph: CompetencyGraph, resolved: ResolvedPolicy) -> CompetencyGraph:
    """A graph whose edge flags ARE the resolved decisions.

    Resolution happens once, at load, rather than at every traversal. Everything
    downstream — `infer_ancestors`, `blockable_children`, the shadow projections — already
    reads `allow_upward_inference` and `allow_downward_blocking`, so baking the effective
    values into the edges makes the policy apply everywhere without a single new check in
    a hot path, and without a second place that could disagree about the answer.

    Only the BANK and EDGE levels are baked in — the two that answer "is this a valid
    dependency". The DEPLOYMENT switch answers "does this deployment act on it", and it is
    enforced in `orchestrator.graph_delta` alongside the blocking switch, so the audit
    mirror still records what would have happened.

    The reporting-only preview is unaffected either way: it passes
    `ignore_edge_validation=True` and walks every prerequisite edge regardless, which is
    what makes it a forecast of enabling an edge rather than a report of the current one.
    """
    decisions = resolved.by_pair()
    edges = tuple(
        replace(
            edge,
            allow_upward_inference=decisions[
                (edge.from_id, edge.to_id)
            ].inference_authorised,
            allow_downward_blocking=decisions[
                (edge.from_id, edge.to_id)
            ].blocking_authorised,
        )
        if edge.relation == "PREREQUISITE" and (edge.from_id, edge.to_id) in decisions
        else edge
        for edge in graph.edges
    )
    return replace(graph, edges=edges)


def describe(resolved: ResolvedPolicy, *, only_enabled: bool = False) -> Iterable[str]:
    """Human-readable lines, for a startup log or a CLI."""
    summary = resolved.summary()
    yield (
        f"prerequisite edges: {summary['prerequisite_edges']}  "
        f"inference enabled: {summary['inference_enabled']}  "
        f"blocking enabled: {summary['blocking_enabled']}  "
        f"failures required to block: {summary['minimum_failures_to_block']}"
    )
    for decision in resolved.decisions:
        if only_enabled and not (
            decision.inference_allowed or decision.blocking_allowed
        ):
            continue
        infer = "infer" if decision.inference_allowed else "-"
        block = "block" if decision.blocking_allowed else "-"
        reason = decision.inference_blocked_by or decision.blocking_blocked_by
        yield (
            f"  {decision.parent:>8} -> {decision.child:<8} "
            f"[{infer:>5}|{block:>5}] {decision.validation_status:<12} {reason}"
        )
