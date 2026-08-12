"""Section 6.4, adapted for a world with no human raters — and honest about the gap.

WHAT THIS MEASURES, AND WHAT IT CANNOT

Running a judge five times and taking the median measures SELF-CONSISTENCY. That is worth
measuring: a metric whose verdict flips between runs cannot gate anything, and the flip
rate is the number that says so.

It is NOT agreement. Cohen's kappa needs two rater POPULATIONS, and repeated draws from
one judge are not a second population. A judge can be perfectly self-consistent and
consistently wrong, and no number of repeats will reveal it — the error is in the
expectation, not the variance.

So section 6.4's kappa >= 0.60 bar is not merely unmet here, it is unmeasurable, and every
judged result stays descriptive as a result. The canary set is the partial substitute: it
supplies cases whose correct verdict IS known, so a systematically wrong judge is caught
even though a systematically biased one on real cases would not be.
"""

from __future__ import annotations

import statistics

#: A metric whose verdict flips this often across identical inputs cannot support a
#: threshold, whatever its mean score is.
MAX_FLIP_RATE = 0.10


def summarise_runs(scores: list[float], threshold: float) -> dict:
    """Median, spread and flip rate over repeated judgements of ONE case.

    The median, not the mean: a judge that returns 0.9, 0.9, 0.9, 0.9, 0.1 has one
    outlier, and the mean moves 0.16 for it while the median does not move at all.
    """
    if not scores:
        return {"runs": 0, "median": None, "flip_rate": None, "usable": False}
    verdicts = [s >= threshold for s in scores]
    passes = sum(verdicts)
    flips = min(passes, len(verdicts) - passes) / len(verdicts)
    ordered = sorted(scores)
    return {
        "runs": len(scores),
        "median": round(statistics.median(scores), 4),
        "p2_5": round(ordered[0], 4),
        "p97_5": round(ordered[-1], 4),
        "verdict": statistics.median(scores) >= threshold,
        "flip_rate": round(flips, 4),
        # A case whose repeated verdicts straddle the threshold is routed to human
        # adjudication by section 6.4. There are no humans here, so it is counted neither
        # way and reported as unresolved — which is the honest handling of a case the
        # instrument cannot decide.
        "straddles_threshold": 0 < flips,
        "usable": flips <= MAX_FLIP_RATE,
    }


def canary_verdict(results: list[dict]) -> dict:
    """Did the judge get the cases whose answer is known?

    A single miss invalidates the run. These are not marginal cases — they are a report
    citing an item that was never served, and a report presenting an inference as an
    observation. A judge that passes the second one is not measuring what G-01 measures.
    """
    missed = [
        r["canary_id"]
        for r in results
        if (r.get("verdict") is True) != (r.get("expected") == "pass")
    ]
    return {
        "canaries": len(results),
        "missed": missed,
        "valid": not missed,
        "note": (
            "A missed canary invalidates every judged score in the run. The judge is not "
            "measuring the property the metric names."
        ),
    }


def reliability_report(per_case: dict[str, dict], canaries: dict) -> dict:
    """The section-6.4 block, with its limitation stated in the output rather than nearby."""
    usable = [c for c in per_case.values() if c.get("usable")]
    flip_rates = [c["flip_rate"] for c in per_case.values() if c.get("flip_rate") is not None]
    straddling = [k for k, c in per_case.items() if c.get("straddles_threshold")]
    return {
        "cases": len(per_case),
        "cases_usable": len(usable),
        "mean_flip_rate": round(statistics.fmean(flip_rates), 4) if flip_rates else None,
        "max_flip_rate_allowed": MAX_FLIP_RATE,
        "cases_straddling_threshold": straddling,
        "canaries": canaries,
        "cohens_kappa": None,
        "kappa_note": (
            "NOT COMPUTABLE. Cohen's kappa requires two rater populations; repeated draws "
            "from one judge are one population sampled repeatedly. What is measured here "
            "is self-consistency, and a judge can be perfectly self-consistent and "
            "consistently wrong. Section 6.4's kappa >= 0.60 bar is therefore unmet and "
            "unmeasurable, so EVERY judged result in this study is descriptive and none "
            "of it has authority over a Tier-2 or Tier-3 endpoint."
        ),
    }
