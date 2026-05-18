import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from decide import desired_state, diff_state


# ── desired_state ──────────────────────────────────────────────────────────────

def test_in_band_both_off():
    fc = [20.0, 21.0, 22.0, 20.5]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=4) == {"heater": "off", "cooler": "off"}


def test_below_band_heater_on():
    fc = [22.0, 18.5, 21.0, 20.0]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=4) == {"heater": "on", "cooler": "off"}


def test_above_band_cooler_on():
    fc = [21.0, 21.5, 24.1, 22.0]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=4) == {"heater": "off", "cooler": "on"}


def test_low_takes_precedence_over_high():
    # If a future point dips below low AND another rises above high, heater wins
    # because we check low first. Predictable behavior.
    fc = [18.0, 24.0]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=2) == {"heater": "on", "cooler": "off"}


def test_lookahead_clips_window():
    # Only the first 2 entries are considered. The cold dip at index 5 is ignored.
    fc = [22.0, 21.0, 21.0, 20.0, 19.5, 17.0]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=2) == {"heater": "off", "cooler": "off"}


def test_boundary_low_inclusive_in_band():
    # min == low is not below low, so we stay in band.
    fc = [19.0, 20.0, 21.0]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=3) == {"heater": "off", "cooler": "off"}


def test_boundary_high_inclusive_in_band():
    fc = [20.0, 23.0, 21.0]
    assert desired_state(fc, low=19.0, high=23.0, lookahead=3) == {"heater": "off", "cooler": "off"}


def test_invalid_lookahead_raises():
    with pytest.raises(ValueError):
        desired_state([20.0], low=19.0, high=23.0, lookahead=0)


def test_invalid_band_raises():
    with pytest.raises(ValueError):
        desired_state([20.0], low=23.0, high=19.0, lookahead=1)


def test_empty_forecast_raises():
    with pytest.raises(ValueError):
        desired_state([], low=19.0, high=23.0, lookahead=1)


# ── diff_state ─────────────────────────────────────────────────────────────────

def test_diff_returns_changes_only():
    desired = {"heater": "on", "cooler": "off"}
    last = {"heater": "off", "cooler": "off"}
    assert diff_state(desired, last) == {"heater": "on"}


def test_diff_empty_when_same():
    desired = {"heater": "off", "cooler": "off"}
    assert diff_state(desired, desired) == {}


def test_diff_returns_all_when_no_history():
    desired = {"heater": "on", "cooler": "off"}
    assert diff_state(desired, {}) == desired


def test_diff_returns_all_when_both_change():
    desired = {"heater": "on", "cooler": "off"}
    last = {"heater": "off", "cooler": "on"}
    assert diff_state(desired, last) == desired
