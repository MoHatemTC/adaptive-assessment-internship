"""Detect when new direct evidence contradicts what the graph already believed.

WHY A DETECTOR AND NOT A SET INTERSECTION

Contradiction used to be derived as `mastered & (blocked | not_mastered)`, recomputed from
scratch after every response. That identity can only notice a node sitting in two sets at
once. It cannot name the prerequisite whose failure caused a block that later evidence
disproved, it cannot see a reversal on a single node, and it cannot distinguish a wrong
inference from a correct one. It also has no memory: a contradiction that gets resolved
simply stops appearing, so nothing records that it ever happened.

The three cases below are the ones the specification's own worked example requires, and
the third is the only in-band source of ground truth for the wrong-inference rate the
review lists as otherwise unmeasurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .evidence import EvidenceEvent
from .models import CompetencyStatus
from .state import CompetencyGraphState

ContradictionKind = Literal["blocked_but_passed", "direct_reversal", "wrong_inference"]


@dataclass(frozen=True)
class Contradiction:
    """A belief the graph held that new direct evidence disputes.

    `node` is what is now in doubt, which is NOT always the node just tested: when a
    blocked node is passed, the doubt belongs to the prerequisite whose failure caused
    the block.
    """

    node: str
    against: str
    kind: ContradictionKind
    evidence_id: str
    detected_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "node": self.node,
            "against": self.against,
            "kind": self.kind,
            "evidence_id": self.evidence_id,
            "detected_at": self.detected_at,
        }


def detect_contradictions(
    *,
    prior_status: CompetencyStatus,
    prior_blocked_by: frozenset[str],
    event: EvidenceEvent,
    strong_success: bool,
    strong_failure: bool,
    graph_state: CompetencyGraphState,
    now: str | None = None,
) -> list[Contradiction]:
    """What this event disputes about the state that preceded it."""
    target = event.target_node
    found: list[Contradiction] = []

    # 1. A blocked node passed outright. The block asserted "the prerequisite was not
    #    demonstrated, so this cannot be either"; passing it says that reasoning was
    #    wrong. The prerequisite is what needs re-testing — not the node just passed.
    if strong_success and prior_status is CompetencyStatus.BLOCKED:
        for blocker in sorted(prior_blocked_by):
            found.append(
                Contradiction(
                    node=blocker,
                    against=target,
                    kind="blocked_but_passed",
                    evidence_id=event.evidence_id,
                    detected_at=now,
                )
            )
        if not prior_blocked_by:
            # Blocked with no recorded blocker: the state is inconsistent with itself, so
            # the node is its own open question.
            found.append(
                Contradiction(
                    node=target,
                    against=target,
                    kind="blocked_but_passed",
                    evidence_id=event.evidence_id,
                    detected_at=now,
                )
            )

    # 2. The same node, directly, both ways. One of the two gradings is wrong, and the
    #    node needs verifying before either is reported as a measurement.
    reversal = (strong_success and prior_status is CompetencyStatus.DIRECT_NOT_MASTERED) or (
        strong_failure and prior_status is CompetencyStatus.DIRECT_MASTERED
    )
    if reversal:
        found.append(
            Contradiction(
                node=target,
                against=target,
                kind="direct_reversal",
                evidence_id=event.evidence_id,
                detected_at=now,
            )
        )
        graph_state.ensure_nodes({target})
        graph_state.nodes[target].status = CompetencyStatus.NEEDS_VERIFICATION

    # 3. A node the graph inferred as mastered, failed directly. The inference was wrong,
    #    and the edge that produced it is what deserves doubt. This is the only routine
    #    way a wrong inference becomes observable in production rather than in a study.
    if strong_failure and prior_status is CompetencyStatus.INFERRED_MASTERED:
        graph_state.ensure_nodes({target})
        sources = sorted(graph_state.nodes[target].inferred_evidence_ids) or ["<unknown>"]
        found.append(
            Contradiction(
                node=target,
                against=sources[0],
                kind="wrong_inference",
                evidence_id=event.evidence_id,
                detected_at=now,
            )
        )

    return found
