"""
Gate the enriched bank on the properties a CAT bank actually needs.

Structural checks (schema, coverage, ladder) are pass/fail. The cueing checks are
statistical: a bank is not "unbiased" because someone eyeballed it, it is
unbiased when the key's position and the key's length carry no signal a
test-taker could exploit. So we test them as hypotheses.

  * position cueing   -> key index should be uniform over {0,1,2,3}: chi-square
  * length cueing     -> P(key is longest) should sit at chance (1/4): two-sided
                         exact binomial. Same for shortest.
  * length-rank cueing-> mean length-rank of the key should sit at 2.5. A bank
                         can pass "is longest" while still having the key
                         reliably 2nd-longest, which is just as learnable.

Usage: python validate_bank.py <bank.json>
Exit code 1 if any hard check fails.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

DIFFICULTIES = ["very_easy", "easy", "medium", "hard", "very_hard"]
DISCRIMINATIONS = ["low", "medium", "high"]
N_OPTIONS = 4
MAX_LEN_RATIO = 1.4
ITEMS_PER_COMPETENCY = 24
ITEMS_PER_SUB = 3

# Fisher information floor the best item in a pool must clear at every ability we
# score. A 3PL item peaks at roughly 0.19*a^2 when c=0.25, so this is about what a
# well-targeted a=1.15 item is worth — i.e. "a real instrument exists here", not
# "an item happens to sit here".
MIN_PEAK_INFO = 0.25

# Reject a cueing hypothesis only on real evidence; with n=120 this catches a
# drift to ~37% longest-is-correct while tolerating honest sampling noise.
ALPHA = 0.01

failures: list[str] = []
warnings: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def binom_pmf(k: int, n: int, p: float) -> float:
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def binom_two_sided_p(k: int, n: int, p: float) -> float:
    """Exact two-sided binomial p-value (method of small p-values)."""
    obs = binom_pmf(k, n, p)
    return min(1.0, sum(binom_pmf(i, n, p) for i in range(n + 1) if binom_pmf(i, n, p) <= obs * (1 + 1e-9)))


def item_info(theta: float, q: dict) -> float:
    """Exact 3PL Fisher information — mirrors engine.fisher_info, kept dependency-free."""
    a, b, c = q["a"], q["b"], q["c"]
    p = c + (1 - c) / (1 + math.exp(-a * (theta - b)))
    p = min(max(p, 1e-9), 1 - 1e-9)
    ratio = (p - c) / (1.0 - c)
    return (a ** 2) * (ratio ** 2) * ((1.0 - p) / p)


def corr(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def chisq_uniform(counts: list[int]) -> tuple[float, int]:
    n = sum(counts)
    exp = n / len(counts)
    stat = sum((c - exp) ** 2 / exp for c in counts)
    return stat, len(counts) - 1


def _lower_gamma_reg(s: float, x: float) -> float:
    """Regularized lower incomplete gamma P(s, x) via series expansion."""
    if x <= 0:
        return 0.0
    term = 1.0 / s
    total = term
    for k in range(1, 500):
        term *= x / (s + k)
        total += term
        if term < total * 1e-14:
            break
    return total * math.exp(-x + s * math.log(x) - math.lgamma(s))


def chisq_sf(stat: float, df: int) -> float:
    """Upper-tail p-value for a chi-square statistic. No scipy in this env."""
    if stat <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - _lower_gamma_reg(df / 2.0, stat / 2.0)))


def main() -> int:
    bank = json.loads(Path(sys.argv[1]).read_text())
    n = len(bank)
    print(f"Bank: {sys.argv[1]}  ({n} items)\n")

    # ---------- structure ----------
    ids = [q["id"] for q in bank]
    if len(set(ids)) != n:
        fail(f"duplicate ids: {sorted({i for i in ids if ids.count(i) > 1})}")

    required = {"id", "competency", "sub_competency", "difficulty", "discrimination",
                "stem", "options", "answer_index", "a", "b", "c"}
    for q in bank:
        missing = required - set(q)
        if missing:
            fail(f"{q.get('id', '?')}: missing fields {sorted(missing)}")
            continue
        opts = q["options"]
        if len(opts) != N_OPTIONS:
            fail(f"{q['id']}: {len(opts)} options (want {N_OPTIONS})")
        if len(set(o.strip().lower() for o in opts)) != len(opts):
            fail(f"{q['id']}: duplicate option text")
        if not isinstance(q["answer_index"], int) or not 0 <= q["answer_index"] < len(opts):
            fail(f"{q['id']}: answer_index out of range")
        if q["difficulty"] not in DIFFICULTIES:
            fail(f"{q['id']}: bad difficulty {q['difficulty']!r}")
        if q["discrimination"] not in DISCRIMINATIONS:
            fail(f"{q['id']}: bad discrimination {q['discrimination']!r}")
        for junk in ("all of the above", "none of the above"):
            if any(junk in o.lower() for o in opts):
                fail(f"{q['id']}: contains '{junk}' option")

    stems = [q["stem"].strip().lower() for q in bank]
    for s, c in Counter(stems).items():
        if c > 1:
            fail(f"duplicate stem x{c}: {s[:70]}…")

    # ---------- IRT parameters ----------
    print("IRT parameters")
    for q in bank:
        if not isinstance(q["a"], (int, float)) or not 0.2 <= q["a"] <= 2.5:
            fail(f"{q['id']}: a={q.get('a')} out of plausible range [0.2, 2.5]")
        if not isinstance(q["b"], (int, float)) or not -3.0 <= q["b"] <= 3.0:
            fail(f"{q['id']}: b={q.get('b')} out of plausible range [-3, 3]")
        if abs(q["c"] - 1 / N_OPTIONS) > 1e-9:
            fail(f"{q['id']}: c={q['c']} != {1 / N_OPTIONS} for a {N_OPTIONS}-option MCQ")

    bs = sorted(q["b"] for q in bank)
    a_s = [q["a"] for q in bank]
    print(f"  b: n_distinct={len(set(bs))}  range=[{bs[0]:+.2f}, {bs[-1]:+.2f}]")
    print(f"  a: n_distinct={len(set(a_s))}  range=[{min(a_s):.2f}, {max(a_s):.2f}]")

    # A CAT needs information everywhere it might land theta. Gaps in b are where
    # Fisher information collapses and the estimate stops converging.
    gaps = [(bs[i + 1] - bs[i], bs[i], bs[i + 1]) for i in range(len(bs) - 1)]
    worst = max(gaps)
    print(f"  largest gap in b ladder: {worst[0]:.2f} (between {worst[1]:+.2f} and {worst[2]:+.2f})")
    if worst[0] > 0.5:
        fail(f"b ladder has a {worst[0]:.2f} gap near {worst[1]:+.2f} — Fisher information collapses there")
    if bs[0] > -2.0 or bs[-1] < 2.0:
        fail(f"b range [{bs[0]:+.2f}, {bs[-1]:+.2f}] does not cover [-2, +2]")

    # b must actually track the difficulty label, else the labels are decorative.
    for i in range(len(DIFFICULTIES) - 1):
        lo = [q["b"] for q in bank if q["difficulty"] == DIFFICULTIES[i]]
        hi = [q["b"] for q in bank if q["difficulty"] == DIFFICULTIES[i + 1]]
        if lo and hi and max(lo) >= min(hi):
            fail(f"b bands overlap: {DIFFICULTIES[i]} reaches {max(lo):+.2f}, "
                 f"{DIFFICULTIES[i + 1]} starts {min(hi):+.2f}")
    for label in DISCRIMINATIONS:
        vals = [q["a"] for q in bank if q["discrimination"] == label]
        if vals:
            print(f"  a[{label}]: [{min(vals):.2f}, {max(vals):.2f}]  n={len(vals)}")

    # ---------- coverage ----------
    print("\nCoverage")
    by_comp = defaultdict(list)
    for q in bank:
        by_comp[q["competency"]].append(q)
    for comp in sorted(by_comp):
        items = by_comp[comp]
        dc = Counter(q["difficulty"] for q in items)
        ladder = "/".join(str(dc.get(d, 0)) for d in DIFFICULTIES)
        subs = Counter(q["sub_competency"] for q in items)
        print(f"  {comp:34s} n={len(items):3d}  ladder(ve/e/m/h/vh)={ladder}  subs={len(subs)}")
        if len(items) != ITEMS_PER_COMPETENCY:
            fail(f"{comp}: {len(items)} items (want {ITEMS_PER_COMPETENCY})")
        for d in DIFFICULTIES:
            if dc.get(d, 0) < 4:
                fail(f"{comp}: only {dc.get(d, 0)} {d} items — thin rung on the ladder")
        if len(subs) != 8:
            fail(f"{comp}: {len(subs)} sub-competencies (want 8)")
        for s, c in sorted(subs.items()):
            if c != ITEMS_PER_SUB:
                fail(f"{comp} / {s}: {c} items (want {ITEMS_PER_SUB})")

    # ---------- information coverage ----------
    # The check that matters most, and the one static ladder stats miss. A bank can
    # span b = [-2.4, +2.4] and still be unable to measure a weak candidate, because
    # information depends on the `a` of the items sitting near that theta — not just
    # on one existing there. If every low-b item also has low a, Fisher information
    # collapses at low theta and theta-hat never converges. So: walk the ability
    # range and ask what the BEST available item is actually worth at each point.
    print("\nInformation coverage (best available item at each ability)")
    print(f"  {'θ':>6} " + " ".join(f"{c.split()[0][:6]:>7}" for c in sorted(by_comp)))
    thin: list[tuple[str, float, float]] = []
    for theta in [-2.5, -2.0, -1.0, 0.0, 1.0, 2.0, 2.5]:
        cells = []
        for comp in sorted(by_comp):
            best = max(item_info(theta, q) for q in by_comp[comp])
            cells.append(f"{best:7.3f}")
            if best < MIN_PEAK_INFO:
                thin.append((comp, theta, best))
        print(f"  {theta:+6.1f} " + " ".join(cells))
    for comp, theta, best in thin:
        fail(f"{comp}: best item at θ={theta:+.1f} yields only I={best:.3f} "
             f"(< {MIN_PEAK_INFO}) — no sharp item measures that ability")

    # A very_easy item CAN discriminate sharply: `a` and `b` are independent. If the
    # bank pins high `a` to high `b`, weak candidates only ever get blunt items.
    print("\n  difficulty x discrimination")
    print(f"  {'':12s} {'low':>5} {'med':>5} {'high':>5}  mean_a")
    for d in DIFFICULTIES:
        dcc = Counter(q["discrimination"] for q in bank if q["difficulty"] == d)
        avals = [q["a"] for q in bank if q["difficulty"] == d]
        print(f"  {d:12s} {dcc.get('low', 0):5d} {dcc.get('medium', 0):5d} "
              f"{dcc.get('high', 0):5d}  {sum(avals) / len(avals):.2f}")
        if dcc.get("high", 0) == 0:
            fail(f"no high-discrimination items at difficulty '{d}' — "
                 f"ability near that b can only be measured bluntly")

    # Quantify the coupling directly rather than eyeballing the table.
    r = corr([DIFFICULTIES.index(q["difficulty"]) for q in bank], [q["a"] for q in bank])
    print(f"  corr(difficulty rank, a) = {r:+.2f}   (0 = a and b independent, as IRT assumes)")
    if abs(r) > 0.75:
        fail(f"discrimination mirrors difficulty (corr={r:+.2f}) — `a` is not independent of `b`")
    elif abs(r) > 0.5:
        warn(f"discrimination partly tracks difficulty (corr={r:+.2f})")

    # ---------- cueing: key position ----------
    print("\nAnti-bias: key position")
    pos = Counter(q["answer_index"] for q in bank)
    counts = [pos.get(i, 0) for i in range(N_OPTIONS)]
    stat, df = chisq_uniform(counts)
    p_pos = chisq_sf(stat, df)
    pct = "  ".join(f"{i}:{c} ({c / n:.0%})" for i, c in enumerate(counts))
    print(f"  distribution  {pct}")
    print(f"  chi-square={stat:.2f} df={df} p={p_pos:.3f}")
    if p_pos < ALPHA:
        fail(f"key position is not uniform (chi-square={stat:.2f}, p={p_pos:.4f})")

    # A uniform overall key distribution can still hide "hard items are always D".
    rows = []
    for d in DIFFICULTIES:
        c = Counter(q["answer_index"] for q in bank if q["difficulty"] == d)
        rows.append([c.get(i, 0) for i in range(N_OPTIONS)])
    stat_i = sum(chisq_uniform(r)[0] for r in rows)
    df_i = sum(chisq_uniform(r)[1] for r in rows)
    p_i = chisq_sf(stat_i, df_i)
    print(f"  position x difficulty: chi-square={stat_i:.2f} df={df_i} p={p_i:.3f}")
    if p_i < ALPHA:
        warn(f"key position correlates with difficulty (chi-square={stat_i:.2f}, p={p_i:.4f})")

    # ---------- cueing: key length ----------
    print("\nAnti-bias: key length")
    longest = shortest = 0
    ratio_bad = []
    rank_sum = 0.0
    for q in bank:
        lens = [len(o) for o in q["options"]]
        kl = lens[q["answer_index"]]
        if kl == max(lens):
            longest += 1
        if kl == min(lens):
            shortest += 1
        if min(lens) and max(lens) / min(lens) > MAX_LEN_RATIO:
            ratio_bad.append((q["id"], max(lens) / min(lens)))
        # 1 = shortest .. 4 = longest; ties share the average rank.
        rank_sum += sum(1 for x in lens if x < kl) + (1 + sum(1 for x in lens if x == kl)) / 2

    p_long = binom_two_sided_p(longest, n, 1 / N_OPTIONS)
    p_short = binom_two_sided_p(shortest, n, 1 / N_OPTIONS)
    print(f"  key is longest    {longest:3d}/{n} ({longest / n:.0%})  chance=25%  p={p_long:.3f}")
    print(f"  key is shortest   {shortest:3d}/{n} ({shortest / n:.0%})  chance=25%  p={p_short:.3f}")
    if p_long < ALPHA:
        fail(f"key length is a cue: longest-is-correct {longest}/{n} ({longest / n:.0%}) vs 25% chance, p={p_long:.4f}")
    if p_short < ALPHA:
        fail(f"key length is a cue: shortest-is-correct {shortest}/{n} ({shortest / n:.0%}) vs 25% chance, p={p_short:.4f}")

    mean_rank = rank_sum / n
    # Under no cueing the key's length-rank is uniform on {1..4}: mean 2.5, var 1.25.
    se = math.sqrt(1.25 / n)
    z = (mean_rank - 2.5) / se
    print(f"  mean length-rank  {mean_rank:.2f}  (2.50 = no cue)  z={z:+.2f}")
    if abs(z) > 2.576:  # alpha=.01 two-sided
        fail(f"key length-rank is a cue: mean {mean_rank:.2f} vs 2.50, z={z:+.2f}")

    print(f"  option length ratio <= {MAX_LEN_RATIO}: {n - len(ratio_bad)}/{n} items")
    for qid, r in sorted(ratio_bad, key=lambda x: -x[1])[:10]:
        fail(f"{qid}: option length ratio {r:.2f} > {MAX_LEN_RATIO} — key may be visually cued")

    # ---------- report ----------
    print("\n" + "=" * 62)
    for w in warnings:
        print(f"WARN  {w}")
    if failures:
        print(f"FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("PASSED — all structural, IRT, and anti-cueing checks green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
