# `cat_engine/evaluation/judged/`

The judged-metric layer. **This makes billed model calls.**

## Five locks, and each is independent

1. This package is outside `testpaths`, so `pytest` never collects it.
2. Its tests carry the `deepeval` marker, which `addopts` excludes by default.
3. `deepeval` is in `requirements-eval.txt`, never `requirements.txt` — a package that
   cannot be imported cannot be invoked by accident.
4. Every module here imports without `deepeval` installed, so importing is never what
   triggers a call.
5. `tests/test_adversarial_and_judged.py` asserts all four of the above, by reading
   `pytest.ini` and `requirements.txt` rather than trusting them.

An accidental `pytest` cannot spend money even if someone moves the files.

## Files

| File | Responsibility |
|---|---|
| `metrics.py` | The judged metrics and the exact DeepEval pin. Pinned by version, not by range: DeepEval moved its parameter enum between releases, and a metric that silently changes shape makes two runs incomparable without saying so. |
| `goldens.py` | The canaries — cases whose verdict is known, so a drifting judge is visible. |
| `reliability.py` | Flip rate across repeated judgements. `MAX_FLIP_RATE` is the gate. |
| `run_judged.py` | Drives the layer. The only entry point, and it is never called by `pytest`. |
| `__init__.py` | Where the five locks are written down, beside the code they protect. |
| `conftest.py` | Marks everything here `deepeval`. |
