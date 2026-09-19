"""All calls to MARTA's rail + bus real-time feeds."""

import logging
import os
import time
from typing import Any, Dict, List, Optional

from urllib.parse import quote, quote_plus

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
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

RAIL_PARAMETER_NAME_ENV = "MARTA_RAIL_PARAMETER_NAME"
REQUEST_TIMEOUT_SECONDS = 10
FEED_CACHE_TTL_SECONDS = 20

_feed_cache: Dict[str, Any] = {}
_ssm_client = None
_rail_key: Optional[str] = None


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
        if not isinstance(arrival, dict):
            continue
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
        return _unwrap(cached)

    api_key = _rail_api_key()

    try:
        response = requests.get(
            RAIL_URL,
            params={"apiKey": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        arrivals = response.json()
    except (requests.RequestException, ValueError) as exc:
        _fail("rail", _redact(f"rail feed unavailable: {exc}"))

    if not isinstance(arrivals, list):
        _fail("rail", "rail feed returned an unexpected payload")

    return _cache_put("rail", arrivals)


def _ssm():
    """Build the SSM client, caching it for reuse."""
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm")
    return _ssm_client


def _rail_api_key() -> str:
    """Read the rail API key from Parameter Store, caching it for the container."""
    global _rail_key
    if _rail_key is not None:
        return _rail_key

    parameter_name = os.environ.get(RAIL_PARAMETER_NAME_ENV)
    if not parameter_name:
        raise MartaFeedError(f"{RAIL_PARAMETER_NAME_ENV} is not set")

    try:
        response = _ssm().get_parameter(Name=parameter_name, WithDecryption=True)
    except (BotoCoreError, ClientError) as exc:
        raise MartaFeedError(f"could not read {parameter_name}: {exc}") from None

    _rail_key = response["Parameter"]["Value"]
    return _rail_key


def _redact(text: str) -> str:
    """Replace the rail API key with a placeholder, raw or URL-encoded."""
    if not _rail_key:
        return text
    for form in (_rail_key, quote_plus(_rail_key), quote(_rail_key, safe="")):
        text = text.replace(form, "***")
    return text


def _fail(cache_key: str, message: str) -> None:
    """Cache a feed failure for the TTL window, then raise it."""
    error = MartaFeedError(message)
    _cache_put(cache_key, error)
    raise error from None


def _unwrap(cached: Any) -> Any:
    """Return a cached feed, re-raising instead if the cached value is a failure."""
    if isinstance(cached, MartaFeedError):
        raise cached
    return cached


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
        return _unwrap(cached)

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        response = requests.get(BUS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        feed.ParseFromString(response.content)
    except (requests.RequestException, DecodeError) as exc:
        _fail("bus", f"bus feed unavailable: {exc}")

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
