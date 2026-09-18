"""Unit tests for error mapping in the on-demand status API."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi import HTTPException

from app import status


def test_a_route_with_an_unknown_mode_reports_misconfiguration(monkeypatch):
    monkeypatch.setattr(status, "db", SimpleNamespace(get_route=lambda rid: {
        "route_id": rid, "mode": "tram", "alert_threshold_seconds": 600}))

    with pytest.raises(HTTPException) as caught:
        status.route_status("morning-tram")

    assert caught.value.status_code == 500
    assert "misconfigured" in caught.value.detail


def test_a_route_missing_a_required_field_reports_misconfiguration(monkeypatch):
    monkeypatch.setattr(status, "db", SimpleNamespace(get_route=lambda rid: {
        "route_id": rid, "mode": "rail", "alert_threshold_seconds": 600}))

    with pytest.raises(HTTPException) as caught:
        status.route_status("morning-rail-red")

    assert caught.value.status_code == 500
