"""
LLM-guided CAT item selection.

The LLM does NOT freestyle-pick questions. It executes a fixed procedural
algorithm on a pre-scored shortlist computed deterministically by the IRT engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import (
    CONTENT_INFO_TOLERANCE,
    fisher_info,
    level_and_band,
    rank_candidates,
    selection_score,
)
from engine_log import get_logger
from llm_client import chat_json, llm_configured
from rephrase_guard import check_rephrase
from tracing import trace_llm_response

# "Within 1% relative info_score" — the band SELECTION_SYSTEM step 3 tells the LLM to
# treat as a tie. Code and prompt must agree on this number or the deterministic
# fallback is a different estimator from the LLM it stands in for.
NEAR_TOP_REL = 0.99

SELECTION_SYSTEM = """You are the Masaar adaptive testing selector.
You MUST follow the procedural algorithm exactly. You may ONLY choose an item id
from the provided shortlist — never invent ids.

Return JSON with this schema:
{
  "selected_id": "<id from shortlist>",
  "criterion_used": "<echo the `criterion` field given to you, verbatim>",
  "procedure_steps": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "adaptation_note": "<1-2 sentences on why this item best refines ability estimate now>",
  "rule_applied": "<which tie-break rule if any, else 'highest information'>",
  "rephrased_stem": "<selected question stem rewritten for the examinee, preserving meaning>"
}

PROCEDURE (execute in order):
1. Read the `criterion` field in the payload — the engine has already decided it
   (KL early in the test, otherwise Fisher). Do not choose it yourself.
2. CONTENT BALANCING (a constraint, not a preference). Consider only items whose
   `info_rel` >= 0.80, i.e. those carrying at least 80% of the best available
   information. Among those, pick the one with the SMALLEST
   `sub_competency_served_count`. A competency score must rest on its whole blueprint,
   not on whichever sub-competency happens to hold the sharpest items.
3. Within that eligible set, pick the highest info_score for the criterion.
4. If two items are within 1% relative info_score, pick the one with smallest |b - theta_hat|.
5. If still tied, pick the highest discrimination (a).
6. selected_id MUST be one of the shortlist ids.

Every item carries `info_rel` (its info_score relative to the shortlist's best) and
`sub_competency_served_count` so steps 2-3 need no arithmetic from you.

REPHRASING RULES:
- After selecting the item, rephrase only the selected item's stem.
- Preserve the technical meaning, answer, options, and difficulty.
- Keep EVERY identifier, code fragment, literal and number exactly as written
  (e.g. `sorted(nums)`, `df.shape`, (3, 4), 1_000_000). Do not paraphrase them
  into prose — the item is calibrated on them and a rewrite that drops one is
  rejected and discarded.
- Never restate any option's wording inside the stem, especially the correct one.
- Adapt wording to examinee_parameters.level_band and certainty_pct:
  lower certainty -> clearer wording; higher level -> use normal technical phrasing.
- If the original stem is already ideal, return it unchanged.
- Never add or remove a negation. "Which is TRUE" must not become "Which is NOT TRUE"
  or "Which is FALSE": grading uses the original answer key, so a flipped question marks
  the examinee wrong for answering correctly. Polarity flips are rejected by code.
"""


@dataclass
class SelectionResult:
    item: dict
    info_score: float
    criterion: str
    fisher_i: float
    llm_used: bool
    procedure_steps: list[str] = field(default_factory=list)
    adaptation_note: str = ""
    rule_applied: str = ""
    shortlist_ids: list[str] = field(default_factory=list)
    rephrased_stem: str = ""
    # Populated when rephrase_guard rejected the LLM's rewrite and the original
    # calibrated stem was administered instead. Surfaced in the UI, not swallowed.
    rephrase_rejected_reason: str = ""
    rephrase_rejected_code: str = ""
    expected_selected_id: str = ""
    procedure_followed: bool = True
    procedure_deviation_reason: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""


def _normalize_criterion(name: str) -> str:
    """Compare criteria by family, not by label.

    The engine reports "E[Fisher]" when it averages Fisher over the posterior, but that
    is still Fisher — the distinction is how it is evaluated, not which criterion. A
    model echoing "Fisher" is agreeing, and warning about it on every single call buries
    the mismatches that would matter (e.g. claiming Fisher during the KL phase).
    """
    n = name.strip().lower()
    if "fisher" in n:
        return "fisher"
    if "kl" in n:
        return "kl"
    return n


def _served_sub_counts(served_ids: list[str], pool: list[dict]) -> dict[str, int]:
    id_to_sub = {q["id"]: q.get("sub_competency", "") for q in pool}
    counts: dict[str, int] = {}
    for sid in served_ids:
        sub = id_to_sub.get(sid, "")
        counts[sub] = counts.get(sub, 0) + 1
    return counts


def _procedure_expected_choice(shortlist: list[dict]) -> dict:
    """Return the item the written LLM procedure would select from a shortlist.

    This is an audit, not a second selector: valid LLM picks are still administered,
    but the run records whether the model actually followed the procedure it was
    instructed to execute.
    """
    if not shortlist:
        return {}

    eligible = [s for s in shortlist if float(s.get("info_rel", 0.0)) >= CONTENT_INFO_TOLERANCE]
    if not eligible:
        eligible = shortlist

    min_served = min(int(s.get("sub_competency_served_count", 0)) for s in eligible)
    balanced = [s for s in eligible if int(s.get("sub_competency_served_count", 0)) == min_served]

    best_info = max(float(s.get("info_score", 0.0)) for s in balanced)
    near_best = [
        s for s in balanced
        if float(s.get("info_score", 0.0)) >= best_info * NEAR_TOP_REL
    ]

    return sorted(
        near_best,
        key=lambda s: (
            float(s.get("b_distance", 999.0)),
            -float(s.get("a", 0.0)),
            str(s.get("id", "")),
        ),
    )[0]


def _procedure_audit(selected_id: str, shortlist: list[dict]) -> dict:
    expected = _procedure_expected_choice(shortlist)
    expected_id = str(expected.get("id", ""))
    selected = next((s for s in shortlist if str(s.get("id", "")) == selected_id), None)
    if not selected:
        return {
            "expected_selected_id": expected_id,
            "procedure_followed": False,
            "procedure_deviation_reason": "selected_id was not in the shortlist",
        }
    if selected_id == expected_id:
        return {
            "expected_selected_id": expected_id,
            "procedure_followed": True,
            "procedure_deviation_reason": "",
        }

    eligible = [s for s in shortlist if float(s.get("info_rel", 0.0)) >= CONTENT_INFO_TOLERANCE]
    min_served = min(int(s.get("sub_competency_served_count", 0)) for s in eligible)
    balanced = [s for s in eligible if int(s.get("sub_competency_served_count", 0)) == min_served]
    best_info = max(float(s.get("info_score", 0.0)) for s in balanced)
    near_best = [
        s for s in balanced
        if float(s.get("info_score", 0.0)) >= best_info * NEAR_TOP_REL
    ]

    reason = f"expected {expected_id} by the documented procedure"
    if float(selected.get("info_rel", 0.0)) < CONTENT_INFO_TOLERANCE:
        reason = (
            f"selected {selected_id} below info_rel floor "
            f"{CONTENT_INFO_TOLERANCE:.2f}; expected {expected_id}"
        )
    elif int(selected.get("sub_competency_served_count", 0)) > min_served:
        reason = (
            f"selected {selected_id} from a more-served sub-competency "
            f"({selected.get('sub_competency_served_count')}); expected {expected_id}"
        )
    elif selected not in near_best:
        reason = (
            f"selected {selected_id} outside the 1% information band "
            f"for the least-served set; expected {expected_id}"
        )
    elif float(selected.get("b_distance", 999.0)) > float(expected.get("b_distance", 999.0)):
        reason = (
            f"selected {selected_id} with larger |b-theta| "
            f"({selected.get('b_distance')}); expected {expected_id}"
        )
    elif float(selected.get("a", 0.0)) < float(expected.get("a", 0.0)):
        reason = (
            f"selected {selected_id} with lower discrimination "
            f"({selected.get('a')}); expected {expected_id}"
        )

    return {
        "expected_selected_id": expected_id,
        "procedure_followed": False,
        "procedure_deviation_reason": reason,
    }


def deterministic_select(
    theta_hat: float,
    q_count: int,
    pool: list[dict],
    served_ids: list[str],
    posterior=None,
    rng=None,
    top_k=None,
    fallback_used: bool = False,
    fallback_reason: str = "",
) -> SelectionResult | None:
    """Pure engine fallback — same rules the LLM must follow."""
    ranked, criterion = rank_candidates(theta_hat, q_count, pool, served_ids, posterior,
                                        rng=rng, top_k=top_k)
    if not ranked:
        return None

    sub_counts = _served_sub_counts(served_ids, pool)
    top_info = ranked[0][1]

    def sort_key(entry):
        q, info, fi, _kl = entry
        rel = info / max(top_info, 1e-9)
        # Content balancing is a constraint, not a tie-break: among items carrying
        # essentially the information the best one does, prefer the least-served
        # sub-competency. It leads because a tie-break behind |b - theta| can never fire
        # (|b - theta| is continuous), which left coverage to luck.
        eligible = rel >= CONTENT_INFO_TOLERANCE
        coverage = -sub_counts.get(q.get("sub_competency", ""), 0) if eligible else -99
        # Then the 1% information band, collapsed into one key so the documented
        # tie-breaks can decide inside it. Keyed on raw `info` first, tuple comparison is
        # lexicographic, so a 0.5% difference settled the order outright and rules 3-5
        # were unreachable: across 505 θ-points the top two scores were never exactly
        # equal, so they ran 0 times. The LLM is told to apply a 1% band and did, so the
        # model and its "identical" fallback ran different procedures.
        primary = 1.0 if rel >= NEAR_TOP_REL else rel
        return (
            coverage,
            primary,
            -abs(q["b"] - theta_hat),
            q.get("a", 1.0),
        )

    ranked.sort(key=sort_key, reverse=True)
    q, info, fi, _ = ranked[0]
    steps = [
        f"Step 1: criterion={criterion} (questions_answered={q_count})",
        f"Step 2: ranked {len(ranked)} shortlist items by information",
        f"Step 3: selected {q['id']} (info={info:.4f}, |b-θ|={abs(q['b']-theta_hat):.2f})",
    ]
    return SelectionResult(
        item=q,
        info_score=float(info),
        criterion=criterion,
        fisher_i=float(fi),
        llm_used=False,
        procedure_steps=steps,
        adaptation_note="Engine-only selection (LLM unavailable).",
        rule_applied="highest information",
        shortlist_ids=[r[0]["id"] for r in ranked],
        expected_selected_id=q["id"],
        procedure_followed=True,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
    )


def llm_select(
    theta_hat: float,
    se: float,
    q_count: int,
    competency: str,
    pool: list[dict],
    served_ids: list[str],
    history: list[dict],
    posterior=None,
    certainty_pct: float | None = None,
    allow_rephrase: bool = True,
    rng=None,
    top_k=None,
) -> SelectionResult | None:
    ranked, criterion = rank_candidates(theta_hat, q_count, pool, served_ids, posterior,
                                        rng=rng, top_k=top_k)
    if not ranked:
        return None

    level, pct, band, low_confidence = level_and_band(theta_hat, se)
    if certainty_pct is None:
        certainty_pct = 100.0 if se <= 0 else max(0.0, min(100.0, 100.0 * (1.0 - se / 2.0)))
    sub_counts = _served_sub_counts(served_ids, pool)
    best_info = max((info for _q, info, _fi, _kl in ranked), default=0.0)
    shortlist = []
    for q, info, fi, kl in ranked:
        shortlist.append(
            {
                "id": q["id"],
                "difficulty": q.get("difficulty", "medium"),
                "discrimination": q.get("discrimination", "medium"),
                "sub_competency": q.get("sub_competency", ""),
                "b": round(q["b"], 2),
                "a": round(q["a"], 2),
                "info_score": round(info, 4),
                # Precomputed so the model never has to divide: content balancing keys
                # off this, and an arithmetic slip there would silently unbalance the
                # blueprint.
                "info_rel": round(info / best_info, 4) if best_info > 0 else 0.0,
                "fisher_i": round(fi, 4),
                "kl_i": round(kl, 4),
                "b_distance": round(abs(q["b"] - theta_hat), 2),
                "sub_competency_served_count": sub_counts.get(q.get("sub_competency", ""), 0),
                "stem": q.get("stem", ""),
            }
        )

    recent = [
        {
            "id": h["id"],
            "correct": h["correct"],
            "difficulty": h.get("difficulty"),
            "theta_hat_after": round(h["theta_hat"], 3),
            "se_after": round(h["se"], 3),
        }
        for h in history[-5:]
    ]

    payload = {
        "competency": competency,
        "theta_hat": round(theta_hat, 3),
        "se": round(se, 3),
        "examinee_parameters": {
            "competency_level": level,
            "competency_pct": pct,
            "level_band": band,
            "low_confidence": low_confidence,
            "certainty_pct": round(certainty_pct, 1),
        },
        "questions_answered": q_count,
        "criterion": criterion,
        "served_ids": served_ids,
        "recent_responses": recent,
        "shortlist": shortlist,
        "content_rule": {
            "info_rel_floor": CONTENT_INFO_TOLERANCE,
            "note": "Only items with info_rel >= floor are eligible; among them prefer "
                    "the smallest sub_competency_served_count.",
        },
        "procedure_reminder": [
            f"Eligible = info_rel >= {CONTENT_INFO_TOLERANCE}",
            "Among eligible: least-served sub_competency wins",
            "Then: highest info_score",
            "Then: smallest b_distance",
            "Then: highest a",
            "Must return selected_id from shortlist only",
            "Rephrase only the selected stem for the examinee level/certainty",
        ],
    }

    import json

    user_msg = json.dumps(payload, indent=2)
    data = chat_json(SELECTION_SYSTEM, user_msg)
    selected_id = str(data.get("selected_id", "")).strip()
    audit = _procedure_audit(selected_id, shortlist)
    trace_llm_response(
        "cat.llm.selection",
        input_data={
            "competency": competency,
            "theta_hat": round(theta_hat, 3),
            "se": round(se, 3),
            "questions_answered": q_count,
            "criterion": criterion,
            "served_ids": served_ids,
            "shortlist": shortlist,
        },
        output_data={**data, "procedure_audit": audit},
        metadata={
            "competency": competency,
            "phase": "llm_selection",
            "expected_selected_id": audit["expected_selected_id"],
            "llm_selected_id": selected_id,
            "procedure_followed": audit["procedure_followed"],
            "procedure_deviation_reason": audit["procedure_deviation_reason"],
        },
    )

    id_map = {q["id"]: q for q, *_ in ranked}
    if selected_id not in id_map:
        get_logger().warning(
            "LLM_SELECT | invalid id=%s — falling back to deterministic",
            selected_id,
        )
        return deterministic_select(theta_hat, q_count, pool, served_ids, posterior,
                                    rng=rng, top_k=top_k, fallback_used=True,
                                    fallback_reason=f"LLM returned invalid id {selected_id!r}")

    q = id_map[selected_id]

    # The rephrase is untrusted model output: check it before it reaches the examinee.
    # A rejected rewrite falls back to the calibrated wording rather than blocking the
    # item, so the worst case is a plainer question, never a leaked key.
    rephrase_reason = ""
    rephrase_code = ""
    if allow_rephrase:
        candidate = str(data.get("rephrased_stem", "")).strip()
        checked = check_rephrase(q.get("stem", ""), candidate, q["options"], q["answer_index"])
        rephrased_stem = checked.stem
        if not checked.ok:
            rephrase_reason, rephrase_code = checked.reason, checked.code
            get_logger().warning(
                "REPHRASE_REJECTED | %s | id=%s | code=%s | %s",
                competency, q["id"], checked.code, checked.reason,
            )
            trace_llm_response(
                "cat.llm.rephrase.rejected",
                input_data={"item_id": q["id"], "original_stem": q.get("stem", ""),
                            "proposed_stem": candidate},
                output_data={"reason": checked.reason, "code": checked.code},
                metadata={"competency": competency, "phase": "rephrase_guard"},
            )
    else:
        rephrased_stem = q.get("stem", "")

    info = selection_score(theta_hat, q_count, q, posterior)
    fi = fisher_info(theta_hat, q)

    # The criterion is a function of q_count (KL below 3, else Fisher) — the engine's
    # procedure decides it, not the model. Taking data["criterion_used"] on trust let a
    # wrong claim reach the log and UI: a KL score (a sum over the theta grid, order
    # 1-10) would get printed as "Fisher I = 8.0", a value 3PL Fisher cannot reach.
    # Report what actually ranked the shortlist, and flag disagreement as a prompt bug.
    llm_criterion = str(data.get("criterion_used", "")).strip()
    if llm_criterion and _normalize_criterion(llm_criterion) != _normalize_criterion(criterion):
        get_logger().warning(
            "LLM_SELECT | criterion mismatch: engine used %s, LLM reported %s (using engine's)",
            criterion, llm_criterion,
        )

    steps = data.get("procedure_steps") or []
    if isinstance(steps, str):
        steps = [steps]

    claimed = str(data.get("criterion_used", criterion))
    if _normalize_criterion(claimed) != _normalize_criterion(criterion):
        get_logger().warning(
            "LLM_SELECT | criterion mismatch: engine used %s, LLM reported %s",
            criterion,
            claimed,
        )

    if not audit["procedure_followed"]:
        get_logger().warning(
            "LLM_SELECT | procedure deviation | selected=%s | expected=%s | %s",
            selected_id,
            audit["expected_selected_id"],
            audit["procedure_deviation_reason"],
        )

    result = SelectionResult(
        item=q,
        info_score=float(info),
        criterion=criterion,
        fisher_i=float(fi),
        llm_used=True,
        procedure_steps=steps,
        adaptation_note=str(data.get("adaptation_note", "")),
        rule_applied=str(data.get("rule_applied", "")),
        shortlist_ids=[s["id"] for s in shortlist],
        rephrased_stem=rephrased_stem,
        rephrase_rejected_reason=rephrase_reason,
        rephrase_rejected_code=rephrase_code,
        expected_selected_id=audit["expected_selected_id"],
        procedure_followed=audit["procedure_followed"],
        procedure_deviation_reason=audit["procedure_deviation_reason"],
        fallback_used=False,
    )

    get_logger().info(
        "LLM_SELECT | %s | q=%d | id=%s | criterion=%s | info=%.4f | rule=%s",
        competency,
        q_count,
        selected_id,
        result.criterion,
        result.info_score,
        result.rule_applied,
    )
    return result


def select_next_item(
    theta_hat: float,
    se: float,
    q_count: int,
    competency: str,
    pool: list[dict],
    served_ids: list[str],
    history: list[dict] | None = None,
    posterior=None,
    certainty_pct: float | None = None,
    *,
    use_llm: bool = True,
    allow_rephrase: bool = True,
    rng=None,
    top_k=None,
) -> SelectionResult | None:
    """Select next item — LLM procedural when configured, else deterministic."""
    history = history or []
    if use_llm and llm_configured():
        try:
            result = llm_select(
                theta_hat,
                se,
                q_count,
                competency,
                pool,
                served_ids,
                history,
                posterior,
                certainty_pct,
                allow_rephrase=allow_rephrase,
                rng=rng,
                top_k=top_k,
            )
            if result is not None:
                return result
        except Exception as exc:
            get_logger().warning("LLM_SELECT | error=%s — deterministic fallback", exc)
            # Annotate fallback so UI can show why LLM was skipped
            fallback = deterministic_select(theta_hat, q_count, pool, served_ids, posterior,
                                            rng=rng, top_k=top_k, fallback_used=True,
                                            fallback_reason=f"{type(exc).__name__}: {exc}")
            if fallback is not None:
                fallback.adaptation_note = (
                    f"LLM unavailable ({type(exc).__name__}: {exc}). "
                    "Used deterministic CAT procedure instead."
                )
                fallback.procedure_steps = [
                    f"LLM call failed: {exc}",
                    *fallback.procedure_steps,
                ]
            return fallback
    return deterministic_select(theta_hat, q_count, pool, served_ids, posterior,
                                rng=rng, top_k=top_k)
