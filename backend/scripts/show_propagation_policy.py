#!/usr/bin/env python3
"""Print what each prerequisite edge is permitted to do, and why it is not.

    python -m scripts.show_propagation_policy --bank AIE
    python -m scripts.show_propagation_policy --bank AIE --what-if inference=on,blocking=on

The question this answers is "I set GRAPH_UPWARD_INFERENCE_ENABLED=true and nothing
changed — why?". Three levels have to agree before an edge acts, and without this the
operator has to read a `.env`, a graph file's `policy` block, and thirty per-edge flags,
then do the AND in their head.

`--what-if` resolves against a hypothetical deployment without needing that deployment to
exist, which is the safe way to answer "what would turning this on actually enable?".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for key, value in {
    "LITELLM_BASE_URL": "http://localhost:4000",
    "LITELLM_API_KEY": "placeholder",
    "LITELLM_MODEL": "placeholder-model",
    "E2B_API_KEY": "placeholder",
}.items():
    os.environ.setdefault(key, value)


def _parse_what_if(raw: str) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        out[key.strip().lower()] = value.strip().lower() in ("on", "true", "yes", "1")
    unknown = sorted(set(out) - {"inference", "blocking"})
    if unknown:
        raise SystemExit(f"unknown --what-if key(s): {unknown}; expected inference/blocking")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default=None, help="registered bank id (default: ACTIVE_BANK)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--only-enabled", action="store_true")
    parser.add_argument(
        "--what-if",
        default="",
        help="resolve against a hypothetical deployment, e.g. 'inference=on,blocking=on'",
    )
    args = parser.parse_args()

    from app.config.settings import settings
    from app.services.competency_graph import load_competency_graph
    from app.services.competency_graph.policy import describe, resolve_policy
    from app.services.orchestrator import registry

    bank_id = registry.resolve_bank_id(args.bank)
    profile = registry.profile(bank_id)
    if profile.graph_path is None:
        raise SystemExit(f"bank {bank_id} declares no competency graph")

    what_if = _parse_what_if(args.what_if)
    inference = what_if.get("inference", settings.graph_upward_inference_enabled)
    blocking = what_if.get("blocking", settings.graph_descendant_blocking_enabled)

    # Loaded from disk rather than through the registry, because the registry returns the
    # graph with the policy ALREADY applied — asking it what the policy is would be asking
    # the answer to explain itself.
    resolved = resolve_policy(
        load_competency_graph(profile.graph_path),
        deployment_inference=inference,
        deployment_blocking=blocking,
        deployment_minimum_failures_to_block=settings.graph_minimum_failures_to_block,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "bank_id": bank_id,
                    "graph": str(profile.graph_path),
                    "hypothetical": bool(what_if),
                    "summary": resolved.summary(),
                    "edges": [d.as_dict() for d in resolved.decisions],
                },
                indent=1,
            )
        )
        return

    print(f"bank {bank_id}  graph {Path(profile.graph_path).name}")
    if what_if:
        print(f"  HYPOTHETICAL deployment: inference={inference} blocking={blocking}")
        print("  (nothing is changed by running this)")
    for line in describe(resolved, only_enabled=args.only_enabled):
        print(line)

    summary = resolved.summary()
    if summary["inert_because"]:
        print("\ninert because:")
        for reason, count in summary["inert_because"].items():
            print(f"  {count:>3} x {reason}")
    if summary["bank_policy"]["notes"]:
        print(f"\nbank policy notes: {summary['bank_policy']['notes']}")


if __name__ == "__main__":
    main()
