import json
from openai import AsyncOpenAI
from app.config.settings import settings

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if not _client:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


def _normalize(v: dict) -> dict:
    """Ensure a valid visual_kind and that the matching artifact field is present.
    Back-compat: an old chart-only payload (chart_data, no visual_kind) → 'chart'."""
    kind = (v.get("visual_kind") or "").strip().lower()
    if kind not in ("chart", "mermaid", "svg", "html"):
        if v.get("mermaid"):
            kind = "mermaid"
        elif v.get("svg"):
            kind = "svg"
        elif v.get("html"):
            kind = "html"
        else:
            kind = "chart"
    v["visual_kind"] = kind
    # keep a caption title (old field was chart_title)
    v["title"] = v.get("title") or v.get("chart_title") or ""
    return v


async def generate_visualization(
    skill_target: str,
    topic_hint: str,
    difficulty: str,
    cv_summary: str,
) -> dict:
    """Generate a VISUAL-STIMULUS question: the candidate is shown a visual artifact and
    asked to analyze/critique it. The model chooses the visual form that best measures the
    competency — a data chart, a Mermaid diagram (process/system/sequence), an SVG UI mockup
    (design critique), or an HTML artifact (marketing/content critique)."""
    prompt = f"""You are creating a VISUAL-STIMULUS assessment question. Show the candidate ONE visual artifact, then ask them to analyze or critique it. Choose the visual form that BEST measures the competency below — a strong item often embeds a realistic but subtly flawed artifact for the candidate to evaluate.

Competency to measure: {skill_target}
Topic: {topic_hint}
Difficulty: {difficulty}
Candidate background: {cv_summary[:400]}

Choose exactly ONE visual_kind and provide ONLY its matching artifact field:
- "chart"   → quantitative / data-analysis competencies. Provide chart_type + chart_data (labels + datasets of numbers).
- "mermaid" → process, system design, architecture, sequence, workflow, or data-model reasoning. Provide `mermaid` as VALID Mermaid source (e.g. `sequenceDiagram`, `flowchart TD`, `erDiagram`, `classDiagram`). Keep it self-contained and syntactically correct.
- "svg"     → UI / UX / visual-design competencies. Provide `svg` as a self-contained <svg>…</svg> mockup of a screen or component (rect/text/line/etc.; realistic layout; viewBox around "0 0 400 640"). No <script>, no external refs.
- "html"    → content / marketing / copywriting / social-media competencies. Provide `html` as a self-contained HTML fragment with INLINE CSS rendering a realistic artifact to critique (e.g. a social-media post card, a display ad, a marketing email, a landing hero). No <script>, no external URLs/images (use CSS shapes/emoji).

Return ONLY valid JSON (no markdown fences), including ONLY the artifact field for the chosen kind:
{{
  "visual_kind": "chart|mermaid|svg|html",
  "chart_type": "bar|line|scatter|pie",
  "chart_data": {{ "labels": ["str"], "datasets": [{{"label": "str", "data": [0.0]}}] }},
  "mermaid": "mermaid source string",
  "svg": "<svg ...>...</svg>",
  "html": "<div ...>...</div>",
  "title": "short caption describing the artifact",
  "question": "what the candidate must analyze / critique",
  "follow_up": "an optional deeper prompt",
  "expected_insights": ["insight the candidate should surface", "..."],
  "time_limit_seconds": 180
}}"""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    return _normalize(json.loads(res.choices[0].message.content))
