"""Tests for the `buoy plugin` CLI (list/info/install)."""

from dataclasses import dataclass, field
from unittest.mock import patch

from buoy.config import BuoyConfig, NetworkConfig, NodeConfig
from buoy.plugins import cli as plugin_cli


@dataclass
class PluginEntry:
    enabled: bool = False
    settings: dict = field(default_factory=dict)


@dataclass
class PluginsConfig:
    enabled: bool = True
    directory: str = "/plugins"
    builtin: dict = field(default_factory=dict)


def _make_config(builtin=None):
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.plugins = PluginsConfig(builtin=builtin or {})
    return config


class TestCmdList:
    def test_lists_discovered_plugins(self, capsys):
        config = _make_config(builtin={"github": PluginEntry(enabled=True)})
        rc = plugin_cli.cmd_list(config)
        out = capsys.readouterr().out

        assert rc == 0
        assert "github" in out
        assert "builtin" in out
        assert "yes" in out  # github is enabled

    def test_empty_when_no_plugins_discovered(self, capsys):
        config = _make_config(builtin={})
        with patch(
            "buoy.plugins.loader.PluginManager.discover_all",
            return_value=[],
        ):
            rc = plugin_cli.cmd_list(config)
        out = capsys.readouterr().out

        assert rc == 0
        assert "No plugins discovered" in out


class TestCmdInfo:
    def test_prints_manifest_for_known_plugin(self, capsys):
        config = _make_config(builtin={"github": PluginEntry(enabled=True, settings={})})
        rc = plugin_cli.cmd_info(config, "github")
        out = capsys.readouterr().out

        assert rc == 0
        assert "id:               github" in out
        assert "refresh_interval: 300" in out
        assert "token" in out

    def test_unknown_plugin_id_returns_error(self, capsys):
        config = _make_config(builtin={})
        rc = plugin_cli.cmd_info(config, "does_not_exist")
        err = capsys.readouterr().err

        assert rc == 1
        assert "does_not_exist" in err


class TestCmdInstall:
    def test_runs_pip_install_and_reports_new_plugin(self, capsys):
        config = _make_config(builtin={})

        discover_results = [
            [],  # before install
            [{"id": "weather", "name": "Weather"}],  # after install
        ]

        with (
            patch("buoy.plugins.cli.subprocess.run") as mock_run,
            patch(
                "buoy.plugins.loader.PluginManager.discover_all",
                side_effect=lambda _config: discover_results.pop(0),
            ),
        ):
            mock_run.return_value.returncode = 0
            rc = plugin_cli.cmd_install(config, "buoy-plugin-weather")

        out = capsys.readouterr().out
        assert rc == 0
        assert "weather" in out
        mock_run.assert_called_once()
        argv = mock_run.call_args[0][0]
        assert argv[-2:] == ["install", "buoy-plugin-weather"]

    def test_pip_failure_propagates_returncode(self):
        config = _make_config(builtin={})

        with patch("buoy.plugins.cli.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            rc = plugin_cli.cmd_install(config, "nonexistent-package-xyz")

        assert rc == 1

    def test_install_with_no_new_plugin_warns(self, capsys):
        config = _make_config(builtin={})

        with (
            patch("buoy.plugins.cli.subprocess.run") as mock_run,
            patch(
                "buoy.plugins.loader.PluginManager.discover_all",
                return_value=[],
            ),
        ):
            mock_run.return_value.returncode = 0
            rc = plugin_cli.cmd_install(config, "some-unrelated-package")

        out = capsys.readouterr().out
        assert rc == 0
        assert "no new" in out.lower()
