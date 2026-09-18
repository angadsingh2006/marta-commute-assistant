"""Unit tests for pagination in the DynamoDB access layer."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app import db


class FakePagedTable:
    """Returns prepared response pages and records the kwargs it was called with."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)


def test_recent_arrivals_follows_every_page(monkeypatch):
    table = FakePagedTable([
        {"Items": [{"route_id": "r", "checked_at": "1"}], "LastEvaluatedKey": {"k": 1}},
        {"Items": [{"route_id": "r", "checked_at": "2"}]},
    ])
    monkeypatch.setitem(db._tables, db.ARRIVAL_LOG_TABLE_ENV, table)

    rows = db.recent_arrivals("r", 90)

    assert len(rows) == 2
    assert table.calls[1]["ExclusiveStartKey"] == {"k": 1}


def test_get_routes_follows_every_page(monkeypatch):
    table = FakePagedTable([
        {"Items": [{"route_id": "a"}], "LastEvaluatedKey": {"k": 1}},
        {"Items": [{"route_id": "b"}]},
    ])
    monkeypatch.setitem(db._tables, db.ROUTES_TABLE_ENV, table)

    assert [r["route_id"] for r in db.get_routes()] == ["a", "b"]
