#!/usr/bin/env python3
"""Generate the frozen simulee cohorts. Run ONCE, from the branch that has the graph.

    python -m evaluation.make_cohort --n 4000 --seed 42 --out ../eval-results/cohorts

Writes one file per DGP arm. Both approach branches then read those files, so a reviewer
can verify the two arms scored the same people — which is the only thing that makes the
paired analysis a paired analysis.
"""

from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=4000, help="target cohort size (rounded up to a balanced design)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--bank", default="AIE")
    parser.add_argument("--out", default="eval-results/cohorts")
    parser.add_argument(
        "--wrong-edge-fraction",
        type=float,
        default=0.25,
        help="DGP-2 only: share of graph edges the world does not have",
    )
    parser.add_argument("--dgp", nargs="*", default=None, help="subset of DGP arms")
    parser.add_argument(
        "--persona",
        nargs="*",
        default=["P01"],
        help="persona ids (see evaluation/personas.py). One cohort file per DGP x persona.",
    )
    args = parser.parse_args()

    from app.services.orchestrator import registry
    from evaluation.dgp import DGP_ARMS, build_cohort, read_graph_prerequisites

    bank_id = registry.resolve_bank_id(args.bank)
    bank = registry.get_bank(bank_id)
    profile = registry.profile(bank_id)
    mains = bank.variables()

    if profile.graph_path is None or not Path(profile.graph_path).exists():
        raise SystemExit(
            f"bank {bank_id} declares no competency graph file — cohort generation needs "
            "the authored prerequisite edges, so run this from the graph-augmented branch"
        )
    graph_edges = read_graph_prerequisites(profile.graph_path)

    out = Path(args.out)
    for dgp in (args.dgp or DGP_ARMS):
        for persona in args.persona:
            cohort = build_cohort(
                dgp=dgp,
                n=args.n,
                seed=args.seed,
                bank=bank,
                bank_id=bank_id,
                mains=mains,
                graph_prerequisites=graph_edges,
                wrong_edge_fraction=args.wrong_edge_fraction,
                persona=persona,
            )
            # Persona is in the FILENAME, not only in the records. A run's results are
            # named after its cohort, so a persona that appeared only inside the file
            # would produce two result sets that overwrite each other.
            name = f"cohort_{dgp}_{persona}_n{len(cohort.simulees)}_seed{args.seed}.json"
            path = cohort.save(out / name)
            print(f"{dgp:8s} {persona} {len(cohort.simulees):5d} simulees -> {path}")
            print(f"         {cohort.notes}")


if __name__ == "__main__":
    main()
