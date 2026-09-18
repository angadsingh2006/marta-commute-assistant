"""Unit tests for alert publishing and message shaping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app import notify

ROUTE = {"route_id": "morning-bus-51", "label": "51 bus @ Auburn Ave",
         "alert_threshold_seconds": 600}
TRIP = {"trip_id": "bus-to-red-line", "label": "51 bus -> Red Line"}


@pytest.fixture
def published(monkeypatch):
    """Capture what would be published instead of calling SNS."""
    calls = []

    class FakeSns:
        def publish(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setenv("ALERT_TOPIC_ARN", "arn:aws:sns:us-east-1:1:commute")
    monkeypatch.setattr(notify, "_client", FakeSns())
    return calls


def test_route_alert_carries_a_subject_so_email_is_readable(published):
    notify.send_route_alert(ROUTE, 900)

    assert published[0]["Subject"]
    assert "51 bus @ Auburn Ave" in published[0]["Message"]


def test_trip_alert_reports_both_legs(published):
    notify.send_trip_alert(TRIP, 720, 840)

    message = published[0]["Message"]
    assert "12 min" in message and "14 min" in message


def test_subject_stays_within_the_sns_limit(published):
    notify.send_route_alert({**ROUTE, "label": "x" * 300}, 900)

    assert len(published[0]["Subject"]) <= 100


def test_subject_has_no_newlines(published):
    notify.send_route_alert({**ROUTE, "label": "line one\nline two"}, 900)

    assert "\n" not in published[0]["Subject"]
