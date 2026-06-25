"""Entry point for `python -m buoy` or the `buoy` CLI command."""

import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="buoy",
        description="A lightweight, per-node system dashboard for homelabs.",
    )
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

    args = parser.parse_args()

    from buoy.config import load_config
    from buoy.server import create_app

    config = load_config(path=args.config, demo=args.demo)

    port = args.port or config.network.listen_port
    app = create_app(config)

    import uvicorn

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
