"""Public auth metadata must bootstrap the UI without exposing credentials."""

import pytest
from starlette.testclient import TestClient

from buoy.config import _build_config
from buoy.server import create_app


@pytest.mark.parametrize(
    ("raw_auth", "expected"),
    [
        (
            {
                "enabled": False,
                "type": "basic",
                "token": "disabled-token",
                "username": "disabled-user",
                "password": "disabled-password",
            },
            {"enabled": False, "type": None},
        ),
        (
            {"enabled": True, "type": "token", "token": "secret-token"},
            {"enabled": True, "type": "token"},
        ),
        (
            {
                "enabled": True,
                "type": "basic",
                "token": "debug-token",
                "username": "admin",
                "password": "secret-password",
            },
            {"enabled": True, "type": "basic"},
        ),
    ],
)
def test_public_config_exposes_only_safe_auth_metadata(raw_auth, expected):
    config = _build_config({"auth": raw_auth})
    app = create_app(config)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["auth"] == expected
    assert set(response.json()["auth"]) == {"enabled", "type"}
    for secret in {
        "disabled-token",
        "disabled-user",
        "disabled-password",
        "secret-token",
        "debug-token",
        "admin",
        "secret-password",
    }:
        assert secret not in response.text
