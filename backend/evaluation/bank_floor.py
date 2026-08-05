#!/usr/bin/env python3
"""What it would take to make every sub-competency measurable. Costs no sessions.

    python -m evaluation.bank_floor --out eval-results/bank_floor.json

THE FINDING THIS EXISTS TO MAKE ACTIONABLE

16 of AIE's 33 sub-competencies cannot reach `cat_se_target` with their ENTIRE item pool
administered to a candidate at theta = 0. Five of DA's six cannot. Those variables can only
ever stop on the question budget, and no propagation setting changes that — it bounds
every accuracy figure the programme will ever produce.

"16 of 33 are short" is a diagnosis. It is not yet a work order, because it does not say
by how much, or what would close it. This does.

WHAT IS COMPUTED

Precision adds: posterior precision after administering a set of items is
`1/prior_sd^2 + sum(I_i)`, and the target is `1/cat_se_target^2`. So the deficit for a
variable is a number in precision units, and the question "how many more items" has an
answer as soon as you say what an item is worth.

An item is worth most when its difficulty sits at the candidate's ability, so the cheapest
possible fix is items at `b = theta`. That makes the counts here a LOWER BOUND — the
minimum number of perfectly-targeted items — which is the right way round: if the floor is
unaffordable at best case, it is unaffordable.

WHY theta = 0 AND WHY THAT IS GENEROUS

Reported at theta = 0 because that is where a bank is most informative and where the
existing test asserts. A candidate at theta = 2 has less information available, so every
count here understates the real requirement.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.config.settings import settings
from app.schemas.adaptive import Item
from app.services.adaptive.irt import fisher_information

#: The prior the CAT starts from, matching `tests/test_session.py`.
PRIOR_SD = 1.7

#: Discrimination for a hypothetical replacement item. The median of what the bank already
#: contains, so the counts describe items this author could plausibly write — not an
#: idealised item nobody has managed to produce here.
def _median_discrimination(items: list[Item]) -> float:
    values = sorted(float(i.a) for i in items)
    return values[len(values) // 2] if values else 1.0


def analyse_bank(bank_id: str, theta: float = 0.0) -> dict:
    from app.services.orchestrator import registry

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


def render(reports: list[dict]) -> str:
    lines = ["# Bank measurement floor — what it would take to fix it\n"]
    add = lines.append
    add("Information adds, so a variable's shortfall is a number in precision units and")
    add("\"how many more items\" has an exact answer. Counts below assume items at")
    add("`b = theta` with the bank's own median discrimination — the cheapest possible")
    add("fix, so they are a **lower bound**.\n")

    for report in reports:
        add(f"## {report['bank_id']}\n")
        add(f"- variables: **{report['variables']}**, of which "
            f"**{report['unreachable']} cannot reach SE {report['se_target']}** with their "
            f"entire pool at theta = {report['theta']}")
        add(f"- items needed to close every gap: **{report['items_needed_total']}** "
            f"(lower bound)\n")
        if not report["unreachable"]:
            add("Every variable is reachable.\n")
            continue
        add("| variable | items | best attainable SE | deficit | items needed |")
        add("|---|---:|---:|---:|---:|")
        for row in sorted(
            (r for r in report["rows"] if not r["reachable"]),
            key=lambda r: -r["items_needed"],
        ):
            add(f"| {row['variable']} | {row['items']} | {row['best_attainable_se']:.3f} | "
                f"{row['deficit']:.3f} | **{row['items_needed']}** |")
        add("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--banks", nargs="*", default=["AIE", "DA", "PY"])
    parser.add_argument("--theta", type=float, default=0.0)
    parser.add_argument("--out", default="eval-results/bank_floor.json")
    args = parser.parse_args()

    reports = [analyse_bank(b, args.theta) for b in args.banks]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(reports, indent=1), encoding="utf-8")
    out.with_suffix(".md").write_text(render(reports), encoding="utf-8")
    print(render(reports))
    print(f"written to {out} and {out.with_suffix('.md')}")


if __name__ == "__main__":
    main()
