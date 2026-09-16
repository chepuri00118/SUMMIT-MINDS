"""Call records on disk: one JSON file per call plus a flat CSV summary."""
from __future__ import annotations

import csv
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DATA_DIR

log = logging.getLogger(__name__)

CALLS_DIR = DATA_DIR / "calls"
SUMMARY_PATH = DATA_DIR / "call_summary.csv"
_LOCK = threading.Lock()

SUMMARY_COLUMNS = [
    "call_sid",
    "started_at",
    "company",
    "contact",
    "phone",
    "outcome",
    "grade",
    "next_action",
    "summary",
]


def write_call_record(record: dict[str, Any]) -> Path:
    with _LOCK:
        CALLS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        sid = record.get("call_sid") or "nosid"
        path = CALLS_DIR / f"{stamp}_{sid}.json"
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        _append_summary(record)
    return path


def _append_summary(record: dict[str, Any]) -> None:
    lead = record.get("lead", {})
    new_file = not SUMMARY_PATH.exists()
    with SUMMARY_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "call_sid": record.get("call_sid"),
                "started_at": record.get("started_at"),
                "company": lead.get("company"),
                "contact": " ".join(
                    filter(None, [lead.get("first_name"), lead.get("last_name")])
                ),
                "phone": lead.get("phone"),
                "outcome": record.get("outcome"),
                "grade": record.get("grade"),
                "next_action": json.dumps(record.get("next_action") or {}),
                "summary": record.get("summary"),
            }
        )
