import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.crm.leads import is_callable, load_leads, within_calling_window

WED = datetime(2026, 9, 16, 10, 0, tzinfo=ZoneInfo("America/New_York"))
SAT = datetime(2026, 9, 19, 10, 0, tzinfo=ZoneInfo("America/New_York"))
EARLY = datetime(2026, 9, 16, 6, 30, tzinfo=ZoneInfo("America/New_York"))


def lead(**over):
    base = {"phone": "+15550100001", "timezone": "America/New_York"}
    base.update(over)
    return base


def test_weekday_business_hours_are_callable():
    assert within_calling_window(lead(), WED)


def test_weekends_are_never_callable():
    assert not within_calling_window(lead(), SAT)


def test_before_business_hours_is_not_callable():
    assert not within_calling_window(lead(), EARLY)


def test_window_follows_the_prospects_timezone_not_ours():
    # 10:00 Eastern is 07:00 Pacific - too early for a Tacoma fabricator.
    pacific = lead(timezone="America/Los_Angeles")
    assert not within_calling_window(pacific, WED)


def test_unknown_timezone_falls_back_without_crashing():
    assert within_calling_window(lead(timezone="Mars/Olympus"), WED)


def test_lead_without_phone_is_skipped():
    ok, reason = is_callable({"phone": ""})
    assert not ok and "phone" in reason


def test_sample_lead_file_loads():
    rows = load_leads(Path(__file__).resolve().parent.parent / "data" / "leads.sample.csv")
    assert len(rows) == 3
    assert rows[0]["company"] == "Dolan Steel Fabricators"


def test_missing_phone_column_is_a_clear_error(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("company,email\nAcme,a@b.c\n", encoding="utf-8")
    with pytest.raises(ValueError, match="phone"):
        load_leads(bad)
