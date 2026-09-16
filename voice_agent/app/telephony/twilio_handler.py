"""Placing calls and generating the TwiML that wires audio to our websocket."""
from __future__ import annotations

import logging
from typing import Any
from xml.sax.saxutils import escape

from twilio.rest import Client

from app.config import settings

log = logging.getLogger(__name__)


def client() -> Client:
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def connect_stream_twiml(lead_id: str) -> str:
    """Hand the call's audio to our media-stream websocket, both directions."""
    # Without this guard an unset PUBLIC_BASE_URL silently yields
    # url="/media-stream", which Twilio rejects - and the only place you would
    # find out is a live call that connects and then drops in silence.
    if not settings.public_base_url:
        raise RuntimeError(
            "PUBLIC_BASE_URL is not set, so Twilio has no address to stream audio to. "
            "Set it to the public https URL of this server (an ngrok URL in development)."
        )
    base = settings.public_base_url.replace("https://", "wss://").replace("http://", "ws://")
    url = f"{base}/media-stream"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Connect><Stream url="{escape(url)}">'
        f'<Parameter name="lead_id" value="{escape(lead_id)}" />'
        "</Stream></Connect>"
        "</Response>"
    )


def place_call(lead: dict[str, Any], lead_id: str) -> str:
    """Dial a lead. Returns the Twilio call SID."""
    if settings.dry_run:
        log.info("DRY RUN - would dial %s (%s)", lead.get("phone"), lead.get("company"))
        return "DRYRUN"

    call = client().calls.create(
        to=lead["phone"],
        from_=settings.twilio_from_number,
        url=f"{settings.public_base_url}/twiml?lead_id={lead_id}",
        status_callback=f"{settings.public_base_url}/call-status",
        status_callback_event=["initiated", "answered", "completed"],
        # If a human does not pick up within this many seconds, give up rather
        # than landing in a long voicemail greeting.
        timeout=25,
        # Let Twilio detect voicemail so we drop a message instead of talking
        # to an answering machine.
        machine_detection="DetectMessageEnd",
        machine_detection_timeout=15,
        record=True,
    )
    log.info("dialed %s sid=%s", lead["phone"], call.sid)
    return call.sid


def transfer_call(call_sid: str, to_number: str) -> None:
    """Warm transfer to a human on request."""
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Say>Putting you through now, one moment.</Say>"
        f"<Dial>{escape(to_number)}</Dial>"
        "</Response>"
    )
    client().calls(call_sid).update(twiml=twiml)
