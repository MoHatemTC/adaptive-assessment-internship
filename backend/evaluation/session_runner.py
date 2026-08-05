"""Run one simulated candidate through whichever engine this branch ships.

One function, both approaches. Everything Approach C adds is read through
`getattr(state, ..., default)`, so the same runner produces the same record shape on the
branch that has no competency graph at all. That is what makes the two arms' outputs
joinable by simulee id, which is the whole basis of the paired analysis.

WHAT A RECORD CONTAINS AND WHY

Appendix B's result schema, plus four additions the validation document asks for:

    common-scale level      M-03 computed on a banding rule identical across arms, since
                            the arms' native rules differ by more than the gate margin
    modelled duration       E-04 against the 90-minute cap, from the virtual clock
    held-out pairs          M-06..M-09 need (p_hat, y) on items the session did NOT
                            administer; predicting the items it did administer would
                            score the model on its own training data
    w_M_direct / w_M_total  per step, so the claim that inferred evidence never reaches
                            the posterior is MEASURED rather than asserted
"""

from __future__ import annotations

import time as _time_module
from dataclasses import dataclass

import numpy as np

from app.schemas.orchestration import BankItem
from app.services.adaptive.irt import THETA_GRID, ability_band, probability_correct

from . import responder
from .bands import COMMON, band_probabilities as common_band_probabilities
from .clock import VirtualClock
from .dgp import Simulee, _seed_of

# Held-out items retained per main. All of them would multiply the result file by the size
# of the bank for no gain in precision that matters at this n.
HELDOUT_PER_MAIN = 20

# Per-modality wall clock, in seconds. Duplicated from the graph branch's
# `DEFAULT_SECONDS_BY_MODALITY` rather than imported, because Approach B's item schema has
# no time on it at all — `BankItem.expected_seconds` does not exist there. Item duration is
# on section 6's freeze list, so the two arms have to be timed by the same table or E-04
# compares two different clocks.
FALLBACK_SECONDS_BY_MODALITY = {"mcq": 75.0, "code": 480.0, "open": 300.0, "voice": 300.0}


def expected_seconds(item: BankItem) -> float:
    """What this item is expected to cost the candidate, identically in every arm."""
    authored = (item.payload or {}).get("estimated_time_seconds")
    if authored is not None:
        try:
            value = float(authored)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return FALLBACK_SECONDS_BY_MODALITY[item.modality]


@dataclass
class StepTrace:
    """Section 26's Phase 3 step trace, with the three fields the validation adds."""

    step: int
    variable: str
    item_id: str
    modality: str
    true_theta: float
    theta_before: float
    theta_after: float
    se_before: float
    se_after: float
    score: float
    weight: float
    w_m_direct: float
    w_m_total: float
    best_information: float
    selected_information: float
    best_utility: float
    selected_utility: float
    direct_nodes: list[str]
    inferred_nodes: list[str]
    blocked_nodes: list[str]
    contradicted_nodes: list[str]
    elapsed_minutes: float
    stop: bool


def _rollup(graded_outcomes: list[dict], mains: set[str]):
    """Per-main direct weight from one graded response, using the engine's own rollup."""
    try:
        from app.services.orchestrator.competency import rollup_outcomes

        return {o.variable: float(o.weight) for o in rollup_outcomes(graded_outcomes, mains)}
    except Exception:  # pragma: no cover — only if a branch renames the rollup
        return {}


def credible_interval(posterior: np.ndarray, mass: float = 0.95) -> tuple[float, float]:
    """Equal-tailed interval from a posterior on THETA_GRID.

    Computed here rather than read off the report, because Approach B's `VariableReport`
    carries no interval field. Interval coverage (U-02) is one of the few direct checks on
    whether an arm's uncertainty is honest, so it has to be available for both.
    """
    weights = np.asarray(posterior, dtype=float)
    total = float(weights.sum())
    if total <= 0.0:
        return (float("nan"), float("nan"))
    cdf = np.cumsum(weights / total)
    lower_q = (1.0 - mass) / 2.0
    lower = float(THETA_GRID[min(int(np.searchsorted(cdf, lower_q)), THETA_GRID.size - 1)])
    upper = float(THETA_GRID[min(int(np.searchsorted(cdf, 1.0 - lower_q)), THETA_GRID.size - 1)])
    return (lower, upper)


def _heldout_items(bank, main: str, served: set[str], simulee_id: str) -> list[BankItem]:
    """A stable subsample of the items this session did not administer for `main`."""
    pool = [i for i in bank.shortlist(main, exclude=served) if i.status == "active"]
    if len(pool) <= HELDOUT_PER_MAIN:
        return pool
    rng = np.random.default_rng(_seed_of("heldout", simulee_id, main))
    ix = rng.choice(len(pool), size=HELDOUT_PER_MAIN, replace=False)
    return [pool[int(i)] for i in sorted(ix.tolist())]


async def run_session(
    *,
    orchestrator,
    bank,
    simulee: Simulee,
    mains: list[str],
    arm: str,
    max_steps: int = 60,
    keep_trace: bool = False,
) -> dict:
    """One paired session. Returns a JSON-serialisable record."""
    from app.services.orchestrator import orchestrator as orchestrator_module

    clock = VirtualClock()
    original_time = orchestrator_module.time
    orchestrator_module.time = clock  # type: ignore[assignment]
    try:
        return await _run(
            orchestrator=orchestrator,
            bank=bank,
            simulee=simulee,
            mains=mains,
            arm=arm,
            max_steps=max_steps,
            keep_trace=keep_trace,
            clock=clock,
        )
    finally:
        orchestrator_module.time = original_time


async def _run(*, orchestrator, bank, simulee, mains, arm, max_steps, keep_trace, clock):
    state = orchestrator.begin(list(mains))
    # `rng` reaches the picker's exposure control only. Seeded per simulee so a re-run
    # reproduces, and NOT per step, so it cannot desynchronise between arms in a way that
    # would look like a graph effect.
    rng = np.random.default_rng(_seed_of("picker", arm, simulee.simulee_id))

    trace: list[StepTrace] = []
    stop_reason = ""
    modelled_seconds = 0.0
    expected_weight_sum = 0.0
    realized_weight_sum = 0.0
    items_by_main: dict[str, list[str]] = {m: [] for m in mains}
    modalities_by_main: dict[str, set[str]] = {m: set() for m in mains}

    for step in range(max_steps):
        state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
        state = orchestrator.ensure_presenting(state)
        stop, stop_reason = orchestrator.should_stop(state)
        if stop:
            break
        # P12 abandons. The cap is drawn per SIMULEE in the cohort, not per step here: a
        # per-item hazard would make whether an item was answered depend on its position,
        # and the paired design cannot survive that. A candidate who walks out leaves the
        # remaining competencies unfinalised, which is the point — incomplete sessions and
        # zero-weight events are what P12 exists to produce.
        abandon_after = getattr(simulee, "abandon_after", None)
        if abandon_after is not None and step >= abandon_after:
            stop_reason = "abandoned"
            break

        nxt = orchestrator.next_item(state)
        if nxt is None:
            stop_reason = "no_candidates_available"
            break
        item, candidate = nxt
        variable = candidate.variable

        before = state.variables[variable]
        theta_before, se_before = before.theta_hat, before.standard_error

        plan = responder.plan_for(simulee, item, variable)
        response = responder.response_for(item, plan, simulee)

        # The clock moves when the candidate spends the time, i.e. before the response is
        # recorded — `record_response` is what reads it into `elapsed_minutes`.
        seconds = expected_seconds(item)
        clock.advance(seconds)
        modelled_seconds += seconds

        responder.CURRENT = plan
        try:
            state, graded = orchestrator.record_response(state, item, response)
        finally:
            responder.CURRENT = None
        state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

        items_by_main.setdefault(variable, []).append(item.item_id)
        modalities_by_main.setdefault(variable, set()).add(item.modality)

        # What selection PREDICTED this item would be worth, against what it delivered.
        # Q-04 asks whether the picker chose the top-scored item; it never asks whether the
        # score was right. This is the second question, and it is the one that decides
        # whether the information ranking means anything.
        direct = _rollup(graded.outcomes, set(mains))
        w_direct = float(direct.get(variable, 0.0))
        expected_weight_sum += float(getattr(candidate, "expected_weight", 1.0) or 0.0)
        realized_weight_sum += w_direct

        if keep_trace:
            after = state.variables[variable]
            trace.append(
                StepTrace(
                    step=step,
                    variable=variable,
                    item_id=item.item_id,
                    modality=item.modality,
                    true_theta=float(simulee.theta.get(variable, 0.0)),
                    theta_before=round(theta_before, 5),
                    theta_after=round(after.theta_hat, 5),
                    se_before=round(se_before, 5),
                    se_after=round(after.standard_error, 5),
                    score=round(plan.score if item.modality != "mcq" else float(plan.correct), 4),
                    weight=w_direct,
                    w_m_direct=w_direct,
                    # Identical by construction on this codebase: inferred consequences
                    # come back as signals carrying no score and no weight, so nothing but
                    # direct evidence can reach a likelihood. Recorded anyway — the
                    # architecture's rollup inflation risk is exactly the claim that a
                    # future edit could break silently.
                    w_m_total=w_direct,
                    # `best_utility` and `utility` are Approach C additions: B's queue
                    # entry records information only, because B has no utility layer to
                    # rank on. Absent means "this arm has no such quantity", which is a
                    # fact about the arm and not a missing measurement.
                    best_information=round(float(getattr(candidate, "best_information", 0.0)), 5),
                    selected_information=round(float(getattr(candidate, "information", 0.0)), 5),
                    best_utility=round(float(getattr(candidate, "best_utility", 0.0) or 0.0), 5),
                    selected_utility=round(float(getattr(candidate, "utility", 0.0) or 0.0), 5),
                    direct_nodes=[str(o.get("variable")) for o in graded.outcomes],
                    inferred_nodes=list(getattr(state, "graph_inferred_mastered_nodes", []) or []),
                    blocked_nodes=list(getattr(state, "graph_blocked_nodes", []) or []),
                    contradicted_nodes=list(getattr(state, "graph_contradicted_nodes", []) or []),
                    elapsed_minutes=round(float(state.elapsed_minutes), 3),
                    stop=False,
                )
            )

    report = orchestrator.summarise(state, stop_reason)

    # --- per-main results -----------------------------------------------------
    served = set(state.served_item_ids)
    variables: list[dict] = []
    heldout: list[dict] = []
    for variable_report in report.variables:
        main = variable_report.variable
        vstate = state.variables[main]
        true_theta = float(simulee.theta.get(main, 0.0))
        theta_hat = float(vstate.theta_hat)
        posterior = np.asarray(vstate.posterior, dtype=float)

        native_level, _label = ability_band(theta_hat)
        common_level = COMMON.band(theta_hat)
        common_probs = common_band_probabilities(posterior)

        variables.append(
            {
                "variable": main,
                "true_theta": round(true_theta, 5),
                "estimated_theta": round(theta_hat, 5),
                "standard_error": round(float(vstate.standard_error), 5),
                "observations": int(vstate.observations),
                "questions_for_main": len(items_by_main.get(main, [])),
                "modalities_used": sorted(modalities_by_main.get(main, set())),
                # NATIVE: whatever this branch would print today. Descriptive only.
                "native_level": int(native_level),
                "native_true_level": int(ability_band(true_theta)[0]),
                # COMMON: the frozen comparison scale. The primary endpoint runs on this.
                "common_level": int(common_level),
                "common_true_level": int(COMMON.band(true_theta)),
                "p_common_reported_band": round(float(common_probs.get(common_level, 0.0)), 5),
                "max_common_band_probability": round(float(max(common_probs.values())), 5),
                # The whole distribution, because decision consistency (M-11) is
                # sum_b P(b)^2 — the probability two independent administrations agree —
                # and it cannot be recovered from the reported band's probability alone.
                "common_band_probabilities": {
                    str(k): round(float(v), 6) for k, v in sorted(common_probs.items())
                },
                "credible_interval_95": list(credible_interval(posterior)),
                "finalised": bool(vstate.finalised),
                "converged": bool(vstate.converged),
                "stop_reason": vstate.stop_reason,
                # Fields Approach C's report added. Read defensively so one runner serves
                # both branches — but TRI-STATE, not boolean.
                #
                # Defaulting a missing field to True says "coverage satisfied" for an arm
                # that has no coverage gate at all, so C-off and B score a perfect gate
                # they never ran. None means "no graph"; the analysis excludes it rather
                # than counting it as a pass. `summarise` now returns the graph block only
                # when the master switch is on, so this path is reached routinely and not
                # only on the B branch.
                "graph_coverage_satisfied": (
                    None
                    if getattr(variable_report, "graph_coverage_satisfied", None) is None
                    else bool(variable_report.graph_coverage_satisfied)
                ),
                "graph_unmeasured_nodes": list(
                    getattr(variable_report, "graph_unmeasured_nodes", []) or []
                ),
                "aberrant_response_count": int(
                    getattr(variable_report, "aberrant_response_count", 0)
                ),
            }
        )

        for item in _heldout_items(bank, main, served, simulee.simulee_id):
            p_hat = float(
                probability_correct(theta_hat, item.cat.a, item.cat.b, item.cat.c)
            )
            plan = responder.plan_for(simulee, item, main)
            heldout.append(
                {
                    "variable": main,
                    "item_id": item.item_id,
                    "modality": item.modality,
                    "p_hat": round(p_hat, 6),
                    "y": int(plan.correct),
                }
            )

    graph_state = {
        # NODE LISTS, not counts. In simulation the cohort file holds every node's true
        # mastery, so an inference or a block can be verified against the truth directly —
        # no verification questions, no separate safety arm. That is the one thing a
        # simulation can do that a pilot cannot, and it is why C-DAG-01/03/04/05 are
        # computable here at full n instead of on the ~150 verified events per gate the
        # validation document's A1 shows a live study would need.
        "inferred_mastered": list(getattr(state, "graph_inferred_mastered_nodes", []) or []),
        "direct_mastered": list(getattr(state, "graph_direct_mastered_nodes", []) or []),
        "direct_not_mastered": list(getattr(state, "graph_direct_not_mastered_nodes", []) or []),
        "blocked": list(getattr(state, "graph_blocked_nodes", []) or []),
        # THE ENFORCED SETS ABOVE ARE EMPTY IN EVERY ARM THAT DOES NOT ENFORCE.
        #
        # `graph_inferred_mastered_nodes` and `graph_blocked_nodes` are gated on the
        # deployment switches, which C-shipped and C-hybrid hold off by definition. A
        # safety analysis reading only those counts zero events for exactly the arms the
        # study is about, and reports a wrong-inference rate of 0/0 — which renders as
        # "no evidence of harm" rather than "no evidence".
        #
        # The mirrors carry what the graph CONCLUDED under the same configuration. That is
        # the quantity C-DAG-03/04 are asking about: what would happen if this were
        # switched on, verified against node truth that costs nothing here.
        "shadow_inferred_mastered": list(
            getattr(state, "graph_shadow_inferred_mastered_nodes", []) or []
        ),
        "shadow_blocked": list(getattr(state, "graph_shadow_blocked_nodes", []) or []),
        # Per-inference provenance: node -> {source_node, distance, edge_path, ...}.
        # Depth- and edge-stratified wrong-inference rates (C-DAG-03d/03e) read this and
        # cannot be computed without it.
        "inferred_records": dict(getattr(state, "graph_inferred_mastery_records", {}) or {}),
        "contradicted": list(getattr(state, "graph_contradicted_nodes", []) or []),
        "contradiction_events": list(getattr(state, "graph_contradictions", []) or []),
        "waived": dict(getattr(state, "graph_waived_nodes", {}) or {}),
        "unblocking_probes": list(getattr(state, "graph_unblocking_probes", []) or []),
        "preview_inferred": dict(getattr(state, "graph_preview_inferred_nodes", {}) or {}),
        "preview_blocked": list(getattr(state, "graph_preview_blocked_nodes", []) or []),
        "preview_refuted": list(getattr(state, "graph_preview_refuted_nodes", []) or []),
        "processed_evidence_ids": len(getattr(state, "graph_processed_evidence_ids", []) or []),
        # The two numbers C-DAG-11 needs, instead of the whole node-state map. Dumping
        # every node's state per session made the result file 25x larger and carried
        # nothing else the analysis reads.
        "direct_evidence_id_count": sum(
            len((s.get("direct_evidence_ids") or []))
            for s in (getattr(state, "graph_node_states", {}) or {}).values()
        ),
        "unique_direct_evidence_ids": len(
            {
                eid
                for s in (getattr(state, "graph_node_states", {}) or {}).values()
                for eid in (s.get("direct_evidence_ids") or [])
            }
        ),
    }

    return {
        "arm": arm,
        "simulee_id": simulee.simulee_id,
        "family": simulee.family,
        "stratum": simulee.stratum,
        # Stratification is pre-declared per §8 and persona is one of the axes, so it has
        # to be on the record rather than inferred from the filename — a re-analysis that
        # reads the JSONL alone must still be able to split by it.
        "persona": getattr(simulee, "persona", "P01"),
        "session_id": state.session_id,
        "items_administered": int(state.items_administered),
        "served_item_ids": list(state.served_item_ids),
        "modelled_duration_seconds": round(modelled_seconds, 1),
        "modelled_duration_minutes": round(modelled_seconds / 60.0, 3),
        "elapsed_minutes": round(float(state.elapsed_minutes), 3),
        "expected_weight_sum": round(expected_weight_sum, 4),
        "realized_weight_sum": round(realized_weight_sum, 4),
        "stop_reason": stop_reason,
        "all_finalised": bool(report.all_finalised),
        "variables": variables,
        "heldout": heldout,
        "graph": graph_state,
        "trace": [t.__dict__ for t in trace],
    }
