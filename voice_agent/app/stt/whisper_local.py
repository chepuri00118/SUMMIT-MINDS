"""Local speech-to-text with faster-whisper. No account, no network.

Whisper is not a streaming model - it transcribes a finished chunk of audio.
So instead of Deepgram's live endpointing we do voice-activity detection here:
collect audio while the person is speaking, and once they have been quiet long
enough to count as a finished turn, transcribe the whole utterance at once.

That costs latency compared with the hosted path (Whisper cannot start until
you stop talking), which is the main thing you give up by running locally.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


class LocalTranscriber:
    """Buffers microphone audio and transcribes each completed utterance."""

    def __init__(self, model_name: str = "base.en", silence_seconds: float = 0.7) -> None:
        from faster_whisper import WhisperModel

        # int8 on CPU is the difference between usable and unusable on a laptop.
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8")
        self.silence_seconds = silence_seconds
        self._buffer: list[np.ndarray] = []
        self._silent_samples = 0
        self._has_speech = False

    @staticmethod
    def _is_speech(block: np.ndarray, threshold: float = 0.015) -> bool:
        """Crude RMS gate. Good enough indoors; a noisy room needs webrtcvad."""
        return float(np.sqrt(np.mean(np.square(block)))) > threshold

    def feed(self, block: np.ndarray) -> str | None:
        """Add a block of mono float32 audio. Returns text once a turn ends."""
        if self._is_speech(block):
            self._has_speech = True
            self._silent_samples = 0
            self._buffer.append(block)
            return None

        if not self._has_speech:
            return None  # still waiting for them to start

        self._buffer.append(block)
        self._silent_samples += len(block)
        if self._silent_samples < self.silence_seconds * SAMPLE_RATE:
            return None

        return self.flush()

    def flush(self) -> str | None:
        """Transcribe whatever has been collected and reset."""
        if not self._buffer:
            return None
        audio = np.concatenate(self._buffer)
        self._buffer.clear()
        self._silent_samples = 0
        self._has_speech = False

        if len(audio) < SAMPLE_RATE * 0.3:
            return None  # too short to be a real turn - a cough or a door

        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,             # greedy; beam search is too slow to talk to
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return text or None
