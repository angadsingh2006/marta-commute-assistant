"""Unit tests for the scheduled checker's orchestration and failure handling."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app import check_commute

DELAYED_BUS = {
    "route_id": "morning-bus-51",
    "mode": "bus",
    "route_number": "51",
    "stop_id": "901234",
    "label": "51 bus",
    "alert_threshold_seconds": 600,
}
DELAYED_RAIL = {
    "route_id": "morning-rail-red",
    "mode": "rail",
    "station": "FIVE POINTS STATION",
    "line": "RED",
    "label": "Red Line",
    "alert_threshold_seconds": 600,
}


def _wire(monkeypatch, *, waits, send_fails_for=(), claim=True):
    """Point check_commute at in-memory fakes and return what they recorded."""
    seen = {"logged": [], "sent": [], "claimed": [], "released": []}

    def send_route_alert(route, wait):
        if route["route_id"] in send_fails_for:
            raise RuntimeError("SNS is down")
        seen["sent"].append(route["route_id"])

    monkeypatch.setattr(check_commute, "db", SimpleNamespace(
        log_arrival=lambda **kw: seen["logged"].append(kw["route_id"]),
        claim_alert=lambda key, cooldown: (seen["claimed"].append(key), claim)[1],
        release_alert=lambda key: seen["released"].append(key),
    ))
    monkeypatch.setattr(check_commute, "marta_client", SimpleNamespace(
        next_wait_seconds=lambda route: waits[route["route_id"]],
        MartaFeedError=RuntimeError,
    ))
    monkeypatch.setattr(check_commute, "notify", SimpleNamespace(
        send_route_alert=send_route_alert,
        send_trip_alert=lambda t, a, b: seen["sent"].append(t["trip_id"]),
    ))
    return seen


def test_failed_send_releases_the_cooldown_so_the_next_run_can_retry(monkeypatch):
    seen = _wire(monkeypatch, waits={"morning-bus-51": 900},
                 send_fails_for=("morning-bus-51",))

    check_commute._check_routes([DELAYED_BUS])

    assert seen["claimed"] == ["morning-bus-51"]
    assert seen["released"] == ["morning-bus-51"]


def test_one_failed_alert_does_not_abandon_the_remaining_routes(monkeypatch):
    seen = _wire(monkeypatch,
                 waits={"morning-bus-51": 900, "morning-rail-red": 900},
                 send_fails_for=("morning-bus-51",))

    check_commute._check_routes([DELAYED_BUS, DELAYED_RAIL])

    assert seen["logged"] == ["morning-bus-51", "morning-rail-red"]
    assert seen["sent"] == ["morning-rail-red"]


def test_a_malformed_route_item_is_skipped_rather_than_ending_the_pass(monkeypatch):
    seen = _wire(monkeypatch, waits={"morning-bus-51": 300})

    waits = check_commute._check_routes([{"mode": "bus"}, DELAYED_BUS])

    assert list(waits) == ["morning-bus-51"]
    assert seen["logged"] == ["morning-bus-51"]


def test_a_route_missing_its_threshold_is_skipped_too(monkeypatch):
    seen = _wire(monkeypatch, waits={"morning-bus-51": 300, "broken": 300})
    broken = {"route_id": "broken", "mode": "bus", "route_number": "1", "stop_id": "2"}

    waits = check_commute._check_routes([broken, DELAYED_BUS])

    assert list(waits) == ["morning-bus-51"]
