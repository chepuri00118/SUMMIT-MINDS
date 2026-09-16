import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.prompts import build_system_prompt, voicemail_text

LEAD = {"first_name": "Mike", "company": "Dolan Steel", "title": "Owner"}


def test_prompt_carries_the_lead_and_company_facts():
    prompt = build_system_prompt(LEAD)
    assert "Mike" in prompt and "Dolan Steel" in prompt
    assert "RNT Steel Detailing" in prompt
    assert "Tekla" in prompt


def test_prompt_forbids_inventing_facts_and_claiming_humanity():
    prompt = build_system_prompt(LEAD)
    assert "Never invent a price" in prompt
    assert "Never claim to be human" in prompt


def test_voicemail_is_personalised_and_short():
    text = voicemail_text(LEAD)
    assert "Mike" in text
    assert len(text.split()) < 80


def test_proactive_disclosure_reaches_the_prompt_when_enabled(monkeypatch):
    # This is a legal requirement in a growing number of US states, and it
    # lived in company.yaml without being wired in - regression guard.
    prompt = build_system_prompt(LEAD)
    assert "DISCLOSE UP FRONT" in prompt
    assert "I'm an AI assistant calling on behalf of" in prompt


def test_disclosure_is_positioned_before_the_pitch():
    prompt = build_system_prompt(LEAD)
    assert "before you pitch anything" in prompt
    assert "do not wait to be asked" in prompt


def test_honest_answer_is_present_either_way():
    assert "Yes, I'm an AI assistant" in build_system_prompt(LEAD)
