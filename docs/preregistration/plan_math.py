"""
Design arithmetic for the C-shipped configurable-propagation test plan.

Anchors, all from run3 (n=1,600/cell, DGP-2, C-full):
  sessions                     1,600
  sessions firing inference      735  (45.9%)
  verified inferences          1,386  (0.87 per session, 1.89 per firing session)
  wrong inference rate         0.2244  (311/1386), 95% UCB 0.2436
  false blocking               0.0872  (262/3006), 95% UCB 0.0961
  blocks verified              3,006

Run with: python docs/preregistration/plan_math.py
Every sample size in C_Shipped_Propagation_Test_Plan.md is derived here, not chosen.
"""
import numpy as np
from scipy.stats import beta

SESS = 1600
INF_VERIFIED = 1386
INF_PER_SESSION = INF_VERIFIED / SESS
WRONG = 0.2244
BLOCK_VERIFIED, BLOCK_RATE = 3006, 0.0872


def cp_upper(k, n, alpha=0.05):
    return 1.0 if k >= n else beta.ppf(1 - alpha, k + 1, n - k)


def events_needed(gate, true_rate, alpha=0.05):
    n = 20
    while n <= 2_000_000:
        if cp_upper(int(round(true_rate * n)), n, alpha) < gate:
            return n
        n = int(n * 1.04) + 1
    return None


print("=" * 80)
print("1 - CORROBORATION DOSE-RESPONSE (require k independent strong successes)")
print("=" * 80)
print("  Observed single-observation wrong rate: 0.2244")
print("  If the k errors were independent, wrong_k = wrong_1^k. They are not:")
print("  errors share the candidate, so model a within-candidate correlation r.")
print("  Approximate: wrong_k = wrong_1 * (r + (1-r)*wrong_1)^(k-1)")
print()
print("   r (error corr)     k=1      k=2      k=3      k=4   | k passing 3% gate")
for r in (0.0, 0.2, 0.4, 0.6, 0.8):
    row, first = [], None
    for k in (1, 2, 3, 4):
        w = WRONG * (r + (1 - r) * WRONG) ** (k - 1)
        row.append(w)
        if first is None and w < 0.03:
            first = k
    print(f"      {r:.1f}          " + "  ".join(f"{w:.4f}" for w in row)
          + f"   | {first if first else 'none'}")
print()
print("  READ: corroboration is the most promising single knob, but its value")
print("  collapses as error correlation rises. If a candidate's misjudged node")
print("  drives BOTH observations, k does almost nothing. Measuring r is")
print("  therefore a first-class objective, not a nuisance parameter.")

print()
print("=" * 80)
print("2 - THE SAFETY PARADOX: tightening shrinks the evidence for safety")
print("=" * 80)
print("  Verified inferences needed for the 95% UCB to clear a 3% gate:")
print()
print("   true wrong rate   verified events needed")
for tr in (0.000, 0.005, 0.010, 0.015, 0.020, 0.025):
    n = events_needed(0.03, tr)
    print(f"        {tr*100:5.2f}%              {n:>8,}")
print()
print("  Now the cost in SESSIONS. Tightening cuts inference volume, so the")
print("  same number of verified events needs proportionally more sessions.")
print("  Baseline yield: 0.87 verified inferences per session.")
print()
print("   volume vs baseline   yield/session   sessions for a 1.0% true rate")
for vol in (1.00, 0.50, 0.20, 0.10, 0.05, 0.02):
    y = INF_PER_SESSION * vol
    need = events_needed(0.03, 0.010)
    print(f"        {vol*100:5.1f}%            {y:.3f}          {need/y:>10,.0f}")
print()
print("  READ: a configuration conservative enough to be safe fires so rarely")
print("  that PROVING it safe costs 10-50x the sessions. Budget for this, or")
print("  the sweep will end with an unfalsifiable 'looks fine' verdict.")
print("  Mitigation: verify against node truth (free in simulation) rather")
print("  than by serving extra questions, and oversample firing sessions.")

print()
print("=" * 80)
print("3 - BLOCKING: is any configuration reachable?")
print("=" * 80)
print("  Structural floor reported: P(descendant mastered | parent not) =")
print("  10.8% (DGP-1) / 14.6% (DGP-2). A block on PERFECT parent knowledge")
print("  is wrong that often. Best attainable false-block rate:")
print()
print("   parent-verdict accuracy   floor 10.8%   floor 14.6%")
for pa in (0.80, 0.90, 0.95, 0.99, 1.00):
    a = pa * 0.108 + (1 - pa) * 0.50
    b = pa * 0.146 + (1 - pa) * 0.50
    print(f"          {pa:.2f}                {a:.4f}        {b:.4f}")
print()
print("  Even at perfect parent knowledge the floor is 5-7x the 2% gate.")
print("  => Blocking is NOT a parameter-tuning problem. The sweep should")
print("     spend ONE cell confirming the floor per edge and stop, rather")
print("     than exploring a space that has no feasible region.")

print()
print("=" * 80)
print("4 - BREAK-EVEN PERSONA PREVALENCE")
print("=" * 80)
print("  Propagation pays only if savings outweigh the accuracy it costs on")
print("  candidates who learned out of order. With:")
print("     S  = questions saved per session (propagation on vs off)")
print("     Y  = accuracy cost, in pp, on a prerequisite-violating persona")
print("     E  = exchange rate, pp of accuracy per question saved")
print("  break-even prevalence  p* = S * E / Y")
print()
print("   S (questions saved)   Y = 5pp    Y = 10pp   Y = 20pp   (at E = 0.5pp/q)")
for S in (0.5, 1.0, 2.0, 3.0):
    row = [S * 0.5 / y for y in (5, 10, 20)]
    print(f"        {S:.1f}              " +
          "  ".join(f"{min(p,1)*100:6.1f}%" for p in row))
print()
print("  READ: if propagation saves 1 question and costs 10pp on spiky")
print("  candidates, it pays only if fewer than 5% of the population is spiky.")
print("  For a self-taught developer pipeline that is implausible. Estimating")
print("  the real prevalence is a GOLD-SET question, not a simulation one.")

print()
print("=" * 80)
print("5 - SCREENING DESIGN SIZE")
print("=" * 80)
factors = {
    "max_propagation_depth": 4, "corroboration_k": 3,
    "min_propagation_confidence": 3, "success_score_threshold": 3,
    "modality_allowlist": 4, "edge_allowlist": 2, "upward_decay": 3,
}
full = int(np.prod(list(factors.values())))
print(f"  Full factorial over {len(factors)} propagation factors: {full:,} cells")
print(f"  At 1,600 sessions/cell: {full*1600:,} simulated sessions - not runnable.")
print()
print("  Resolution-IV fractional factorial at 2 levels each:")
for k in (7, 8):
    for frac in (4, 8, 16):
        runs = 2 ** k // frac
        if runs >= 2 * k:
            print(f"    {k} factors, 1/{frac} fraction -> {runs} runs "
                  f"({runs*1600:,} sessions) - estimates all main effects")
            break
print()
print("  Then a focused response surface on the 2-3 active factors only.")
print("  Screening first is what keeps this runnable.")
