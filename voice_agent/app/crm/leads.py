"""Lead list loading, filtering and call-window checks."""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.compliance.dnc import is_suppressed
from app.config import settings

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = {"phone"}


def load_leads(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Lead file {path} is missing column(s): {', '.join(missing)}")
        leads = [{k: (v or "").strip() for k, v in row.items()} for row in reader]
    log.info("loaded %d leads from %s", len(leads), path)
    return leads


def within_calling_window(lead: dict[str, Any], now: datetime | None = None) -> bool:
    """Respect the prospect's local business hours, not ours.

    Calling a fabricator in Seattle at 6am because the dialer runs on IST time
    is the fastest route to a complaint.
    """
    tz_name = lead.get("timezone") or "America/New_York"
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        log.warning("unknown timezone %r for %s, defaulting to Eastern", tz_name, lead.get("phone"))
        zone = ZoneInfo("America/New_York")

    local = (now or datetime.now(tz=zone)).astimezone(zone)
    if local.weekday() >= 5:      # no weekend cold calls
        return False
    start, end = settings.calling_window_local
    return start <= local.hour < end


def is_callable(lead: dict[str, Any]) -> tuple[bool, str]:
    """Every reason a lead should be skipped, in one place."""
    phone = lead.get("phone", "")
    if not phone:
        return False, "no phone number"
    if is_suppressed(phone):
        return False, "on do-not-call list"
    if lead.get("status", "").lower() in {"do_not_call", "converted", "disqualified"}:
        return False, f"status is {lead['status']}"
    if not within_calling_window(lead):
        return False, "outside local calling hours"
    return True, ""
