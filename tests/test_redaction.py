"""Unit tests for API key redaction in app/marta_client.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import marta_client


def test_redacts_the_rail_api_key_wherever_it_appears(monkeypatch):
    monkeypatch.setenv("MARTA_RAIL_API_KEY", "SUPER-SECRET-KEY-123")

    cleaned = marta_client._redact(
        "Max retries exceeded with url: /RealTimeService?apiKey=SUPER-SECRET-KEY-123"
    )

    assert "SUPER-SECRET-KEY-123" not in cleaned


def test_keeps_the_rest_of_the_message_intact(monkeypatch):
    monkeypatch.setenv("MARTA_RAIL_API_KEY", "SUPER-SECRET-KEY-123")

    cleaned = marta_client._redact("401 Client Error for url: x?apiKey=SUPER-SECRET-KEY-123")

    assert cleaned.startswith("401 Client Error")


def test_leaves_text_alone_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv("MARTA_RAIL_API_KEY", raising=False)

    assert marta_client._redact("bus feed unavailable") == "bus feed unavailable"
