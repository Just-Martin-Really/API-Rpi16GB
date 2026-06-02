import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api_client
from api_client import ApiClient


@pytest.fixture
def secret_file(tmp_path):
    f = tmp_path / "secret.txt"
    f.write_text("test-secret-value\n")
    return f


def _token_response(access_token="tok-1", expires_in=300):
    r = MagicMock()
    r.json.return_value = {"access_token": access_token, "expires_in": expires_in}
    r.status_code = 200
    r.raise_for_status = MagicMock()
    return r


def _api_response(payload, status_code=200):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = status_code

    def maybe_raise():
        if status_code >= 400:
            raise Exception(f"HTTP {status_code}")

    r.raise_for_status = MagicMock(side_effect=maybe_raise)
    return r


def _build_client(secret_file, base_url="https://www.lab.local", token_url="http://kc/realms/iot/token"):
    return ApiClient(
        base_url=base_url,
        ca_cert=None,
        token_url=token_url,
        client_id="lstm-client",
        client_secret_file=str(secret_file),
    )


def test_missing_ca_cert_path_raises(secret_file):
    # Regression for the silent verify=False fallback. A bogus path used to
    # produce verify=False on every call; it now refuses to construct.
    import pytest
    with pytest.raises(RuntimeError, match="CA cert not found"):
        ApiClient(
            base_url="https://www.lab.local",
            ca_cert="/tmp/definitely-not-here.crt",
            token_url="http://kc/realms/iot/token",
            client_id="lstm-client",
            client_secret_file=str(secret_file),
        )


def test_first_call_fetches_token_then_calls_api(secret_file):
    client = _build_client(secret_file)
    with patch.object(api_client.requests, "post") as mock_post, \
         patch.object(api_client.requests, "request") as mock_request:
        mock_post.return_value = _token_response()
        mock_request.return_value = _api_response([{"id": 1}])

        result = client.get_sensor_data()

    assert result == [{"id": 1}]
    mock_post.assert_called_once()
    token_args = mock_post.call_args
    assert token_args.args[0] == "http://kc/realms/iot/token"
    assert token_args.kwargs["data"]["grant_type"] == "client_credentials"
    assert token_args.kwargs["data"]["client_id"] == "lstm-client"
    assert token_args.kwargs["data"]["client_secret"] == "test-secret-value"

    mock_request.assert_called_once()
    api_args = mock_request.call_args
    assert api_args.args == ("get", "https://www.lab.local/api/v1/sensor-data")
    assert api_args.kwargs["headers"]["Authorization"] == "Bearer tok-1"


def test_token_is_cached_within_ttl(secret_file):
    client = _build_client(secret_file)
    with patch.object(api_client.requests, "post") as mock_post, \
         patch.object(api_client.requests, "request") as mock_request:
        mock_post.return_value = _token_response()
        mock_request.return_value = _api_response([])

        client.get_sensor_data()
        client.get_sensor_data()
        client.get_sensor_data()

    assert mock_post.call_count == 1
    assert mock_request.call_count == 3


def test_expired_token_triggers_refresh(secret_file):
    client = _build_client(secret_file)
    with patch.object(api_client.requests, "post") as mock_post, \
         patch.object(api_client.requests, "request") as mock_request:
        mock_post.side_effect = [
            _token_response(access_token="tok-1", expires_in=10),
            _token_response(access_token="tok-2", expires_in=300),
        ]
        mock_request.return_value = _api_response([])

        client.get_sensor_data()
        # Simulate the token having been issued long enough ago to need refresh.
        client._token_expires_at = time.time() - 1
        client.get_sensor_data()

    assert mock_post.call_count == 2
    assert mock_request.call_args_list[-1].kwargs["headers"]["Authorization"] == "Bearer tok-2"


def test_401_forces_refresh_and_retries_once(secret_file):
    client = _build_client(secret_file)

    api_responses = [_api_response([], status_code=401), _api_response([{"ok": True}])]

    with patch.object(api_client.requests, "post") as mock_post, \
         patch.object(api_client.requests, "request") as mock_request:
        mock_post.side_effect = [
            _token_response(access_token="tok-1"),
            _token_response(access_token="tok-2"),
        ]
        mock_request.side_effect = api_responses

        result = client.get_sensor_data()

    assert result == [{"ok": True}]
    assert mock_post.call_count == 2
    assert mock_request.call_count == 2
    assert mock_request.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer tok-2"


def test_post_actuator_command_sends_payload(secret_file):
    client = _build_client(secret_file)
    with patch.object(api_client.requests, "post") as mock_post, \
         patch.object(api_client.requests, "request") as mock_request:
        mock_post.return_value = _token_response()
        mock_request.return_value = _api_response({"queued": True})

        result = client.post_actuator_command("heater01", "on", issued_by="machine")

    assert result == {"queued": True}
    api_args = mock_request.call_args
    assert api_args.args == ("post", "https://www.lab.local/api/v1/actuator-command")
    assert api_args.kwargs["json"] == {
        "actuator_id": "heater01",
        "command": "on",
        "issued_by": "machine",
    }
    assert api_args.kwargs["headers"]["Authorization"].startswith("Bearer ")
