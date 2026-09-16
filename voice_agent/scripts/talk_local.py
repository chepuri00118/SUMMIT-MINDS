#!/usr/bin/env python3
"""Talk to the agent out loud, on your own machine, with no accounts at all.

    python scripts/talk_local.py

Microphone -> faster-whisper -> a local model via Ollama -> Piper -> speakers.
Nothing leaves your laptop and nothing is billed.

What this does NOT show you: real phone latency, phone-quality audio, or
barge-in. Whisper cannot transcribe until you stop talking, so turn-taking here
is slower and more polite than the hosted phone path. Use this to judge what
the agent SAYS. Judge how it FEELS on a real call to your own mobile.

Run scripts/setup_local.sh first.
"""
from __future__ import annotations

import asyncio
import queue
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from app.brain.agent import CallAgent  # noqa: E402
from app.brain.humanizer import humanize  # noqa: E402
from app.config import settings  # noqa: E402
from app.conversation.outcomes import CallOutcome  # noqa: E402
from app.stt.whisper_local import SAMPLE_RATE, LocalTranscriber  # noqa: E402
from app.tts.piper_local import LocalSpeaker  # noqa: E402

LEAD = {
    "first_name": "Mike",
    "last_name": "Dolan",
    "company": "Dolan Steel Fabricators",
    "title": "Owner",
    "phone": "+15550100001",
    "city": "Akron",
    "state": "OH",
}

GREY, CYAN, GREEN, RESET = "\033[90m", "\033[96m", "\033[92m", "\033[0m"


async def main() -> int:
    try:
        import sounddevice as sd
    except OSError as exc:
        print(f"No audio device available: {exc}", file=sys.stderr)
        return 1

    speaker = LocalSpeaker(settings.piper_voice)
    transcriber = LocalTranscriber(settings.whisper_model)
    outcome = CallOutcome(LEAD)
    agent = CallAgent(LEAD, tool_handler=_traced(outcome))
    tmp = Path(tempfile.mkdtemp())

    print(f"{GREY}Loading models... speak when you see 'listening'.")
    print(f"Brain: {agent.brain.name}. Ctrl-C to hang up.{RESET}\n")

    blocks: queue.Queue[np.ndarray] = queue.Queue()

    def on_audio(indata, frames, time_info, status):
        blocks.put(indata[:, 0].copy())

    async def say(text: str) -> None:
        text = humanize(text)
        if not text.strip():
            return
        print(f"{CYAN}agent:{RESET} {text}")
        outcome.add_transcript("agent", text)
        samples, rate = speaker.synthesize(text, tmp / "turn.wav")
        sd.play(samples, rate)
        sd.wait()

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, blocksize=1600,
                        dtype="float32", callback=on_audio):
        try:
            while True:
                # The agent speaks first - this is an outbound cold call.
                spoken = ""
                async for delta in agent.next_turn():
                    spoken += delta
                await say(spoken)

                if outcome.hang_up_requested:
                    print(f"\n{GREY}--- agent hung up ---{RESET}")
                    break

                # Drop anything the mic picked up while we were talking, or the
                # agent transcribes its own voice and answers itself.
                while not blocks.empty():
                    blocks.get_nowait()

                print(f"{GREY}listening...{RESET}", end="\r", flush=True)
                heard = None
                while heard is None:
                    block = await asyncio.get_event_loop().run_in_executor(None, blocks.get)
                    heard = transcriber.feed(block)

                print(f"{GREEN}you:{RESET}   {heard}        ")
                outcome.add_transcript("prospect", heard)
                agent.record_prospect(heard)

        except KeyboardInterrupt:
            print(f"\n{GREY}--- you hung up ---{RESET}")

    await outcome.finalize(agent.turns)
    print(f"\n{GREY}outcome: {outcome.outcome}   grade: {outcome.grade}")
    print(f"learned: {outcome.discovery}")
    print(f"saved to data/calls/{RESET}")
    return 0


def _traced(outcome: CallOutcome):
    def handler(name: str, payload: dict) -> str:
        print(f"{GREY}  [tool] {name} {payload}{RESET}")
        return outcome.handle_tool(name, payload)
    return handler


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
