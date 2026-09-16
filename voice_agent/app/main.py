"""FastAPI app: Twilio webhooks plus the bidirectional media-stream websocket."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Form, Request, WebSocket
from fastapi.responses import JSONResponse, Response

from app.brain.prompts import voicemail_text
from app.conversation.outcomes import CallOutcome
from app.conversation.session import CallSession
from app.telephony.twilio_handler import connect_stream_twiml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="RNT Steel Detailing - outbound call agent")

# Leads for in-flight calls, keyed by the id we pass through Twilio. Twilio's
# webhook cannot carry the whole record, only a short parameter.
ACTIVE_LEADS: dict[str, dict[str, Any]] = {}


def register_lead(lead: dict[str, Any]) -> str:
    lead_id = uuid.uuid4().hex[:12]
    ACTIVE_LEADS[lead_id] = lead
    return lead_id


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/leads")
async def register(lead: dict[str, Any]) -> JSONResponse:
    """Called by the dialer just before it places a call."""
    lead_id = register_lead(lead)
    return JSONResponse({"lead_id": lead_id})


@app.post("/twiml")
async def twiml(request: Request) -> Response:
    """Twilio fetches this when the call connects."""
    lead_id = request.query_params.get("lead_id", "")
    return Response(content=connect_stream_twiml(lead_id), media_type="application/xml")


@app.post("/call-status")
async def call_status(
    CallSid: str = Form(""),
    CallStatus: str = Form(""),
    AnsweredBy: str = Form(""),
) -> JSONResponse:
    """Status callbacks, including Twilio's answering-machine verdict."""
    log.info("status sid=%s status=%s answered_by=%s", CallSid, CallStatus, AnsweredBy)
    return JSONResponse({"ok": True})


@app.post("/voicemail")
async def voicemail(request: Request) -> Response:
    """TwiML for the voicemail drop when a machine picks up."""
    lead_id = request.query_params.get("lead_id", "")
    lead = ACTIVE_LEADS.get(lead_id, {})
    message = voicemail_text(lead)
    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Say>{message}</Say><Hangup/></Response>"
        ),
        media_type="application/xml",
    )


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    """The live call. Audio in and out over one socket."""
    await websocket.accept()
    session: CallSession | None = None

    try:
        # Twilio sends a `connected` frame, then `start`. Only `start` carries
        # the custom lead_id parameter we need to build the session.
        while True:
            message = await websocket.receive_json()
            if message.get("event") == "start":
                params = message["start"].get("customParameters", {}) or {}
                lead = ACTIVE_LEADS.get(params.get("lead_id", ""), {})
                if not lead:
                    log.warning("no lead registered for id %r", params.get("lead_id"))
                session = CallSession(websocket, lead, CallOutcome(lead))
                break

        await session.run(start_message=message)

    except Exception:
        log.exception("media stream failed")
    finally:
        if session:
            await session.shutdown()
