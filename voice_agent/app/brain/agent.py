"""The conversational brain: streams turns out of Claude and runs call tools."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable

from anthropic import AsyncAnthropic

from app.brain.prompts import build_system_prompt
from app.config import settings

log = logging.getLogger(__name__)

# Tools the agent uses to record outcomes. They never produce speech - the
# prospect hears nothing when one fires.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "log_discovery",
        "description": (
            "Record a fact learned about the prospect. Call this as soon as you learn "
            "something, not at the end of the call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "enum": [
                        "detailing_in_house",
                        "current_vendor",
                        "software_used",
                        "backlog_status",
                        "pain_point",
                        "project_type",
                        "decision_maker",
                        "other",
                    ],
                },
                "value": {"type": "string", "description": "What they said, briefly."},
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "set_qualification",
        "description": "Grade the lead. Call once you can tell how interested they are.",
        "input_schema": {
            "type": "object",
            "properties": {
                "grade": {"type": "string", "enum": ["hot", "warm", "cold", "disqualified"]},
                "reason": {"type": "string"},
            },
            "required": ["grade", "reason"],
        },
    },
    {
        "name": "book_meeting",
        "description": "The prospect agreed to a call with an estimator.",
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "Their words, e.g. 'Thursday morning'."},
                "email": {"type": "string"},
                "phone": {"type": "string"},
            },
            "required": ["when"],
        },
    },
    {
        "name": "capture_email",
        "description": "They agreed to receive the capability deck. Record the address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "send": {
                    "type": "string",
                    "enum": ["capability_deck", "sample_drawings", "both"],
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "schedule_callback",
        "description": "They asked to be called at another time.",
        "input_schema": {
            "type": "object",
            "properties": {"when": {"type": "string"}},
            "required": ["when"],
        },
    },
    {
        "name": "mark_do_not_call",
        "description": (
            "They asked not to be called again, or were hostile. This is permanent and "
            "suppresses the number forever. Always call this when asked."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "transfer_to_human",
        "description": "They want a person. Warm-transfer the call.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "end_call",
        "description": (
            "Hang up. Only after you have said a closing line - the audio finishes "
            "playing before the line drops."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": [
                        "meeting_booked",
                        "email_captured",
                        "callback_scheduled",
                        "not_interested",
                        "do_not_call",
                        "wrong_person",
                        "transferred",
                        "voicemail",
                        "other",
                    ],
                },
                "summary": {"type": "string", "description": "Two sentences for the CRM."},
            },
            "required": ["outcome", "summary"],
        },
    },
]


class CallAgent:
    """Holds the transcript for one call and streams the next thing to say."""

    def __init__(
        self,
        lead: dict[str, Any],
        tool_handler: Callable[[str, dict[str, Any]], Any],
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.lead = lead
        self.system = build_system_prompt(lead)
        self.messages: list[dict[str, Any]] = []
        self.tool_handler = tool_handler
        self.client = client or AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.should_hang_up = False
        self._generation = 0

    # -- transcript -------------------------------------------------------
    def record_prospect(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def cancel_current_turn(self) -> None:
        """Called on barge-in. Invalidates any in-flight generation."""
        self._generation += 1

    # -- generation -------------------------------------------------------
    async def next_turn(self) -> AsyncIterator[str]:
        """Stream the agent's reply as text deltas, running tools as they come.

        Yields raw text from the model. The caller is responsible for chunking
        it into speech - see humanizer.split_for_streaming.
        """
        generation = self._generation
        if not self.messages:
            # Kick the model off: it needs a user turn to respond to.
            self.messages.append({"role": "user", "content": "[call connected]"})

        while True:
            assistant_blocks: list[dict[str, Any]] = []
            tool_uses: list[dict[str, Any]] = []
            text_buffer = ""

            async with self.client.messages.stream(
                model=settings.model,
                max_tokens=300,  # a phone turn is short by construction
                # Thinking stays on but at the lowest effort. Turning it off
                # entirely is the obvious latency move and it is a trap here:
                # with thinking disabled the model sometimes writes a tool call
                # into its visible text instead of a tool_use block, which on
                # this app means the agent literally says "log_discovery" out
                # loud to a prospect. Low effort gets the latency without that.
                thinking={"type": "adaptive"},
                output_config={"effort": "low"},
                system=self.system,
                tools=TOOLS,
                messages=self.messages,
            ) as stream:
                async for event in stream:
                    if generation != self._generation:
                        # Barge-in: the prospect started talking. Stop mid-word.
                        log.info("turn cancelled by barge-in")
                        return
                    if event.type == "text":
                        # Thinking blocks also stream; only spoken text counts.
                        text_buffer += event.text
                        yield event.text

                final = await stream.get_final_message()

            for block in final.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

            self.messages.append({"role": "assistant", "content": assistant_blocks})

            if not tool_uses:
                return

            results = []
            for use in tool_uses:
                try:
                    outcome = self.tool_handler(use["name"], use["input"])
                    if asyncio.iscoroutine(outcome):
                        outcome = await outcome
                except Exception as exc:  # a tool failure must not kill the call
                    log.exception("tool %s failed", use["name"])
                    outcome = f"error: {exc}"
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use["id"],
                        "content": str(outcome or "ok"),
                    }
                )

            self.messages.append({"role": "user", "content": results})

            if self.should_hang_up:
                return
