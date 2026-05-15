"""
test_anomaly.py — Tests for anomaly.py

Coverage:
  - check_sanity returns True for 20 °C (in range), False for -100 °C and
    200 °C (physically impossible)
  - check_stuck flags exactly STUCK_THRESHOLD identical values in a row
    and does NOT flag fewer
  - check_timeout returns True when (now - last_ts) > TIMEOUT_S, False on
    the boundary and below
  - check_residual fires only when |actual - predicted| > threshold;
    boundary case (==) does not trigger
  - classify_anomaly returns the correct AlertType in priority order:
    PHYSICAL_OUT_OF_RANGE > TIMEOUT > STUCK > RESIDUAL_SPIKE
  - classify_anomaly returns None when all checks pass on a normal reading
"""

from collections import deque

import anomaly
import config


def make_history(values):
    """deque mirroring controller_lstm's stuck_history."""
    return deque(values, maxlen=config.STUCK_THRESHOLD)


# ---------------------------------------------------------------------- sanity
def test_check_sanity_normal():
    assert anomaly.check_sanity(20.0) is True


def test_check_sanity_too_cold():
    assert anomaly.check_sanity(-100.0) is False


def test_check_sanity_too_hot():
    assert anomaly.check_sanity(200.0) is False


# ----------------------------------------------------------------------- stuck
def test_check_stuck_triggers_at_threshold():
    history = make_history([20.0] * config.STUCK_THRESHOLD)
    assert anomaly.check_stuck(history) is True


def test_check_stuck_below_threshold():
    history = make_history([20.0] * (config.STUCK_THRESHOLD - 1))
    assert anomaly.check_stuck(history) is False


def test_check_stuck_mixed_values():
    n = config.STUCK_THRESHOLD
    history = make_history([20.0] * (n - 1) + [20.5])
    assert anomaly.check_stuck(history) is False


# --------------------------------------------------------------------- timeout
def test_check_timeout_exceeded():
    last = 0.0
    now = config.TIMEOUT_SECONDS + 1
    assert anomaly.check_timeout(last, now) is True


def test_check_timeout_boundary():
    last = 0.0
    now = float(config.TIMEOUT_SECONDS)
    assert anomaly.check_timeout(last, now) is False


# -------------------------------------------------------------------- residual
def test_check_residual_above_threshold():
    assert anomaly.check_residual(actual=21.0, predicted=20.0, threshold=0.5) is True


def test_check_residual_at_threshold():
    # Strict inequality: equal-to-threshold must NOT fire.
    assert anomaly.check_residual(actual=20.5, predicted=20.0, threshold=0.5) is False


def test_check_residual_below_threshold():
    assert anomaly.check_residual(actual=20.1, predicted=20.0, threshold=0.5) is False


# --------------------------------------------------------- classify_anomaly
def test_classify_returns_none_when_normal():
    result = anomaly.classify_anomaly(
        value=20.0,
        predicted=20.05,
        history=make_history([20.0, 20.1, 20.2]),
        last_ts=0.0,
        residual_threshold=1.0,
        now=10.0,
    )
    assert result is None


def test_classify_physical_has_highest_priority():
    # Value is also stuck and would trigger residual, but physical wins.
    result = anomaly.classify_anomaly(
        value=999.0,
        predicted=20.0,
        history=make_history([999.0] * config.STUCK_THRESHOLD),
        last_ts=0.0,
        residual_threshold=0.1,
        now=config.TIMEOUT_SECONDS + 100,
    )
    assert result == anomaly.AlertType.PHYSICAL_OUT_OF_RANGE


def test_classify_timeout_beats_stuck_and_residual():
    result = anomaly.classify_anomaly(
        value=20.0,
        predicted=20.0,
        history=make_history([20.0] * config.STUCK_THRESHOLD),
        last_ts=0.0,
        residual_threshold=1.0,
        now=config.TIMEOUT_SECONDS + 100,
    )
    assert result == anomaly.AlertType.TIMEOUT


def test_classify_residual_spike():
    result = anomaly.classify_anomaly(
        value=25.0,
        predicted=20.0,
        history=make_history([20.0, 20.1, 20.2]),
        last_ts=0.0,
        residual_threshold=1.0,
        now=10.0,
    )
    assert result == anomaly.AlertType.RESIDUAL_SPIKE