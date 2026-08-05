#!/usr/bin/env python3
"""Run the judged layer. Lock 5: prints the cost first and refuses without --confirm-cost.

    EVAL_LIVE_LLM=1 /tmp/evalvenv/bin/python -m evaluation.judged.run_judged \
        --canaries-only --confirm-cost

CANARIES FIRST, ALWAYS. If the judge misscores a case whose answer is known, every other
score it produced in the same run is void — so there is no reason to pay for the rest
before that check has passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.judged import goldens, reliability  # noqa: E402
from evaluation.judged.metrics import DEEPEVAL_PIN, g01_prefilter  # noqa: E402

RUNS_PER_CASE = 5


def _judge_model():
    """The judge, pinned by fingerprint and routed through the same proxy as everything else.

    TLS verification follows LITELLM_SSL_VERIFY, as the rest of the project does — the
    proxy is addressed by IP with a self-signed certificate. Read from configuration
    rather than hard-coded off, so an environment with a valid certificate still verifies.
    """
    import httpx
    from deepeval.models import GPTModel

    verify = os.environ.get("LITELLM_SSL_VERIFY", "true").strip().lower() not in (
        "false", "0", "no",
    )
    return GPTModel(
        model=os.environ.get("EVAL_JUDGE_MODEL", "gpt-4o-mini"),
        base_url=os.environ["LITELLM_BASE_URL"],
        api_key=os.environ["LITELLM_API_KEY"],
        # This judge only accepts the default temperature. That is not a workaround:
        # deepeval defaults to 0 for determinism, and a judge pinned to 0 would make the
        # flip-rate check vacuous by construction — it would measure the absence of
        # sampling rather than the stability of the metric. At the default temperature the
        # flip rate measures what section 6.4 actually wants to know.
        temperature=float(os.environ.get("EVAL_JUDGE_TEMPERATURE", "1")),
        http_client=httpx.Client(verify=verify, timeout=120.0),
        async_http_client=httpx.AsyncClient(verify=verify, timeout=120.0),
    )


def run_canaries(metric, threshold: float) -> tuple[list[dict], dict]:
    from deepeval.test_case import LLMTestCase

    results = []
    per_case = {}
    for canary in goldens.CANARIES:
        # The deterministic pre-filter runs first and can settle the case without a call.
        prefilter = g01_prefilter(canary["actual_output"], canary["evidence"])
        scores = []
        for _ in range(RUNS_PER_CASE):
            case = LLMTestCase(
                input="Summarise this candidate's competencies.",
                actual_output=canary["actual_output"],
                context=canary["evidence"]["context"],
            )
            metric.measure(case)
            scores.append(float(metric.score))
        summary = reliability.summarise_runs(scores, threshold)
        per_case[canary["canary_id"]] = summary
        results.append(
            {
                "canary_id": canary["canary_id"],
                "expected": canary["expected"],
                "verdict": summary["verdict"],
                "median": summary["median"],
                "flip_rate": summary["flip_rate"],
                "prefilter_passed": prefilter.passed,
                "prefilter_failures": list(prefilter.failures),
            }
        )
    return results, per_case


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canaries-only", action="store_true")
    parser.add_argument("--out", default="eval-results/judged.json")
    parser.add_argument("--confirm-cost", action="store_true")
    args = parser.parse_args()

    if os.environ.get("EVAL_LIVE_LLM") != "1":
        raise SystemExit("set EVAL_LIVE_LLM=1 — this makes billed model calls")
    if os.environ.get("LITELLM_API_KEY", "placeholder") == "placeholder":
        raise SystemExit("LITELLM_API_KEY is the placeholder; refusing to pretend to run")

    calls = len(goldens.CANARIES) * RUNS_PER_CASE
    print(f"deepeval {DEEPEVAL_PIN}; judge {os.environ.get('EVAL_JUDGE_MODEL', 'gpt-4o-mini')}")
    print(f"estimated judged calls: {calls}")
    if not args.confirm_cost:
        raise SystemExit("pass --confirm-cost to proceed")

    from evaluation.judged.metrics import build_metrics

    metrics = build_metrics(_judge_model())
    metric = metrics["G-01_evidence_integrity"]

    results, per_case = run_canaries(metric, metric.threshold)
    canaries = reliability.canary_verdict(results)
    report = {
        "deepeval": DEEPEVAL_PIN,
        "judge_model": os.environ.get("EVAL_JUDGE_MODEL", "gpt-4o-mini"),
        "runs_per_case": RUNS_PER_CASE,
        "canary_results": results,
        "reliability": reliability.reliability_report(per_case, canaries),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1), encoding="utf-8")

    for row in results:
        mark = "OK " if (row["verdict"] is True) == (row["expected"] == "pass") else "MISS"
        print(f"  [{mark}] {row['canary_id']}: median={row['median']} flip={row['flip_rate']}")
    print(f"\ncanaries valid: {canaries['valid']}  missed: {canaries['missed']}")
    print(f"kappa: {report['reliability']['cohens_kappa']} — {report['reliability']['kappa_note'][:60]}...")
    print(f"written to {out}")


if __name__ == "__main__":
    main()
