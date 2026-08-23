"""Configuration loader for Buoy.

Resolution order:
1. buoy.yaml (or path from BUOY_CONFIG env / --config flag)
2. Environment variables override YAML (prefix: BUOY_)
3. CLI flags override everything

Minimal config: just `node.name`. Everything else has sensible defaults.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("buoy.config")


class ConfigError(Exception):
    """Raised when configuration input is invalid (bad env value, etc.)."""


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass
class NodeConfig:
    name: str = "buoy"
    tier: str = ""
    role: str = ""


@dataclass
class PeerConfig:
    name: str = ""
    url: str = ""
    tier: str = ""
    verify_ssl: bool | None = None  # None = inherit network.verify_ssl


@dataclass
class NetworkConfig:
    tailnet_domain: str = ""
    listen_port: int = 8090
    peers: list[PeerConfig] = field(default_factory=list)
    allowed_origins: list[str] = field(default_factory=list)
    trusted_proxies: list[str] = field(default_factory=list)
    verify_ssl: bool = True  # TLS verification for peer polling (default on)


@dataclass
class ServiceOverride:
    name: str = ""
    icon: str = ""
    desc: str = ""
    port: int | None = None
    path: str = ""


@dataclass
class ServicesConfig:
    hidden: list[str] = field(default_factory=list)
    overrides: dict[str, ServiceOverride] = field(default_factory=dict)


@dataclass
class ThemeConfig:
    preset: str = "terminal"  # terminal | light | solarized | nord | high-contrast
    custom: dict[str, str] = field(default_factory=dict)


@dataclass
class AuthConfig:
    enabled: bool = False
    type: str = "token"  # token | basic
    token: str = ""
    username: str = ""
    password: str = ""


@dataclass
class FeaturesConfig:
    websocket: bool = True
    history: bool = False
    demo_mode: bool = False
    night_mode: str = "auto"  # auto | always | never
    keyboard_shortcuts: bool = True
    image_updates: bool = False  # Docker image update checker (off by default)


@dataclass
class RefreshConfig:
    stats_interval: int = 5
    services_interval: int = 30
    fleet_interval: int = 15
    plugins_interval: int = 60
    image_updates_interval: int = 21600  # 6 hours


@dataclass
class PluginEntry:
    enabled: bool = False
    # Per-instance override for manifest.refresh_interval (seconds); None means
    # use the plugin's author-set default.
    refresh_interval: int | None = None
    # Additional plugin-specific config stored as dict
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginsConfig:
    enabled: bool = True
    directory: str = "/plugins"
    builtin: dict[str, PluginEntry] = field(default_factory=dict)
    user: dict[str, PluginEntry] = field(default_factory=dict)


@dataclass
class AlertsConfig:
    webhook_url: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class BuoyConfig:
    node: NodeConfig = field(default_factory=NodeConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    services: ServicesConfig = field(default_factory=ServicesConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    refresh: RefreshConfig = field(default_factory=RefreshConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ── Loader ─────────────────────────────────────────────────────────────────────


def _find_config_path(explicit_path: str | None) -> Path | None:
    """Find the config file, checking multiple locations."""
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
        logger.warning("Config file not found: %s", explicit_path)
        return None

    # Check env var
    env_path = os.environ.get("BUOY_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # Check common locations
    candidates = [
        Path("buoy.yaml"),
        Path("buoy.yml"),
        Path("/config/buoy.yaml"),
        Path("/config/buoy.yml"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply BUOY_ prefixed environment variables as config overrides.

    Mapping: BUOY_NODE_NAME → node.name, BUOY_NETWORK_LISTEN_PORT → network.listen_port
    """
    env_map = {
        "BUOY_NODE_NAME": ("node", "name"),
        "BUOY_NODE_TIER": ("node", "tier"),
        "BUOY_NODE_ROLE": ("node", "role"),
        "BUOY_NETWORK_LISTEN_PORT": ("network", "listen_port"),
        "BUOY_NETWORK_TAILNET_DOMAIN": ("network", "tailnet_domain"),
        "BUOY_NETWORK_ALLOWED_ORIGINS": ("network", "allowed_origins"),
        "BUOY_NETWORK_TRUSTED_PROXIES": ("network", "trusted_proxies"),
        "BUOY_NETWORK_VERIFY_SSL": ("network", "verify_ssl"),
        "BUOY_AUTH_ENABLED": ("auth", "enabled"),
        "BUOY_AUTH_TOKEN": ("auth", "token"),
        "BUOY_AUTH_TYPE": ("auth", "type"),
        "BUOY_AUTH_USERNAME": ("auth", "username"),
        "BUOY_AUTH_PASSWORD": ("auth", "password"),
        "BUOY_THEME_PRESET": ("theme", "preset"),
        "BUOY_FEATURES_DEMO_MODE": ("features", "demo_mode"),
        "BUOY_FEATURES_WEBSOCKET": ("features", "websocket"),
        "BUOY_FEATURES_HISTORY": ("features", "history"),
        "BUOY_FEATURES_IMAGE_UPDATES": ("features", "image_updates"),
        "BUOY_REFRESH_STATS_INTERVAL": ("refresh", "stats_interval"),
        "BUOY_REFRESH_SERVICES_INTERVAL": ("refresh", "services_interval"),
        "BUOY_REFRESH_FLEET_INTERVAL": ("refresh", "fleet_interval"),
        "BUOY_REFRESH_PLUGINS_INTERVAL": ("refresh", "plugins_interval"),
        "BUOY_REFRESH_IMAGE_UPDATES_INTERVAL": ("refresh", "image_updates_interval"),
        "BUOY_ALERTS_WEBHOOK_URL": ("alerts", "webhook_url"),
        "BUOY_LOG_LEVEL": ("logging", "level"),
    }

    for env_key, path in env_map.items():
        value = os.environ.get(env_key)
        if value is None:
            continue

        section, key = path
        if section not in raw:
            raw[section] = {}

        # Type coercion
        if key in (
            "listen_port",
            "stats_interval",
            "services_interval",
            "fleet_interval",
            "plugins_interval",
            "image_updates_interval",
        ):
            # An empty string (e.g. `BUOY_NETWORK_LISTEN_PORT=`) is treated as an
            # explicit invalid value, not "unset" — only a missing env var (checked
            # above) falls back to the YAML/default. There's no sensible int for "",
            # so we surface the same ConfigError as any other unparsable value.
            try:
                raw[section][key] = int(value)
            except ValueError as exc:
                raise ConfigError(
                    f"Invalid value for {env_key}: {value!r} (expected an integer)"
                ) from exc
        elif key in ("enabled", "websocket", "history", "demo_mode", "image_updates", "verify_ssl"):
            raw[section][key] = value.lower() in ("true", "1", "yes")
        elif key == "allowed_origins":
            raw[section][key] = [origin.strip() for origin in value.split(",") if origin.strip()]
        elif key == "trusted_proxies":
            raw[section][key] = [entry.strip() for entry in value.split(",") if entry.strip()]
        else:
            raw[section][key] = value

    return raw


def _parse_peers(raw_peers: list[dict]) -> list[PeerConfig]:
    """Parse peer config entries."""
    peers = []
    for p in raw_peers:
        raw_verify = p.get("verify_ssl")
        # Keep None when absent so the collector can inherit network.verify_ssl.
        # Only coerce to bool when the key is explicitly present.
        verify_ssl = bool(raw_verify) if raw_verify is not None else None
        peers.append(
            PeerConfig(
                name=p.get("name", ""),
                url=p.get("url", ""),
                tier=p.get("tier", ""),
                verify_ssl=verify_ssl,
            )
        )
    return peers


def _parse_overrides(raw_overrides: dict[str, dict]) -> dict[str, ServiceOverride]:
    """Parse service override entries."""
    overrides = {}
    for name, cfg in raw_overrides.items():
        overrides[name] = ServiceOverride(
            name=cfg.get("name", name),
            icon=cfg.get("icon", ""),
            desc=cfg.get("desc", ""),
            port=cfg.get("port"),
            path=cfg.get("path", ""),
        )
    return overrides


def _parse_plugins(
    raw_plugins: dict[str, dict], default_enabled: bool = False
) -> dict[str, PluginEntry]:
    """Parse plugin config entries.

    Builtins are opt-in (``default_enabled=False``): omitting a plugin, or
    omitting ``enabled``, leaves it off. User drop-in plugins are opt-out
    (``default_enabled=True``): they load unless explicitly disabled.
    """
    entries = {}
    for plugin_id, cfg in raw_plugins.items():
        enabled = cfg.pop("enabled", default_enabled) if isinstance(cfg, dict) else default_enabled
        raw_interval = cfg.pop("refresh_interval", None) if isinstance(cfg, dict) else None
        refresh_interval = int(raw_interval) if raw_interval is not None else None
        settings = cfg if isinstance(cfg, dict) else {}
        entries[plugin_id] = PluginEntry(
            enabled=enabled, refresh_interval=refresh_interval, settings=settings
        )
    return entries


def _build_config(raw: dict[str, Any]) -> BuoyConfig:
    """Build a BuoyConfig from raw YAML dict (after env overlay)."""
    node_raw = raw.get("node", {})
    network_raw = raw.get("network", {})
    services_raw = raw.get("services", {})
    theme_raw = raw.get("theme", {})
    auth_raw = raw.get("auth", {})
    features_raw = raw.get("features", {})
    refresh_raw = raw.get("refresh", {})
    plugins_raw = raw.get("plugins", {})
    alerts_raw = raw.get("alerts", {})
    logging_raw = raw.get("logging", {})

    node = NodeConfig(
        name=node_raw.get("name", "buoy"),
        tier=node_raw.get("tier", ""),
        role=node_raw.get("role", ""),
    )

    peers = _parse_peers(network_raw.get("peers", []))
    network = NetworkConfig(
        tailnet_domain=network_raw.get("tailnet_domain", ""),
        listen_port=int(network_raw.get("listen_port", 8090)),
        peers=peers,
        allowed_origins=list(network_raw.get("allowed_origins", [])),
        trusted_proxies=list(network_raw.get("trusted_proxies", [])),
        verify_ssl=bool(network_raw.get("verify_ssl", True)),
    )

    services = ServicesConfig(
        hidden=services_raw.get("hidden", []),
        overrides=_parse_overrides(services_raw.get("overrides", {})),
    )

    theme = ThemeConfig(
        preset=theme_raw.get("preset", "terminal"),
        custom=theme_raw.get("custom", {}),
    )

    auth = AuthConfig(
        enabled=bool(auth_raw.get("enabled", False)),
        type=auth_raw.get("type", "token"),
        token=auth_raw.get("token", ""),
        username=auth_raw.get("username", ""),
        password=auth_raw.get("password", ""),
    )

    features = FeaturesConfig(
        websocket=bool(features_raw.get("websocket", True)),
        history=bool(features_raw.get("history", False)),
        demo_mode=bool(features_raw.get("demo_mode", False)),
        night_mode=features_raw.get("night_mode", "auto"),
        keyboard_shortcuts=bool(features_raw.get("keyboard_shortcuts", True)),
        image_updates=bool(features_raw.get("image_updates", False)),
    )

    refresh = RefreshConfig(
        stats_interval=int(refresh_raw.get("stats_interval", 5)),
        services_interval=int(refresh_raw.get("services_interval", 30)),
        fleet_interval=int(refresh_raw.get("fleet_interval", 15)),
        plugins_interval=int(refresh_raw.get("plugins_interval", 60)),
        image_updates_interval=int(refresh_raw.get("image_updates_interval", 21600)),
    )

    plugins = PluginsConfig(
        enabled=bool(plugins_raw.get("enabled", True)),
        directory=plugins_raw.get("directory", "/plugins"),
        builtin=_parse_plugins(plugins_raw.get("builtin", {})),
        user=_parse_plugins(plugins_raw.get("user", {}), default_enabled=True),
    )

    alerts = AlertsConfig(
        webhook_url=alerts_raw.get("webhook_url", "") if isinstance(alerts_raw, dict) else "",
    )

    logging_cfg = LoggingConfig(
        level=logging_raw.get("level", "INFO") if isinstance(logging_raw, dict) else "INFO",
    )

    return BuoyConfig(
        node=node,
        network=network,
        services=services,
        theme=theme,
        auth=auth,
        features=features,
        refresh=refresh,
        plugins=plugins,
        alerts=alerts,
        logging=logging_cfg,
    )


def load_config(path: str | None = None, demo: bool = False) -> BuoyConfig:
    """Load and return the Buoy configuration.

    Args:
        path: Explicit config file path (optional).
        demo: If True, force demo mode regardless of config.

    Returns:
        Fully resolved BuoyConfig.
    """
    config_path = _find_config_path(path)

    if config_path:
        logger.info("Loading config from %s", config_path)
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        logger.info("No config file found, using defaults")
        raw = {}

    # Apply environment variable overrides
    raw = _apply_env_overrides(raw)

    # Build typed config
    config = _build_config(raw)

    # CLI demo flag overrides everything
    if demo:
        config.features.demo_mode = True

    return config
