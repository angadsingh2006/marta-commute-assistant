"""All DynamoDB access."""

import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

ROUTES_TABLE_ENV = "ROUTES_TABLE"
TRIPS_TABLE_ENV = "TRIPS_TABLE"
ARRIVAL_LOG_TABLE_ENV = "ARRIVAL_LOG_TABLE"
ALERT_STATE_TABLE_ENV = "ALERT_STATE_TABLE"

_resource = None
_tables: Dict[str, Any] = {}


def _table(env_var: str):
    """Build the Table resource named by an env var, caching it for reuse."""
    global _resource
    if env_var not in _tables:
        if _resource is None:
            _resource = boto3.resource("dynamodb")
        _tables[env_var] = _resource.Table(os.environ[env_var])
    return _tables[env_var]


def get_routes() -> List[Dict[str, Any]]:
    """Return every watched route."""
    response = _table(ROUTES_TABLE_ENV).scan()
    return [_undecimal(item) for item in response.get("Items", [])]


def get_route(route_id: str) -> Optional[Dict[str, Any]]:
    """Return one route by id, or None if it isn't configured."""
    response = _table(ROUTES_TABLE_ENV).get_item(Key={"route_id": route_id})
    item = response.get("Item")
    return _undecimal(item) if item else None


def get_trips() -> List[Dict[str, Any]]:
    """Return every configured trip."""
    response = _table(TRIPS_TABLE_ENV).scan()
    return [_undecimal(item) for item in response.get("Items", [])]


def get_trip(trip_id: str) -> Optional[Dict[str, Any]]:
    """Return one trip by id, or None if it isn't configured."""
    response = _table(TRIPS_TABLE_ENV).get_item(Key={"trip_id": trip_id})
    item = response.get("Item")
    return _undecimal(item) if item else None


def log_arrival(
    route_id: str,
    checked_at: datetime,
    wait_seconds: Optional[int],
    was_delayed: bool,
) -> None:
    """Append one check to the arrival history, omitting an absent wait."""
    item = {
        "route_id": route_id,
        "checked_at": checked_at.isoformat(),
        "was_delayed": was_delayed,
        "day_of_week": checked_at.strftime("%A"),
    }
    if wait_seconds is not None:
        item["wait_seconds"] = wait_seconds
    _table(ARRIVAL_LOG_TABLE_ENV).put_item(Item=item)


def recent_arrivals(route_id: str, days: int) -> List[Dict[str, Any]]:
    """Return one route's logged checks from the last `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    response = _table(ARRIVAL_LOG_TABLE_ENV).query(
        KeyConditionExpression=Key("route_id").eq(route_id)
        & Key("checked_at").gte(cutoff)
    )
    return [_undecimal(item) for item in response.get("Items", [])]


def claim_alert(alert_key: str, cooldown_seconds: int) -> bool:
    """Claim the right to send one alert, returning False if still cooling down."""
    now = int(time.time())
    try:
        _table(ALERT_STATE_TABLE_ENV).put_item(
            Item={
                "alert_key": alert_key,
                "expires_at": now + cooldown_seconds,
                "alerted_at": datetime.now(timezone.utc).isoformat(),
            },
            ConditionExpression="attribute_not_exists(alert_key) OR expires_at < :now",
            ExpressionAttributeValues={":now": now},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _undecimal(value: Any) -> Any:
    """Recursively convert DynamoDB Decimals into plain ints and floats."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: _undecimal(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_undecimal(inner) for inner in value]
    return value
