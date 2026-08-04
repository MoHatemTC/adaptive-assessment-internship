"""The B-vs-C evaluation harness.

Implements the executable core of `APPROACH_B_C_EVALUATION_METRICS_TEST_PLAN.md` as
amended by `BC_Test_Plan_Validation_and_Amendments.md`.

WHY THIS IS A SEPARATE PACKAGE AND NOT A TEST

`backend/tests` asserts that the engine does what it says. This package MEASURES what the
engine achieves, which is a different activity with a different output: a test returns
pass/fail, an evaluation returns an estimate with an interval. Mixing them is how a
measurement acquires a threshold nobody derived — the failure the validation document's
B1 and B4 are both about.

The same file set ships on BOTH approach branches. Everything graph-specific is
feature-detected at import time (`HAS_GRAPH`), so one runner produces comparable records
from Approach B and Approach C without a fork.
"""

from __future__ import annotations

import importlib.util

# Probed with `find_spec` rather than by importing. Importing the graph config would import
# `app.config.settings`, and `Settings` is instantiated at import time — so merely asking
# "does this branch have a graph?" would freeze the configuration before `run_arm` had
# applied the arm's flags, and every arm would silently run the defaults. That failure is
# invisible: the flags are set, the run completes, and the numbers are all from one
# configuration.
HAS_GRAPH = importlib.util.find_spec("app.services.competency_graph") is not None

__all__ = ["HAS_GRAPH"]
