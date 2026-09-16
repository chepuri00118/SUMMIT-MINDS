#!/usr/bin/env python3
"""Run a calling campaign from a lead CSV.

    python scripts/dial_campaign.py --leads data/leads.sample.csv --limit 25

The server (app.main) must already be running and reachable at PUBLIC_BASE_URL,
because Twilio calls back into it to set up each call's audio.

Start with --dry-run. It walks the whole list, applies every filter, and prints
exactly who would be dialed without placing a single call.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.crm.leads import is_callable, load_leads  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("campaign")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dial a lead list.")
    parser.add_argument("--leads", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=25, help="Max calls this run.")
    parser.add_argument(
        "--delay",
        type=float,
        default=20.0,
        help="Seconds between dials. Keep this above ~15s; bursts get numbers flagged as spam.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Filter and print, never dial.")
    args = parser.parse_args()

    if args.dry_run:
        settings.dry_run = True
    else:
        settings.require_live_credentials()

    from app.telephony.twilio_handler import place_call

    leads = load_leads(args.leads)
    dialed = skipped = 0

    for lead in leads:
        if dialed >= args.limit:
            log.info("hit limit of %d calls", args.limit)
            break

        ok, reason = is_callable(lead)
        if not ok:
            log.info("SKIP %-28s %s", lead.get("company", "?"), reason)
            skipped += 1
            continue

        # Register the lead with the running server so the media-stream handler
        # knows who it is talking to when Twilio connects.
        lead_id = _register(lead)
        if lead_id is None:
            skipped += 1
            continue

        if settings.dry_run:
            log.info("WOULD DIAL %-28s %s", lead.get("company", "?"), lead.get("phone"))
        else:
            try:
                sid = place_call(lead, lead_id)
                log.info("DIALED %-28s %s sid=%s", lead.get("company", "?"), lead["phone"], sid)
            except Exception:
                log.exception("failed to dial %s", lead.get("phone"))
                skipped += 1
                continue

        dialed += 1
        if dialed < args.limit:
            time.sleep(args.delay)

    log.info("done - %d dialed, %d skipped", dialed, skipped)
    return 0


def _register(lead: dict) -> str | None:
    """Hand the lead to the server and get back the id Twilio will echo."""
    if settings.dry_run and not settings.public_base_url:
        return "dryrun"
    try:
        response = httpx.post(
            f"{settings.public_base_url}/leads", json=lead, timeout=10
        )
        response.raise_for_status()
        return response.json()["lead_id"]
    except Exception:
        log.exception("could not register lead with server at %s", settings.public_base_url)
        return None


if __name__ == "__main__":
    raise SystemExit(main())
