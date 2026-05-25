import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import data_source
from data_source import _simulate


def test_simulate_shape():
    out = _simulate()
    assert out.shape == (data_source.SIM_MINUTES, 1)


def test_simulate_is_deterministic():
    # Same seed in module state should produce identical output across calls.
    a = _simulate()
    b = _simulate()
    np.testing.assert_array_equal(a, b)


def test_simulate_values_in_plausible_range():
    # Two-sine-wave room-temperature simulation: daily mean ~20, swing ~2C,
    # plus noise. Should never escape a reasonable indoor band.
    out = _simulate()
    assert out.min() > 10.0
    assert out.max() < 30.0


def test_from_api_filters_by_unit_and_sorts():
    # Fake ApiClient returning mixed-unit rows in non-chronological order.
    # _from_api should keep only unit=="C" and return them sorted oldest first.
    fake_rows = [
        {"id": 3, "sensor_id": "sensor01", "value": 22.0, "unit": "C",
         "recorded_at": "2026-05-20T10:00:00Z"},
        {"id": 1, "sensor_id": "sensor01", "value": 50.0, "unit": "%",
         "recorded_at": "2026-05-20T09:00:00Z"},
        {"id": 2, "sensor_id": "sensor01", "value": 21.5, "unit": "C",
         "recorded_at": "2026-05-20T09:30:00Z"},
    ]

    class FakeApiClient:
        def get_sensor_data(self):
            return fake_rows

    with patch.object(data_source, "DAYS", 365):  # keep everything
        with patch.dict("sys.modules"):
            # Patch the lazy import inside _from_api.
            import api_client
            with patch.object(api_client, "ApiClient", FakeApiClient):
                out = data_source._from_api()

    assert out.shape == (2, 1)
    # Sorted chronologically: 21.5 (09:30) before 22.0 (10:00).
    np.testing.assert_allclose(out.flatten(), [21.5, 22.0])


def test_from_api_applies_days_cutoff():
    # Two C rows two weeks apart; DAYS=7 should drop the older one because
    # the cutoff is max(recorded_at) - 7 days.
    fake_rows = [
        {"id": 1, "sensor_id": "sensor01", "value": 18.0, "unit": "C",
         "recorded_at": "2026-05-01T12:00:00Z"},
        {"id": 2, "sensor_id": "sensor01", "value": 24.0, "unit": "C",
         "recorded_at": "2026-05-20T12:00:00Z"},
    ]

    class FakeApiClient:
        def get_sensor_data(self):
            return fake_rows

    with patch.object(data_source, "DAYS", 7):
        import api_client
        with patch.object(api_client, "ApiClient", FakeApiClient):
            out = data_source._from_api()

    assert out.shape == (1, 1)
    np.testing.assert_allclose(out.flatten(), [24.0])
