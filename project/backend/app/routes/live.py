import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.gemini_live import GeminiLiveSession

router = APIRouter()


@router.websocket("/ws/{session_id}")
async def live_websocket(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = GeminiLiveSession(session_id=session_id)

    try:
        await session.connect()

        async def receive_from_client():
            while True:
                data = await websocket.receive_bytes()
                await session.send_audio(data)

        async def send_to_client():
            async for chunk in session.receive_audio():
                await websocket.send_bytes(chunk)

        await asyncio.gather(receive_from_client(), send_to_client())

    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        transcript = session.get_transcript()
        if transcript:
            await websocket.send_text(json.dumps({"type": "transcript", "text": transcript}))
