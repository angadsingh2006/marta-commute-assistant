"""Unit tests for API key redaction in app/marta_client.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from app import marta_client


def test_redacts_the_rail_api_key_wherever_it_appears(monkeypatch):
    monkeypatch.setattr(marta_client, "_rail_key", "SUPER-SECRET-KEY-123")

    cleaned = marta_client._redact(
        "Max retries exceeded with url: /RealTimeService?apiKey=SUPER-SECRET-KEY-123"
    )

    assert "SUPER-SECRET-KEY-123" not in cleaned


def test_keeps_the_rest_of_the_message_intact(monkeypatch):
    monkeypatch.setattr(marta_client, "_rail_key", "SUPER-SECRET-KEY-123")

    cleaned = marta_client._redact("401 Client Error for url: x?apiKey=SUPER-SECRET-KEY-123")

    assert cleaned.startswith("401 Client Error")


def test_leaves_text_alone_when_no_key_has_been_fetched(monkeypatch):
    monkeypatch.setattr(marta_client, "_rail_key", None)

    assert marta_client._redact("bus feed unavailable") == "bus feed unavailable"


def test_redacts_the_key_as_requests_percent_encodes_it_into_urls(monkeypatch):
    key = "ab+cd/ef gh=="
    monkeypatch.setattr(marta_client, "_rail_key", key)

    try:
        requests.get("http://127.0.0.1:9/x", params={"apiKey": key}, timeout=1)
    except requests.RequestException as exc:
        message = str(exc)

    cleaned = marta_client._redact(message)

    assert "ab%2Bcd%2Fef+gh%3D%3D" not in cleaned
    assert key not in cleaned
