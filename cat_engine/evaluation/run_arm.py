#!/usr/bin/env python3
"""Run one arm of the B-vs-C experiment over one frozen cohort.

    python -m evaluation.run_arm --arm C-full --cohort <cohort.json> --out <dir>

Writes `<out>/<arm>__<dgp>.jsonl`, one JSON record per simulee, plus a manifest recording
every setting that was in force. The manifest is not decoration: section 6 requires the
experiment to be reproducible from it, and the validation document's pre-registration
amendment requires the configuration to be fixed before the data exists rather than
described after it.

ENVIRONMENT IS SET BEFORE THE APP IS IMPORTED. `Settings` is a pydantic-settings object
instantiated at module import, so a flag applied afterwards would be read by nothing while
appearing to have been applied — the exact failure mode the graph master switch was added
to prevent.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cat_engine.evaluation.arms import ARMS, FROZEN_ENV


def _apply_env(arm_name: str, factors: dict[str, str] | None = None) -> dict[str, str]:
    """Freeze list first, then the arm's own flags, then this cell's factor levels.

    Refuses to proceed if `Settings` already exists. A flag applied after that object is
    built is read by nothing while looking applied: the run completes, the manifest records
    the arm's name, and every arm silently produces the default configuration's numbers.
    An assertion is the only defence, because nothing downstream can tell the difference.

    `factors` is how a sweep cell reaches this process. It is deliberately the LAST layer
    and deliberately not allowed to touch the freeze list: FROZEN_ENV holds the
    confounders that must not vary between cells, so a factor colliding with one would
    silently convert a controlled comparison into an uncontrolled one. That is an error,
    not an override.
    """
    if arm_name not in ARMS:
        raise SystemExit(f"unknown arm {arm_name!r}; expected one of {sorted(ARMS)}")
    if "cat_engine.engine.config.settings" in sys.modules:
        raise SystemExit(
            "cat_engine.engine.config.settings was imported before the arm was applied — every flag in "
            f"arm {arm_name!r} would be ignored. Import order in run_arm.py is load-bearing."
        )
    applied = {**FROZEN_ENV, **ARMS[arm_name].env}

    for key, value in (factors or {}).items():
        if key in FROZEN_ENV:
            raise SystemExit(
                f"factor {key!r} collides with the freeze list. FROZEN_ENV exists to hold "
                "the confounders constant across cells; letting a factor move one would "
                "make the design measure two things and attribute them to one."
            )
        applied[key] = str(value)

    os.environ.update(applied)
    return applied


def current_factors() -> dict[str, str]:
    """The factor levels in force, as the manifest records them.

    ONE definition, used both to write the manifest and to check a resume against it.
    They were computed separately and differed by the freeze-list keys, so every resume
    compared a set against a strict superset of itself and refused — a guard that fires
    on every correct input is worse than no guard, because the fix is to remove it.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("GRAPH_", "CAT_")) and key not in FROZEN_ENV
    }


def _force_enable_edges(graph_service):
    """Rebuild a graph with every PREREQUISITE edge permitted to infer and to block.

    The authored edges ship with both permissions false and `validation_status:
    unvalidated`, so on the shipped configuration Approach C's propagation cannot fire at
    all. Enabling them here — in the harness, never in the bank data — is what makes the
    C-full arm a measurement of the architecture rather than of an inert feature flag.
    """
    from dataclasses import replace

    from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService

    graph = graph_service.graph
    edges = tuple(
        replace(edge, allow_upward_inference=True, allow_downward_blocking=True)
        if edge.relation == "PREREQUISITE"
        else edge
        for edge in graph.edges
    )
    return CompetencyGraphService(replace(graph, edges=edges))


async def _run_all(
    *, arm_name, cohort, out_dir, limit, max_steps, trace_every, run_name="", resume=False,
    sample_seed=0, cohort_source: Path | None = None,
):
    from cat_engine.engine.config.settings import settings
    from cat_engine.engine.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
    from cat_engine.engine.services.orchestrator.grader import GraderAgent
    from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
    from cat_engine.evaluation import HAS_GRAPH, responder
    from cat_engine.evaluation.session_runner import run_session

    responder.install_stubs()

    if HAS_GRAPH:
        from cat_engine.engine.services.orchestrator import registry

        bank_id = registry.resolve_bank_id(cohort.bank_id)
        bank = registry.get_bank(bank_id)
        graph = registry.get_graph_service(bank_id)
        if graph is not None and ARMS[arm_name].force_enable_edges:
            graph = _force_enable_edges(graph)
        orchestrator = Orchestrator(
            bank,
            GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository())),
            graph=graph,
            coverage_critical_only=registry.profile(bank_id).coverage_critical_only,
            bank_id=bank_id,
        )
    else:
        # Approach B: no registry, no graph. The bank file is the same one, addressed
        # directly — the two branches ship identical `question_bank_AIE.json`.
        from cat_engine.engine.services.orchestrator.bank import JsonUnifiedBank

        bank_path = Path(__file__).resolve().parents[1] / "engine" / "data" / "question_bank_AIE.json"
        bank = JsonUnifiedBank(bank_path)
        orchestrator = Orchestrator(
            bank, GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))
        )

    available = set(bank.variables())
    mains = [m for m in cohort.mains if m in available]
    if not mains:
        raise SystemExit(f"cohort mains {cohort.mains} are not in this bank ({sorted(available)})")

    # STRIDE, NOT TRUNCATE. `build_cohort` emits stratum 0's simulees, then stratum 1's,
    # and so on, so `simulees[:limit]` is not a smaller cohort — it is the WEAKEST
    # candidates only. At limit=1200 of 4000 that is strata 0-2 of 8, and every absolute
    # accuracy, reliability and calibration figure computed from it describes the bottom
    # third of the ability range while reading like a population number.
    # ...and SHUFFLED, deterministically, so that any PREFIX is representative too. A
    # strided sample is still emitted in ascending-ability order, so a run stopped early —
    # which is how every run here has ended — would again describe only the weak end.
    # Shuffling makes "stopped at k sessions" a random subsample instead of a truncation.
    import numpy as _np

    # WHICH candidates this cell runs on. `sample_seed` of 0 means "the cohort's own seed",
    # which gives every cell the SAME sample — that identical pairing is what buys the
    # paired design its variance reduction, and it is the right default for a factorial
    # contrast.
    #
    # A non-zero value draws a different stratified subsample, and exists for centre
    # points. Replicates that run on identical candidates in a deterministic harness are
    # not replicates: they return the same number, pure error comes out as exactly 0, and
    # the |effect| > 2*SE activity rule divides by it and calls every effect active. A
    # centre point has to replicate the SAMPLING, not the arithmetic.
    #
    # The subsample must differ in MEMBERSHIP, not merely in order — reshuffling the same
    # 200 candidates returns the same aggregate and estimates the same zero pure error.
    # So a seeded cell takes one RANDOM member from each stride block rather than a fixed
    # phase within it. A fixed phase gives only `step` distinct samples and collides
    # readily: the first version of this shifted the stride by a fraction of a step, and
    # three of the four centre seeds landed in the same integer bucket and drew identical
    # cohorts. Per-block selection preserves the stratification exactly — one candidate per
    # block, blocks unchanged — while making the draw genuinely random.
    simulees = list(cohort.simulees)
    rng_seed = int(sample_seed) if sample_seed else cohort.seed
    if limit and limit < len(simulees):
        step = len(simulees) / limit
        if sample_seed:
            picker = _np.random.default_rng(rng_seed)
            chosen = []
            for i in range(limit):
                low = int(i * step)
                high = min(len(simulees), int((i + 1) * step))
                chosen.append(simulees[int(picker.integers(low, max(high, low + 1)))])
            simulees = chosen
        else:
            simulees = [simulees[int(i * step)] for i in range(limit)]
    order = _np.random.default_rng(rng_seed).permutation(len(simulees))
    simulees = [simulees[int(i)] for i in order]
    planned = len(simulees)

    cohort_sha256 = None
    if cohort_source is not None:
        cohort_sha256 = hashlib.sha256(cohort_source.read_bytes()).hexdigest()
    cohort_personas = sorted({str(s.persona) for s in cohort.simulees})

    def _resolved_policy_summary(bank_id: str | None) -> dict | None:
        """The permission lattice actually in force, for R1.

        Recorded because the harness rewrites the edge flags after resolution: without
        this, a C-full result cannot be distinguished from a claim about the edges as
        they ship, and the shipped ones are all inert.
        """
        if not HAS_GRAPH:
            return None
        try:
            from cat_engine.engine.services.orchestrator.registry import get_propagation_policy

            resolved = get_propagation_policy(bank_id)
        except (ImportError, KeyError, OSError, ValueError):
            return None
        return resolved.summary() if resolved is not None else None

    out_dir.mkdir(parents=True, exist_ok=True)
    basename = run_name or f"{arm_name}__{cohort.dgp}"
    results_path = out_dir / f"{basename}.jsonl"
    manifest_path = out_dir / f"{basename}.manifest.json"

    # A persona is part of the data-generating process even though `cohort.dgp` retains
    # the broad DGP-2/DGP-3 label. The default basename therefore collides across persona
    # cohorts. Refuse to overwrite or resume a result under a different cohort instead of
    # silently relabelling P01 evidence as P02/P09 (or vice versa).
    previous_manifest = None
    if manifest_path.exists():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous_hash = previous_manifest.get("cohort_sha256")
        if results_path.exists() and cohort_sha256 and previous_hash != cohort_sha256:
            known = previous_hash or "<missing from legacy manifest>"
            raise SystemExit(
                f"{basename}: output already belongs to cohort sha256 {known}; requested "
                f"{cohort_sha256}. Use a separate --out or --run-name."
            )

    # RESUME. The simulee order above is already deterministic — a stride, then a shuffle
    # seeded on the cohort — so the first N of it are the same N on every invocation.
    # Counting the lines already written therefore identifies exactly which simulees are
    # done, without recording a cursor that could disagree with the file.
    done = 0
    if resume and results_path.exists():
        # A killed run leaves a partial final line. Counting it would both overstate the
        # progress by one and append the next record onto the fragment, producing a line
        # nothing can parse — so the fragment is dropped and the file truncated to the
        # last complete record before anything is added to it.
        raw = results_path.read_text(encoding="utf-8")
        complete = []
        for line in raw.splitlines(keepends=True):
            if not line.strip():
                continue
            if not line.endswith("\n"):
                break
            try:
                json.loads(line)
            except json.JSONDecodeError:
                break
            complete.append(line)
        done = len(complete)
        if len("".join(complete)) != len(raw):
            results_path.write_text("".join(complete), encoding="utf-8")
            print(f"{basename}: dropped a truncated trailing record before resuming")
        if done >= planned:
            print(f"{basename}: already complete ({done} sessions)")
            return
        # A resume that changed the configuration would silently splice two cells into
        # one file, and nothing downstream could separate them again.
        if (
            previous_manifest is not None
            and "factors" in previous_manifest
            and previous_manifest["factors"] != current_factors()
        ):
            raise SystemExit(
                f"{basename}: refusing to resume — the factor levels on disk differ "
                f"from the ones requested. Delete the cell to re-run it."
            )
        simulees = simulees[done:]
        print(f"{basename}: resuming after {done} sessions")

    # PRE-8: WRITTEN BEFORE THE LOOP, NOT AFTER IT.
    #
    # The manifest used to be the last artefact produced, so any run that was killed —
    # the documented normal case, since every run so far has been stopped early — left a
    # .jsonl of results with no record of the settings that produced them. Results whose
    # configuration is unknown are not partial results; they are unusable ones.
    #
    # `status` carries what the old schema could not: a manifest that exists before the
    # count does has to be able to say the count is not final yet. A reader finding
    # "running" on a run nobody is running knows it died, which is also information the
    # old shape could not express.
    def write_manifest(*, status: str, n_written: int) -> None:
        manifest_path.write_text(
            json.dumps(
                {
                    "arm": arm_name,
                    "arm_description": ARMS[arm_name].description,
                    "dgp": cohort.dgp,
                    "status": status,
                    "n_planned": planned,
                    "n_written": n_written,
                    "cohort_seed": cohort.seed,
                    "cohort_file": cohort_source.name if cohort_source is not None else None,
                    "cohort_sha256": cohort_sha256,
                    "cohort_personas": cohort_personas,
                    # 0 = the cohort's own seed, i.e. the shared paired sample. Non-zero
                    # marks a cell that drew its own subsample; centre points do.
                    "sample_seed": int(sample_seed),
                    # Retained under its old name so existing readers keep working; it is
                    # the final count only when status == "complete".
                    "n": n_written,
                    "bank_id": cohort.bank_id,
                    "mains": mains,
                    "has_graph_module": HAS_GRAPH,
                    "force_enable_edges": ARMS[arm_name].force_enable_edges,
                    # R1: the harness forces every PREREQUISITE edge live, AFTER all three
                    # policy levels have resolved. Recorded rather than left to be
                    # reconstructed, so nobody reads a C-full number as a statement about
                    # the shipped edge set.
                    "policy_override": "harness:force_enable_edges"
                    if ARMS[arm_name].force_enable_edges
                    else None,
                    "resolved_policy": _resolved_policy_summary(cohort.bank_id),
                    "results": results_path.name,
                    # The factor levels for this cell, so a result can name its own
                    # configuration without joining back to the sweep design file.
                    "factors": current_factors(),
                    "settings": {
                        key: getattr(settings, key)
                        for key in sorted(type(settings).model_fields)
                        if key.startswith(
                            ("cat_", "graph_", "competency_", "orchestrator_", "code_")
                        )
                        and not key.endswith(("_api_key", "_key"))
                    },
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    write_manifest(status="running", n_written=done)

    written = done
    with results_path.open("a" if done else "w", encoding="utf-8") as handle:
        for index, simulee in enumerate(simulees):
            record = await run_session(
                orchestrator=orchestrator,
                bank=bank,
                simulee=simulee,
                mains=mains,
                arm=arm_name,
                max_steps=max_steps,
                keep_trace=(trace_every > 0 and index % trace_every == 0),
            )
            record["dgp"] = cohort.dgp
            handle.write(json.dumps(record) + "\n")
            written += 1
            if written % 100 == 0:
                print(f"  {arm_name} {cohort.dgp}: {written}/{planned}", flush=True)

    write_manifest(status="complete", n_written=written)
    print(f"{arm_name} {cohort.dgp}: {written} sessions -> {results_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--cohort", required=True, help="cohort JSON from make_cohort.py")
    parser.add_argument("--out", default="eval-results/runs")
    parser.add_argument("--limit", type=int, default=0, help="0 = the whole cohort")
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument(
        "--trace-every",
        type=int,
        default=50,
        help="keep a full step trace for every Nth session (0 = never)",
    )
    parser.add_argument(
        "--factors",
        default="",
        help=(
            "JSON object of extra env vars for this sweep cell, e.g. "
            '\'{"GRAPH_MAXIMUM_PROPAGATION_DEPTH": "1"}\'. Applied after the arm and '
            "refused if it collides with the freeze list."
        ),
    )
    parser.add_argument(
        "--run-name",
        default="",
        help="output basename; defaults to <arm>__<dgp>. A sweep uses the cell id.",
    )
    parser.add_argument(
        "--expected-session-length",
        type=float,
        default=0.0,
        help="E[L] from the throughput probe, for the order-free persona surrogates",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted cell, skipping sessions already written",
    )
    parser.add_argument(
        "--sample-seed",
        type=int,
        default=0,
        help="0 = the cohort's shared paired sample. Non-zero draws a different stratified "
             "subsample, so centre-point replicates replicate the SAMPLING rather than the "
             "arithmetic and pure error is not identically zero.",
    )
    args = parser.parse_args()

    factors = json.loads(args.factors) if args.factors else {}
    _apply_env(args.arm, factors)

    from cat_engine.evaluation.dgp import Cohort

    if args.expected_session_length > 0:
        from cat_engine.evaluation import responder

        responder.set_expected_session_length(args.expected_session_length)

    cohort_source = Path(args.cohort).resolve()
    cohort = Cohort.load(cohort_source)
    asyncio.run(
        _run_all(
            arm_name=args.arm,
            cohort=cohort,
            out_dir=Path(args.out),
            limit=args.limit,
            max_steps=args.max_steps,
            trace_every=args.trace_every,
            run_name=args.run_name,
            resume=args.resume,
            sample_seed=args.sample_seed,
            cohort_source=cohort_source,
        )
    )


if __name__ == "__main__":
    main()
