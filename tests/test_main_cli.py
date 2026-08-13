"""Tests for buoy.__main__ argument parsing and subcommand dispatch."""

from unittest.mock import MagicMock, patch

import pytest

import buoy.__main__ as main_mod
from buoy.config import ConfigError


class TestBuildParserBackwardCompat:
    """`buoy` with no subcommand must keep behaving like the pre-plugin-CLI parser."""

    def test_no_subcommand_defaults_to_serve_flags(self):
        parser = main_mod.build_parser()
        args = parser.parse_args(["--demo", "--port", "9090"])

        assert args.command is None
        assert args.demo is True
        assert args.port == 9090
        assert args.host == "0.0.0.0"

    def test_no_args_has_serve_defaults(self):
        parser = main_mod.build_parser()
        args = parser.parse_args([])

        assert args.command is None
        assert args.demo is False
        assert args.config is None
        assert args.dev is False

    def test_explicit_serve_subcommand_accepts_same_flags(self):
        parser = main_mod.build_parser()
        args = parser.parse_args(["serve", "--demo", "--dev"])

        assert args.command == "serve"
        assert args.demo is True
        assert args.dev is True


class TestBuildParserPluginSubcommand:
    def test_plugin_list(self):
        parser = main_mod.build_parser()
        args = parser.parse_args(["plugin", "list"])

        assert args.command == "plugin"
        assert args.plugin_command == "list"

    def test_plugin_info_requires_id(self):
        parser = main_mod.build_parser()
        args = parser.parse_args(["plugin", "info", "github"])

        assert args.plugin_command == "info"
        assert args.plugin_id == "github"

    def test_plugin_install_requires_spec(self):
        parser = main_mod.build_parser()
        args = parser.parse_args(["plugin", "install", "buoy-plugin-weather"])

        assert args.plugin_command == "install"
        assert args.spec == "buoy-plugin-weather"

    def test_plugin_with_no_further_subcommand(self):
        parser = main_mod.build_parser()
        args = parser.parse_args(["plugin"])

        assert args.command == "plugin"
        assert args.plugin_command is None


class TestPluginDispatch:
    """_plugin() routes to the right buoy.plugins.cli function."""

    def _args(self, argv_tail):
        parser = main_mod.build_parser()
        return parser.parse_args(["plugin", *argv_tail])

    def test_dispatches_to_cmd_list(self):
        args = self._args(["list"])
        with (
            patch("buoy.config.load_config", return_value=MagicMock()),
            patch("buoy.plugins.cli.cmd_list", return_value=0) as mock_list,
        ):
            rc = main_mod._plugin(args)

        mock_list.assert_called_once()
        assert rc == 0

    def test_dispatches_to_cmd_info(self):
        args = self._args(["info", "github"])
        with (
            patch("buoy.config.load_config", return_value=MagicMock()),
            patch("buoy.plugins.cli.cmd_info", return_value=0) as mock_info,
        ):
            rc = main_mod._plugin(args)

        mock_info.assert_called_once()
        assert mock_info.call_args[0][1] == "github"
        assert rc == 0

    def test_dispatches_to_cmd_install(self):
        args = self._args(["install", "buoy-plugin-weather"])
        with (
            patch("buoy.config.load_config", return_value=MagicMock()),
            patch("buoy.plugins.cli.cmd_install", return_value=0) as mock_install,
        ):
            rc = main_mod._plugin(args)

        mock_install.assert_called_once()
        assert mock_install.call_args[0][1] == "buoy-plugin-weather"
        assert rc == 0

    def test_no_plugin_subcommand_prints_help_and_errors(self, capsys):
        args = self._args([])
        with patch("buoy.config.load_config", return_value=MagicMock()):
            rc = main_mod._plugin(args)

        assert rc == 1
        assert "usage" in capsys.readouterr().out.lower()

    def test_config_error_exits_cleanly(self, capsys):
        args = self._args(["list"])
        with patch(
            "buoy.config.load_config",
            side_effect=ConfigError("Invalid value for BUOY_NETWORK_LISTEN_PORT: 'nope'"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main_mod._plugin(args)

        assert exc_info.value.code == 2
        assert "Invalid value for BUOY_NETWORK_LISTEN_PORT" in capsys.readouterr().err
