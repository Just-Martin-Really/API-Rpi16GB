"""
HTTP integration tests for the backend API.

Run from the 16GB Pi (must be in the 192.168.50.0/24 subnet):
    pip3 install requests pytest
    pytest tests/

Environment variables:
    BACKEND_URL     Base URL of the backend (default: https://192.168.50.92)
    DASHBOARD_USER  Dashboard username (default: admin)
    DASHBOARD_PASS  Dashboard password (default: changeme)
    CA_CERT         Path to CA cert file for TLS verification (default: disabled)
"""

import os

import pytest
import requests
import urllib3

BASE_URL = os.environ.get("BACKEND_URL", "https://192.168.50.92")
USER = os.environ.get("DASHBOARD_USER", "admin")
PASS = os.environ.get("DASHBOARD_PASS", "changeme")
CA_CERT = os.environ.get("CA_CERT", False)

if not CA_CERT:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _get(path, **kwargs):
    return requests.get(f"{BASE_URL}{path}", verify=CA_CERT, **kwargs)


def _post(path, **kwargs):
    return requests.post(f"{BASE_URL}{path}", verify=CA_CERT, **kwargs)


def _delete(path, **kwargs):
    return requests.delete(f"{BASE_URL}{path}", verify=CA_CERT, **kwargs)


@pytest.fixture(scope="session")
def token():
    r = _post("/auth/login", json={"username": USER, "password": PASS})
    assert r.status_code == 200, f"Login failed: {r.text}"
    return r.json()["token"]


@pytest.fixture
def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── /health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_ok(self):
        r = _get("/health")
        assert r.status_code == 200


# ── /auth/login ────────────────────────────────────────────────────────────────

class TestLogin:
    def test_valid_credentials_returns_token(self):
        r = _post("/auth/login", json={"username": USER, "password": PASS})
        assert r.status_code == 200
        body = r.json()
        assert "token" in body
        assert len(body["token"].split(".")) == 3

    def test_wrong_password(self):
        r = _post("/auth/login", json={"username": USER, "password": "wrongpassword"})
        assert r.status_code == 401

    def test_unknown_user(self):
        r = _post("/auth/login", json={"username": "nobody", "password": "x"})
        assert r.status_code == 401

    def test_invalid_json(self):
        r = _post(
            "/auth/login",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400


# ── /api/v1/sensor-data ────────────────────────────────────────────────────────

class TestSensorData:
    def test_get_without_token_rejected(self):
        r = _get("/api/v1/sensor-data")
        assert r.status_code == 401

    def test_get_with_invalid_token_rejected(self):
        r = _get(
            "/api/v1/sensor-data",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert r.status_code == 401

    def test_get_returns_json_array(self, auth):
        r = _get("/api/v1/sensor-data", headers=auth)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_post_creates_reading(self, auth):
        r = _post(
            "/api/v1/sensor-data",
            json={"sensor_id": "test_sensor", "value": 42.0, "unit": "C"},
            headers=auth,
        )
        assert r.status_code == 201
        assert r.json().get("created") is True

    def test_post_without_token_rejected(self):
        r = _post(
            "/api/v1/sensor-data",
            json={"sensor_id": "test_sensor", "value": 1.0, "unit": "C"},
        )
        assert r.status_code == 401

    def test_post_invalid_json(self, auth):
        r = _post(
            "/api/v1/sensor-data",
            data="not json",
            headers={**auth, "Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_wrong_method_returns_405(self, auth):
        r = _delete("/api/v1/sensor-data", headers=auth)
        assert r.status_code == 405


# ── routing ────────────────────────────────────────────────────────────────────

class TestRouting:
    def test_unknown_route_returns_404(self):
        r = _get("/nonexistent")
        assert r.status_code == 404
