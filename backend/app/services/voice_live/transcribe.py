"""Audio transcription via LiteLLM (OpenAI-compatible API)."""

from __future__ import annotations

from io import BytesIO

from openai import APIConnectionError, APIStatusError, OpenAI, OpenAIError

from app.config.settings import settings
from app.services.litellm_http import sync_http

_client: OpenAI | None = None


def _stt_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=settings.litellm_base_url,
            api_key=settings.litellm_api_key,
            timeout=settings.litellm_timeout_seconds,
            max_retries=0,
            http_client=sync_http(),
        )
    return _client


def reset_client() -> None:
    global _client
    _client = None


def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "answer.wav",
    *,
    language: str | None = "en",
) -> str:
    """Return transcript text from recorded audio bytes.

    Uses `settings.litellm_transcribe_model` through the same LiteLLM gateway.
    """
    if not settings.litellm_api_key:
        raise RuntimeError("LITELLM_API_KEY is not set")
    if not audio_bytes:
        raise ValueError("no audio bytes provided")

    stream = BytesIO(audio_bytes)
    stream.name = filename
    try:
        kwargs = {
            "model": settings.litellm_transcribe_model,
            "file": stream,
            # The assessment is English-only. Supplying the ISO-639-1 hint prevents
            # technical English from being decoded as romanized speech in another
            # language, which would otherwise trigger the deterministic score clamp.
            "prompt": (
                "English technical interview about Python and software engineering. "
                "Preserve programming terms, identifiers, and code syntax."
            ),
        }
        if language:
            kwargs["language"] = language
        response = _stt_client().audio.transcriptions.create(**kwargs)
    except APIConnectionError as exc:
        raise RuntimeError(
            "LiteLLM transcription connection failed. "
            f"base_url={settings.litellm_base_url!r} "
            f"model={settings.litellm_transcribe_model!r} "
            f"ssl_verify={settings.litellm_ssl_verify} "
            "(set LITELLM_SSL_VERIFY=false if the cert SAN does not match the host)"
        ) from exc
    except APIStatusError as exc:
        raise RuntimeError(
            "LiteLLM transcription request rejected. "
            f"http={exc.status_code} model={settings.litellm_transcribe_model!r}"
        ) from exc
    except OpenAIError as exc:
        raise RuntimeError(
            "LiteLLM transcription client error. "
            f"type={type(exc).__name__} model={settings.litellm_transcribe_model!r}"
        ) from exc

    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise RuntimeError("transcription returned empty text")
    return text
