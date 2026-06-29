"""Tests for the buoy.server._factory() reload-mode entry point."""

import os

import pytest
from starlette.applications import Starlette


class TestFactory:
    def test_returns_starlette_app(self, monkeypatch):
        monkeypatch.delenv("BUOY_CONFIG", raising=False)
        monkeypatch.delenv("BUOY_DEMO", raising=False)

        from buoy.server import _factory

        app = _factory()
        assert isinstance(app, Starlette)

    def test_demo_env_var(self, monkeypatch):
        monkeypatch.delenv("BUOY_CONFIG", raising=False)
        monkeypatch.setenv("BUOY_DEMO", "1")

        from buoy.server import _factory

        app = _factory()
        assert isinstance(app, Starlette)

    def test_config_env_var_overrides_default(self, monkeypatch, tmp_path):
        cfg = tmp_path / "buoy.yaml"
        cfg.write_text("node:\n  name: test-node\n")

        monkeypatch.setenv("BUOY_CONFIG", str(cfg))
        monkeypatch.setenv("BUOY_DEMO", "0")

        from buoy.server import _factory

        app = _factory()
        assert isinstance(app, Starlette)

    def test_empty_config_env_var_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv("BUOY_CONFIG", "")
        monkeypatch.setenv("BUOY_DEMO", "0")

        from buoy.server import _factory

        app = _factory()
        assert isinstance(app, Starlette)
