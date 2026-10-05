"""Keep deploy/demo/fly.toml in sync with the Dockerfile it deploys.

fly.toml is hand-written, not generated, so nothing enforces that it still
points at the published image, runs it in --demo mode, or targets the port
the Dockerfile's EXPOSE/HEALTHCHECK actually use. This test fails the build
the moment either drifts.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FLY_TOML = REPO_ROOT / "deploy" / "demo" / "fly.toml"


def _load_fly_toml() -> dict:
    return tomllib.loads(FLY_TOML.read_text())


def test_build_image_is_published_buoy_image():
    config = _load_fly_toml()
    assert config["build"]["image"].startswith("ghcr.io/gfargo/buoy")


def test_process_runs_demo_mode():
    config = _load_fly_toml()
    assert "--demo" in config["processes"]["app"]


def test_http_service_targets_dockerfile_port():
    config = _load_fly_toml()
    assert config["http_service"]["internal_port"] == 8090


def test_health_check_uses_api_health_path():
    config = _load_fly_toml()
    checks = config["http_service"]["checks"]
    assert any(check["path"] == "/api/health" for check in checks)


def test_scales_to_zero_when_idle():
    config = _load_fly_toml()
    assert config["http_service"]["min_machines_running"] == 0


def test_node_name_is_set():
    config = _load_fly_toml()
    assert config["env"]["BUOY_NODE_NAME"]
