"""Turns tool calls made during a call into CRM records."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.compliance.dnc import add_to_dnc
from app.crm.call_log import write_call_record

log = logging.getLogger(__name__)


class CallOutcome:
    """Accumulates everything learned on one call, then persists it."""

    def __init__(self, lead: dict[str, Any]) -> None:
        self.lead = lead
        self.call_sid: str | None = None
        self.started_at = datetime.now(timezone.utc)
        self.discovery: dict[str, str] = {}
        self.grade: str | None = None
        self.grade_reason: str | None = None
        self.outcome: str | None = None
        self.summary: str | None = None
        self.next_action: dict[str, Any] = {}
        self.transcript: list[dict[str, str]] = []
        self.hang_up_requested = False
        self.transfer_requested = False

    def add_transcript(self, speaker: str, text: str) -> None:
        self.transcript.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "speaker": speaker,
                "text": text,
            }
        )

    def record_outcome(self, outcome: str, summary: str) -> None:
        self.outcome = outcome
        self.summary = summary

    # -- tool dispatch ----------------------------------------------------
    def handle_tool(self, name: str, payload: dict[str, Any]) -> str:
        log.info("tool %s %s", name, json.dumps(payload))

        if name == "log_discovery":
            self.discovery[payload["field"]] = payload["value"]
            return "logged"

        if name == "set_qualification":
            self.grade = payload["grade"]
            self.grade_reason = payload["reason"]
            return "graded"

        if name == "book_meeting":
            self.next_action = {"type": "meeting", **payload}
            self.grade = self.grade or "hot"
            return "meeting recorded, an estimator will confirm by email"

        if name == "capture_email":
            self.next_action = {"type": "email", **payload}
            self.grade = self.grade or "warm"
            return "email recorded"

        if name == "schedule_callback":
            self.next_action = {"type": "callback", **payload}
            return "callback recorded"

        if name == "mark_do_not_call":
            add_to_dnc(self.lead.get("phone", ""), payload.get("reason", ""))
            self.grade = "disqualified"
            self.record_outcome("do_not_call", payload.get("reason", "Requested removal."))
            return "number suppressed permanently"

        if name == "transfer_to_human":
            self.transfer_requested = True
            self.record_outcome("transferred", payload.get("reason", ""))
            return "transferring"

        if name == "end_call":
            self.record_outcome(payload["outcome"], payload["summary"])
            self.hang_up_requested = True
            return "ending"

        return f"unknown tool {name}"

    # -- persistence ------------------------------------------------------
    async def finalize(self, messages: list[dict[str, Any]]) -> None:
        record = {
            "call_sid": self.call_sid,
            "lead": self.lead,
            "started_at": self.started_at.isoformat(),
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "outcome": self.outcome or "incomplete",
            "grade": self.grade,
            "grade_reason": self.grade_reason,
            "summary": self.summary,
            "discovery": self.discovery,
            "next_action": self.next_action,
            "transcript": self.transcript,
        }
        write_call_record(record)
        log.info("call finalized outcome=%s grade=%s", record["outcome"], record["grade"])
