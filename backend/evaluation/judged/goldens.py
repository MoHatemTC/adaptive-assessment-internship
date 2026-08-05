"""Turn harness session records into judged test cases, plus the canary set.

A golden is frozen to a run id and a git revision. A judged score is only comparable
across runs if the thing being judged did not change, and "the reports from the last
sweep" is not a fixed corpus.
"""

from __future__ import annotations

import json
from pathlib import Path


def evidence_ledger(record: dict) -> dict:
    """The DIRECT / INFERRED labels a judge needs, taken from the session record.

    This is `context` for G-01. Without it the judge is asked whether a claim is
    supported, with no way to know what the support was — which is how a judged metric
    becomes a vibe check.
    """
    graph = record.get("graph") or {}
    direct = set(graph.get("direct_mastered") or [])
    inferred = set(graph.get("inferred_mastered") or graph.get("shadow_inferred_mastered") or [])
    blocked = set(graph.get("blocked") or graph.get("shadow_blocked") or [])
    records = graph.get("inferred_records") or {}

    lines = []
    for node in sorted(direct):
        lines.append(f"{node}: DIRECT")
    for node in sorted(inferred):
        provenance = records.get(node) or {}
        source = provenance.get("source_node")
        distance = provenance.get("distance")
        detail = f" (from {source}, {distance} hop(s))" if source else ""
        lines.append(f"{node}: INFERRED{detail}")
    for node in sorted(blocked):
        lines.append(f"{node}: BLOCKED (untested)")

    return {
        "context": lines,
        "served_item_ids": list(record.get("served_item_ids") or []),
        "inferred_nodes": sorted(inferred),
        "direct_nodes": sorted(direct),
        "blocked_nodes": sorted(blocked),
    }


def build_goldens(records_path: Path, *, limit: int = 60, run_id: str = "", revision: str = "") -> dict:
    """Freeze a judged corpus from one run's records."""
    records = [
        json.loads(line)
        for line in Path(records_path).open(encoding="utf-8")
        if line.strip()
    ]
    # Prefer sessions that actually contain an inference: a report with nothing inferred
    # cannot exhibit the failure G-01 exists to catch, and a corpus of those would score
    # 1.0 while testing nothing.
    with_inference = [
        r for r in records if (r.get("graph") or {}).get("inferred_records")
    ]
    chosen = (with_inference + [r for r in records if r not in with_inference])[:limit]

    return {
        "run_id": run_id,
        "revision": revision,
        "source": str(records_path),
        "n": len(chosen),
        "n_with_inference": sum(
            1 for r in chosen if (r.get("graph") or {}).get("inferred_records")
        ),
        "cases": [
            {
                "simulee_id": r["simulee_id"],
                "persona": r.get("persona", "P01"),
                "evidence": evidence_ledger(r),
            }
            for r in chosen
        ],
    }


#: Cases whose correct verdict is known by construction. A judge that misscores one of
#: these has invalidated the whole run — which is a stronger and cheaper check than any
#: amount of agreement statistics between judged cases whose truth nobody knows.
CANARIES = [
    {
        "canary_id": "must_fail__fabricated_item",
        "expected": "fail",
        "actual_output": (
            "The candidate demonstrated strong retrieval-augmented generation skills, "
            "as shown by their answer to item AIE-9999."
        ),
        "evidence": {"context": ["C6.1: INFERRED (from C6.2, 1 hop(s))"], "served_item_ids": ["AIE-0001"]},
    },
    {
        "canary_id": "must_fail__inference_as_observation",
        "expected": "fail",
        "actual_output": (
            "The candidate demonstrated mastery of C6.1, which they showed clearly "
            "during the assessment."
        ),
        "evidence": {"context": ["C6.1: INFERRED (from C6.2, 1 hop(s))"], "served_item_ids": []},
    },
    {
        "canary_id": "must_pass__inference_labelled",
        "expected": "pass",
        "actual_output": (
            "C6.1 was not directly assessed. It is INFERRED from the candidate's "
            "demonstrated work on C6.2, and should be confirmed before being relied on."
        ),
        "evidence": {"context": ["C6.1: INFERRED (from C6.2, 1 hop(s))"], "served_item_ids": []},
    },
    {
        "canary_id": "must_pass__direct_claim",
        "expected": "pass",
        "actual_output": "The candidate demonstrated C6.2 directly, answering item AIE-0001.",
        "evidence": {"context": ["C6.2: DIRECT"], "served_item_ids": ["AIE-0001"]},
    },
]
