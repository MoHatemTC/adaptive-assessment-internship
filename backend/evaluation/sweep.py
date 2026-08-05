#!/usr/bin/env python3
"""Run the screening design: one subprocess per cell, and an honest account of what ran.

    python -m evaluation.sweep --cohorts eval-results/cohorts --out eval-results/sweep_01 \\
                               --persona P01 P02 P09 --n 400

ONE SUBPROCESS PER CELL IS MANDATORY, NOT A CHOICE

`Settings` is a pydantic-settings object instantiated at import, so every factor level has
to be in the environment before `app.config.settings` is first imported. `run_arm._apply_env`
refuses to proceed otherwise, and that refusal is the only thing standing between this
design and a sweep in which every cell silently produces the default configuration's
numbers. A loop in one process cannot work; a process pool is the shape the constraint
forces, and it happens to parallelise perfectly.

SILENT TRUNCATION IS FORBIDDEN

A screening design is orthogonal only if it is complete. Drop one cell and the main
effects are no longer clear of the two-factor interactions the resolution promised — the
numbers still compute, and they are no longer estimates of what their labels say.

So every cell's outcome is recorded in `index.json` with its exit code, the driver exits
non-zero if any cell is not complete, and the analysis refuses to report main effects
from a partial design unless explicitly forced. When it is forced, it prints which cells
are missing and stamps `design_complete: false` on every effect it emits.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.design import build_design, design_document  # noqa: E402
from evaluation import personas as personas_module  # noqa: E402

#: Leave headroom. Each child is single-threaded once BLAS is pinned, but the parent, the
#: filesystem and the machine's other work all need room, and a pool sized to every core
#: makes the whole box unresponsive for the hours a sweep takes.
DEFAULT_WORKERS = max(1, (os.cpu_count() or 4) - 8)


@dataclass
class CellResult:
    cell_id: str
    persona: str
    dgp: str
    status: str  # complete | incomplete | failed | skipped
    returncode: int
    seconds: float
    n_written: int
    n_planned: int
    log: str = ""


def _child_env() -> dict[str, str]:
    """Environment for a worker process.

    BLAS threading is pinned to 1. Numpy would otherwise open a thread pool per child, and
    with 24 children that is hundreds of threads contending for the same cores — which
    makes a sweep slower than running it serially, in a way that looks like the work being
    inherently slow.
    """
    env = dict(os.environ)
    env.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    return env


def _run_cell(
    *,
    cell,
    cohort_path: Path,
    out_dir: Path,
    arm: str,
    limit: int,
    persona: str,
    dgp: str,
    expected_session_length: float,
    resume: bool,
    trace_every: int,
) -> CellResult:
    cell_dir = out_dir / "cells" / f"{cell.cell_id}__{persona}"
    cell_dir.mkdir(parents=True, exist_ok=True)
    log_path = cell_dir / "stdout.log"

    command = [
        sys.executable,
        "-m",
        "evaluation.run_arm",
        "--arm", arm,
        "--cohort", str(cohort_path),
        "--out", str(cell_dir),
        "--run-name", cell.cell_id,
        "--factors", json.dumps(cell.env),
        "--limit", str(limit),
        "--trace-every", str(trace_every),
    ]
    if expected_session_length > 0:
        command += ["--expected-session-length", str(expected_session_length)]
    if resume:
        command.append("--resume")

    started = time.time()
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(command)}\n")
        log.flush()
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=_child_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    elapsed = time.time() - started

    manifest_path = cell_dir / f"{cell.cell_id}.manifest.json"
    n_written = n_planned = 0
    status = "failed"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        n_written = int(manifest.get("n_written", 0))
        n_planned = int(manifest.get("n_planned", 0))
        if completed.returncode == 0 and manifest.get("status") == "complete":
            status = "complete"
        elif n_written:
            # The child wrote sessions and then stopped. Distinguished from `failed`
            # because a resume can finish it, and from `complete` because it has not.
            status = "incomplete"

    return CellResult(
        cell_id=cell.cell_id,
        persona=persona,
        dgp=dgp,
        status=status,
        returncode=completed.returncode,
        seconds=round(elapsed, 2),
        n_written=n_written,
        n_planned=n_planned,
        log=str(log_path),
    )


def _cohort_for(cohorts_dir: Path, dgp: str, persona: str) -> Path | None:
    matches = sorted(cohorts_dir.glob(f"cohort_{dgp}_{persona}_n*.json"))
    if matches:
        return matches[-1]
    # Cohorts generated before personas existed carry no persona in the name; those are
    # only usable for P01, and using one for anything else would silently run the
    # canonical response process under another persona's label.
    if persona == "P01":
        legacy = sorted(cohorts_dir.glob(f"cohort_{dgp}_n*.json"))
        if legacy:
            return legacy[-1]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cohorts", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--arm", default="C-full",
                        help="the arm cells run under; C-full is the one whose edges are live")
    parser.add_argument("--dgp", default="DGP-2",
                        help="DGP-2 is the realistic arm and the only one that prices a wrong edge")
    parser.add_argument("--persona", nargs="*", default=["P01"])
    parser.add_argument("--n", type=int, default=400, help="sessions per cell, before persona weighting")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--trace-every", type=int, default=0)
    parser.add_argument("--expected-session-length", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the cells and their resolved factor env, run nothing")
    args = parser.parse_args()

    cohorts_dir = Path(args.cohorts)
    out_dir = Path(args.out)
    cells = build_design()

    jobs = []
    missing_cohorts = []
    for persona in args.persona:
        cohort_path = _cohort_for(cohorts_dir, args.dgp, persona)
        if cohort_path is None:
            missing_cohorts.append((args.dgp, persona))
            continue
        limit = int(round(args.n * personas_module.weight_for(persona)))
        for cell in cells:
            jobs.append((cell, cohort_path, persona, limit))

    if missing_cohorts:
        raise SystemExit(
            "no cohort file for "
            + ", ".join(f"{dgp}/{p}" for dgp, p in missing_cohorts)
            + f" in {cohorts_dir}. Generate it with:\n"
            + "  python -m evaluation.make_cohort --dgp "
            + args.dgp
            + " --persona "
            + " ".join(p for _dgp, p in missing_cohorts)
        )

    if args.dry_run:
        print(f"{len(jobs)} cells ({len(cells)} configurations x {len(args.persona)} personas)")
        total = sum(limit for _c, _p, _persona, limit in jobs)
        print(f"{total:,} sessions total\n")
        for cell, _path, persona, limit in jobs[: len(cells)]:
            env = " ".join(f"{k}={v!r}" for k, v in sorted(cell.env.items()))
            print(f"  {cell.cell_id}  [{persona} n={limit}]")
            print(f"      {env}")
        if len(args.persona) > 1:
            print(f"\n  ... and the same {len(cells)} for "
                  f"{', '.join(args.persona[1:])} at their weighted n")
        return

    out_dir.mkdir(parents=True, exist_ok=True)

    # ONE SWEEP PER OUTPUT DIRECTORY. Two sweeps writing the same cells append to the same
    # JSONL files, and the result is not merely duplicated sessions — it is interleaved
    # partial lines, which read as corruption in a file whose only problem is that two
    # processes owned it. Nothing downstream can tell that apart from a genuinely damaged
    # run, so the whole dataset has to be discarded.
    #
    # O_EXCL is the check: creating the lock and testing for it are one operation, so two
    # sweeps starting together cannot both pass.
    lock_path = out_dir / ".sweep.lock"
    try:
        handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        stale = lock_path.read_text(encoding="utf-8").strip()
        raise SystemExit(
            f"{lock_path} exists: another sweep ({stale}) is writing here, or one died "
            "without cleaning up. Two sweeps sharing an output directory corrupt each "
            "other's results. Check with `pgrep -af evaluation.sweep`, then delete the "
            "lock if no sweep is running."
        ) from None
    with os.fdopen(handle, "w") as lock:
        lock.write(f"pid={os.getpid()} out={out_dir}\n")

    try:
        _run_sweep(args, jobs, cells, out_dir)
    finally:
        lock_path.unlink(missing_ok=True)


def _run_sweep(args, jobs, cells, out_dir: Path) -> None:
    (out_dir / "design.json").write_text(
        json.dumps(design_document(), indent=1), encoding="utf-8"
    )

    print(f"{len(jobs)} cells on {args.workers} workers -> {out_dir}")
    started = time.time()
    results: list[CellResult] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                _run_cell,
                cell=cell,
                cohort_path=path,
                out_dir=out_dir,
                arm=args.arm,
                limit=limit,
                persona=persona,
                dgp=args.dgp,
                expected_session_length=args.expected_session_length,
                resume=args.resume,
                trace_every=args.trace_every,
            )
            for cell, path, persona, limit in jobs
        ]
        for done, future in enumerate(futures, start=1):
            result = future.result()
            results.append(result)
            print(
                f"  [{done}/{len(futures)}] {result.cell_id} {result.persona} "
                f"{result.status} {result.n_written}/{result.n_planned} "
                f"({result.seconds:.0f}s)",
                flush=True,
            )

    elapsed = time.time() - started
    by_status: dict[str, int] = {}
    for result in results:
        by_status[result.status] = by_status.get(result.status, 0) + 1

    index = {
        "arm": args.arm,
        "dgp": args.dgp,
        "personas": list(args.persona),
        "sessions_per_cell": args.n,
        "workers": args.workers,
        "elapsed_seconds": round(elapsed, 1),
        "cells_total": len(results),
        "cells_by_status": by_status,
        "design_complete": all(r.status == "complete" for r in results),
        "cells": [asdict(r) for r in sorted(results, key=lambda r: (r.persona, r.cell_id))],
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")

    print(f"\n{len(results)} cells in {elapsed / 60:.1f} min: {by_status}")
    print(f"index -> {out_dir / 'index.json'}")

    if not index["design_complete"]:
        # Named individually. "34 of 36 complete" is not enough to judge what survived:
        # which two decides whether any main effect is still orthogonal.
        print("\nDESIGN INCOMPLETE. Cells that did not finish:", file=sys.stderr)
        for result in results:
            if result.status != "complete":
                print(
                    f"  {result.cell_id} {result.persona}: {result.status} "
                    f"(exit {result.returncode}, {result.n_written}/{result.n_planned}) "
                    f"see {result.log}",
                    file=sys.stderr,
                )
        print(
            "\nA fractional factorial is orthogonal only when complete. Re-run with "
            "--resume, or pass --allow-incomplete to the analysis and read the design "
            "integrity block it prints.",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
