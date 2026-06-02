import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def silence_logs(capsys):
    """control_loop.iteration prints a few status lines per call; tests
    don't care about them and pytest's capsys default fixture already
    captures them. This fixture exists purely to make that explicit."""
    yield


def _make_iteration_args(forecast_values):
    """Build the (model, mean, std, client, last_sent, dry_run) tuple
    iteration() expects, with stubs that produce the requested forecast.

    forecast_values is the array unscale() should ultimately return.
    """
    fc = np.asarray(forecast_values, dtype=np.float32)

    import control_loop

    model = MagicMock(name="keras_model")
    client = MagicMock(name="api_client")

    # Patch the helpers iteration() imports at module top.
    patches = [
        patch.object(control_loop, "latest_window",
                     return_value=np.full((control_loop.LOOKAHEAD, 1), 20.0, dtype=np.float32)),
        patch.object(control_loop, "scale", side_effect=lambda x, m, s: x),
        patch.object(control_loop, "unscale", side_effect=lambda x, m, s: x),
        patch.object(control_loop, "run_forecast", return_value=fc),
    ]
    return model, client, patches


STEADY_IN_BAND = {"heater": "off", "cooler": "off"}


def _run_iteration(forecast_values, last_sent, dry_run=False):
    import control_loop

    model, client, patches = _make_iteration_args(forecast_values)
    for p in patches:
        p.start()
    try:
        out = control_loop.iteration(model, 0.0, 1.0, client, last_sent, dry_run=dry_run)
    finally:
        for p in patches:
            p.stop()
    return out, client


def test_iteration_in_band_with_steady_state_emits_nothing():
    # Forecast inside the band, last_sent already says off/off → no POST.
    import control_loop
    out, client = _run_iteration([21.0] * control_loop.LOOKAHEAD, STEADY_IN_BAND)
    assert out == STEADY_IN_BAND
    client.post_actuator_command.assert_not_called()


def test_iteration_below_band_turns_heater_on():
    # Min predicted temp 17, below TARGET_LOW=19 → heater on.
    import control_loop
    out, client = _run_iteration([17.0] * control_loop.LOOKAHEAD, STEADY_IN_BAND)
    assert out["heater"] == "on"
    assert out["cooler"] == "off"
    client.post_actuator_command.assert_called_once()
    args, kwargs = client.post_actuator_command.call_args
    assert args[0] == control_loop.HEATER_ID
    assert args[1] == "HEAT_ON"
    assert kwargs.get("issued_by") == "machine"


def test_iteration_above_band_turns_cooler_on():
    import control_loop
    out, client = _run_iteration([26.0] * control_loop.LOOKAHEAD, STEADY_IN_BAND)
    assert out["cooler"] == "on"
    assert out["heater"] == "off"
    client.post_actuator_command.assert_called_once()
    args, _ = client.post_actuator_command.call_args
    assert args[0] == control_loop.COOLER_ID
    assert args[1] == "FAN_ON"


def test_iteration_dedupes_repeated_state():
    # Below band twice in a row. First call sends heater=on; second call
    # sees last_sent already at heater=on and emits nothing.
    import control_loop
    first, client = _run_iteration([17.0] * control_loop.LOOKAHEAD, STEADY_IN_BAND)
    assert first["heater"] == "on"
    second, client2 = _run_iteration([17.0] * control_loop.LOOKAHEAD, first)
    assert second == first
    client2.post_actuator_command.assert_not_called()


def test_iteration_dry_run_does_not_post():
    import control_loop
    out, client = _run_iteration([17.0] * control_loop.LOOKAHEAD, STEADY_IN_BAND, dry_run=True)
    assert out["heater"] == "on"
    client.post_actuator_command.assert_not_called()
