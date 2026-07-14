"""
LLM-guided CAT item selection.

The LLM does NOT freestyle-pick questions. It executes a fixed procedural
algorithm on a pre-scored shortlist computed deterministically by the IRT engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine import fisher_info, rank_candidates, selection_score
from engine_log import get_logger
from llm_client import chat_json, llm_configured
from tracing import trace_llm_response

SELECTION_SYSTEM = """You are the Masaar adaptive testing selector.
You MUST follow the procedural algorithm exactly. You may ONLY choose an item id
from the provided shortlist — never invent ids.

Return JSON with this schema:
{
  "selected_id": "<id from shortlist>",
  "criterion_used": "KL" | "Fisher",
  "procedure_steps": [
    "Step 1: ...",
    "Step 2: ..."
  ],
  "adaptation_note": "<1-2 sentences on why this item best refines ability estimate now>",
  "rule_applied": "<which tie-break rule if any, else 'highest information'>"
}

PROCEDURE (execute in order):
1. Note questions_answered and criterion (KL if <3 else Fisher).
2. From shortlist, pick the item with the highest info_score for that criterion.
3. If two items are within 1% relative info_score, pick the one with smallest |b - theta_hat|.
4. If still tied, pick the sub_competency least represented in served_history.
5. If still tied, pick the highest discrimination (a).
6. selected_id MUST be one of the shortlist ids.
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
) -> SelectionResult | None:
    """Pure engine fallback — same rules the LLM must follow."""
    ranked, criterion = rank_candidates(theta_hat, q_count, pool, served_ids, posterior)
    if not ranked:
        return None

    sub_counts = _served_sub_counts(served_ids, pool)
    top_info = ranked[0][1]

    def sort_key(entry):
        q, info, fi, _kl = entry
        rel = info / max(top_info, 1e-9)
        near_top = rel >= 0.99
        return (
            info,
            -abs(q["b"] - theta_hat) if near_top else 0,
            -sub_counts.get(q.get("sub_competency", ""), 0) if near_top else 0,
            q.get("a", 1.0) if near_top else 0,
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
) -> SelectionResult | None:
    ranked, criterion = rank_candidates(theta_hat, q_count, pool, served_ids, posterior)
    if not ranked:
        return None

    sub_counts = _served_sub_counts(served_ids, pool)
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
        "procedure_reminder": [
            "Pick highest info_score for criterion",
            "Tie-break: smallest b_distance",
            "Then: least-served sub_competency",
            "Then: highest a",
            "Must return selected_id from shortlist only",
        ],
    }

    import json

    user_msg = json.dumps(payload, indent=2)
    data = chat_json(SELECTION_SYSTEM, user_msg)
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
        return deterministic_select(theta_hat, q_count, pool, served_ids, posterior)

    q = id_map[selected_id]
    info = selection_score(theta_hat, q_count, q, posterior)
    fi = fisher_info(theta_hat, q)

    steps = data.get("procedure_steps") or []
    if isinstance(steps, str):
        steps = [steps]

    result = SelectionResult(
        item=q,
        info_score=float(info),
        criterion=str(data.get("criterion_used", criterion)),
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
) -> SelectionResult | None:
    """Select next item — LLM procedural when configured, else deterministic."""
    history = history or []
    if use_llm and llm_configured():
        try:
            result = llm_select(
                theta_hat, se, q_count, competency, pool, served_ids, history, posterior
            )
            if result is not None:
                return result
        except Exception as exc:
            get_logger().warning("LLM_SELECT | error=%s — deterministic fallback", exc)
            # Annotate fallback so UI can show why LLM was skipped
            fallback = deterministic_select(theta_hat, q_count, pool, served_ids, posterior)
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
    return deterministic_select(theta_hat, q_count, pool, served_ids, posterior)
