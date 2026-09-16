"""Do-not-call suppression.

This list is the hard stop. Nothing dials a number that appears here - not a
campaign, not a retry, not a manual run. Entries are never removed by code.
"""
from __future__ import annotations

import csv
import logging
import re
import threading
from datetime import datetime, timezone

from app.config import DATA_DIR

log = logging.getLogger(__name__)

DNC_PATH = DATA_DIR / "do_not_call.csv"
_LOCK = threading.Lock()


def normalize(phone: str) -> str:
    """Reduce a number to digits so formatting differences cannot slip past."""
    digits = re.sub(r"\D", "", phone or "")
    # Strip a leading US country code so +1-555 and 555 compare equal.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _ensure_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DNC_PATH.exists():
        with DNC_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(["phone", "added_at", "reason"])


def add_to_dnc(phone: str, reason: str) -> None:
    normalized = normalize(phone)
    if not normalized:
        log.warning("refusing to add empty number to DNC")
        return
    with _LOCK:
        _ensure_file()
        if normalized in _load():
            return
        with DNC_PATH.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                [normalized, datetime.now(timezone.utc).isoformat(), reason]
            )
    log.info("added to DNC: %s", normalized)


def _load() -> set[str]:
    if not DNC_PATH.exists():
        return set()
    with DNC_PATH.open(encoding="utf-8") as handle:
        return {normalize(row["phone"]) for row in csv.DictReader(handle) if row.get("phone")}


def is_suppressed(phone: str) -> bool:
    return normalize(phone) in _load()
