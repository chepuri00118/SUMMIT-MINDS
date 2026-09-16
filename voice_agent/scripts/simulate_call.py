#!/usr/bin/env python3
"""Rehearse a call in your terminal - no phone, no Twilio, no call minutes.

    python scripts/simulate_call.py
    python scripts/simulate_call.py --persona busy

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

GREEN = "\033[92m"
CYAN = "\033[96m"
GREY = "\033[90m"
RESET = "\033[0m"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=sorted(PERSONAS), default="default")
    args = parser.parse_args()

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
