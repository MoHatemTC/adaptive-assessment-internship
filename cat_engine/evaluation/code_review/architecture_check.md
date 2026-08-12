# Architecture audit

## Baseline

The actual defaults and resolved AIE policy match the intended C-shipped graph baseline:
graph bookkeeping and convergence coverage are enabled; upward inference, descendant
blocking, filtering, and graph utility are disabled; edge preview is reporting-only. All 30
prerequisite edges resolve to zero inference-enabled and zero blocking-enabled edges.

Selection is time-aware and parses the modality floor as `code >= 1` and `voice >= 1` per
main competency. AIE resolves to critical-only direct coverage because C6 declares sixteen
sub-nodes against a twelve-question cap.

## Flow and isolation

The implementation has a clean deterministic measurement boundary:

- all modalities reduce to `GradedOutcome`;
- the generic fractional likelihood reduces bit-identically to the binary update;
- inferred graph signals have no score or weight and cannot enter the posterior;
- one administered response is rolled up once per main competency;
- inferred/preview nodes are separate from directly measured coverage;
- invalid or unavailable picker responses fall back to the deterministic choice.

The post-fix backend suite passed 611 tests with one intentional skip, and the independent
service seam passed 67 tests. The evaluation-only release contract passes all 11 assertions.

## Operational boundary remediation

HTTP, Streamlit, and voice sessions now own one entropy-backed generator for their lifetime.
Explicit creation seeds remain available for deterministic tests and replay; answer-level
seeds no longer reconstruct the exposure stream.

The band-probability requirement now vetoes every normal measurement-convergence branch.
Modality credit is recorded only for the main that actually presented the item, and
attainable code/voice minima veto convergence alongside the existing direct-coverage gate.

Streamlit-to-live-helper calls now use httpx's verified TLS defaults. Post-fix Bandit reports
zero high-severity findings.
