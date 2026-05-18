"""Authenticated HTTP client for the backend API.

The LSTM control loop runs as the 'lstm' service account in dashboard_users.
On startup it POSTs /auth/login to obtain a 24h JWT, then calls /api/v1/*
endpoints with that bearer token. The token is refreshed before it expires.
"""
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API_BASE_URL = os.environ.get("API_BASE_URL", "https://nginx")
API_CA_CERT = os.environ.get("API_CA_CERT", "/run/secrets/ca_cert")
LSTM_USER = os.environ.get("LSTM_USER", "lstm")
LSTM_PASS_FILE = os.environ.get("LSTM_PASS_FILE", "/run/secrets/dashboard_lstm_password")

# Refresh the JWT after this many seconds. Backend issues 24h tokens.
TOKEN_TTL_SECONDS = 23 * 60 * 60


class ApiClient:
    def __init__(self, base_url=API_BASE_URL, ca_cert=API_CA_CERT, user=LSTM_USER, pass_file=LSTM_PASS_FILE):
        self.base_url = base_url.rstrip("/")
        self.ca_cert = ca_cert if ca_cert and Path(ca_cert).exists() else False
        self.user = user
        self.pass_file = pass_file
        self._token = None
        self._token_acquired_at = 0.0

    def _password(self):
        return Path(self.pass_file).read_text().strip()

    def _login(self):
        r = requests.post(
            f"{self.base_url}/auth/login",
            json={"username": self.user, "password": self._password()},
            verify=self.ca_cert,
            timeout=10,
        )
        r.raise_for_status()
        self._token = r.json()["token"]
        self._token_acquired_at = time.time()

    def _auth_headers(self):
        if self._token is None or time.time() - self._token_acquired_at > TOKEN_TTL_SECONDS:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def get_sensor_data(self):
        r = requests.get(
            f"{self.base_url}/api/v1/sensor-data",
            headers=self._auth_headers(),
            verify=self.ca_cert,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def post_actuator_command(self, actuator_id, command, issued_by="machine"):
        r = requests.post(
            f"{self.base_url}/api/v1/actuator-command",
            json={"actuator_id": actuator_id, "command": command, "issued_by": issued_by},
            headers=self._auth_headers(),
            verify=self.ca_cert,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
