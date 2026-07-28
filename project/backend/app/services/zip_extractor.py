"""
zip_extractor.py — Extract text/media content from a ZIP task submission.

Supported content inside the ZIP:
  Text/code : any extension readable as UTF-8 (.py .js .ts .html .css .json
              .yaml .csv .sql .sh .md .txt .r .go .java .cpp … any code file)
  Documents : .pdf  → PyMuPDF text extraction
              .docx → python-docx
              .xlsx / .xls → openpyxl
  Images    : .png .jpg .jpeg .webp .gif → Gemini vision description
  Audio     : .mp3 .wav .m4a .ogg .flac .aac .opus → Whisper transcription
  Video     : .mp4 .mov .avi .webm .mkv → key-frame extraction → Gemini vision
"""

import asyncio
import base64
import io
import os
import tempfile
import zipfile
from pathlib import Path

from openai import AsyncOpenAI

from app.config.settings import settings

# ── File-type sets ────────────────────────────────────────────────────────────

IMAGE_EXTENSIONS  = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"}
AUDIO_EXTENSIONS  = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus", ".mp4a"}
VIDEO_EXTENSIONS  = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".flv", ".wmv", ".m4v", ".3gp"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}

# Everything else is treated as readable text (code, config, data, markup…)
# We try UTF-8 decode; if it fails we skip.
SKIP_ALWAYS = {".exe", ".dll", ".so", ".bin", ".pyc", ".pyo", ".class", ".o", ".a"}
SKIP_DIRS   = {"__pycache__", ".git", ".svn", "node_modules", ".DS_Store"}

# Per-file content cap (characters)
TEXT_CAP    = 6_000
DOC_CAP     = 8_000
VISION_CAP  = 300    # tokens for image/frame description
MAX_FILES   = 60
MAX_FRAMES  = 5      # key frames extracted per video

# ── MIME helpers ──────────────────────────────────────────────────────────────

_AUDIO_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",  ".flac": "audio/flac", ".aac": "audio/aac",
    ".opus": "audio/opus",
}
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


# ── Document extractors (sync) ────────────────────────────────────────────────

def _extract_pdf(data: bytes, name: str) -> str:
    try:
        import fitz  # PyMuPDF — already in requirements
        doc = fitz.open(stream=data, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        return text[:DOC_CAP]
    except Exception as e:
        return f"[PDF extraction failed for {name}: {e}]"


def _extract_docx(data: bytes, name: str) -> str:
    try:
        from docx import Document  # python-docx
        doc = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        # Also extract tables
        for table in doc.tables:
            for row in table.rows:
                paragraphs.append(" | ".join(c.text for c in row.cells if c.text.strip()))
        return "\n".join(paragraphs)[:DOC_CAP]
    except Exception as e:
        return f"[DOCX extraction failed for {name}: {e}]"


def _extract_xlsx(data: bytes, name: str) -> str:
    try:
        import openpyxl  # openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows: list[str] = []
        for ws in wb.worksheets:
            rows.append(f"[Sheet: {ws.title}]")
            for row in ws.iter_rows(values_only=True):
                if any(c is not None for c in row):
                    rows.append(",".join("" if c is None else str(c) for c in row))
        return "\n".join(rows)[:DOC_CAP]
    except Exception as e:
        return f"[XLSX extraction failed for {name}: {e}]"


# ── Async media handlers ──────────────────────────────────────────────────────

async def _describe_image(data: bytes, name: str, llm: AsyncOpenAI) -> str:
    ext  = Path(name).suffix.lower()
    mime = _IMAGE_MIME.get(ext, "image/jpeg")
    b64  = base64.b64encode(data).decode()
    try:
        res = await llm.chat.completions.create(
            model=settings.litellm_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        "Describe what you see in this image with focus on any work, "
                        "deliverables, diagrams, data, or relevant content for assessment purposes."
                    )},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            }],
            max_tokens=VISION_CAP,
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        return f"[Image description failed for {name}: {e}]"


async def _transcribe_audio(data: bytes, name: str, llm: AsyncOpenAI) -> str:
    ext  = Path(name).suffix.lower()
    mime = _AUDIO_MIME.get(ext, "audio/mpeg")
    try:
        res = await llm.audio.transcriptions.create(
            model=settings.litellm_whisper_model,
            file=(name, data, mime),
        )
        return res.text
    except Exception as e:
        return f"[Audio transcription failed for {name}: {e}]"


async def _extract_video_frames(data: bytes, name: str, llm: AsyncOpenAI) -> str:
    """Extract key frames from video using OpenCV, then describe each with LLM vision."""
    tmp_path = None
    try:
        import cv2  # opencv-python-headless
        from PIL import Image as PILImage

        suffix = Path(name).suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(data)
            tmp_path = f.name

        cap = cv2.VideoCapture(tmp_path)
        fps          = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration_s   = total_frames / fps if fps > 0 else 0

        # 1 frame every 10 seconds, capped at MAX_FRAMES
        interval = max(1, int(fps * 10))
        frame_indices = list(range(0, total_frames, interval))[:MAX_FRAMES]

        frame_tasks: list[tuple[float, asyncio.Task]] = []
        for idx in frame_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                continue
            # BGR → JPEG bytes
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img   = PILImage.fromarray(frame_rgb)
            buf = io.BytesIO()
            pil_img.save(buf, format="JPEG", quality=65)
            ts  = idx / fps
            task = asyncio.create_task(_describe_image(buf.getvalue(), f"frame_{idx}.jpg", llm))
            frame_tasks.append((ts, task))

        cap.release()

        descriptions: list[str] = [f"Video: {name}  ({duration_s:.0f}s, {len(frame_tasks)} key frames)\n"]
        for ts, task in frame_tasks:
            desc = await task
            descriptions.append(f"  [{ts:.1f}s] {desc}")
        return "\n".join(descriptions)

    except ImportError:
        return f"[Video {name}: install opencv-python-headless to enable frame extraction]"
    except Exception as e:
        return f"[Video {name} frame extraction failed: {e}]"
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ── Main entry point ──────────────────────────────────────────────────────────

async def extract_zip_contents(zip_bytes: bytes) -> str:
    """
    Extract ALL readable content from a ZIP submission.
    Returns a single string with clearly labelled sections per file.
    """
    llm = AsyncOpenAI(base_url=settings.litellm_base_url, api_key=settings.litellm_api_key)

    sync_parts: list[str]                              = []
    async_tasks: list[tuple[str, asyncio.Task[str]]]  = []

    try:
        zf_obj = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        return "[Submission error: could not open ZIP file]"

    with zf_obj as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]

        for name in names[:MAX_FILES]:
            # Skip system / hidden paths
            parts = Path(name).parts
            if any(p in SKIP_DIRS or p.startswith(".") for p in parts):
                continue

            ext = Path(name).suffix.lower()
            if ext in SKIP_ALWAYS:
                continue

            try:
                data = zf.read(name)
            except Exception:
                continue

            label = f"=== {name} ==="

            # ── Documents ────────────────────────────────────────
            if ext == ".pdf":
                text = _extract_pdf(data, name)
                sync_parts.append(f"{label}\n{text}")

            elif ext in (".docx", ".doc"):
                text = _extract_docx(data, name)
                sync_parts.append(f"{label}\n{text}")

            elif ext in (".xlsx", ".xls"):
                text = _extract_xlsx(data, name)
                sync_parts.append(f"{label}\n{text}")

            # ── Media (async) ────────────────────────────────────
            elif ext in IMAGE_EXTENSIONS:
                task = asyncio.create_task(_describe_image(data, name, llm))
                async_tasks.append((label, task))

            elif ext in AUDIO_EXTENSIONS:
                task = asyncio.create_task(_transcribe_audio(data, name, llm))
                async_tasks.append((label, task))

            elif ext in VIDEO_EXTENSIONS:
                task = asyncio.create_task(_extract_video_frames(data, name, llm))
                async_tasks.append((label, task))

            # ── Everything else → try to read as text ────────────
            else:
                try:
                    text = data.decode("utf-8", errors="strict")[:TEXT_CAP]
                    sync_parts.append(f"{label}\n{text}")
                except (UnicodeDecodeError, ValueError):
                    # Binary file we can't read as text — skip silently
                    pass

    # Collect async results
    for label, task in async_tasks:
        try:
            result = await task
            sync_parts.append(f"{label}\n{result}")
        except Exception as e:
            sync_parts.append(f"{label}\n[processing failed: {e}]")

    return "\n\n".join(sync_parts) if sync_parts else "[No readable content found in ZIP]"
