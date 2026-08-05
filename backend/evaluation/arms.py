"""The arms of the experiment, and why there are four rather than two.

The test plan compares B against C. On these two branches that comparison is confounded,
and the confound is larger than the effect:

    band boundaries       B rounds `3 + theta` to 1.0-wide bands; C uses 1.6-wide cuts
    stability ceiling     B stops a stable band at SE <= 0.80; C at <= 0.55
    corroboration         C refuses to converge without an item near the estimate; B has
                          no such rule
    time-aware selection  C ranks on information per minute and enforces a modality
                          blueprint; B ranks on raw information
    item budget           B 60 items per session, C 120

Section 6 freezes every one of those and says "only the adaptation architecture should
differ". Between these branches, seven other things differ. A two-arm B-vs-C contrast
would attribute all of it to the DAG.

So the graph branch runs three configurations of ONE codebase, and the difference between
them is the DAG and nothing else:

    C-off      the master switch off. The same code as C, with no graph at all. This is
               the arm the DAG is actually measured against.
    C-shipped  the branch's own defaults: coverage gate on, inference/blocking/utility
               off, every prerequisite edge inert. What would ship today.
    C-full     inference, blocking, filtering and graph utility on, with the prerequisite
               edges force-enabled. Approach C as the architecture describes it.

and the B branch contributes:

    B          Approach B as it stands, on the same cohort and the same items.

B vs C-off prices the branch divergence. C-off vs C-full prices the DAG. Reporting only
the first would be the confounded comparison; reporting only the second would ignore that
the branches are not interchangeable today. Both are needed and both are reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Env vars are the only way to configure `Settings`, and it is instantiated at import time,
# so an arm has to be applied BEFORE `app.config.settings` is first imported. `run_arm.py`
# does that; nothing here imports the app.
GRAPH_OFF = {
    "COMPETENCY_GRAPH_ENABLED": "false",
    "GRAPH_SHADOW_MODE": "false",
    "GRAPH_FILTERING_ENABLED": "false",
    "GRAPH_UPWARD_INFERENCE_ENABLED": "false",
    "GRAPH_DESCENDANT_BLOCKING_ENABLED": "false",
    "GRAPH_UTILITY_ENABLED": "false",
    "GRAPH_CONVERGENCE_GATE_ENABLED": "false",
    "GRAPH_EDGE_PREVIEW_ENABLED": "false",
}

GRAPH_SHIPPED = {
    "COMPETENCY_GRAPH_ENABLED": "true",
    "GRAPH_SHADOW_MODE": "true",
    "GRAPH_FILTERING_ENABLED": "false",
    "GRAPH_UPWARD_INFERENCE_ENABLED": "false",
    "GRAPH_DESCENDANT_BLOCKING_ENABLED": "false",
    "GRAPH_UTILITY_ENABLED": "false",
    "GRAPH_CONVERGENCE_GATE_ENABLED": "true",
    "GRAPH_EDGE_PREVIEW_ENABLED": "true",
}

GRAPH_FULL = {
    "COMPETENCY_GRAPH_ENABLED": "true",
    "GRAPH_SHADOW_MODE": "false",
    "GRAPH_FILTERING_ENABLED": "true",
    "GRAPH_UPWARD_INFERENCE_ENABLED": "true",
    "GRAPH_DESCENDANT_BLOCKING_ENABLED": "true",
    "GRAPH_UTILITY_ENABLED": "true",
    "GRAPH_CONVERGENCE_GATE_ENABLED": "true",
    "GRAPH_EDGE_PREVIEW_ENABLED": "true",
}


@dataclass(frozen=True)
class Arm:
    name: str
    env: dict[str, str] = field(default_factory=dict)
    # Rebuild the graph with every PREREQUISITE edge permitted to infer and to block.
    # The edges ship inert and validated by nothing; C-full is the arm that asks what
    # they would do, which is precisely what the offline edge-validity check (Phase 0b)
    # exists to answer BEFORE anyone enables them on a candidate.
    force_enable_edges: bool = False
    description: str = ""


# The configuration the first run's evidence actually argues for. Everything the graph
# layer measurably earned, and nothing it did not:
#
#   coverage gate ON      the only row the graph won on. Without it, NO competency ends
#                         with all its required sub-competencies directly measured — the
#                         information-greedy selector never covers what the report claims.
#   inference OFF         fired once in 960 sessions even fully enabled
#   blocking OFF          4.8-5.1% false blocking against a 2% gate, on correct edges
#   filtering/utility OFF penalties collapsed modality breadth to 0.714
#   P(band) stop ON       +2.1pp accuracy for +1.6 items in the corrected pre-study, and
#                         reachable for the first time now that both callers pass it
GRAPH_HYBRID = {
    "COMPETENCY_GRAPH_ENABLED": "true",
    "GRAPH_SHADOW_MODE": "true",
    "GRAPH_FILTERING_ENABLED": "false",
    "GRAPH_UPWARD_INFERENCE_ENABLED": "false",
    "GRAPH_DESCENDANT_BLOCKING_ENABLED": "false",
    "GRAPH_UTILITY_ENABLED": "false",
    "GRAPH_CONVERGENCE_GATE_ENABLED": "true",
    "GRAPH_EDGE_PREVIEW_ENABLED": "true",
    "CAT_BAND_PROBABILITY_STOP_ENABLED": "true",
}

ARMS: dict[str, Arm] = {
    "B": Arm(
        name="B",
        env={},
        description="Approach B, its own branch, its own defaults. Has no graph module.",
    ),
    "C-off": Arm(
        name="C-off",
        env=GRAPH_OFF,
        description="Approach C's code with the graph master switch off. DAG baseline.",
    ),
    "C-shipped": Arm(
        name="C-shipped",
        env=GRAPH_SHIPPED,
        description="Approach C as it would ship: coverage gate on, propagation inert.",
    ),
    "C-full": Arm(
        name="C-full",
        env=GRAPH_FULL,
        force_enable_edges=True,
        description="Approach C with inference, blocking, filtering, utility and edges on.",
    ),
    "C-hybrid": Arm(
        name="C-hybrid",
        env=GRAPH_HYBRID,
        description=(
            "The configuration the first run's evidence argues for: coverage gate on, "
            "propagation off, P(band) stopping on."
        ),
    ),
}

# Settings that must be identical in every arm for the comparison to mean anything. Applied
# on top of the arm's own env by `run_arm.py`, and written into the manifest so a reader
# can see what was held still. These are section 6's freeze list, restricted to the keys
# the two branches actually disagree about.
FROZEN_ENV = {
    "ACTIVE_BANK": "AIE",
    "CAT_SE_TARGET": "0.55",
    "CAT_MAX_QUESTIONS": "12",
    "CAT_MIN_QUESTIONS": "6",
    "CAT_PRECISION_MIN_QUESTIONS": "6",
    "CAT_STABLE_WINDOW": "3",
    # B ships 0.80 and C ships 0.55. Frozen to C's value: a stable-band stop at SE 0.80 is
    # a different stopping rule, not a different adaptation architecture.
    "CAT_STABILITY_SE_CEILING": "0.55",
    # PRE-3. Was "1", which is maximally concentrated selection and which
    # docs/operations.md forbids in production. Every exposure figure in the prior study
    # was measured there and does not transfer. 3 is the shipped default, so exposure
    # numbers from this study describe a configuration someone might actually run.
    #
    # It is frozen rather than swept because it is a confounder, not a factor: top_k
    # changes which items are served, which changes the evidence, which changes every
    # propagation input.
    "CAT_EXPOSURE_TOP_K": "3",
    "CAT_CONTENT_BALANCE_FLOOR": "0.80",
    "CAT_BAND_PROBABILITY_STOP_ENABLED": "false",
    # PRE-5. The P(band) rule, when an arm turns it on, must be a refinement of the
    # precision stop rather than an alternative to it. Every propagation configuration is
    # judged by its effect on a posterior, so a rule that can finalise at an arbitrary SE
    # would confound the sweep with a second defect. Off in production, on here.
    "CAT_BAND_PROBABILITY_STOP_CONJUNCTIVE": "true",
    # R3. Blocking now requires two consistent failures; the prior 4.8-5.1% false-blocking
    # figures were measured at one. Frozen so that a factor sweep moves it deliberately or
    # not at all — an unpinned threshold that changed between the two studies would make
    # the blocking numbers incomparable without anything saying so.
    "GRAPH_MINIMUM_FAILURES_TO_BLOCK": "2",
    "ORCHESTRATOR_MAX_ITEMS": "120",
    "ORCHESTRATOR_TIME_LIMIT_MINUTES": "90",
    "ORCHESTRATOR_SHORTLIST_SIZE": "5",
    "CODE_APPROACH": "B",
    "CODE_RUBRIC": "mid",
    # Placeholders so `Settings` validates with no secrets present.
    "LITELLM_BASE_URL": "http://localhost:4000",
    "LITELLM_API_KEY": "placeholder",
    "LITELLM_MODEL": "placeholder-model",
    "E2B_API_KEY": "placeholder",
}
