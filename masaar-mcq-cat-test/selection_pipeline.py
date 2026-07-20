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
    rank_candidates,
    selection_score,
)
from engine_log import get_logger
from llm_client import chat_json, llm_configured
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
  "rule_applied": "<which tie-break rule if any, else 'highest information'>"
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


def _normalize_criterion(name: str) -> str:
    """Map criterion labels to a family. The engine may report "E[Fisher]" while the
    LLM echoes "Fisher"; both are the same criterion for mismatch purposes."""
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


def deterministic_select(
    theta_hat: float,
    q_count: int,
    pool: list[dict],
    served_ids: list[str],
    posterior=None,
    rng=None,
    top_k=None,
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
    rng=None,
    top_k=None,
) -> SelectionResult | None:
    ranked, criterion = rank_candidates(theta_hat, q_count, pool, served_ids, posterior,
                                        rng=rng, top_k=top_k)
    if not ranked:
        return None

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
                "stem_preview": (q.get("stem", "")[:120] + "…") if len(q.get("stem", "")) > 120 else q.get("stem", ""),
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
        ],
    }

    import json

    user_msg = json.dumps(payload, indent=2)
    # selected_id is what makes this a selection rather than a paragraph about one; a
    # reply without it is retried instead of silently becoming a coded fallback.
    data = chat_json(SELECTION_SYSTEM, user_msg, require=("selected_id",))
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
        output_data=data,
        metadata={"competency": competency, "phase": "llm_selection"},
    )

    selected_id = str(data.get("selected_id", "")).strip()
    id_map = {q["id"]: q for q, *_ in ranked}
    if selected_id not in id_map:
        get_logger().warning(
            "LLM_SELECT | invalid id=%s — falling back to deterministic",
            selected_id,
        )
        return deterministic_select(theta_hat, q_count, pool, served_ids, posterior,
                                    rng=rng, top_k=top_k)

    q = id_map[selected_id]
    info = selection_score(theta_hat, q_count, q, posterior)
    fi = fisher_info(theta_hat, q)

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
    *,
    use_llm: bool = True,
    rng=None,
    top_k=None,
) -> SelectionResult | None:
    """Select next item — LLM procedural when configured, else deterministic."""
    history = history or []
    if use_llm and llm_configured():
        try:
            result = llm_select(
                theta_hat, se, q_count, competency, pool, served_ids, history, posterior,
                rng=rng, top_k=top_k,
            )
            if result is not None:
                return result
        except Exception as exc:
            get_logger().warning("LLM_SELECT | error=%s — deterministic fallback", exc)
            # Annotate fallback so UI can show why LLM was skipped
            fallback = deterministic_select(theta_hat, q_count, pool, served_ids, posterior,
                                            rng=rng, top_k=top_k)
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
