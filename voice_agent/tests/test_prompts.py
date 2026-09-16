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
