import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.compliance import dnc
from app.conversation.outcomes import CallOutcome
from app.crm import call_log


def make(tmp_path, monkeypatch):
    monkeypatch.setattr(dnc, "DNC_PATH", tmp_path / "dnc.csv")
    monkeypatch.setattr(dnc, "DATA_DIR", tmp_path)
    monkeypatch.setattr(call_log, "CALLS_DIR", tmp_path / "calls")
    monkeypatch.setattr(call_log, "SUMMARY_PATH", tmp_path / "summary.csv")
    return CallOutcome({"phone": "+15550100001", "company": "Dolan Steel"})


def test_discovery_and_grading_accumulate(tmp_path, monkeypatch):
    outcome = make(tmp_path, monkeypatch)
    outcome.handle_tool("log_discovery", {"field": "software_used", "value": "Tekla"})
    outcome.handle_tool("set_qualification", {"grade": "hot", "reason": "backlog"})
    assert outcome.discovery["software_used"] == "Tekla"
    assert outcome.grade == "hot"


def test_do_not_call_suppresses_immediately(tmp_path, monkeypatch):
    outcome = make(tmp_path, monkeypatch)
    outcome.handle_tool("mark_do_not_call", {"reason": "asked to stop"})
    assert dnc.is_suppressed("+15550100001")
    assert outcome.outcome == "do_not_call"
    assert outcome.grade == "disqualified"


def test_booking_a_meeting_defaults_the_grade_to_hot(tmp_path, monkeypatch):
    outcome = make(tmp_path, monkeypatch)
    outcome.handle_tool("book_meeting", {"when": "Thursday am", "email": "m@x.com"})
    assert outcome.grade == "hot"
    assert outcome.next_action["type"] == "meeting"


def test_end_call_sets_the_hangup_flag(tmp_path, monkeypatch):
    outcome = make(tmp_path, monkeypatch)
    assert not outcome.hang_up_requested
    outcome.handle_tool("end_call", {"outcome": "not_interested", "summary": "Declined."})
    assert outcome.hang_up_requested


def test_unknown_tool_does_not_raise(tmp_path, monkeypatch):
    outcome = make(tmp_path, monkeypatch)
    assert "unknown" in outcome.handle_tool("nonsense", {})


def test_finalize_writes_a_record_and_summary_row(tmp_path, monkeypatch):
    outcome = make(tmp_path, monkeypatch)
    outcome.call_sid = "CA123"
    outcome.add_transcript("agent", "Hi there")
    outcome.handle_tool("end_call", {"outcome": "email_captured", "summary": "Sent deck."})
    asyncio.run(outcome.finalize([]))
    files = list((tmp_path / "calls").glob("*.json"))
    assert len(files) == 1
    assert "email_captured" in files[0].read_text()
    assert "Dolan Steel" in (tmp_path / "summary.csv").read_text()
