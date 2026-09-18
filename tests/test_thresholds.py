"""Unit tests for the two alert predicates in app/check_commute.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.check_commute import is_connection_at_risk, is_delayed


def test_wait_longer_than_threshold_is_delayed():
    assert is_delayed(720, 600) is True


def test_wait_shorter_than_threshold_is_not_delayed():
    assert is_delayed(300, 600) is False


def test_wait_exactly_at_threshold_is_not_delayed():
    assert is_delayed(600, 600) is False


def test_missing_arrival_data_is_not_delayed():
    assert is_delayed(None, 600) is False


def test_connection_is_at_risk_when_leg1_plus_buffer_exceeds_leg2():
    assert is_connection_at_risk(480, 600, 300) is True


def test_connection_is_safe_when_leg1_plus_buffer_fits_before_leg2():
    assert is_connection_at_risk(240, 900, 300) is False


def test_connection_arriving_exactly_at_departure_is_not_at_risk():
    assert is_connection_at_risk(300, 600, 300) is False


def test_small_leg1_delay_is_at_risk_even_though_neither_leg_is_delayed():
    assert is_connection_at_risk(360, 420, 300) is True


def test_big_leg1_delay_is_safe_when_leg2_is_delayed_too():
    assert is_connection_at_risk(900, 1500, 300) is False


def test_connection_with_missing_leg_data_is_not_at_risk():
    assert is_connection_at_risk(None, 600, 300) is False
    assert is_connection_at_risk(480, None, 300) is False
