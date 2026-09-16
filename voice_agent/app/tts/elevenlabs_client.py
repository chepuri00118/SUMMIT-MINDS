"""Streaming text-to-speech from ElevenLabs, straight into Twilio's format.

We request mu-law 8kHz output so no resampling happens anywhere in the path -
every millisecond of conversion is a millisecond of awkward silence on the call.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import AsyncIterator

import httpx
import websockets

from app.config import settings

log = logging.getLogger(__name__)

WS_URL = "wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
HTTP_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

# Voice settings chosen for phone cold calls specifically:
#  - stability low enough that the delivery varies between sentences, because a
#    perfectly even read is the clearest tell that nobody is home.
#  - style modest; high style over-acts and sounds like an audiobook.
VOICE_SETTINGS = {
    "stability": 0.45,
    "similarity_boost": 0.75,
    "style": 0.35,
    "use_speaker_boost": True,
    "speed": 1.05,
}


class ElevenLabsStream:
    """Persistent websocket so each turn skips connection setup."""

    def __init__(self) -> None:
        self._socket: websockets.WebSocketClientProtocol | None = None

    async def connect(self) -> None:
        url = WS_URL.format(voice_id=settings.elevenlabs_voice_id)
        url += f"?model_id={settings.elevenlabs_model}&output_format=ulaw_8000"
        self._socket = await websockets.connect(
            url, additional_headers={"xi-api-key": settings.elevenlabs_api_key}
        )
        await self._socket.send(
            json.dumps(
                {
                    "text": " ",
                    "voice_settings": VOICE_SETTINGS,
                    # Flush early on the first chunks so the first syllable
                    # arrives fast, then buffer more for smoother prosody.
                    "generation_config": {"chunk_length_schedule": [50, 120, 160, 290]},
                }
            )
        )
        log.info("elevenlabs connected")

    async def speak(self, text: str) -> AsyncIterator[bytes]:
        """Send text, yield mu-law audio chunks as they come back."""
        if not self._socket:
            await self.connect()
        assert self._socket is not None

        await self._socket.send(json.dumps({"text": text + " ", "try_trigger_generation": True}))
        await self._socket.send(json.dumps({"text": "", "flush": True}))

        while True:
            message = json.loads(await self._socket.recv())
            if message.get("audio"):
                yield base64.b64decode(message["audio"])
            if message.get("isFinal"):
                break

    async def close(self) -> None:
        if self._socket:
            try:
                await self._socket.send(json.dumps({"text": ""}))
                await self._socket.close()
            except Exception:
                pass


async def synthesize_once(text: str) -> bytes:
    """One-shot synthesis for voicemail drops, where latency does not matter."""
    url = HTTP_URL.format(voice_id=settings.elevenlabs_voice_id)
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            url,
            params={"output_format": "ulaw_8000"},
            headers={"xi-api-key": settings.elevenlabs_api_key},
            json={
                "text": text,
                "model_id": settings.elevenlabs_model,
                "voice_settings": VOICE_SETTINGS,
            },
        )
        response.raise_for_status()
        return response.content
