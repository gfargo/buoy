"""HTTP authentication challenges must match the configured auth mode."""

import pytest
from starlette.testclient import TestClient

from buoy.auth import _rate_limit
from buoy.config import _build_config
from buoy.server import create_app


@pytest.fixture(autouse=True)
def clear_rate_limit():
    _rate_limit.clear()
    yield
    _rate_limit.clear()


@pytest.mark.parametrize(
    ("raw_auth", "expected_challenge"),
    [
        (
            {"enabled": True, "type": "token", "token": "secret-token"},
            'Bearer realm="buoy"',
        ),
        (
            {
                "enabled": True,
                "type": "basic",
                "username": "admin",
                "password": "secret-password",
            },
            'Basic realm="buoy", charset="UTF-8"',
        ),
    ],
)
def test_protected_endpoint_uses_mode_correct_challenge(raw_auth, expected_challenge):
    app = create_app(_build_config({"auth": raw_auth}))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/container/example")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == expected_challenge
