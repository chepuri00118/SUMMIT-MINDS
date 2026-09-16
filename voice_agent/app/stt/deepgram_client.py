"""Streaming speech-to-text over Deepgram's live websocket.

Twilio sends 8kHz mu-law audio. We hand it to Deepgram untranscoded, which is
both cheaper and lower latency than converting to PCM first.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable
from urllib.parse import urlencode

import websockets

from app.config import settings

log = logging.getLogger(__name__)

DEEPGRAM_URL = "wss://api.deepgram.com/v1/listen"


class DeepgramStream:
    """Feeds audio in, calls back with partial and final transcripts.

    on_partial fires the moment any speech is detected - that is the barge-in
    trigger. on_final fires once the speaker has paused long enough to count as
    a finished turn.
    """

    def __init__(
        self,
        on_partial: Callable[[str], Awaitable[None] | None],
        on_final: Callable[[str], Awaitable[None] | None],
    ) -> None:
        self.on_partial = on_partial
        self.on_final = on_final
        self._socket: websockets.WebSocketClientProtocol | None = None
        self._reader: asyncio.Task | None = None
        self._utterance: list[str] = []

    def _url(self) -> str:
        params = {
            "model": "nova-3",
            "language": "en-US",
            "encoding": "mulaw",
            "sample_rate": "8000",
            "channels": "1",
            # Interim results are what make barge-in possible.
            "interim_results": "true",
            # Deepgram tells us when a pause means "they're done".
            "endpointing": str(settings.endpointing_ms),
            "utterance_end_ms": "1000",
            "vad_events": "true",
            "smart_format": "true",
            "punctuate": "true",
            # Domain vocabulary - without these, "Tekla" comes back "tackler".
            "keyterm": [
                "Tekla",
                "SDS/2",
                "detailing",
                "fabricator",
                "erector",
                "rebar",
                "AISC",
                "shop drawings",
                "connection design",
                "RNT",
            ],
        }
        return f"{DEEPGRAM_URL}?{urlencode(params, doseq=True)}"

    async def connect(self) -> None:
        self._socket = await websockets.connect(
            self._url(),
            additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
        )
        self._reader = asyncio.create_task(self._read_loop())
        log.info("deepgram connected")

    async def send_audio(self, payload: bytes) -> None:
        if self._socket:
            try:
                await self._socket.send(payload)
            except websockets.ConnectionClosed:
                log.warning("deepgram socket closed while sending")

    async def _emit(self, callback, text: str) -> None:
        result = callback(text)
        if asyncio.iscoroutine(result):
            await result

    async def _read_loop(self) -> None:
        assert self._socket is not None
        try:
            async for raw in self._socket:
                message = json.loads(raw)
                kind = message.get("type")

                if kind == "Results":
                    alternatives = message["channel"]["alternatives"]
                    transcript = alternatives[0]["transcript"].strip() if alternatives else ""
                    if not transcript:
                        continue
                    if message.get("is_final"):
                        self._utterance.append(transcript)
                        if message.get("speech_final"):
                            await self._flush()
                    else:
                        await self._emit(self.on_partial, transcript)

                elif kind == "UtteranceEnd":
                    # Safety net: a final arrived but speech_final never did.
                    await self._flush()

        except websockets.ConnectionClosed:
            log.info("deepgram stream ended")
        except Exception:
            log.exception("deepgram read loop crashed")

    async def _flush(self) -> None:
        if not self._utterance:
            return
        text = " ".join(self._utterance).strip()
        self._utterance.clear()
        if text:
            await self._emit(self.on_final, text)

    async def close(self) -> None:
        if self._socket:
            try:
                await self._socket.send(json.dumps({"type": "CloseStream"}))
                await self._socket.close()
            except Exception:
                pass
        if self._reader:
            self._reader.cancel()
