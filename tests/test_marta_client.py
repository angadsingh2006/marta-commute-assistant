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

BUS_ROUTE = {
    "route_id": "morning-bus-51",
    "mode": "bus",
    "route_number": "51",
    "stop_id": "901234",
    "alert_threshold_seconds": 600,
}


class FakeResponse:
    """Stand-in for a requests Response that always succeeded."""

    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    marta_client._feed_cache.clear()
    monkeypatch.setattr(marta_client, "_rail_key", "test-key")
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


class FakeSsm:
    """Stand-in for the SSM client that records what it was asked to read."""

    def __init__(self, value="fetched-key"):
        self.value = value
        self.reads = []

    def get_parameter(self, Name, WithDecryption):
        self.reads.append((Name, WithDecryption))
        return {"Parameter": {"Value": self.value}}


def _empty_bus_feed():
    """Serialize a GTFS-realtime feed carrying no trip updates."""
    feed = marta_client.gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    return feed.SerializeToString()


def test_the_rail_key_is_read_once_and_reused(monkeypatch):
    ssm = FakeSsm()
    monkeypatch.setattr(marta_client, "_rail_key", None)
    monkeypatch.setattr(marta_client, "_ssm", lambda: ssm)
    monkeypatch.setenv("MARTA_RAIL_PARAMETER_NAME", "/commute-assistant/marta-rail-key")

    assert marta_client._rail_api_key() == "fetched-key"
    assert marta_client._rail_api_key() == "fetched-key"

    assert ssm.reads == [("/commute-assistant/marta-rail-key", True)]


def test_a_missing_parameter_name_raises_a_feed_error(monkeypatch):
    monkeypatch.setattr(marta_client, "_rail_key", None)
    monkeypatch.delenv("MARTA_RAIL_PARAMETER_NAME", raising=False)

    with pytest.raises(marta_client.MartaFeedError):
        marta_client._rail_api_key()


def test_a_bus_route_never_reads_the_rail_key(monkeypatch):
    def boom():
        raise AssertionError("the bus path must not reach Parameter Store")

    monkeypatch.setattr(marta_client, "_rail_key", None)
    monkeypatch.setattr(marta_client, "_ssm", boom)
    monkeypatch.setattr(
        marta_client.requests, "get",
        lambda *a, **k: FakeResponse(content=_empty_bus_feed()))

    assert marta_client.next_wait_seconds(BUS_ROUTE) is None
