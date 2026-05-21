"""Authenticated HTTP client for the backend API.

The LSTM control loop authenticates as the Keycloak `lstm-client` confidential
client via the client-credentials flow. Tokens are cached and refreshed shortly
before they expire.
"""
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_BASE_URL = os.environ.get("API_BASE_URL", "https://www.lab.local")
API_CA_CERT = os.environ.get("API_CA_CERT", "/run/secrets/ca_cert")

KEYCLOAK_TOKEN_URL = os.environ.get(
    "KEYCLOAK_TOKEN_URL",
    "http://keycloak:8080/realms/iot/protocol/openid-connect/token",
)
LSTM_CLIENT_ID = os.environ.get("LSTM_CLIENT_ID", "lstm-client")
LSTM_CLIENT_SECRET_FILE = os.environ.get(
    "LSTM_CLIENT_SECRET_FILE", "/run/secrets/keycloak_lstm_secret"
)

# Refresh the access token this many seconds before its declared expiry,
# so a request that arrives right at the boundary still uses a valid token.
TOKEN_REFRESH_MARGIN_SECONDS = 30


class ApiClient:
    def __init__(
        self,
        base_url=API_BASE_URL,
        ca_cert=API_CA_CERT,
        token_url=KEYCLOAK_TOKEN_URL,
        client_id=LSTM_CLIENT_ID,
        client_secret_file=LSTM_CLIENT_SECRET_FILE,
    ):
        self.base_url = base_url.rstrip("/")
        self.ca_cert = ca_cert if ca_cert and Path(ca_cert).exists() else False
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret_file = client_secret_file
        self._token = None
        self._token_expires_at = 0.0

    def _client_secret(self):
        return Path(self.client_secret_file).read_text().strip()

    def _fetch_token(self):
        r = requests.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self._client_secret(),
            },
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        expires_in = int(body.get("expires_in", 300))
        self._token_expires_at = time.time() + expires_in

    def _auth_headers(self):
        if (
            self._token is None
            or time.time() >= self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS
        ):
            self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _force_refresh(self):
        self._token = None
        self._token_expires_at = 0.0

    def get_sensor_data(self):
        r = self._request_with_retry(
            "get",
            f"{self.base_url}/api/v1/sensor-data",
            timeout=30,
        )
        return r.json()

    def post_actuator_command(self, actuator_id, command, issued_by="machine"):
        r = self._request_with_retry(
            "post",
            f"{self.base_url}/api/v1/actuator-command",
            json={"actuator_id": actuator_id, "command": command, "issued_by": issued_by},
            timeout=10,
        )
        return r.json()

    def _request_with_retry(self, method, url, **kwargs):
        """Run a request with the current token. On 401 (token may have been
        revoked or rotated mid-flight), force a refresh and try once more."""
        kwargs.setdefault("verify", self.ca_cert)
        kwargs["headers"] = {**kwargs.get("headers", {}), **self._auth_headers()}
        r = requests.request(method, url, **kwargs)
        if r.status_code == 401:
            self._force_refresh()
            kwargs["headers"] = {**kwargs.get("headers", {}), **self._auth_headers()}
            r = requests.request(method, url, **kwargs)
        r.raise_for_status()
        return r
