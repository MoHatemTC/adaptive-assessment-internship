"""The data-generating process, and the cohort file both arms read.

THE DECISION THIS FILE MAKES

Validation blocker B2: the test plan specifies 2,000 simulees as "8 levels x 5 families x
50" and never says how a RESPONSE is produced. That omission decides the study. If
responses come from the same 3PL the CAT scores with and no prerequisite structure exists,
Approach C's propagation injects evidence with no basis in the data. If the generator is
built with the graph's own prerequisite structure, C is validated against a world
constructed to make it correct.

So there are four arms and all four are run:

    DGP-0  null      no prerequisite structure; responses from the scoring model alone.
                     C's FALSE-POSITIVE arm. If C beats B here, the benefit is an
                     artefact and nothing else in the study means anything.
    DGP-1  matched   true structure identical to the graph's PREREQUISITE edges.
                     The upper bound on achievable benefit.
    DGP-2  partial   a fraction of the graph's edges are absent from the truth, and an
                     equal number of edges the graph does not assert are present in it.
                     The realistic case, and the only one that prices a wrong edge.
    DGP-3  noisy     DGP-1 plus a slip parameter and grader error.

THE COHORT IS A FILE, NOT A FUNCTION

Approach B's branch has no competency graph module and no graph data file, so it cannot
derive node-level truth even if it wanted to. Both arms therefore load ONE frozen cohort
file, generated once here. That is not a workaround: section 6 requires the candidate
intake to be frozen across arms, and a cohort on disk with a manifest is what "frozen"
means operationally. It also makes the pairing auditable — a reviewer can check that arm B
and arm C scored the same person.

TRUE NODE MASTERY

Each sub-competency gets a difficulty read off the bank items that measure it, so the
truth is tied to the instrument rather than invented beside it. Mastery is then drawn
against the simulee's ability, and — under DGP-1/2/3 — forced to 0 when a TRUE
prerequisite parent is not mastered, with probability `edge_strength`. A strength below
1.0 matters: a deterministic gate would make the prerequisite relation perfectly
detectable and every inference trivially right.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

DGP_ARMS = ("DGP-0", "DGP-1", "DGP-2", "DGP-3")

PROFILE_FAMILIES = (
    "monotonic",
    "borderline",
    "contradictory",
    "shared_core_asymmetric",
    "noisy",
)

# Eight true-ability strata, as the plan's section 5.3 asks for. They spread the truth over
# the scale; they are NOT the reported scale, which has five bands. Placed inside [-2.8,
# 2.8] because a bank cannot measure a candidate it has no item within reach of, and a
# simulee at theta 4.0 would only ever be reporting bank exhaustion.
LEVEL_STRATA: tuple[float, ...] = (-2.8, -2.0, -1.2, -0.4, 0.4, 1.2, 2.0, 2.8)

# Where a "borderline" simulee sits: on a cut point of the common reporting scale, which is
# where an exact-level endpoint is hardest and where the two arms most often disagree.
BORDERLINE_POINTS: tuple[float, ...] = (-2.4, -0.8, 0.8, 2.4)


def _seed_of(*parts: object) -> int:
    """A stable 63-bit seed from any tuple of identifiers.

    Deliberately hashed rather than counted: response draws must be reproducible from
    (simulee, item) alone, with no dependence on the ORDER items were administered in.
    Order-dependence is what would break the pairing, because the whole point of Approach C
    is that it administers a different sequence.
    """
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8)
    return int.from_bytes(digest.digest(), "big") >> 1


@dataclass
class Simulee:
    """One simulated candidate, with everything a response draw needs."""

    simulee_id: str
    family: str
    stratum: int
    theta: dict[str, float]
    # sub-competency id -> 1 mastered / 0 not. Empty under DGP-0, which has no node truth.
    nodes: dict[str, int] = field(default_factory=dict)
    # Upper asymptote on any response: the probability a fully able candidate still slips.
    slip_ceiling: float = 1.0
    # SD of grader error added to a non-MCQ score before it is reported.
    grader_error_sd: float = 0.0


@dataclass
class Cohort:
    """A frozen simulated intake. Written once, read by every arm."""

    dgp: str
    seed: int
    bank_id: str
    mains: list[str]
    simulees: list[Simulee]
    # The structure the WORLD has. Under DGP-0 it is empty.
    true_prerequisites: list[tuple[str, str]] = field(default_factory=list)
    # The structure the GRAPH asserts. Kept beside the truth so edge validity can be
    # scored without re-reading a graph file the B branch does not have.
    graph_prerequisites: list[tuple[str, str]] = field(default_factory=list)
    # Graph edges absent from the truth (these produce false blocks and wrong inferences)
    # and true edges the graph does not assert (these produce missed blocks).
    wrong_edges: list[tuple[str, str]] = field(default_factory=list)
    missing_edges: list[tuple[str, str]] = field(default_factory=list)
    edge_strength: float = 0.85
    notes: str = ""

    def by_id(self) -> dict[str, Simulee]:
        return {s.simulee_id: s for s in self.simulees}

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **{k: v for k, v in asdict(self).items() if k != "simulees"},
            "simulees": [asdict(s) for s in self.simulees],
        }
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> "Cohort":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        simulees = [Simulee(**s) for s in raw.pop("simulees")]
        for key in ("true_prerequisites", "graph_prerequisites", "wrong_edges", "missing_edges"):
            raw[key] = [tuple(e) for e in raw.get(key, [])]
        return cls(simulees=simulees, **raw)


# --- node difficulty, read off the bank -------------------------------------------


def node_difficulties(bank) -> dict[str, float]:
    """Sub-competency -> mean difficulty of the items that measure it.

    Ties the truth to the instrument. A node measured only by easy items is an easy node,
    which is what makes the simulation's level distribution resemble the one the bank can
    actually produce.
    """
    sums: dict[str, list[float]] = {}
    for item in bank.all_items():
        for measure in item.measures:
            sums.setdefault(measure.variable, []).append(float(item.cat.b))
    return {node: float(np.mean(bs)) for node, bs in sums.items()}


def read_graph_prerequisites(graph_path: Path | str) -> list[tuple[str, str]]:
    """PREREQUISITE edges as (parent, child), straight from the graph file.

    Read as JSON rather than through `CompetencyGraphService` so cohort generation works
    from either branch and needs no import of the graph layer under test.
    """
    raw = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    return [
        (str(e["from"]), str(e["to"]))
        for e in raw.get("edges", [])
        if str(e.get("relation")) == "PREREQUISITE"
    ]


# --- cohort construction ------------------------------------------------------------


def _theta_for(family: str, stratum: int, mains: list[str], rng: np.random.Generator) -> dict[str, float]:
    """One simulee's true ability per main competency, by profile family."""
    base = LEVEL_STRATA[stratum]
    if family == "monotonic":
        # Coherent candidate: the same person across every main, small honest variation.
        return {m: float(base + rng.normal(0, 0.15)) for m in mains}
    if family == "borderline":
        # Sitting on a reporting cut point, where the level endpoint is hardest.
        point = BORDERLINE_POINTS[stratum % len(BORDERLINE_POINTS)]
        return {m: float(point + rng.normal(0, 0.08)) for m in mains}
    if family == "shared_core_asymmetric":
        # Strong on one main, weak on another. The profile shared sub-competencies are
        # supposed to help with, and the one cross-main leakage would damage.
        offsets = np.linspace(0.9, -0.9, num=len(mains))
        return {m: float(base + offsets[i] + rng.normal(0, 0.15)) for i, m in enumerate(mains)}
    if family == "contradictory":
        # Ability is coherent; the NODE draw below is what will contradict the structure.
        return {m: float(base + rng.normal(0, 0.2)) for m in mains}
    # noisy
    return {m: float(base + rng.normal(0, 0.35)) for m in mains}


def _draw_nodes(
    *,
    simulee_id: str,
    family: str,
    theta: dict[str, float],
    difficulty: dict[str, float],
    parents: dict[str, list[str]],
    order: list[str],
    edge_strength: float,
    rng: np.random.Generator,
) -> dict[str, int]:
    """True mastery per sub-competency, respecting the TRUE prerequisite structure."""
    mastered: dict[str, int] = {}
    for node in order:
        main = node.split(".", 1)[0]
        ability = theta.get(main, 0.0)
        b = difficulty.get(node, 0.0)
        p = float(1.0 / (1.0 + np.exp(-1.7 * (ability - b))))
        value = int(rng.random() < p)

        blocked = False
        for parent in parents.get(node, ()):
            if mastered.get(parent, 1) == 0 and rng.random() < edge_strength:
                blocked = True
                break
        if blocked:
            value = 0

        if family == "contradictory" and rng.random() < 0.25:
            # A candidate who genuinely knows the advanced skill without the prerequisite —
            # self-taught, or the edge is simply wrong for them. Every blocking rule has to
            # survive these, and a design that never generates one cannot see false blocks.
            value = 1 - value
        mastered[node] = value
    return mastered


def _topological(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Nodes in an order where every parent precedes its child. Cycles are broken by name.

    Never raises on a cycle: an authored graph can contain one, and refusing to generate a
    cohort is a worse outcome than generating one whose cycle was cut deterministically.
    """
    parents: dict[str, set[str]] = {n: set() for n in nodes}
    for parent, child in edges:
        if child in parents and parent in parents:
            parents[child].add(parent)

    ordered: list[str] = []
    placed: set[str] = set()
    remaining = sorted(nodes)
    while remaining:
        ready = [n for n in remaining if parents[n] <= placed]
        if not ready:  # cycle — cut it at the alphabetically first node
            ready = [remaining[0]]
        for node in ready:
            ordered.append(node)
            placed.add(node)
        remaining = [n for n in remaining if n not in placed]
    return ordered


def build_cohort(
    *,
    dgp: str,
    n: int,
    seed: int,
    bank,
    bank_id: str,
    mains: list[str],
    graph_prerequisites: list[tuple[str, str]],
    wrong_edge_fraction: float = 0.25,
    edge_strength: float = 0.85,
) -> Cohort:
    """Generate `n` simulees under one DGP arm.

    `n` is rounded UP to a multiple of (8 strata x 5 families) so the design stays balanced;
    an unbalanced cell would make the by-family reporting the validation document asks for
    incomparable across families.
    """
    if dgp not in DGP_ARMS:
        raise ValueError(f"unknown DGP arm {dgp!r}; expected one of {DGP_ARMS}")

    rng = np.random.default_rng(_seed_of("cohort", dgp, seed))
    difficulty = node_difficulties(bank)
    sub_nodes = sorted(node for node in difficulty if "." in node)

    true_edges: list[tuple[str, str]] = []
    wrong_edges: list[tuple[str, str]] = []
    missing_edges: list[tuple[str, str]] = []

    if dgp == "DGP-0":
        pass  # no structure at all: the control arm
    elif dgp in ("DGP-1", "DGP-3"):
        true_edges = list(graph_prerequisites)
    else:  # DGP-2
        graph_edges = list(graph_prerequisites)
        k = int(round(wrong_edge_fraction * len(graph_edges)))
        dropped_ix = set(rng.choice(len(graph_edges), size=k, replace=False).tolist()) if k else set()
        wrong_edges = [e for i, e in enumerate(graph_edges) if i in dropped_ix]
        true_edges = [e for i, e in enumerate(graph_edges) if i not in dropped_ix]

        # ...and the same number of edges the world has that the graph never asserted.
        # Without these the arm only prices over-claiming; missed blocking would be
        # unmeasurable by construction.
        asserted = set(graph_edges)
        candidates = [
            (a, b)
            for a in sub_nodes
            for b in sub_nodes
            if a != b
            and a.split(".")[0] == b.split(".")[0]
            and (a, b) not in asserted
            and (b, a) not in asserted
        ]
        if candidates and k:
            picked = rng.choice(len(candidates), size=min(k, len(candidates)), replace=False)
            missing_edges = [candidates[int(i)] for i in picked]
            true_edges += missing_edges

    parents: dict[str, list[str]] = {}
    for parent, child in true_edges:
        parents.setdefault(child, []).append(parent)
    order = _topological(sub_nodes, true_edges)

    cells = len(LEVEL_STRATA) * len(PROFILE_FAMILIES)
    per_cell = max(1, -(-n // cells))  # ceil
    simulees: list[Simulee] = []
    for stratum in range(len(LEVEL_STRATA)):
        for family in PROFILE_FAMILIES:
            for k in range(per_cell):
                simulee_id = f"sim-{len(simulees):05d}"
                srng = np.random.default_rng(_seed_of("simulee", dgp, seed, stratum, family, k))
                theta = _theta_for(family, stratum, mains, srng)
                nodes = (
                    {}
                    if dgp == "DGP-0"
                    else _draw_nodes(
                        simulee_id=simulee_id,
                        family=family,
                        theta=theta,
                        difficulty=difficulty,
                        parents=parents,
                        order=order,
                        edge_strength=edge_strength,
                        rng=srng,
                    )
                )
                simulees.append(
                    Simulee(
                        simulee_id=simulee_id,
                        family=family,
                        stratum=stratum,
                        theta=theta,
                        nodes=nodes,
                        # A 4PL upper asymptote. Only DGP-3 carries one: a candidate who
                        # knows the material still gets 5% of items wrong, which is the
                        # error mode a grader actually produces and the one an
                        # over-confident inference rule is least robust to.
                        slip_ceiling=0.95 if dgp == "DGP-3" else 1.0,
                        grader_error_sd=0.10 if dgp == "DGP-3" else 0.0,
                    )
                )

    return Cohort(
        dgp=dgp,
        seed=seed,
        bank_id=bank_id,
        mains=list(mains),
        simulees=simulees,
        true_prerequisites=true_edges,
        graph_prerequisites=list(graph_prerequisites),
        wrong_edges=wrong_edges,
        missing_edges=missing_edges,
        edge_strength=edge_strength,
        notes=(
            f"{dgp}: {len(simulees)} simulees, {len(LEVEL_STRATA)} strata x "
            f"{len(PROFILE_FAMILIES)} families x {per_cell}. "
            f"true edges={len(true_edges)} graph edges={len(graph_prerequisites)} "
            f"wrong={len(wrong_edges)} missing={len(missing_edges)}"
        ),
    )
