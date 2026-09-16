"""Orchestrates one live call: audio in, thinking, audio out, interruptions.

The design goal is that the prospect never notices a machine. Three things do
most of that work:

  1. Barge-in. The instant Deepgram hears speech while we are talking, we stop
     playback, clear Twilio's buffer, and throw away the rest of the turn. A bot
     that talks over you is the fastest way to get hung up on.
  2. Backchannel. A short "mm-hmm" goes out the moment they stop talking, while
     the model is still generating. Without it there is a dead second on every
     turn, which reads as a bad connection or a script.
  3. Sentence-level streaming. Audio starts after the model's first sentence,
     not its last.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import time
from typing import Any

from app.brain.agent import CallAgent
from app.brain.humanizer import humanize, split_for_streaming, stall_phrase, thinking_sound
from app.config import settings
from app.conversation.outcomes import CallOutcome
from app.stt.deepgram_client import DeepgramStream
from app.tts.elevenlabs_client import ElevenLabsStream

log = logging.getLogger(__name__)

# Below this many characters an interim transcript is probably a cough, a door,
# or the tail of our own audio bleeding back. Not a real interruption.
BARGE_IN_MIN_CHARS = 3

# Backchannels are only appropriate after the prospect says something
# substantial; firing one after "yeah" sounds deranged.
BACKCHANNEL_MIN_WORDS = 6


class CallSession:
    """One phone call, from answer to hangup."""

    def __init__(self, websocket, lead: dict[str, Any], outcome: CallOutcome) -> None:
        self.ws = websocket
        self.lead = lead
        self.outcome = outcome
        self.stream_sid: str | None = None

        self.agent = CallAgent(lead, tool_handler=self.outcome.handle_tool)
        self.stt = DeepgramStream(on_partial=self._on_partial, on_final=self._on_final)
        self.tts = ElevenLabsStream()

        self.is_speaking = False
        self.turn_task: asyncio.Task | None = None
        self.last_activity = time.monotonic()
        self.started_at = time.monotonic()
        self._pending: asyncio.Queue[str] = asyncio.Queue()
        self._closed = False

    # -- lifecycle --------------------------------------------------------
    async def run(self, start_message: dict[str, Any] | None = None) -> None:
        """Run the call. `start_message` is Twilio's `start` frame if the caller
        already consumed it off the socket while resolving the lead."""
        if start_message is not None:
            self._apply_start(start_message)
        await self.stt.connect()
        await self.tts.connect()
        watchdog = asyncio.create_task(self._watchdog())
        try:
            await asyncio.gather(self._twilio_loop(), self._turn_loop())
        finally:
            watchdog.cancel()
            await self.shutdown()

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.stt.close()
        await self.tts.close()
        await self.outcome.finalize(self.agent.messages)

    def _apply_start(self, message: dict[str, Any]) -> None:
        """Record stream identifiers and queue our opening turn. Idempotent -
        the frame may arrive here or be handed in by the websocket route."""
        if self.stream_sid and self.outcome.call_sid:
            return
        self.stream_sid = message["start"]["streamSid"]
        self.outcome.call_sid = message["start"].get("callSid")
        log.info("call started stream=%s", self.stream_sid)
        # We speak first - it is an outbound call.
        self._pending.put_nowait("")

    # -- inbound audio ----------------------------------------------------
    async def _twilio_loop(self) -> None:
        """Pump Twilio's media websocket into Deepgram."""
        async for raw in self.ws.iter_text():
            message = json.loads(raw)
            event = message.get("event")

            if event == "start":
                self._apply_start(message)

            elif event == "media":
                await self.stt.send_audio(base64.b64decode(message["media"]["payload"]))

            elif event == "stop":
                log.info("call ended by carrier")
                break

    # -- transcript callbacks ---------------------------------------------
    async def _on_partial(self, text: str) -> None:
        """Interim transcript. Only interesting as a barge-in signal."""
        if self.is_speaking and len(text) >= BARGE_IN_MIN_CHARS:
            await self._interrupt()

    async def _on_final(self, text: str) -> None:
        self.last_activity = time.monotonic()
        log.info("prospect: %s", text)
        self.outcome.add_transcript("prospect", text)
        self.agent.record_prospect(text)

        if len(text.split()) >= BACKCHANNEL_MIN_WORDS and random.random() < 0.5:
            # Fire and forget - it must not delay the real answer.
            asyncio.create_task(self._say(thinking_sound(), backchannel=True))

        await self._pending.put(text)

    async def _interrupt(self) -> None:
        """Stop talking immediately and drop what we were going to say."""
        log.info("barge-in")
        self.is_speaking = False
        self.agent.cancel_current_turn()
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
        # Twilio buffers audio ahead of playback; without this the prospect
        # keeps hearing us for a second after we have stopped generating.
        if self.stream_sid:
            await self.ws.send_text(
                json.dumps({"event": "clear", "streamSid": self.stream_sid})
            )

    # -- outbound speech --------------------------------------------------
    async def _turn_loop(self) -> None:
        while not self._closed:
            await self._pending.get()
            if self._closed:
                return
            self.turn_task = asyncio.create_task(self._generate_and_speak())
            try:
                await self.turn_task
            except asyncio.CancelledError:
                continue
            if self.outcome.hang_up_requested:
                await self._hang_up()
                return

    async def _generate_and_speak(self) -> None:
        buffer = ""
        spoke_anything = False
        started = time.monotonic()
        stalled = False

        async for delta in self.agent.next_turn():
            buffer += delta

            # If the model is slow on the first sentence, bridge the gap rather
            # than leaving the line silent.
            if not spoke_anything and not stalled and time.monotonic() - started > 1.2:
                stalled = True
                await self._say(stall_phrase(), backchannel=True)

            for chunk in list(split_for_streaming(buffer)):
                buffer = buffer[len(chunk):].lstrip()
                await self._say(chunk)
                spoke_anything = True

        if buffer.strip():
            await self._say(buffer)

    async def _say(self, text: str, *, backchannel: bool = False) -> None:
        text = text if backchannel else humanize(text)
        if not text.strip():
            return
        if not backchannel:
            log.info("agent: %s", text)
            self.outcome.add_transcript("agent", text)

        self.is_speaking = True
        try:
            async for audio in self.tts.speak(text):
                if not self.is_speaking:
                    return  # interrupted mid-sentence
                await self._send_audio(audio)
        except Exception:
            log.exception("tts failed")
        finally:
            if not backchannel:
                self.is_speaking = False
            self.last_activity = time.monotonic()

    async def _send_audio(self, payload: bytes) -> None:
        if not self.stream_sid:
            return
        await self.ws.send_text(
            json.dumps(
                {
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": base64.b64encode(payload).decode()},
                }
            )
        )

    # -- guards -----------------------------------------------------------
    async def _watchdog(self) -> None:
        """Never let a call hang open burning minutes."""
        while not self._closed:
            await asyncio.sleep(1)
            now = time.monotonic()
            if now - self.started_at > settings.max_call_seconds:
                log.warning("call hit max duration")
                self.outcome.record_outcome("other", "Hit maximum call duration.")
                await self._hang_up()
                return
            if not self.is_speaking and now - self.last_activity > settings.max_silence_seconds:
                log.info("silence timeout")
                await self._say("Seems like I lost you there - I'll try you another time. Take care.")
                self.outcome.record_outcome("other", "Call dropped to silence.")
                await self._hang_up()
                return

    async def _hang_up(self) -> None:
        # Let the last words actually reach the caller before the line drops.
        await asyncio.sleep(0.8)
        if self.stream_sid:
            try:
                await self.ws.send_text(
                    json.dumps(
                        {
                            "event": "mark",
                            "streamSid": self.stream_sid,
                            "mark": {"name": "goodbye"},
                        }
                    )
                )
            except Exception:
                pass
        self._closed = True
        try:
            await self.ws.close()
        except Exception:
            pass
