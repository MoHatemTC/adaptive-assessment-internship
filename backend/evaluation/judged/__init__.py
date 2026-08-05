"""The judged layer (plan section 6). Isolated from the rest of the harness on purpose.

NOTHING HERE RUNS DURING `pytest`. Five independent locks, because one lock that someone
disables during debugging is not a lock:

  1. this package sits outside `testpaths = tests`, so pytest never collects it;
  2. every judged test carries `@pytest.mark.deepeval`, and pytest.ini excludes that mark;
  3. `conftest.py` skips unless EVAL_LIVE_LLM=1 AND the resolved key is not a placeholder;
  4. `deepeval` is pinned in requirements-eval.txt and absent from requirements.txt, so in
     the default environment the import fails before any call can be made;
  5. the runner prints a cost estimate and requires --confirm-cost.

The backstop is pytest.ini's own `env` block, which pins LITELLM_BASE_URL to localhost —
an accidental run fails to connect rather than billing.

WHY THE WHOLE LAYER IS DESCRIPTIVE

Section 6.4 requires Cohen's kappa >= 0.60 against >= 50 human dual-rated cases before a
judged metric may gate anything. There are no human raters here. Running the judge five
times and taking the median measures SELF-CONSISTENCY, which is a different quantity: a
judge can be perfectly consistent and consistently wrong, and no number of repeats
detects that. So every result this package produces is reported as descriptive, and none
of it has authority over a Tier-2 or Tier-3 endpoint. That is a permanent limitation of
running the suite without raters, not a to-do.
"""
