"""Entry point for `python -m buoy` or the `buoy` CLI command."""

import argparse
import os
import sys

from buoy._version import VERSION


def _add_serve_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=None,
        help="Path to buoy.yaml config file (default: ./buoy.yaml or /config/buoy.yaml)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode with mock data (no Docker socket or host access needed)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Listen port (overrides config, default: 8090)",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Enable hot-reload and debug logging for local development",
    )


def _serve(args) -> None:
    from buoy.config import ConfigError, load_config
    from buoy.logging_setup import setup_logging

    # Bootstrap logging before config load so the config-loading log messages
    # are visible; re-applied below once the configured/--dev level is known.
    setup_logging(os.environ.get("BUOY_LOG_LEVEL", "INFO"))

    if args.dev:
        # --dev raises the *application* log level to DEBUG, not just uvicorn's.
        os.environ["BUOY_LOG_LEVEL"] = "DEBUG"

    try:
        config = load_config(path=args.config, demo=args.demo)
    except ConfigError as exc:
        print(f"[buoy] {exc}", file=sys.stderr)
        sys.exit(2)
    setup_logging(config.logging.level)
    port = args.port or config.network.listen_port

    import uvicorn

    if args.dev:
        os.environ["BUOY_CONFIG"] = args.config or ""
        os.environ["BUOY_DEMO"] = "1" if args.demo else "0"
        uvicorn.run(
            "buoy.server:_factory",
            factory=True,
            host=args.host,
            port=port,
            reload=True,
            log_level="debug",
            proxy_headers=False,
        )
    else:
        from buoy.server import create_app

        app = create_app(config)
        uvicorn.run(app, host=args.host, port=port, log_level="info", proxy_headers=False)


def _plugin(args) -> int:
    from buoy.config import ConfigError, load_config
    from buoy.plugins import cli as plugin_cli

    try:
        config = load_config(path=args.config, demo=False)
    except ConfigError as exc:
        print(f"[buoy] {exc}", file=sys.stderr)
        sys.exit(2)

    if args.plugin_command == "list":
        return plugin_cli.cmd_list(config)
    if args.plugin_command == "info":
        return plugin_cli.cmd_info(config, args.plugin_id)
    if args.plugin_command == "install":
        return plugin_cli.cmd_install(config, args.spec)
    args.plugin_parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buoy",
        description="A lightweight, per-node system dashboard for homelabs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    # Serve remains the default when no subcommand is given, so all its flags
    # live on the top-level parser too (kept in sync with the `serve` subparser).
    _add_serve_arguments(parser)

    subparsers = parser.add_subparsers(dest="command")

    serve_parser = subparsers.add_parser("serve", help="Run the dashboard server (default)")
    _add_serve_arguments(serve_parser)

    plugin_parser = subparsers.add_parser("plugin", help="Inspect and manage plugins")
    plugin_parser.add_argument(
        "--config",
        default=None,
        help="Path to buoy.yaml config file (default: ./buoy.yaml or /config/buoy.yaml)",
    )
    plugin_subparsers = plugin_parser.add_subparsers(dest="plugin_command")

    plugin_subparsers.add_parser("list", help="List every discoverable plugin")

    info_parser = plugin_subparsers.add_parser("info", help="Show a plugin's manifest")
    info_parser.add_argument("plugin_id", help="Plugin id, e.g. 'github'")

    install_parser = plugin_subparsers.add_parser(
        "install", help="Install a plugin package via pip"
    )
    install_parser.add_argument("spec", help="pip requirement spec, e.g. 'buoy-plugin-weather'")

    parser.set_defaults(plugin_parser=plugin_parser)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "plugin":
        sys.exit(_plugin(args))
    else:
        # No subcommand, or explicit `serve` — both take the serve args.
        _serve(args)


if __name__ == "__main__":
    main()
