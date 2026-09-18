"""Unit tests for feed parsing, error handling and caching in marta_client."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import requests

from app import marta_client

RAIL_ROUTE = {
    "route_id": "morning-rail-red",
    "mode": "rail",
    "station": "FIVE POINTS STATION",
    "line": "RED",
    "alert_threshold_seconds": 600,
}


class FakeResponse:
    """Stand-in for a requests Response that always succeeded."""

    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    marta_client._feed_cache.clear()
    monkeypatch.setenv("MARTA_RAIL_API_KEY", "test-key")
    yield
    marta_client._feed_cache.clear()


def test_a_rail_payload_that_is_not_a_list_raises_a_feed_error(monkeypatch):
    monkeypatch.setattr(
        marta_client.requests, "get",
        lambda *a, **k: FakeResponse({"Message": "invalid api key"}))

    with pytest.raises(marta_client.MartaFeedError):
        marta_client.next_wait_seconds(RAIL_ROUTE)


def test_rail_entries_that_are_not_objects_are_ignored(monkeypatch):
    monkeypatch.setattr(
        marta_client.requests, "get",
        lambda *a, **k: FakeResponse(["junk", {
            "STATION": "FIVE POINTS STATION", "LINE": "RED",
            "DIRECTION": "N", "WAITING_SECONDS": "120"}]))

    assert marta_client.next_wait_seconds(RAIL_ROUTE) == 120


def test_a_failing_feed_is_only_attempted_once_per_cache_window(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("feed is down")

    monkeypatch.setattr(marta_client.requests, "get", boom)

    for _ in range(3):
        with pytest.raises(marta_client.MartaFeedError):
            marta_client.next_wait_seconds(RAIL_ROUTE)

    assert len(calls) == 1
