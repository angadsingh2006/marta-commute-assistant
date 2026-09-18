"""All calls to MARTA's rail + bus real-time feeds."""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

logger = logging.getLogger(__name__)

RAIL_URL = (
    "https://developerservices.itsmarta.com/RealtimeTrain/RESTService"
    "/RealTimeService/GetRealtimeArrivals"
)
BUS_URL = (
    "https://gtfs-rt.itsmarta.com/TMGTFSRealTimeWebService/tripupdate/tripupdates.pb"
)

RAIL_API_KEY_ENV = "MARTA_RAIL_API_KEY"
REQUEST_TIMEOUT_SECONDS = 10
FEED_CACHE_TTL_SECONDS = 20

_feed_cache: Dict[str, Any] = {}


class MartaFeedError(RuntimeError):
    """Raised when a feed can't be reached or can't be parsed."""


def next_wait_seconds(route: Dict[str, Any]) -> Optional[int]:
    """Return seconds until the next vehicle for a route, or None if none is due."""
    mode = route.get("mode")
    if mode == "rail":
        return _next_rail_wait(route)
    if mode == "bus":
        return _next_bus_wait(route)
    raise ValueError(f"route {route.get('route_id')!r} has unknown mode {mode!r}")


def _next_rail_wait(route: Dict[str, Any]) -> Optional[int]:
    """Return the soonest wait for a rail route's station, line and direction."""
    station = route["station"].strip().upper()
    line = route["line"].strip().upper()
    direction = (route.get("direction") or "").strip().upper()

    waits = []
    for arrival in _rail_arrivals():
        if arrival.get("STATION", "").strip().upper() != station:
            continue
        if arrival.get("LINE", "").strip().upper() != line:
            continue
        if direction and arrival.get("DIRECTION", "").strip().upper() != direction:
            continue
        seconds = _coerce_seconds(arrival.get("WAITING_SECONDS"))
        if seconds is not None:
            waits.append(seconds)

    return min(waits) if waits else None


def _rail_arrivals() -> List[Dict[str, Any]]:
    """Fetch and cache the rail feed's full list of system-wide arrivals."""
    cached = _cache_get("rail")
    if cached is not None:
        return cached

    api_key = os.environ.get(RAIL_API_KEY_ENV)
    if not api_key:
        raise MartaFeedError(f"{RAIL_API_KEY_ENV} is not set")

    try:
        response = requests.get(
            RAIL_URL,
            params={"apiKey": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        arrivals = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MartaFeedError(f"rail feed unavailable: {exc}") from exc

    return _cache_put("rail", arrivals)


def _coerce_seconds(raw: Any) -> Optional[int]:
    """Parse the rail feed's string WAITING_SECONDS, clamping negatives to 0."""
    try:
        seconds = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return max(seconds, 0)


def _next_bus_wait(route: Dict[str, Any]) -> Optional[int]:
    """Return the soonest wait for a bus route at its configured stop."""
    route_number = str(route["route_number"]).strip()
    stop_id = str(route["stop_id"]).strip()
    now = int(time.time())

    waits = []
    for entity in _bus_feed().entity:
        if not entity.HasField("trip_update"):
            continue
        trip_update = entity.trip_update
        if trip_update.trip.route_id.strip() != route_number:
            continue
        for stop_time_update in trip_update.stop_time_update:
            if stop_time_update.stop_id.strip() != stop_id:
                continue
            event = None
            if stop_time_update.HasField("arrival"):
                event = stop_time_update.arrival
            elif stop_time_update.HasField("departure"):
                event = stop_time_update.departure
            if event is None or not event.time:
                continue
            seconds = event.time - now
            if seconds >= 0:
                waits.append(seconds)

    return min(waits) if waits else None


def _bus_feed() -> Any:
    """Fetch and cache the bus GTFS-realtime trip update feed."""
    cached = _cache_get("bus")
    if cached is not None:
        return cached

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        response = requests.get(BUS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        feed.ParseFromString(response.content)
    except (requests.RequestException, DecodeError) as exc:
        raise MartaFeedError(f"bus feed unavailable: {exc}") from exc

    return _cache_put("bus", feed)


def _cache_get(key: str) -> Any:
    """Return a cached feed if it was fetched within the TTL, else None."""
    entry = _feed_cache.get(key)
    if entry and time.monotonic() - entry["fetched_at"] < FEED_CACHE_TTL_SECONDS:
        return entry["value"]
    return None


def _cache_put(key: str, value: Any) -> Any:
    """Store a fetched feed with its fetch time and return it."""
    _feed_cache[key] = {"value": value, "fetched_at": time.monotonic()}
    return value
