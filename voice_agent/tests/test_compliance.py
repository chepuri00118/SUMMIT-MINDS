import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.compliance import dnc
from app.crm import leads as leads_module


@pytest.fixture
def temp_dnc(tmp_path, monkeypatch):
    monkeypatch.setattr(dnc, "DNC_PATH", tmp_path / "do_not_call.csv")
    monkeypatch.setattr(dnc, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dnc.__dict__["_ensure_file"].__globals__["DATA_DIR"], "__class__", Path)
    return tmp_path


def test_normalize_strips_formatting_and_country_code():
    assert dnc.normalize("+1 (555) 010-9999") == "5550109999"
    assert dnc.normalize("555.010.9999") == "5550109999"
    assert dnc.normalize("15550109999") == "5550109999"


def test_suppression_survives_reformatting(tmp_path, monkeypatch):
    monkeypatch.setattr(dnc, "DNC_PATH", tmp_path / "dnc.csv")
    monkeypatch.setattr(dnc, "DATA_DIR", tmp_path)
    dnc.add_to_dnc("+1 (555) 010-9999", "asked to be removed")
    assert dnc.is_suppressed("555-010-9999")
    assert dnc.is_suppressed("1 555 010 9999")
    assert not dnc.is_suppressed("5550100000")


def test_empty_number_is_never_added(tmp_path, monkeypatch):
    monkeypatch.setattr(dnc, "DNC_PATH", tmp_path / "dnc.csv")
    monkeypatch.setattr(dnc, "DATA_DIR", tmp_path)
    dnc.add_to_dnc("", "bug")
    assert not (tmp_path / "dnc.csv").exists()


def test_suppressed_lead_is_not_callable(tmp_path, monkeypatch):
    monkeypatch.setattr(dnc, "DNC_PATH", tmp_path / "dnc.csv")
    monkeypatch.setattr(dnc, "DATA_DIR", tmp_path)
    dnc.add_to_dnc("+15550100001", "requested")
    ok, reason = leads_module.is_callable(
        {"phone": "+15550100001", "timezone": "America/New_York"}
    )
    assert not ok
    assert "do-not-call" in reason
