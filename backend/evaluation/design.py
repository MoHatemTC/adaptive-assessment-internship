"""The screening design: which configurations get run, and what they can tell you.

A full factorial over the seven sweepable factors is 2,592 cells and 4.1M sessions. It is
not runnable, and it would spend all of that resolving three- and four-factor interactions
nobody would act on.

RESOLUTION IV, AND WHAT THAT COSTS

2^(7-2) = 32 runs. Every main effect is estimable and clear of two-factor interactions;
two-factor interactions are aliased with each other and cannot be separated. That is the
right trade for a screening stage whose only question is "which of these knobs matters",
and it is stated here rather than discovered later — the alias structure is written into
`design.json` so a reader can see which pairs are confounded before they read an effect.

The generators are M = D*K*C and E = D*K*S. Chosen so the two CATEGORICAL factors carry
the generated columns: a categorical factor has no meaningful centre point and no
curvature, so aliasing it costs less than aliasing a continuous one whose response surface
the next stage would want to trace.

CENTRE POINTS

Four, at the midpoint of every continuous factor. They buy two things a fractional
factorial cannot supply on its own: an estimate of pure error that does not assume the
model is right, and a curvature test — if the centre response sits well off the mean of
the factorial points, the response is not linear and the main effects are describing a
plane through a curve.

Their pure-error estimate has 3 degrees of freedom. That is thin, and every effect
reported from this design says so.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from pathlib import Path

#: The seven factors, their environment keys, and their low/high screening levels.
#:
#: Low/high are the plan's §4.2 settings. They are deliberately wide: screening asks
#: whether a factor matters anywhere in its range, and a narrow contrast answers that
#: question with a false negative.
FACTORS: dict[str, dict] = {
    "D": {
        "env": "GRAPH_MAXIMUM_PROPAGATION_DEPTH",
        "low": "1",
        "high": "4",
        "centre": "2",
        "continuous": True,
        "label": "propagation depth",
    },
    "K": {
        "env": "GRAPH_MINIMUM_CORROBORATIONS",
        "low": "1",
        "high": "3",
        "centre": "2",
        "continuous": True,
        "label": "corroboration requirement",
    },
    "C": {
        "env": "GRAPH_MINIMUM_PROPAGATION_CONFIDENCE",
        "low": "0.80",
        "high": "0.95",
        "centre": "0.875",
        "continuous": True,
        "label": "minimum propagation confidence",
    },
    "S": {
        "env": "GRAPH_STRONG_SUCCESS_THRESHOLD",
        "low": "0.80",
        "high": "0.95",
        "centre": "0.875",
        "continuous": True,
        "label": "success score threshold",
    },
    "L": {
        "env": "GRAPH_UPWARD_DECAY",
        "low": "0.3",
        "high": "0.7",
        "centre": "0.5",
        "continuous": True,
        "label": "upward decay (lambda)",
    },
    "M": {
        "env": "GRAPH_INFERENCE_MODALITIES",
        "low": "code",
        "high": "code,voice",
        "centre": None,
        "continuous": False,
        "label": "modality allowlist",
    },
    "E": {
        "env": "GRAPH_ACCEPTED_VALIDATION_STATUSES",
        "low": "validated",
        "high": "",  # empty = no deployment opinion = every non-refuted status
        "centre": None,
        "continuous": False,
        "label": "edge allowlist",
    },
}

#: Base factors get their own column; generated ones are products of base columns.
BASE = ("D", "K", "C", "S", "L")
GENERATORS = {"M": ("D", "K", "C"), "E": ("D", "K", "S")}

N_CENTRE_POINTS = 4

#: One candidate subsample per centre-point replicate. Arbitrary but fixed: the design has
#: to be reproducible, and what matters is only that they DIFFER — a replicate that reuses
#: the shared sample measures the arithmetic rather than the sampling.
CENTRE_SAMPLE_SEEDS = (10_007, 20_011, 30_013, 40_017)


@dataclass(frozen=True)
class Cell:
    """One configuration to run, with everything needed to identify and reproduce it."""

    cell_id: str
    kind: str  # "factorial" | "centre"
    levels: dict[str, str]  # factor -> "low" | "high" | "centre"
    env: dict[str, str] = field(default_factory=dict)
    # 0 = the cohort's shared paired sample, which every factorial cell uses so that the
    # contrast between them is paired. Centre points get distinct non-zero values so their
    # replicates differ in WHICH candidates they ran on.
    #
    # Without this, replicates in a deterministic harness return identical numbers, pure
    # error is exactly 0, and the |effect| > 2*SE activity rule divides by zero-ish and
    # declares every effect active — including one of +0.0017. Measured that way in the
    # first S1 run; see amendment 13.5.
    sample_seed: int = 0

    def as_dict(self) -> dict:
        return {
            "cell_id": self.cell_id,
            "kind": self.kind,
            "levels": dict(self.levels),
            "env": dict(self.env),
            "sample_seed": self.sample_seed,
        }


def _sign_label(sign: int) -> str:
    return "high" if sign > 0 else "low"


def _env_for(levels: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for factor, level in levels.items():
        spec = FACTORS[factor]
        value = spec[level] if level != "centre" else spec["centre"]
        if value is None:
            raise ValueError(f"factor {factor} has no centre level")
        env[spec["env"]] = value
    return env


def _cell_id(index: int, levels: dict[str, str], kind: str) -> str:
    """A name that says what the cell IS, not merely which number it is.

    A run directory full of `cell_00`..`cell_35` makes every mistake invisible; one where
    the directory reads `r07_D-hi_K-lo_...` makes a mislabelled cell obvious at a glance.
    """
    prefix = "c" if kind == "centre" else "r"
    parts = [f"{f}-{levels[f][:2]}" for f in FACTORS if f in levels]
    return f"{prefix}{index:02d}_" + "_".join(parts)


def build_design() -> list[Cell]:
    """The 32 factorial cells plus 4 centre points."""
    cells: list[Cell] = []

    for index, signs in enumerate(itertools.product((-1, 1), repeat=len(BASE))):
        assignment = dict(zip(BASE, signs))
        for generated, sources in GENERATORS.items():
            product = 1
            for source in sources:
                product *= assignment[source]
            assignment[generated] = product

        levels = {f: _sign_label(assignment[f]) for f in FACTORS}
        cells.append(
            Cell(
                cell_id=_cell_id(index, levels, "factorial"),
                kind="factorial",
                levels=levels,
                env=_env_for(levels),
            )
        )

    # Centre points. M and E are categorical and have no midpoint, so the replicates are
    # split evenly across their two levels rather than given a fabricated middle value.
    # Reporting a "centre" for a categorical factor would invent a configuration that does
    # not exist and then estimate curvature from it.
    for index in range(N_CENTRE_POINTS):
        levels = {f: "centre" for f in FACTORS if FACTORS[f]["continuous"]}
        levels["M"] = "high" if index % 2 else "low"
        levels["E"] = "high" if (index // 2) % 2 else "low"
        cells.append(
            Cell(
                cell_id=_cell_id(index, levels, "centre"),
                kind="centre",
                levels=levels,
                env=_env_for(levels),
                # Distinct per replicate, and fixed so the design is reproducible.
                sample_seed=CENTRE_SAMPLE_SEEDS[index % len(CENTRE_SAMPLE_SEEDS)],
            )
        )

    return cells


def alias_structure() -> dict[str, list[str]]:
    """Which two-factor interactions cannot be told apart from each other.

    Reported with every screening result. A Resolution-IV design is not a design in which
    interactions are absent; it is one in which they are confounded in a KNOWN pattern,
    and the difference matters when an effect comes out large.
    """
    words = ["".join(sorted(("M",) + GENERATORS["M"])), "".join(sorted(("E",) + GENERATORS["E"]))]
    # The defining relation is I = MDKC = EDKS, and their product.
    third = set(GENERATORS["M"]) ^ set(GENERATORS["E"]) | {"M", "E"}
    words.append("".join(sorted(third)))

    aliases: dict[str, list[str]] = {}
    factors = list(FACTORS)
    for a, b in itertools.combinations(factors, 2):
        pair = {a, b}
        partners = []
        for word in words:
            other = set(word) ^ pair
            if len(other) == 2:
                partners.append("".join(sorted(other)))
        if partners:
            aliases["".join(sorted(pair))] = sorted(set(partners))
    return aliases


def design_document() -> dict:
    """Everything a reader needs to interpret an effect from this design."""
    cells = build_design()
    return {
        "resolution": "IV",
        "runs": len(cells),
        "factorial_runs": sum(1 for c in cells if c.kind == "factorial"),
        "centre_points": sum(1 for c in cells if c.kind == "centre"),
        "pure_error_df": N_CENTRE_POINTS - 1,
        "generators": {k: "*".join(v) for k, v in GENERATORS.items()},
        "defining_relation": "I = D*K*C*M = D*K*S*E",
        "factors": {
            name: {
                "env": spec["env"],
                "label": spec["label"],
                "low": spec["low"],
                "high": spec["high"],
                "centre": spec["centre"],
                "continuous": spec["continuous"],
            }
            for name, spec in FACTORS.items()
        },
        "aliased_two_factor_interactions": alias_structure(),
        "caveats": [
            "Main effects are clear of two-factor interactions; two-factor interactions "
            "are aliased with each other and cannot be separated by this design.",
            f"Pure error has {N_CENTRE_POINTS - 1} degrees of freedom. The 2-sigma rule "
            "for declaring a factor active is a screening heuristic, not a hypothesis test.",
            "Curvature is undefined for M and E, which are categorical.",
            "Centre points draw DIFFERENT candidate subsamples from the factorial cells, "
            "so pure error includes sampling variability. That is what makes it pure "
            "error at all in a deterministic harness — but it means the curvature "
            "estimate carries that sampling too, and is only detectable above it.",
        ],
        "cells": [c.as_dict() for c in cells],
    }


def write_design(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(design_document(), indent=1), encoding="utf-8")
    return path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Emit the screening design")
    parser.add_argument("--out", default="", help="write design.json here")
    args = parser.parse_args()

    document = design_document()
    if args.out:
        print(f"wrote {write_design(Path(args.out))}")
        return

    print(f"Resolution {document['resolution']}: {document['runs']} runs "
          f"({document['factorial_runs']} factorial + {document['centre_points']} centre)")
    print(f"Defining relation: {document['defining_relation']}")
    print("\nAliased two-factor interactions:")
    for pair, partners in sorted(document["aliased_two_factor_interactions"].items()):
        print(f"  {pair} = {' = '.join(partners)}")
    print("\nCaveats:")
    for caveat in document["caveats"]:
        print(f"  - {caveat}")


if __name__ == "__main__":
    main()
