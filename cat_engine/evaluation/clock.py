"""A virtual clock, so the 90-minute limit is a real constraint in simulation.

WHY THIS IS NOT OPTIONAL

The orchestrator reads wall clock: `started_at = time.time()`, `elapsed_minutes` from the
difference, and — on the graph branch — reserves time for other competencies' unmet
modality minimums before an item is admissible. A simulated session runs in milliseconds,
so under a real clock `elapsed_minutes` is ~0 for every session ever run. Three things then
silently stop existing:

    - the `time_limit` stop can never fire
    - `_time_admissible` never filters anything, so the modality reservation never binds
    - E-04 (assessment duration) measures the simulator's speed, not the assessment's

and the architecture's own item-time estimates put a three-main session close enough to the
90-minute cap that whether it binds is one of the questions worth asking. The validation
document asks for E-04 to carry a gate; without a modelled clock there is nothing to gate.

So the clock advances by the item's own `expected_seconds` as each item is administered.
That is the same number selection divides information by, which means the session is
measured against the budget it was planned against.
"""

from __future__ import annotations


class VirtualClock:
    """Monotonic simulated wall clock, in seconds since session start."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self._now = float(start)

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        self._now += float(seconds)
        return self._now

    # `monotonic` and `perf_counter` are not used by the orchestrator today. Provided so
    # substituting this object for the `time` module cannot fail on an attribute a future
    # edit reaches for.
    monotonic = time
    perf_counter = time

    def sleep(self, seconds: float) -> None:  # pragma: no cover — never called
        self.advance(seconds)
