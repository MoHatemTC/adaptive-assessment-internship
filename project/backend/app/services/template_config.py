"""Normalization + back-compat helpers for P1 template config.

Every reader tolerates pre-P1 data:
  - single `assessment_type` (string) instead of `modes` (list)
  - `tool_config.competencies` as `list[str]` instead of `list[{name, tag}]`
  - missing `adaptivity_level` / `intake_config`
so the app keeps working before/after the 011–012 migrations are applied.
"""

# Canonical run order for a multi-mode assessment.
MODE_ORDER = ["career", "technical", "behavioural"]

# Legacy assessment_type values -> canonical mode names.
_MODE_ALIASES = {
    "track": "technical",
    "discover": "career",
    "hr": "behavioural",
    "behavioral": "behavioural",
    "personality": "psychometric",
    "mbti": "psychometric",
}


def resolve_modes(template: dict) -> list[str]:
    """Ordered list of modes for a template. Falls back to the legacy
    `assessment_type`. Known modes are ordered Career -> Technical -> Behavioural;
    any others (e.g. psychometric) follow in their given order."""
    modes = list(template.get("modes") or [])
    if not modes:
        at = template.get("assessment_type")
        modes = [at] if at else []
    norm: list[str] = []
    for m in modes:
        m = _MODE_ALIASES.get(str(m).lower(), str(m).lower())
        if m not in norm:
            norm.append(m)
    ordered = [m for m in MODE_ORDER if m in norm]
    ordered += [m for m in norm if m not in MODE_ORDER]
    return ordered or ["technical"]


def primary_mode(template: dict) -> str:
    """Single representative mode (first in canonical order) — used where the
    legacy code still expects one assessment_type."""
    return resolve_modes(template)[0]


def normalize_competencies(tool_config: dict | None) -> list[dict]:
    """Coerce competencies into `[{name, tag}]`. Legacy bare strings are tagged
    'behavioural' by default."""
    raw = (tool_config or {}).get("competencies") or []
    out: list[dict] = []
    for c in raw:
        if isinstance(c, str) and c.strip():
            out.append({"name": c.strip(), "tag": "behavioural"})
        elif isinstance(c, dict) and c.get("name"):
            tag = (c.get("tag") or "behavioural").lower()
            out.append({"name": c["name"], "tag": "technical" if tag == "technical" else "behavioural"})
    return out


def competency_names(tool_config: dict | None) -> list[str]:
    """Just the competency names — the axis the report/scoring uses."""
    return [c["name"] for c in normalize_competencies(tool_config)]


def resolve_adaptivity(template: dict) -> str:
    """low | medium | high (defaults to high = today's per-question adaptive)."""
    lvl = str(template.get("adaptivity_level") or "high").lower()
    return lvl if lvl in ("low", "medium", "high") else "high"


def intake_config(template: dict) -> dict:
    """Admin intake config: {cv_required: bool, questions: [...]}."""
    cfg = template.get("intake_config") or {}
    return {
        "cv_required": cfg.get("cv_required", True),
        "questions": cfg.get("questions") or [],
    }
