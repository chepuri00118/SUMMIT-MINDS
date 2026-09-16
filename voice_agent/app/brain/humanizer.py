"""Makes generated text sound like a person when it hits the speech engine.

Two jobs:
  1. Scrub anything unspeakable that leaked out of the LLM (markdown, lists).
  2. Add the small timing and hesitation cues that separate "read aloud" from
     "talking".
"""
from __future__ import annotations

import random
import re

# Said while the model is still thinking, so the line is never dead. These are
# spoken the instant the prospect stops talking, before the LLM has answered.
THINKING_SOUNDS = [
    "mm-hmm",
    "yeah",
    "right",
    "okay",
    "got it",
    "sure",
    "uh huh",
]

# Used when the model takes unusually long - a longer bridge buys more time.
STALL_PHRASES = [
    "yeah, let me think about that for a sec",
    "hmm, good question",
    "so - one sec",
]

_MARKDOWN = re.compile(r"[*_`#>]|^\s*[-•]\s+", re.MULTILINE)
_BRACKETS = re.compile(r"\((.*?)\)")
_STAGE_DIRECTION = re.compile(r"\[[^\]]*\]")
_MULTISPACE = re.compile(r"\s{2,}")

_SPOKEN_SYMBOLS = {
    "&": " and ",
    "%": " percent ",
    "/": " or ",
    "+": " plus ",
    "@": " at ",
}

_ABBREVIATIONS = {
    r"\bRFI\b": "R F I",
    r"\bRFIs\b": "R F Is",
    r"\bEOR\b": "E O R",
    r"\bAISC\b": "A I S C",
    r"\bCISC\b": "C I S C",
    r"\bBIM\b": "B I M",
    r"\bPE\b": "P E",
    r"\bSDS/2\b": "S D S two",
    r"\bCAD\b": "cad",
    r"\bGC\b": "G C",
    r"\bGCs\b": "G Cs",
}


def clean_for_speech(text: str) -> str:
    """Strip anything that would be read out as punctuation noise."""
    text = _STAGE_DIRECTION.sub("", text)
    text = _MARKDOWN.sub("", text)
    # Parentheticals are fine as speech, just drop the brackets themselves.
    text = _BRACKETS.sub(r"\1", text)
    # Abbreviations first: some of them contain symbols ("SDS/2") that the
    # symbol pass below would otherwise mangle into "SDS or 2".
    for pattern, spoken in _ABBREVIATIONS.items():
        text = re.sub(pattern, spoken, text)
    for symbol, spoken in _SPOKEN_SYMBOLS.items():
        text = text.replace(symbol, spoken)
    # Newlines inside a spoken turn become pauses, not line breaks.
    text = text.replace("\n", " ... ")
    text = _MULTISPACE.sub(" ", text)
    return text.strip()


def add_breath(text: str, *, rate: float = 0.35) -> str:
    """Insert a short pause after the first clause some of the time.

    Real speech does not run at a constant tempo. A pause after an opening
    clause is the single most recognisable "human" cue, so we add one on
    roughly a third of turns rather than every turn (which sounds like a tic).
    """
    if random.random() > rate:
        return text
    match = re.search(r"^([^,.?!]{8,45})([,.])\s", text)
    if not match:
        return text
    head, punct = match.group(1), match.group(2)
    return text.replace(f"{head}{punct} ", f"{head}{punct}.. ", 1)


def thinking_sound() -> str:
    return random.choice(THINKING_SOUNDS)


def stall_phrase() -> str:
    return random.choice(STALL_PHRASES)


def humanize(text: str) -> str:
    """Full pipeline applied to every LLM turn before synthesis."""
    return add_breath(clean_for_speech(text))


def split_for_streaming(text: str, min_chars: int = 40):
    """Yield speakable chunks so audio starts before the LLM finishes.

    We flush on sentence boundaries, but only once a chunk is long enough that
    the speech engine has enough context to get the prosody right. Flushing on
    every comma makes the voice sound chopped.
    """
    buffer = ""
    for piece in re.split(r"(?<=[.?!,;:])\s+", text):
        buffer = f"{buffer} {piece}".strip()
        if len(buffer) >= min_chars and buffer[-1] in ".?!":
            yield buffer
            buffer = ""
    if buffer:
        yield buffer
