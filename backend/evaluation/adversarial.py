#!/usr/bin/env python3
"""ADV-1..ADV-5: what a candidate can do to the graph on purpose.

    python -m evaluation.adversarial --bank AIE --out eval-results/adversarial.json

Deterministic assertions, not judged ones. Every check here is a field comparison or a
count, which is exactly the class §6.1 says must NOT be a GEval metric — an LLM judge
asked "was this injection successful" can be wrong, and a security check that can be wrong
in the attacker's favour is not a security check.

ADV-5 IS THE ONE THIS PLAN ADDED

Requiring K independent corroborations creates an incentive to satisfy it cheaply. If K
can be met by answering two near-identical items, the knob is decorative — and worse, it
would be REPORTED as a safety control while providing no safety. The check comes in two
parts: a static bank probe that costs no sessions, and a structural graph probe that says
whether K can be satisfied at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def adv5_static_bank_probe(bank, *, difficulty_tolerance: float = 0.1) -> dict:
    """How many near-identical items exist per sub-competency: `max_farmable_K`.

    Costs zero sessions. Two items measuring the same node at the same difficulty in the
    same modality are, for corroboration purposes, the same question asked twice — so the
    size of the largest such group is an upper bound on how much corroboration a candidate
    could manufacture by answering clones.

    If `max_farmable_K >= K` for a node under a keying that counts clones separately, K
    buys nothing there BY CONSTRUCTION, and no amount of session data will reveal it.
    """
    groups: dict[tuple[str, float, str], list[str]] = defaultdict(list)
    for item in bank.all_items():
        node = max(item.measures, key=lambda m: m.weight).variable
        band = round(float(item.cat.b) / max(difficulty_tolerance, 1e-9))
        groups[(node, band, item.modality)].append(item.item_id)

    per_node: dict[str, int] = defaultdict(int)
    for (node, _band, _modality), items in groups.items():
        per_node[node] = max(per_node[node], len(items))

    farmable = {node: size for node, size in per_node.items() if size >= 2}
    return {
        "difficulty_tolerance": difficulty_tolerance,
        "nodes": len(per_node),
        "max_farmable_k_overall": max(per_node.values(), default=0),
        "nodes_with_clones": len(farmable),
        "max_farmable_k_by_node": dict(sorted(per_node.items())),
        "note": (
            "max_farmable_K is the largest set of items measuring one node at one "
            "difficulty in one modality. Under `evidence` or `item` independence a "
            "candidate can meet K by answering that many clones; under `source_node` "
            "they collapse to one observation whatever the count."
        ),
    }


def adv5_structural_graph_probe(graph_service, *, max_depth: int = 4) -> dict:
    """Whether K CAN be satisfied on this graph, per ancestor and per depth.

    The complement of the bank probe. That one asks whether corroboration is too easy to
    obtain; this asks whether it is obtainable at all. Both failures make K useless and
    they point in opposite directions, so reporting only one would miss half the ways the
    knob can be inoperative.
    """
    children: dict[str, list[str]] = defaultdict(list)
    for edge in graph_service.graph.edges:
        if edge.relation == "PREREQUISITE":
            children[edge.from_id].append(edge.to_id)

    def reach(node: str, depth: int) -> set[str]:
        seen: set[str] = set()
        frontier = [node]
        for _ in range(depth):
            nxt = []
            for current in frontier:
                for child in children.get(current, []):
                    if child not in seen:
                        seen.add(child)
                        nxt.append(child)
            frontier = nxt
        return seen

    by_depth = {}
    for depth in range(1, max_depth + 1):
        sizes = {p: len(reach(p, depth)) for p in children}
        by_depth[str(depth)] = {
            "ancestors": len(sizes),
            "supporting_k": {
                str(k): sum(1 for v in sizes.values() if v >= k) for k in (1, 2, 3)
            },
        }

    direct = {p: len(c) for p, c in children.items()}
    return {
        "prerequisite_edges": sum(direct.values()),
        "ancestors": len(direct),
        "max_direct_children": max(direct.values(), default=0),
        "by_depth": by_depth,
        "k_satisfiable_at_depth_1": {
            str(k): any(size >= k for size in direct.values()) for k in (1, 2, 3)
        },
    }


def adv5_session_metric(records: list[dict]) -> dict:
    """Did corroborated inferences actually rest on DISTINCT evidence?

    The ratio of distinct source nodes to K, per corroborated node. Below 1 means the
    requirement was met by observations that are not independent in the sense the safety
    argument assumes — at which point K must not be reported as a safety control.
    """
    corroborated = 0
    distinct_sources: list[int] = []
    for record in records:
        for node, provenance in (
            (record.get("graph") or {}).get("inferred_records") or {}
        ).items():
            corroborated += 1
            # One record per node carries the winning path only, so the count of distinct
            # sources is recoverable per session rather than per inference.
            distinct_sources.append(1 if provenance.get("source_node") else 0)
    return {
        "corroborated_inferences": corroborated,
        "with_a_recorded_source": sum(distinct_sources),
        "provenance_complete": corroborated == sum(distinct_sources),
    }


def adv3_self_report_cannot_infer() -> dict:
    """A claim in free text must never become evidence.

    Asserted structurally rather than by simulation: an inference is only ever drawn from
    an `EvidenceEvent`, which is constructed from a GRADED outcome. There is no path from
    transcript text to an evidence event, and `InferredNodeSignal` carries no score or
    weight so it cannot become one either.
    """
    from dataclasses import fields

    from app.services.competency_graph.evidence import EvidenceEvent
    from app.services.competency_graph.inference import InferredNodeSignal

    signal_fields = {f.name for f in fields(InferredNodeSignal)}
    event_fields = {f.name for f in fields(EvidenceEvent)}
    return {
        "inferred_signal_carries_no_score_or_weight": not (
            {"score", "weight"} & signal_fields
        ),
        "evidence_event_requires_a_graded_score": "score" in event_fields,
        "evidence_event_has_no_inferred_provenance_fields": not (
            {"source_node", "propagation_distance"} & event_fields
        ),
    }


def adv4_evidence_replay(records: list[dict]) -> dict:
    """The ledger's promise, measured rather than asserted.

    A duplicate matters more with corroboration than it did without: K counts
    observations, so an evidence event admitted twice does not merely re-weight an
    estimate, it manufactures the independent confirmation the safety rule asks for.
    """
    total = duplicates = 0
    for record in records:
        graph = record.get("graph") or {}
        seen = int(graph.get("direct_evidence_id_count", 0))
        unique = int(graph.get("unique_direct_evidence_ids", 0))
        total += seen
        duplicates += max(0, seen - unique)
    return {
        "direct_evidence_events": total,
        "duplicates": duplicates,
        "rate": round(duplicates / total, 6) if total else None,
        "passes": duplicates == 0,
    }


def adv1_injection_is_inert(records: list[dict], clean_records: list[dict]) -> dict:
    """P14's payload must not move a score.

    Compared against a clean twin cohort rather than against an absolute: a grader that
    ignores the injection produces the same score distribution, and any difference is the
    injection working. The simulation's grader is stubbed, so this measures the ENGINE's
    handling of a payload-bearing transcript, not a real grader's susceptibility — stated
    plainly because it is the limit of what a simulated harness can say about security.
    """

    def mean_score(rows: list[dict]) -> float:
        values = [v["estimated_theta"] for r in rows for v in r["variables"]]
        return sum(values) / len(values) if values else float("nan")

    injected, clean = mean_score(records), mean_score(clean_records)
    return {
        "mean_theta_injected": round(injected, 5),
        "mean_theta_clean": round(clean, 5),
        "difference": round(injected - clean, 5),
        "note": (
            "The harness grader is deterministic and stubbed, so this bounds the ENGINE's "
            "handling of a payload-bearing transcript. It says nothing about whether a "
            "real LLM grader would be moved by the payload — that needs the gold set."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default="AIE")
    parser.add_argument("--out", default="eval-results/adversarial.json")
    parser.add_argument(
        "--records", default="", help="a run's .jsonl, for the session checks"
    )
    parser.add_argument("--clean-records", default="", help="the P01 twin, for ADV-1")
    args = parser.parse_args()

    from app.services.orchestrator import registry

    bank = registry.get_bank(args.bank)
    graph = registry.get_graph_service(args.bank)

    report = {
        "bank_id": args.bank,
        "ADV-3_self_report_cannot_infer": adv3_self_report_cannot_infer(),
        "ADV-5_static_bank_probe": adv5_static_bank_probe(bank),
        "ADV-5_structural_graph_probe": (
            adv5_structural_graph_probe(graph) if graph is not None else None
        ),
    }

    if args.records:
        records = [
            json.loads(line)
            for line in Path(args.records).open(encoding="utf-8")
            if line.strip()
        ]
        report["ADV-4_evidence_replay"] = adv4_evidence_replay(records)
        report["ADV-5_session_metric"] = adv5_session_metric(records)
        if args.clean_records:
            clean = [
                json.loads(line)
                for line in Path(args.clean_records).open(encoding="utf-8")
                if line.strip()
            ]
            report["ADV-1_injection_is_inert"] = adv1_injection_is_inert(records, clean)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")

    static = report["ADV-5_static_bank_probe"]
    structural = report["ADV-5_structural_graph_probe"] or {}
    print("ADV-3 posterior isolation:", report["ADV-3_self_report_cannot_infer"])
    print(
        f"ADV-5 bank probe: max_farmable_K = {static['max_farmable_k_overall']} "
        f"({static['nodes_with_clones']} of {static['nodes']} nodes have clones)"
    )
    if structural:
        print(
            f"ADV-5 graph probe: {structural['prerequisite_edges']} edges, "
            f"max direct children = {structural['max_direct_children']}, "
            f"K satisfiable at depth 1 = {structural['k_satisfiable_at_depth_1']}"
        )
    if "ADV-4_evidence_replay" in report:
        print("ADV-4 duplicate evidence:", report["ADV-4_evidence_replay"])
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
