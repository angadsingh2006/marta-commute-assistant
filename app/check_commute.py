"""Scheduled Lambda: check watched routes, log them, and alert on trouble."""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import db, marta_client, notify

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_COOLDOWN_SECONDS = 1800
COOLDOWN_ENV = "ALERT_COOLDOWN_SECONDS"


def is_delayed(wait_seconds: Optional[int], threshold_seconds: int) -> bool:
    """Report whether one leg's wait exceeds the threshold configured for it."""
    if wait_seconds is None:
        return False
    return wait_seconds > threshold_seconds


def is_connection_at_risk(
    leg1_wait_seconds: Optional[int],
    leg2_wait_seconds: Optional[int],
    transfer_buffer_seconds: int,
) -> bool:
    """Report whether leg 1 plus the transfer buffer lands after leg 2 departs."""
    if leg1_wait_seconds is None or leg2_wait_seconds is None:
        return False
    return leg1_wait_seconds + transfer_buffer_seconds > leg2_wait_seconds


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Run one scheduled pass over every route and trip, and log what it saw."""
    routes = db.get_routes()
    trips = db.get_trips()

    waits = _check_routes(routes)
    trips_checked = _check_trips(trips, waits)

    summary = {
        "routes_checked": len(waits),
        "trips_checked": trips_checked,
        "waits": waits,
    }
    logger.info("commute check complete: %s", summary)
    return summary


def _check_routes(routes: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """Check, log and alert on each leg individually; return the waits read."""
    waits: Dict[str, Optional[int]] = {}

    for route in routes:
        try:
            route_id = route["route_id"]
            threshold_seconds = route["alert_threshold_seconds"]
            wait_seconds = marta_client.next_wait_seconds(route)
        except (marta_client.MartaFeedError, KeyError, ValueError):
            logger.exception(
                "could not check route %s", route.get("route_id", "<no route_id>")
            )
            continue

        waits[route_id] = wait_seconds
        delayed = is_delayed(wait_seconds, threshold_seconds)

        db.log_arrival(
            route_id=route_id,
            checked_at=datetime.now(timezone.utc),
            wait_seconds=wait_seconds,
            was_delayed=delayed,
        )

        if delayed:
            _alert_once(
                route_id,
                lambda r=route, w=wait_seconds: notify.send_route_alert(r, w),
            )

    return waits


def _check_trips(
    trips: List[Dict[str, Any]],
    waits: Dict[str, Optional[int]],
) -> int:
    """Evaluate each trip's connection risk from this pass's waits and alert."""
    checked = 0

    for trip in trips:
        trip_id = trip["trip_id"]
        leg1_id = trip["leg1_route_id"]
        leg2_id = trip["leg2_route_id"]

        if leg1_id not in waits or leg2_id not in waits:
            logger.warning(
                "skipping trip %s: missing wait for %s or %s",
                trip_id,
                leg1_id,
                leg2_id,
            )
            continue

        checked += 1
        leg1_wait = waits[leg1_id]
        leg2_wait = waits[leg2_id]

        if not is_connection_at_risk(
            leg1_wait, leg2_wait, trip["transfer_buffer_seconds"]
        ):
            continue

        _alert_once(
            f"trip:{trip_id}",
            lambda t=trip, a=leg1_wait, b=leg2_wait: notify.send_trip_alert(t, a, b),
        )

    return checked


def _alert_once(alert_key: str, send) -> None:
    """Claim the cooldown and send, releasing the claim if the send fails."""
    if not db.claim_alert(alert_key, _cooldown_seconds()):
        return
    try:
        send()
    except Exception:
        logger.exception("failed to send alert for %s", alert_key)
        try:
            db.release_alert(alert_key)
        except Exception:
            logger.exception("could not release cooldown for %s", alert_key)
    else:
        logger.info("alerted on %s", alert_key)


def _cooldown_seconds() -> int:
    """Return the configured alert cooldown in seconds."""
    return int(os.environ.get(COOLDOWN_ENV, DEFAULT_COOLDOWN_SECONDS))
