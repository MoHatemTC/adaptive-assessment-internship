import asyncio
from google import genai
from google.genai import types
from app.config.settings import settings

SYSTEM_PROMPT = """You are Masar, a professional AI interviewer conducting a structured assessment.
Ask one question at a time. Listen carefully. Be concise, professional, and encouraging.
Do not reveal scores or give feedback during the interview."""


class GeminiLiveSession:
    def __init__(self, session_id: str, mode: str = "technical"):
        self.session_id = session_id
        self.mode = mode
        self.transcript: list[str] = []
        # Live WebSocket requires a direct Google API key — not proxyable via LiteLLM.
        # Will raise at connect() time if no real Gemini key is available.
        self._client = genai.Client(api_key=settings.litellm_api_key)
        self._session = None

    async def connect(self):
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_PROMPT,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
                )
            ),
        )
        self._session = await self._client.aio.live.connect(
            model="gemini-2.0-flash-live-001",
            config=config,
        ).__aenter__()

    async def send_audio(self, audio_bytes: bytes) -> None:
        if self._session:
            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[types.Blob(data=audio_bytes, mime_type="audio/pcm")]
                )
            )

    async def receive_audio(self):
        if not self._session:
            return
        async for response in self._session.receive():
            if response.data:
                yield response.data
            if response.text:
                self.transcript.append(response.text)

    def get_transcript(self) -> str:
        return " ".join(self.transcript)

    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
