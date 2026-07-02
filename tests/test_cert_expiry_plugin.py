"""Tests for the TLS certificate expiry plugin."""

from __future__ import annotations

import shutil
import ssl
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures" / "certs"


def _cert_epoch(name: str) -> float:
    info = ssl._ssl._test_decode_cert(str(_FIXTURES / f"{name}.crt"))  # noqa: SLF001
    return ssl.cert_time_to_seconds(info["notAfter"])


def _make_plugin(cert_dir: str = "", warn_days: int = 30, critical_days: int = 7):
    from buoy.plugins.builtin.cert_expiry import CertExpiryPlugin

    plugin = CertExpiryPlugin()
    cfg: dict = {"warn_days": warn_days, "critical_days": critical_days}
    if cert_dir:
        cfg["cert_dir"] = cert_dir
    plugin.configure(cfg)
    return plugin


# ---------------------------------------------------------------------------
# Unit tests for _days_remaining
# ---------------------------------------------------------------------------


class TestDaysRemaining:
    def test_future(self):
        from buoy.plugins.builtin.cert_expiry import _days_remaining

        now = 1_000_000.0
        assert _days_remaining(now + 60 * 86400, now) == 60

    def test_past_expired(self):
        from buoy.plugins.builtin.cert_expiry import _days_remaining

        now = 1_000_000.0
        assert _days_remaining(now - 86400, now) == -1

    def test_boundary_warn(self):
        from buoy.plugins.builtin.cert_expiry import _days_remaining

        now = 0.0
        assert _days_remaining(30 * 86400, now) == 30

    def test_boundary_critical(self):
        from buoy.plugins.builtin.cert_expiry import _days_remaining

        now = 0.0
        assert _days_remaining(7 * 86400, now) == 7


# ---------------------------------------------------------------------------
# collect() — no/missing directory
# ---------------------------------------------------------------------------


class TestCertExpiryDisabled:
    @pytest.mark.asyncio
    async def test_no_cert_dir_returns_disabled(self):
        plugin = _make_plugin(cert_dir="/nonexistent/path/certs")
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_default_cert_dir_missing_returns_disabled(self):
        # Default /etc/caddy/certs likely absent in CI
        plugin = _make_plugin()
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_empty_dir_returns_disabled(self, tmp_path):
        plugin = _make_plugin(cert_dir=str(tmp_path))
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_key_files_ignored(self, tmp_path):
        (tmp_path / "server.key").write_text("not a cert")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_unreadable_cert_skipped(self, tmp_path):
        (tmp_path / "bad.crt").write_text("not valid PEM")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        result = await plugin.collect()
        assert result.status == "disabled"


# ---------------------------------------------------------------------------
# collect() — with fixture certs, patching time.time()
# ---------------------------------------------------------------------------


class TestCertExpiryCollect:
    """Tests using real PEM fixtures generated at build time.

    We patch time.time() to a fixed 'now' so cert bucket tests are deterministic
    regardless of when the fixtures were generated.
    """

    def _now_for_cert(self, name: str, days_before_expiry: int) -> float:
        """Return a fake 'now' such that the cert expires in exactly *days_before_expiry* days."""
        epoch = _cert_epoch(name)
        return epoch - days_before_expiry * 86400

    @pytest.mark.asyncio
    async def test_green_cert(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_60d.crt", tmp_path / "cert_60d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        fake_now = self._now_for_cert("cert_60d", 60)
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert result.status == "ok"
        certs = result.detail["certs"]
        assert len(certs) == 1
        assert certs[0]["status"] == "ok"
        assert certs[0]["days"] == 60

    @pytest.mark.asyncio
    async def test_amber_cert(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_20d.crt", tmp_path / "cert_20d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        fake_now = self._now_for_cert("cert_20d", 20)
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert result.status == "warn"
        certs = result.detail["certs"]
        assert certs[0]["status"] == "warn"
        assert certs[0]["days"] == 20

    @pytest.mark.asyncio
    async def test_red_cert(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_3d.crt", tmp_path / "cert_3d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        fake_now = self._now_for_cert("cert_3d", 3)
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert result.status == "error"
        certs = result.detail["certs"]
        assert certs[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_expired_cert(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_3d.crt", tmp_path / "cert_3d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        # Set now *after* expiry
        fake_now = _cert_epoch("cert_3d") + 86400
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert result.status == "error"
        assert result.detail["certs"][0]["days"] < 0

    @pytest.mark.asyncio
    async def test_worst_status_wins(self, tmp_path):
        """Multiple certs: overall status = worst individual status."""
        shutil.copy(_FIXTURES / "cert_60d.crt", tmp_path / "cert_60d.crt")
        shutil.copy(_FIXTURES / "cert_3d.crt", tmp_path / "cert_3d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        # Make cert_60d green (60d left) and cert_3d red (3d left)
        epoch_60 = _cert_epoch("cert_60d")
        fake_now = epoch_60 - 60 * 86400  # cert_60d has 60d; cert_3d will have < 7d
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        statuses = {c["name"]: c["status"] for c in result.detail["certs"]}
        assert statuses["cert_60d"] == "ok"
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_summary_single_cert(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_60d.crt", tmp_path / "cert_60d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        fake_now = self._now_for_cert("cert_60d", 60)
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert "cert_60d" in result.summary
        assert "60d" in result.summary

    @pytest.mark.asyncio
    async def test_summary_multiple_certs(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_60d.crt", tmp_path / "cert_60d.crt")
        shutil.copy(_FIXTURES / "cert_20d.crt", tmp_path / "cert_20d.crt")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        epoch_20 = _cert_epoch("cert_20d")
        fake_now = epoch_20 - 20 * 86400
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert "2 certs" in result.summary
        assert "soonest" in result.summary

    @pytest.mark.asyncio
    async def test_pem_extension_recognized(self, tmp_path):
        shutil.copy(_FIXTURES / "cert_60d.crt", tmp_path / "mysite.pem")
        plugin = _make_plugin(cert_dir=str(tmp_path))
        fake_now = self._now_for_cert("cert_60d", 60)
        with patch("time.time", return_value=fake_now):
            result = await plugin.collect()
        assert result.status != "disabled"
        assert result.detail["certs"][0]["name"] == "mysite"


# ---------------------------------------------------------------------------
# frontend_js
# ---------------------------------------------------------------------------


class TestCertExpiryFrontendJs:
    def test_has_frontend_js(self):
        plugin = _make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_cert_expiry" in js
