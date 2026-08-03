"""Measure whether a PREREQUISITE edge predicts anything, and only then enable it.

Run from `backend/`:

    PYTHONPATH=. python scripts/validate_prerequisite_edges.py --bank AIE --sessions dumps/
    PYTHONPATH=. python scripts/validate_prerequisite_edges.py --bank AIE --sessions dumps/ --apply

`--apply` is the ONLY thing in this codebase permitted to set `allow_upward_inference` or
`allow_downward_blocking` to true on an authored edge. Everything else ships them false.

WHY THIS EXISTS

The graph's central claim is that failing a prerequisite tells you something about its
dependants. The review's own arithmetic makes the bar concrete: against a 2% false-blocking
target, only an edge whose P(pass child | fail parent) is genuinely near zero can be safely
*enforced* — and no edge in the specification had ever been measured against it.

The statistic, per edge P -> C, over sessions where BOTH nodes received direct evidence:

    n_pf  = sessions where P failed
    k     = of those, sessions where C passed
    p_hat = k / n_pf                        P(pass child | fail parent)

reported with a Wilson 95% interval, because at n = 30 a normal approximation on a
proportion near zero is not honest.

THE CONTRAST MATTERS AS MUCH AS THE RATE

A low `p_hat` means nothing on its own: if the child is hard for everybody, almost nobody
passes it whether or not they passed the parent, and the edge has explained nothing. So
P(pass child | pass parent) is reported beside it, and an edge is validated only when the
RISK DIFFERENCE is positive beyond its own interval — that is, passing the parent actually
raises the chance of passing the child.

SIMULATED SESSIONS CANNOT VALIDATE AN EDGE

A simulated candidate has no dependency structure; an edge "validated" against one has been
validated against the assumption that generated it. Records not marked `source: live` are
counted and reported, and `--apply` refuses to run on them.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.services.competency_graph.models import CompetencyEdge
from app.services.orchestrator import registry
from app.services.orchestrator.session_dump import SOURCE_LIVE, read_sessions

DATA = Path(__file__).resolve().parent.parent / "app" / "data"

# Defaults. `--max-pass-rate 0.25` is the bar for enabling INFERENCE; enabling BLOCKING
# honestly needs ~0.05 or below, since a false block denies a candidate the chance to
# demonstrate a skill they have. Pass --max-pass-rate explicitly for that.
DEFAULT_MIN_PARENT_FAILURES = 30
DEFAULT_MAX_PASS_RATE = 0.25

VALIDATED = "validated"
REFUTED = "refuted"
INSUFFICIENT = "insufficient_data"


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion. (0.0, 1.0) when n is 0."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


@dataclass
class EdgeEvidence:
    """One edge's counts across the corpus."""

    parent: str
    child: str
    parent_failed_child_measured: int = 0
    parent_failed_child_passed: int = 0
    parent_passed_child_measured: int = 0
    parent_passed_child_passed: int = 0
    notes: list[str] = field(default_factory=list)

    def verdict(self, *, min_parent_failures: int, max_pass_rate: float) -> dict:
        n_pf = self.parent_failed_child_measured
        k = self.parent_failed_child_passed
        p_hat = (k / n_pf) if n_pf else None
        lower, upper = wilson_interval(k, n_pf)

        n_pp = self.parent_passed_child_measured
        k_pp = self.parent_passed_child_passed
        p_pass = (k_pp / n_pp) if n_pp else None
        pass_lower, pass_upper = wilson_interval(k_pp, n_pp)

        if n_pf < min_parent_failures:
            status = INSUFFICIENT
            reason = f"only {n_pf} sessions failed {self.parent} with {self.child} measured"
        elif upper > max_pass_rate:
            status = REFUTED
            reason = (
                f"P(pass {self.child} | fail {self.parent}) upper bound "
                f"{upper:.3f} exceeds {max_pass_rate:.2f}"
            )
        elif p_pass is None or n_pp == 0:
            status = INSUFFICIENT
            reason = f"no sessions passed {self.parent} with {self.child} measured"
        elif pass_lower <= upper:
            # The intervals overlap: the child may simply be hard for everyone.
            status = REFUTED
            reason = (
                f"no separation — P(pass|pass)={p_pass:.3f} [{pass_lower:.3f}, "
                f"{pass_upper:.3f}] overlaps P(pass|fail) upper bound {upper:.3f}"
            )
        else:
            status = VALIDATED
            reason = (
                f"P(pass|fail)={p_hat:.3f} (CI upper {upper:.3f}) against "
                f"P(pass|pass)={p_pass:.3f} (CI lower {pass_lower:.3f})"
            )

        return {
            "parent": self.parent,
            "child": self.child,
            "status": status,
            "reason": reason,
            "n_parent_failures": n_pf,
            "n_child_passed_after_parent_failure": k,
            "p_pass_child_given_fail_parent": None if p_hat is None else round(p_hat, 4),
            "ci_lower": round(lower, 4),
            "ci_upper": round(upper, 4),
            "n_parent_passes": n_pp,
            "p_pass_child_given_pass_parent": None if p_pass is None else round(p_pass, 4),
            "contrast_ci_lower": round(pass_lower, 4),
            "notes": self.notes,
        }


def collect(records: list[dict], edges: list[CompetencyEdge]) -> tuple[dict, int, int]:
    """Count each edge across the corpus. Returns (evidence, live, non_live)."""
    evidence = {
        (e.from_id, e.to_id): EdgeEvidence(parent=e.from_id, child=e.to_id) for e in edges
    }
    live = non_live = 0

    for record in records:
        if record.get("source") != SOURCE_LIVE:
            non_live += 1
            continue
        live += 1
        state = record.get("state") or {}
        mastered = set(state.get("graph_direct_mastered_nodes") or [])
        not_mastered = set(state.get("graph_direct_not_mastered_nodes") or [])
        measured = set(state.get("graph_direct_measured_nodes") or []) | mastered | not_mastered

        for (parent, child), entry in evidence.items():
            if child not in measured or parent not in measured:
                continue
            child_passed = child in mastered
            if parent in not_mastered:
                entry.parent_failed_child_measured += 1
                entry.parent_failed_child_passed += int(child_passed)
            elif parent in mastered:
                entry.parent_passed_child_measured += 1
                entry.parent_passed_child_passed += int(child_passed)

    return evidence, live, non_live


def apply_verdicts(graph_path: Path, verdicts: list[dict], corpus: str) -> int:
    """Rewrite the graph file, enabling only validated edges. Returns how many changed."""
    document = json.loads(graph_path.read_text(encoding="utf-8"))
    by_pair = {(v["parent"], v["child"]): v for v in verdicts}
    changed = 0

    for edge in document.get("edges", []):
        if edge.get("relation") != "PREREQUISITE":
            continue
        verdict = by_pair.get((edge.get("from"), edge.get("to")))
        if verdict is None or verdict["status"] == INSUFFICIENT:
            continue

        enable = verdict["status"] == VALIDATED
        metadata = dict(edge.get("metadata") or {})
        metadata.update(
            {
                "validation_status": verdict["status"],
                "validated_at": date.today().isoformat(),
                "corpus": corpus,
                "n_parent_failures": verdict["n_parent_failures"],
                "p_pass_child_given_fail_parent": verdict["p_pass_child_given_fail_parent"],
                "ci_upper": verdict["ci_upper"],
                "p_pass_child_given_pass_parent": verdict["p_pass_child_given_pass_parent"],
            }
        )
        # A refuted edge keeps its metadata and stays disabled. Deleting it would erase
        # the negative result and invite someone to re-author the same hypothesis.
        edge["metadata"] = metadata
        edge["allow_upward_inference"] = enable
        edge["allow_downward_blocking"] = enable
        changed += 1

    graph_path.write_text(json.dumps(document, indent=1, ensure_ascii=False), encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", default=None, help="registered bank id")
    parser.add_argument(
        "--sessions", type=Path, required=True, help="a .jsonl dump or a directory of them"
    )
    parser.add_argument("--min-parent-failures", type=int, default=DEFAULT_MIN_PARENT_FAILURES)
    parser.add_argument("--max-pass-rate", type=float, default=DEFAULT_MAX_PASS_RATE)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite the graph, enabling validated edges. Refuses on simulated data.",
    )
    args = parser.parse_args()

    bank_id = registry.resolve_bank_id(args.bank)
    profile = registry.profile(bank_id)
    if profile.graph_path is None:
        print(f"{bank_id} declares no competency graph", file=sys.stderr)
        return 1
    if not args.sessions.exists():
        print(f"no session corpus at {args.sessions}", file=sys.stderr)
        return 1

    graph = registry.get_graph_service(bank_id)
    prerequisites = [e for e in graph.graph.edges if e.relation == "PREREQUISITE"]
    if not prerequisites:
        print(f"{bank_id} has no PREREQUISITE edges to validate")
        return 0

    records = read_sessions(args.sessions)
    evidence, live, non_live = collect(records, prerequisites)
    verdicts = [
        entry.verdict(
            min_parent_failures=args.min_parent_failures,
            max_pass_rate=args.max_pass_rate,
        )
        for entry in evidence.values()
    ]
    verdicts.sort(key=lambda v: (v["status"], -v["n_parent_failures"]))

    print(f"corpus: {len(records)} records ({live} live, {non_live} not live)")
    print(f"{len(prerequisites)} PREREQUISITE edges in {bank_id}\n")
    print(f"{'edge':<20} {'status':<18} {'n_pf':>5} {'p(pass|fail)':>13} {'ci_up':>7}")
    for verdict in verdicts:
        rate = verdict["p_pass_child_given_fail_parent"]
        print(
            f"{verdict['parent']}->{verdict['child']:<12} {verdict['status']:<18} "
            f"{verdict['n_parent_failures']:>5} "
            f"{'—' if rate is None else f'{rate:.3f}':>13} "
            f"{verdict['ci_upper']:>7.3f}"
        )

    counts = {status: sum(1 for v in verdicts if v["status"] == status)
              for status in (VALIDATED, REFUTED, INSUFFICIENT)}
    print(f"\n{counts[VALIDATED]} validated, {counts[REFUTED]} refuted, "
          f"{counts[INSUFFICIENT]} insufficient data")

    report_path = DATA / f"edge_validity_{bank_id}_{date.today():%Y%m%d}.json"
    report_path.write_text(
        json.dumps(
            {
                "bank_id": bank_id,
                "generated": date.today().isoformat(),
                "corpus": str(args.sessions),
                "records": len(records),
                "live_records": live,
                "non_live_records": non_live,
                "min_parent_failures": args.min_parent_failures,
                "max_pass_rate": args.max_pass_rate,
                "edges": verdicts,
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"report: {report_path}")

    if not args.apply:
        if counts[VALIDATED]:
            print("\n--apply would enable the validated edges above.")
        return 0

    if live == 0:
        print(
            "\nrefusing to --apply: no live sessions in the corpus. A simulated candidate "
            "has no dependency structure, so an edge validated against one has been "
            "validated against its own generator.",
            file=sys.stderr,
        )
        return 1

    changed = apply_verdicts(profile.graph_path, verdicts, str(args.sessions))
    registry.reset_caches()
    print(f"\nrewrote {profile.graph_path}: {changed} edges stamped, "
          f"{counts[VALIDATED]} enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
