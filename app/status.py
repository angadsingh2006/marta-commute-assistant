"""On-demand Lambda: current route and trip status, plus logged reliability."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from mangum import Mangum

from app import db, marta_client
from app.check_commute import is_connection_at_risk, is_delayed

logger = logging.getLogger()
logger.setLevel(logging.INFO)

app = FastAPI(
    title="Commute Assistant",
    description="Live MARTA status for the routes and trips you watch.",
)

DAYS_OF_WEEK = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def summarize_reliability(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Roll logged checks up into an overall and per-day-of-week on-time rate."""
    total = len(rows)
    if total == 0:
        return {"checks": 0, "on_time_rate": None, "by_day_of_week": {}}

    delayed_total = sum(1 for row in rows if row["was_delayed"])

    by_day: Dict[str, Dict[str, Any]] = {}
    for day in DAYS_OF_WEEK:
        day_rows = [row for row in rows if row["day_of_week"] == day]
        if not day_rows:
            continue
        day_delayed = sum(1 for row in day_rows if row["was_delayed"])
        by_day[day] = {
            "checks": len(day_rows),
            "delayed": day_delayed,
            "on_time_rate": _rate(len(day_rows) - day_delayed, len(day_rows)),
        }

    return {
        "checks": total,
        "on_time_rate": _rate(total - delayed_total, total),
        "by_day_of_week": by_day,
    }


def _rate(on_time: int, total: int) -> Optional[float]:
    """Return the share of checks that were on time, rounded."""
    return round(on_time / total, 3)


@app.get("/routes/{route_id}/status")
def route_status(route_id: str) -> Dict[str, Any]:
    """Return one route's next arrival and whether it is past its threshold."""
    route = db.get_route(route_id)
    if route is None:
        raise HTTPException(status_code=404, detail=f"no route {route_id!r}")

    wait_seconds = _live_wait(route)
    return {
        "route_id": route_id,
        "label": route.get("label"),
        "mode": route["mode"],
        "wait_seconds": wait_seconds,
        "threshold_seconds": route["alert_threshold_seconds"],
        "is_delayed": is_delayed(wait_seconds, route["alert_threshold_seconds"]),
    }


@app.get("/routes/{route_id}/reliability")
def route_reliability(
    route_id: str,
    days: int = Query(default=14, ge=1, le=90),
) -> Dict[str, Any]:
    """Return one route's on-time rate over a window, broken down by weekday."""
    if db.get_route(route_id) is None:
        raise HTTPException(status_code=404, detail=f"no route {route_id!r}")

    summary = summarize_reliability(db.recent_arrivals(route_id, days))
    return {"route_id": route_id, "days": days, **summary}


@app.get("/trips/{trip_id}/status")
def trip_status(trip_id: str) -> Dict[str, Any]:
    """Return both legs' current waits and whether the connection is at risk."""
    trip = db.get_trip(trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail=f"no trip {trip_id!r}")

    legs = {}
    for position, key in (("leg1", "leg1_route_id"), ("leg2", "leg2_route_id")):
        route = db.get_route(trip[key])
        if route is None:
            raise HTTPException(
                status_code=404,
                detail=f"trip {trip_id!r} references unknown route {trip[key]!r}",
            )
        legs[position] = {
            "route_id": route["route_id"],
            "label": route.get("label"),
            "wait_seconds": _live_wait(route),
        }

    buffer_seconds = trip["transfer_buffer_seconds"]
    return {
        "trip_id": trip_id,
        "label": trip.get("label"),
        "transfer_buffer_seconds": buffer_seconds,
        **legs,
        "connection_at_risk": is_connection_at_risk(
            legs["leg1"]["wait_seconds"],
            legs["leg2"]["wait_seconds"],
            buffer_seconds,
        ),
    }


def _live_wait(route: Dict[str, Any]) -> Optional[int]:
    """Fetch a live wait, raising 503 rather than 500 on a feed outage."""
    try:
        return marta_client.next_wait_seconds(route)
    except marta_client.MartaFeedError as exc:
        logger.warning("feed unavailable for route %s: %s", route.get("route_id"), exc)
        raise HTTPException(
            status_code=503, detail="MARTA feed is unavailable right now"
        ) from None


handler = Mangum(app)
