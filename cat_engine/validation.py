"""Is the shipped data sound?

Two checks, both about the BANKS AND GRAPHS this package ships rather than about the code
that reads them. They live here because they import nothing but the engine, they are the
only things that can answer their questions, and a bank whose precision target is
unreachable or a graph whose prerequisite edges are refuted is a defect that no amount of
correct code fixes.

THE BANK FLOOR

`analyse_bank` asks whether a bank can reach the configured standard-error target at all.
The answer is arithmetic, not simulation: Fisher information per item at a given ability,
against the precision the stopping rule demands. When it cannot, the report is a WORK ORDER
— how many more items of what discrimination each variable needs — because "this bank is
too thin" is not something an author can act on.

PREREQUISITE EDGE VALIDITY

`collect` asks, of a real session corpus, how often a candidate passed a child having
failed its parent. A prerequisite edge claims that should be rare; the Wilson interval says
whether the corpus has enough observations to claim anything at all. An edge is VALIDATED,
REFUTED or INSUFFICIENT_DATA, and only the first licenses turning inference on for it.

The default bar is `0.25` for enabling INFERENCE. Enabling BLOCKING honestly needs ~0.05 or
below, because a false block denies a candidate the chance to demonstrate a skill they have.

WHERE THESE CAME FROM

The simulation harness and the bank-authoring scripts, both removed. These two survived
because they check the shipped artefacts rather than produce them, and because the test
suite asserts against them — `tests/test_session.py` for the floor, `tests/test_edge_validity.py`
for the edges.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from cat_engine.engine.config.paths import DATA_DIR
from cat_engine.engine.config.settings import settings
from cat_engine.engine.schemas.adaptive import Item
from cat_engine.engine.services.adaptive.irt import fisher_information
from cat_engine.engine.services.competency_graph.models import CompetencyEdge
from cat_engine.engine.services.orchestrator.session_dump import SOURCE_LIVE

__all__ = [
    "INSUFFICIENT",
    "REFUTED",
    "VALIDATED",
    "EdgeEvidence",
    "analyse_bank",
    "collect",
    "wilson_interval",
]

DATA = DATA_DIR


# --- the bank floor -----------------------------------------------------------

#: The prior the CAT starts from, matching `tests/test_session.py`.
PRIOR_SD = 1.7

#: Discrimination for a hypothetical replacement item. The median of what the bank already
#: contains, so the counts describe items this author could plausibly write — not an
#: idealised item nobody has managed to produce here.
def _median_discrimination(items: list[Item]) -> float:
    values = sorted(float(i.a) for i in items)
    return values[len(values) // 2] if values else 1.0


def analyse_bank(bank_id: str, theta: float = 0.0) -> dict:
    from cat_engine.engine.services.orchestrator import registry

    bank = registry.get_bank(bank_id)
    pools: dict[str, list[Item]] = defaultdict(list)
    for unified in bank.all_items():
        for measure in unified.measures:
            pools[measure.variable].append(
                Item(
                    id=unified.item_id, competency=measure.variable, stem="",
                    options=["a", "b"], answer_index=0,
                    a=unified.cat.a, b=unified.cat.b, c=unified.cat.c,
                )
            )

    required = 1.0 / (settings.cat_se_target**2)
    prior = 1.0 / (PRIOR_SD**2)

    rows = []
    for variable, items in sorted(pools.items()):
        total = sum(fisher_information(theta, i.a, i.b, i.c) for i in items)
        attained = prior + total
        deficit = max(0.0, required - attained)

        # A replacement item at b = theta, with the bank's own median discrimination and
        # the median guessing floor of this variable's pool.
        a_new = _median_discrimination(items)
        c_new = sorted(float(i.c) for i in items)[len(items) // 2]
        per_item = fisher_information(theta, a_new, theta, c_new)

        rows.append({
            "variable": variable,
            "items": len(items),
            "information_total": round(total, 4),
            "attained_precision": round(attained, 4),
            "required_precision": round(required, 4),
            "best_attainable_se": round(attained**-0.5, 4),
            "deficit": round(deficit, 4),
            "reachable": deficit <= 0.0,
            "replacement_item_information": round(per_item, 4),
            # The lower bound: perfectly targeted items, at the bank's own median quality.
            "items_needed": 0 if deficit <= 0 else int(-(-deficit // per_item)),
        })

    unreachable = [r for r in rows if not r["reachable"]]
    return {
        "bank_id": bank_id,
        "theta": theta,
        "se_target": settings.cat_se_target,
        "prior_sd": PRIOR_SD,
        "variables": len(rows),
        "unreachable": len(unreachable),
        "items_needed_total": sum(r["items_needed"] for r in rows),
        "worst": sorted(unreachable, key=lambda r: -r["items_needed"])[:5],
        "rows": rows,
    }


# --- prerequisite edge validity ----------------------------------------------

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
