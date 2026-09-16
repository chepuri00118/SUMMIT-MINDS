#!/usr/bin/env python3
"""Rehearse a call in your terminal - no phone, no Twilio, no call minutes.

    python scripts/simulate_call.py
    python scripts/simulate_call.py --persona busy

    python scripts/simulate_call.py --script interested
    python scripts/simulate_call.py --script hostile

With --script it replays a canned prospect and needs no typing, so you can read
a whole call end to end. Without it, you type the prospect's side yourself.

This runs the real prompt, the real tools and the real humanizer, so what you
read here is close to what a prospect would hear. Use it to tune
config/script.yaml before you dial anybody.

Only ANTHROPIC_API_KEY is needed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.agent import CallAgent  # noqa: E402
from app.brain.humanizer import humanize  # noqa: E402
from app.conversation.outcomes import CallOutcome  # noqa: E402

PERSONAS = {
    "default": {
        "first_name": "Mike",
        "last_name": "Dolan",
        "company": "Dolan Steel Fabricators",
        "title": "Owner",
        "phone": "+15550100001",
        "city": "Akron",
        "state": "OH",
        "notes": "Found on a fabricators' directory.",
    },
    "busy": {
        "first_name": "Dana",
        "last_name": "Ruiz",
        "company": "Crestline Erectors",
        "title": "Chief Estimator",
        "phone": "+15550100002",
        "city": "Denver",
        "state": "CO",
        "notes": "Bidding two mid-rise jobs, very short on time.",
    },
}

# Canned prospect sides, so a full call can be read without typing. These are
# the four conversations that actually happen on a steel detailing cold call.
SCRIPTED_PROSPECTS = {
    "interested": [
        "Yeah, I've got a minute. What's this about?",
        "We detail in house, two guys on Tekla. Why?",
        "Honestly they're buried. We just won a warehouse job and I don't know how we're drawing it.",
        "What does something like that run?",
        "Alright, yeah. Thursday could work.",
        "mike at dolansteel dot com.",
    ],
    "brushoff": [
        "We already have a detailer.",
        "No, they're fine. We've used them for years.",
        "Just send me an email I guess.",
        "mike at dolansteel dot com. Alright, bye.",
    ],
    "hostile": [
        "How did you get this number?",
        "Is this a robot? You sound like a recording.",
        "Take me off your list and don't call here again.",
    ],
    "skeptical": [
        "Where are you guys located?",
        "Yeah, that's my concern. We've been burned by offshore detailing before, drawings came back garbage.",
        "How do I know yours are any different?",
        "What's the smallest thing you'd take on?",
        "Okay. Send me something and I'll look at it.",
    ],
}

GREEN = "\033[92m"
CYAN = "\033[96m"
GREY = "\033[90m"
RESET = "\033[0m"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=sorted(PERSONAS), default="default")
    parser.add_argument(
        "--script",
        choices=sorted(SCRIPTED_PROSPECTS),
        help="Replay a canned prospect instead of typing. Non-interactive - "
             "prints the whole call, so you can read a full transcript in one go.",
    )
    args = parser.parse_args()

    replies = list(SCRIPTED_PROSPECTS[args.script]) if args.script else None

    lead = PERSONAS[args.persona]
    outcome = CallOutcome(lead)
    agent = CallAgent(lead, tool_handler=_traced(outcome))

    print(f"{GREY}--- simulated call to {lead['first_name']} at {lead['company']} ---")
    print(f"Type what the prospect says. Ctrl-C or an empty line ends the call.{RESET}\n")

    while True:
        print(f"{CYAN}agent:{RESET} ", end="", flush=True)
        spoken = ""
        async for delta in agent.next_turn():
            spoken += delta
        print(humanize(spoken) or f"{GREY}(silence){RESET}")

        if outcome.hang_up_requested:
            print(f"\n{GREY}--- agent hung up ---{RESET}")
            break

        if replies is not None:
            if not replies:
                break
            reply = replies.pop(0)
            print(f"{GREEN}you:{RESET} {reply}")
        else:
            try:
                reply = input(f"{GREEN}you:{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                break
        if not reply:
            break
        agent.record_prospect(reply)

    print(f"\n{GREY}outcome:  {outcome.outcome}")
    print(f"grade:    {outcome.grade}")
    print(f"learned:  {json.dumps(outcome.discovery)}")
    print(f"next:     {json.dumps(outcome.next_action)}{RESET}")
    return 0


def _traced(outcome: CallOutcome):
    def handler(name: str, payload: dict) -> str:
        print(f"\n{GREY}  [tool] {name} {json.dumps(payload)}{RESET}")
        return outcome.handle_tool(name, payload)

    return handler


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
