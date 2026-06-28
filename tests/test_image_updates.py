"""Tests for the image update checker."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from buoy.collectors.image_updates import (
    ImageUpdateChecker,
    _parse_ref,
)
from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig
from buoy.demo import DemoImageUpdateChecker


def _make_config():
    cfg = BuoyConfig()
    cfg.node = NodeConfig(name="test")
    cfg.features = FeaturesConfig()
    return cfg


# ── _parse_ref ──────────────────────────────────────────────────────────────


class TestParseRef:
    def test_docker_hub_implicit_library(self):
        r = _parse_ref("nginx:latest")
        assert r == {"registry": "registry-1.docker.io", "repo": "library/nginx", "tag": "latest"}

    def test_docker_hub_user_repo(self):
        r = _parse_ref("myuser/myapp:1.2.3")
        assert r == {"registry": "registry-1.docker.io", "repo": "myuser/myapp", "tag": "1.2.3"}

    def test_ghcr_with_tag(self):
        r = _parse_ref("ghcr.io/gfargo/buoy:latest")
        assert r == {"registry": "ghcr.io", "repo": "gfargo/buoy", "tag": "latest"}

    def test_implicit_latest_tag(self):
        r = _parse_ref("redis")
        assert r is not None
        assert r["tag"] == "latest"
        assert r["repo"] == "library/redis"

    def test_private_registry_with_port(self):
        r = _parse_ref("registry:5000/myapp:1.0")
        assert r == {"registry": "registry:5000", "repo": "myapp", "tag": "1.0"}

    def test_digest_pinned_skipped(self):
        r = _parse_ref("myapp@sha256:abc123")
        assert r is None

    def test_none_image_skipped(self):
        r = _parse_ref("<none>")
        assert r is None

    def test_empty_image_skipped(self):
        r = _parse_ref("")
        assert r is None

    def test_sha256_prefix_skipped(self):
        r = _parse_ref("sha256:abc123def456")
        assert r is None


# ── Status comparison logic ─────────────────────────────────────────────────


class TestStatusComparison:
    @pytest.mark.asyncio
    async def test_up_to_date(self):
        cfg = _make_config()
        checker = ImageUpdateChecker(cfg)

        digest = "abc123def456"
        with (
            patch(
                "buoy.collectors.image_updates._run",
                new=AsyncMock(return_value=(0, '["{}/repo@sha256:abc123def456"]'.format("reg"))),
            ),
            patch(
                "buoy.collectors.image_updates._remote_digest",
                new=AsyncMock(return_value=digest),
            ),
            patch.object(
                checker,
                "_docker_image_refs",
                new=AsyncMock(return_value=[{"container": "myapp", "image": "myapp:latest"}]),
            ),
        ):
            result = await checker.check_all()

        assert result["myapp"]["status"] == "up_to_date"

    @pytest.mark.asyncio
    async def test_update_available(self):
        cfg = _make_config()
        checker = ImageUpdateChecker(cfg)

        with (
            patch(
                "buoy.collectors.image_updates._local_digest",
                new=AsyncMock(return_value="olddigest"),
            ),
            patch(
                "buoy.collectors.image_updates._remote_digest",
                new=AsyncMock(return_value="newdigest"),
            ),
            patch.object(
                checker,
                "_docker_image_refs",
                new=AsyncMock(return_value=[{"container": "myapp", "image": "myapp:latest"}]),
            ),
        ):
            result = await checker.check_all()

        assert result["myapp"]["status"] == "update_available"

    @pytest.mark.asyncio
    async def test_unknown_when_remote_fails(self):
        cfg = _make_config()
        checker = ImageUpdateChecker(cfg)

        with (
            patch(
                "buoy.collectors.image_updates._local_digest",
                new=AsyncMock(return_value="somedigest"),
            ),
            patch(
                "buoy.collectors.image_updates._remote_digest",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                checker,
                "_docker_image_refs",
                new=AsyncMock(return_value=[{"container": "myapp", "image": "myapp:latest"}]),
            ),
        ):
            result = await checker.check_all()

        assert result["myapp"]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_skipped_for_digest_pinned(self):
        cfg = _make_config()
        checker = ImageUpdateChecker(cfg)

        with patch.object(
            checker,
            "_docker_image_refs",
            new=AsyncMock(
                return_value=[{"container": "myapp", "image": "myapp@sha256:abc123"}]
            ),
        ):
            result = await checker.check_all()

        assert result["myapp"]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_exception(self):
        """If _remote_digest raises, status should be unknown (no crash)."""
        cfg = _make_config()
        checker = ImageUpdateChecker(cfg)

        with (
            patch(
                "buoy.collectors.image_updates._local_digest",
                new=AsyncMock(return_value="somedigest"),
            ),
            patch(
                "buoy.collectors.image_updates._remote_digest",
                new=AsyncMock(side_effect=RuntimeError("network failure")),
            ),
            patch.object(
                checker,
                "_docker_image_refs",
                new=AsyncMock(return_value=[{"container": "myapp", "image": "myapp:latest"}]),
            ),
        ):
            # Should not raise
            result = await checker.check_all()

        # gather with return_exceptions=True catches the error → unknown
        assert result["myapp"]["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_when_no_containers(self):
        cfg = _make_config()
        checker = ImageUpdateChecker(cfg)

        with patch.object(
            checker,
            "_docker_image_refs",
            new=AsyncMock(return_value=[]),
        ):
            result = await checker.check_all()

        assert result == {}


# ── DemoImageUpdateChecker ──────────────────────────────────────────────────


class TestDemoImageUpdateChecker:
    @pytest.mark.asyncio
    async def test_returns_all_demo_containers(self):
        from buoy.demo import _DEMO_CONTAINERS

        cfg = _make_config()
        checker = DemoImageUpdateChecker(cfg)
        result = await checker.check_all()

        assert len(result) == len(_DEMO_CONTAINERS)

    @pytest.mark.asyncio
    async def test_status_values_are_valid(self):
        cfg = _make_config()
        checker = DemoImageUpdateChecker(cfg)
        result = await checker.check_all()

        valid = {"up_to_date", "update_available", "unknown", "skipped"}
        for name, entry in result.items():
            assert entry["status"] in valid, f"{name} has invalid status {entry['status']}"

    @pytest.mark.asyncio
    async def test_entry_shape(self):
        cfg = _make_config()
        checker = DemoImageUpdateChecker(cfg)
        result = await checker.check_all()

        for name, entry in result.items():
            assert "status" in entry
            assert "image" in entry
            assert "checked_at" in entry
            assert isinstance(entry["checked_at"], float)

    @pytest.mark.asyncio
    async def test_known_statuses(self):
        cfg = _make_config()
        checker = DemoImageUpdateChecker(cfg)
        result = await checker.check_all()

        assert result["grafana"]["status"] == "update_available"
        assert result["postgres"]["status"] == "up_to_date"
        assert result["redis"]["status"] == "unknown"
