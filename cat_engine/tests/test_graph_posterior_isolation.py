"""The graph shapes selection and reporting. It never touches the posterior.

This is the review's primary ruling (A1), and these two tests are what make it a property
of the code rather than a claim in a document.

WHY IT MATTERS. Propagated evidence is a deduction FROM a response that is already in the
likelihood. Multiplying it in again counts one response twice — 1.80x weight inflation on
the specification's own worked example, saturating the `min(1, ·)` cap on denser graphs.
The damage is not mainly to the ability estimate; it is to the standard error, which is
what the assessment stops on. A session would converge on evidence it never collected.
"""

from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.competency_graph.inference import InferredNodeSignal
from cat_engine.engine.services.orchestrator import variables as variables_module
from cat_engine.tests.conftest import orchestrator_for

GRAPH_PACKAGE = Path(variables_module.__file__).resolve().parents[1] / "competency_graph"

# Every graph effect, on. If any of these can move an estimate, the boundary is a
# convention rather than a property.
ALL_GRAPH_FLAGS = (
    "competency_graph_enabled",
    "graph_shadow_mode",
    "graph_filtering_enabled",
    "graph_upward_inference_enabled",
    "graph_descendant_blocking_enabled",
    "graph_utility_enabled",
    "graph_convergence_gate_enabled",
)


def test_the_graph_layer_cannot_import_the_orchestrator() -> None:
    """The structural half of the guarantee.

    The graph layer cannot construct a `GradedOutcome` because it cannot import one. This
    also removes a real circular import: the rollup imported the orchestrator's competency
    module, whose package imports the orchestrator, which imported the rollup — it only
    worked because the orchestrator always happened to be imported first.
    """
    offenders: list[str] = []
    for module in sorted(GRAPH_PACKAGE.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name.startswith("cat_engine.engine.services.orchestrator"):
                    offenders.append(f"{module.name}:{node.lineno} imports {name}")

    assert not offenders, "the graph layer must not depend on the orchestrator: " + "; ".join(
        offenders
    )


def test_apply_outcome_refuses_an_inferred_signal() -> None:
    """An inferred signal has no score and no weight, so it cannot be graded evidence."""
    signal = InferredNodeSignal(
        node="DA.1",
        source_node="DA.6",
        distance=1,
        strength=0.55,
        source_evidence_id="e",
        modality="code",
    )
    state = variables_module.seed_variable("DA", None, False)

    with pytest.raises((TypeError, AttributeError)):
        variables_module.apply_outcome(state, signal, 1.2, 0.0, 0.0, "item")


async def _run_session(orchestrator, targets: list[str], *, max_steps: int = 20):
    from cat_engine.tests.test_orchestration_flow import answer_for

    state = orchestrator.begin(targets, intake={t: 3 for t in targets})
    rng = np.random.default_rng(11)
    for _ in range(max_steps):
        state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
        stop, _ = orchestrator.should_stop(state)
        if stop:
            break
        state = orchestrator.ensure_presenting(state)
        nxt = orchestrator.next_item(state)
        if nxt is None:
            break
        item, _candidate = nxt
        state, _graded = orchestrator.record_response(state, item, answer_for(item))
        state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)
    return state


@pytest.mark.asyncio
async def test_every_graph_flag_on_produces_the_same_posterior_as_every_flag_off(
    monkeypatch,
) -> None:
    """The behavioural half. One scripted session, run twice.

    Selection may legitimately differ — that is what the graph is FOR — so this compares
    the measurement produced from whatever was administered, per item, rather than the
    end state of two differently-ordered sessions. If a graph consequence ever reaches
    `apply_outcome`, the two columns diverge.
    """
    from cat_engine.engine.services.code_adaptive.bank import JsonQuestionRepository
    from cat_engine.engine.services.code_adaptive.session import CodeAdaptiveSession
    from cat_engine.engine.services.orchestrator.grader import GraderAgent
    from cat_engine.tests.test_orchestration_flow import answer_for

    def measure(flag_value: bool) -> list[tuple[str, float, float, int]]:
        for flag in ALL_GRAPH_FLAGS:
            monkeypatch.setattr(settings, flag, flag_value)

        orchestrator = orchestrator_for(
            "DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        # A fixed script of items, so both runs measure exactly the same responses.
        items = [i for i in orchestrator._bank.all_items() if i.modality == "mcq"][:8]
        trace: list[tuple[str, float, float, int]] = []
        for item in items:
            state, _ = orchestrator.record_response(state, item, answer_for(item))
            variable = state.variables["DA"]
            trace.append(
                (
                    item.item_id,
                    round(variable.theta_hat, 12),
                    round(variable.standard_error, 12),
                    variable.observations,
                )
            )
        return trace

    with_graph = measure(True)
    without_graph = measure(False)

    assert with_graph == without_graph, (
        "a graph consequence reached the CAT posterior — theta, SE or the observation "
        "count moved when graph flags were enabled"
    )
