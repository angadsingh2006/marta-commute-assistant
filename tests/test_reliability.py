"""Unit tests for the reliability rollup in app/status.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.status import summarize_reliability


def _row(day, was_delayed):
    """Build one arrival_log row for a given weekday and delay flag."""
    return {"day_of_week": day, "was_delayed": was_delayed, "wait_seconds": 300}


def test_counts_every_logged_check():
    rows = [_row("Monday", False), _row("Monday", True), _row("Tuesday", False)]

    assert summarize_reliability(rows)["checks"] == 3


def test_on_time_rate_is_the_share_of_checks_that_were_not_delayed():
    rows = [_row("Monday", False), _row("Monday", False), _row("Monday", True)]

    assert summarize_reliability(rows)["on_time_rate"] == 0.667


def test_breaks_the_rate_down_by_day_of_week():
    rows = [_row("Monday", True), _row("Monday", True), _row("Friday", False)]

    by_day = summarize_reliability(rows)["by_day_of_week"]

    assert by_day["Monday"] == {"checks": 2, "delayed": 2, "on_time_rate": 0.0}
    assert by_day["Friday"] == {"checks": 1, "delayed": 0, "on_time_rate": 1.0}


def test_days_come_back_in_calendar_order_not_first_seen_order():
    rows = [_row("Wednesday", False), _row("Monday", True), _row("Tuesday", False)]

    days = list(summarize_reliability(rows)["by_day_of_week"])

    assert days == ["Monday", "Tuesday", "Wednesday"]


def test_days_with_no_logged_checks_are_omitted_entirely():
    rows = [_row("Monday", False)]

    assert list(summarize_reliability(rows)["by_day_of_week"]) == ["Monday"]


def test_empty_history_reports_no_rate_rather_than_a_perfect_one():
    summary = summarize_reliability([])

    assert summary["checks"] == 0
    assert summary["on_time_rate"] is None
    assert summary["by_day_of_week"] == {}
