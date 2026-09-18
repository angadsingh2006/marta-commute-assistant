"""SNS publishing and the alert wording that goes with it."""

import os
from typing import Any, Dict, Optional

import boto3

ALERT_TOPIC_ARN_ENV = "ALERT_TOPIC_ARN"

_client = None


def _sns():
    """Build the SNS client, caching it for reuse."""
    global _client
    if _client is None:
        _client = boto3.client("sns")
    return _client


def send_route_alert(route: Dict[str, Any], wait_seconds: int) -> None:
    """Send an alert that one leg is running past its threshold."""
    label = route.get("label") or route["route_id"]
    threshold = route["alert_threshold_seconds"]
    message = (
        f"{label}: next arrival is {_minutes(wait_seconds)} away "
        f"(over your {_minutes(threshold)} limit)."
    )
    _publish(message)


def send_trip_alert(
    trip: Dict[str, Any],
    leg1_wait_seconds: int,
    leg2_wait_seconds: int,
) -> None:
    """Send an alert that a transfer is at risk, with both legs' waits."""
    label = trip.get("label") or trip["trip_id"]
    message = (
        f"{label}: connection at risk. Leg 1 in {_minutes(leg1_wait_seconds)}, "
        f"leg 2 in {_minutes(leg2_wait_seconds)} — not enough transfer time."
    )
    _publish(message)


def _publish(message: str) -> None:
    """Publish one message to the alert topic."""
    _sns().publish(TopicArn=os.environ[ALERT_TOPIC_ARN_ENV], Message=message)


def _minutes(seconds: Optional[int]) -> str:
    """Render a wait in seconds the way a person would say it out loud."""
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return "under a min"
    return f"{round(seconds / 60)} min"
