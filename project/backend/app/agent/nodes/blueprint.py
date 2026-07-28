import json
from openai import AsyncOpenAI
from app.config.settings import settings
from app.agent.state import AgentState

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


BLUEPRINT_SYSTEM = """You are an assessment architect. Given an admin's assessment prompt and config,
generate a structured assessment blueprint as JSON. Be precise and practical.
The blueprint must include:
- total_questions: int
- tool_sequence: list of tool types in order (values: mcq, diagram, voice, camera, coding)
- skill_weights: dict with keys thinking/soft/work/digital_ai/growth summing to 1.0
- difficulty_start: "easy" | "medium" | "hard"
- voice_time_limit_minutes: int
- camera_time_limit_minutes: int
- coding_time_limit_minutes: int
Return ONLY valid JSON."""


async def generate_blueprint(state: AgentState) -> dict:
    template_id = state["blueprint"].get("template_id", "")
    admin_prompt = state["blueprint"].get("admin_prompt", "")
    tool_config = state["blueprint"].get("enabled_tools", {})
    track = state.get("track", "general")

    prompt = f"""Admin prompt: {admin_prompt}
Track: {track}
Enabled tools: {json.dumps(tool_config)}
Generate the assessment blueprint."""

    res = await _llm().chat.completions.create(
        model=settings.litellm_model,
        messages=[
            {"role": "system", "content": BLUEPRINT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    blueprint_data = json.loads(res.choices[0].message.content)
    blueprint_data["template_id"] = template_id
    blueprint_data["track"] = track
    blueprint_data["enabled_tools"] = tool_config
    return blueprint_data
