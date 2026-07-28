try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None  # type: ignore

import base64
import json
import re
from openai import AsyncOpenAI
from app.config.settings import settings
from app.services.llm_retry import chat_completion

_client: AsyncOpenAI | None = None


def _llm() -> AsyncOpenAI:
    global _client
    if not _client:
        _client = AsyncOpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
        )
    return _client


EXTRACT_PROMPT = """You are a CV parser. Extract ALL information from this CV.

Return ONLY a valid JSON object — no markdown, no code fences, no trailing commas, no comments.
Use this exact structure:
{
  "skills": [{"name": "string", "level": "beginner|intermediate|advanced|expert", "years": 0}],
  "experience": [{"title": "string", "company": "string", "duration_months": 0, "description": "string"}],
  "education": [{"degree": "string", "institution": "string", "year": 0}],
  "projects": [{"name": "string", "description": "string", "technologies": ["string"]}],
  "claimed_expertise": ["string"],
  "raw_summary": "string"
}"""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse LLM response as JSON. Raw start: {text[:300]}")


def _pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract plain text from PDF using fitz (fast, token-efficient)."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        if i >= 6:
            break
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages).strip()


def _pdf_to_images(pdf_bytes: bytes) -> list[dict]:
    """Fallback: render pages as base64 PNG for scanned PDFs."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    for i, page in enumerate(doc):
        if i >= 3:
            break
        pix = page.get_pixmap(dpi=100)
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
        images.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
        })
    doc.close()
    return images


async def parse_cv(pdf_bytes: bytes) -> dict:
    if fitz is None:
        raise RuntimeError("pymupdf not installed — run: pip install pymupdf")

    # Try text extraction first — much cheaper on tokens
    text = _pdf_to_text(pdf_bytes)
    use_vision = len(text) < 200  # scanned PDF: very little extractable text

    if use_vision:
        images = _pdf_to_images(pdf_bytes)
        content = [{"type": "text", "text": EXTRACT_PROMPT}] + images
    else:
        # Cap text at ~6000 chars to stay well within output budget
        capped = text[:6000]
        content = [{"type": "text", "text": f"{EXTRACT_PROMPT}\n\nCV TEXT:\n{capped}"}]

    res = await chat_completion(
        _llm(),
        model=settings.litellm_model,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    raw = res.choices[0].message.content or ""
    return _extract_json(raw)


def looks_like_cv(parsed: dict) -> bool:
    """Heuristic: did we actually extract a résumé, or an empty/irrelevant PDF?
    Used to reject blank pages and non-CV uploads with a friendly message."""
    if not isinstance(parsed, dict):
        return False
    signal = sum(
        len(parsed.get(k) or [])
        for k in ("skills", "experience", "education", "projects", "claimed_expertise")
    )
    summary = (parsed.get("raw_summary") or "").strip()
    return signal >= 2 or len(summary) >= 120
