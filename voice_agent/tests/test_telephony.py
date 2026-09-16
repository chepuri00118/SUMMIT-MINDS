import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.telephony import twilio_handler


def test_twiml_points_at_a_real_websocket_url(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://calls.example.com")
    xml = twilio_handler.connect_stream_twiml("abc123")
    assert 'url="wss://calls.example.com/media-stream"' in xml
    assert 'value="abc123"' in xml


def test_missing_base_url_fails_loudly_instead_of_emitting_a_broken_url(monkeypatch):
    # A silent failure here surfaces only as a live call that connects and
    # then drops in silence, which is the worst place to debug it.
    monkeypatch.setattr(settings, "public_base_url", "")
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        twilio_handler.connect_stream_twiml("abc123")


def test_lead_id_is_escaped_into_the_xml(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://calls.example.com")
    xml = twilio_handler.connect_stream_twiml('a"&<b')
    assert '"&<b' not in xml
    assert "&amp;" in xml
