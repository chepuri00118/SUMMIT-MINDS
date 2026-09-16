"""Local text-to-speech with Piper. No account, no network.

Piper is a small neural TTS that runs comfortably on a laptop CPU. Quality is
below ElevenLabs - it is noticeably synthetic on close listening - but it is
fast enough to hold a conversation, which is what matters for rehearsal.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class LocalSpeaker:
    """Synthesises speech to a numpy array, ready to play or write to disk."""

    def __init__(self, voice_path: str) -> None:
        self.voice = Path(voice_path)
        if not self.voice.exists():
            raise FileNotFoundError(
                f"Piper voice not found at {self.voice}. Run scripts/setup_local.sh, "
                "or set PIPER_VOICE to a downloaded .onnx voice file."
            )
        self.binary = shutil.which("piper")
        if not self.binary:
            raise RuntimeError(
                "The 'piper' binary is not on PATH. Run scripts/setup_local.sh."
            )

    def synthesize(self, text: str, out_path: Path) -> tuple[np.ndarray, int]:
        """Speak `text` into a wav file and return (samples, sample_rate)."""
        subprocess.run(
            [self.binary, "--model", str(self.voice), "--output_file", str(out_path)],
            input=text.encode("utf-8"),
            check=True,
            capture_output=True,
        )
        with wave.open(str(out_path), "rb") as handle:
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, rate
