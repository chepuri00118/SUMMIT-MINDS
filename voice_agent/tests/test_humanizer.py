import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.humanizer import clean_for_speech, split_for_streaming


def test_markdown_never_reaches_the_speech_engine():
    spoken = clean_for_speech("**Hi** - we do:\n- detailing\n- BIM (fast)")
    assert "*" not in spoken and "-" not in spoken.split()[0]
    assert "B I M" in spoken


def test_industry_abbreviations_are_spelled_out():
    assert "R F I" in clean_for_speech("Send us the RFI")
    assert "S D S two" in clean_for_speech("We use SDS/2 daily")
    assert "A I S C" in clean_for_speech("Built to AISC standards")


def test_symbols_become_words():
    assert "and" in clean_for_speech("stairs & rails")
    assert "percent" in clean_for_speech("30% faster")


def test_streaming_chunks_break_on_sentences_not_mid_word():
    text = "Yeah, totally. We handle overflow detailing for fabricators. Does that fit?"
    chunks = list(split_for_streaming(text))
    assert len(chunks) >= 2
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_short_text_is_not_split():
    assert list(split_for_streaming("Sure.")) == ["Sure."]
