"""The instrumented author view. Everything the candidate boundary withholds.

WHY THIS EXISTS AS AN ENDPOINT AT ALL

The deleted tester UI read these numbers out of engine internals directly —
`variables.certainty`, `posterior_interval`, `picker.criterion_for`, `information_for`,
`ability_band`, `rollup_outcomes`. An external frontend cannot, so either the API serves
them or the ability to see why an item was chosen disappears with the UI that used to show
it. An assessment nobody can audit mid-flight is a worse thing to ship than one whose
diagnostics need a flag.

WHY IT IS GATED SERVER-SIDE

Because the alternative is a client choosing not to render a field. These responses carry
the posterior, the shortlist, and which item the engine would have picked — enough to
reverse-engineer difficulty, and enough for a candidate to tell how they are doing while
they are still being measured. `AUTHOR_DIAGNOSTICS_ENABLED` is off by default, and the
service refuses rather than returning a thinner body: a 404 tells an operator the feature
is off, an empty object tells them nothing.
"""

from __future__ import annotations

from typing import Any

from cat_engine.contracts import DiagnosticsResponse, VariableDiagnosticsDTO
from cat_engine.engine.schemas.orchestration import AssessmentState
from cat_engine.engine.services.adaptive.irt import ability_band
from cat_engine.engine.services.competency_graph.coverage import unmeasured_required_nodes
from cat_engine.engine.services.orchestrator import variables as variables_module
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
from cat_engine.engine.services.orchestrator.picker import criterion_for, information_for


def _unmeasured(orchestrator: Orchestrator, state: AssessmentState, variable: str) -> list[str]:
    graph = orchestrator._graph()  # noqa: SLF001 - diagnostics reads what selection reads
    if graph is None:
        return []
    return sorted(
        unmeasured_required_nodes(
            graph,
            variable,
            measured=state.graph_direct_measured_nodes,
            mastered=state.graph_direct_mastered_nodes,
            not_mastered=state.graph_direct_not_mastered_nodes,
            critical_only=orchestrator.coverage_critical_only(),
        )
    )


def build(
    *,
    orchestrator: Orchestrator,
    state: AssessmentState,
    bank_id: str,
) -> DiagnosticsResponse:
    """Per-variable estimates, the queue's reasoning, and the graph's view."""
    presenting_pair = orchestrator.next_item(state)
    presenting_item = presenting_pair[0] if presenting_pair else None

    variables: list[VariableDiagnosticsDTO] = []
    for name, variable_state in sorted(state.variables.items()):
        level, band = ability_band(variable_state.theta_hat)
        information: float | None = None
        if (
            presenting_item is not None
            and state.presenting is not None
            and state.presenting.variable == name
        ):
            # Always Fisher at the estimate, never the phase's criterion: `information_for`
            # returns KL during the early phase and the two have no comparable scale, so a
            # column mixing them would invite exactly the cross-phase comparison the
            # picker's docstring forbids.
            information = round(
                information_for(presenting_item, variable_state, name), 6
            )
        variables.append(
            VariableDiagnosticsDTO(
                variable=name,
                theta_hat=round(variable_state.theta_hat, 4),
                standard_error=round(variable_state.standard_error, 4),
                precision_index_pct=round(variables_module.certainty(variable_state), 2),
                level=level,
                band=band,
                credible_interval_95=tuple(
                    round(x, 4) for x in variables_module.posterior_interval(variable_state)
                ),
                observations=variable_state.observations,
                finalised=variable_state.finalised,
                converged=variable_state.converged,
                stop_reason=variable_state.stop_reason,
                criterion=criterion_for(variable_state.observations),
                served_item_ids=list(variable_state.served_item_ids),
                score_history=list(variable_state.score_history),
                band_history=list(variable_state.band_history),
                presenting_information=information,
                graph_unmeasured_nodes=_unmeasured(orchestrator, state, name),
            )
        )

    graph: dict[str, Any] = {
        "node_states": state.graph_node_states,
        "blocked": list(state.graph_blocked_nodes),
        "directly_measured": list(state.graph_direct_measured_nodes),
        "directly_mastered": list(state.graph_direct_mastered_nodes),
        "directly_not_mastered": list(state.graph_direct_not_mastered_nodes),
        "inferred_mastered": list(state.graph_inferred_mastered_nodes),
        "contradicted": list(state.graph_contradicted_nodes),
        "waived": dict(state.graph_waived_nodes),
        # The unvalidated-edge forecast: what the edges WOULD have concluded. Present
        # whatever the enforcement flags say — a record that only appears once a feature is
        # enabled cannot be used to decide whether to enable it.
        "preview_inferred": dict(state.graph_preview_inferred_nodes),
        "preview_blocked": list(state.graph_preview_blocked_nodes),
        "preview_refuted": list(state.graph_preview_refuted_nodes),
        "propagation_manifest_hash": state.propagation_manifest_hash,
        "propagation_manifest_drift": list(state.propagation_manifest_drift),
    }

    return DiagnosticsResponse(
        session_id=state.session_id,
        bank_id=bank_id,
        variables=variables,
        # The full queued candidate: criterion, information, utility, what the engine
        # would have picked, whether the model overrode it and why. This is what makes a
        # selection auditable rather than merely logged.
        queue={name: candidate.model_dump() for name, candidate in state.queue.items()},
        presenting=state.presenting.model_dump() if state.presenting else None,
        graph=graph,
        elapsed_minutes=state.elapsed_minutes,
        seconds_by_item=dict(state.item_seconds),
        aberrant_responses=list(state.aberrant_responses),
    )
