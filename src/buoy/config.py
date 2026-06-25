"""Configuration loader for Buoy.

Resolution order:
1. buoy.yaml (or path from BUOY_CONFIG env / --config flag)
2. Environment variables override YAML (prefix: BUOY_)
3. CLI flags override everything

Minimal config: just `node.name`. Everything else has sensible defaults.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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


@dataclass
class NetworkConfig:
    tailnet_domain: str = ""
    listen_port: int = 8090
    peers: list[PeerConfig] = field(default_factory=list)


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
    preset: str = "terminal"  # terminal | light | custom
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


@dataclass
class RefreshConfig:
    stats_interval: int = 5
    services_interval: int = 30
    fleet_interval: int = 15
    plugins_interval: int = 60


@dataclass
class PluginEntry:
    enabled: bool = False
    # Additional plugin-specific config stored as dict
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginsConfig:
    enabled: bool = True
    directory: str = "/plugins"
    builtin: dict[str, PluginEntry] = field(default_factory=dict)


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


# ── Loader ─────────────────────────────────────────────────────────────────────


def _find_config_path(explicit_path: str | None) -> Path | None:
    """Find the config file, checking multiple locations."""
    if explicit_path:
        p = Path(explicit_path)
        if p.exists():
            return p
        print(f"[buoy] Config file not found: {explicit_path}", file=sys.stderr)
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
        "BUOY_AUTH_ENABLED": ("auth", "enabled"),
        "BUOY_AUTH_TOKEN": ("auth", "token"),
        "BUOY_AUTH_TYPE": ("auth", "type"),
        "BUOY_AUTH_USERNAME": ("auth", "username"),
        "BUOY_AUTH_PASSWORD": ("auth", "password"),
        "BUOY_THEME_PRESET": ("theme", "preset"),
        "BUOY_FEATURES_DEMO_MODE": ("features", "demo_mode"),
        "BUOY_FEATURES_WEBSOCKET": ("features", "websocket"),
        "BUOY_FEATURES_HISTORY": ("features", "history"),
    }

    for env_key, path in env_map.items():
        value = os.environ.get(env_key)
        if value is None:
            continue

        section, key = path
        if section not in raw:
            raw[section] = {}

        # Type coercion
        if key in ("listen_port", "stats_interval", "fleet_interval"):
            raw[section][key] = int(value)
        elif key in ("enabled", "websocket", "history", "demo_mode"):
            raw[section][key] = value.lower() in ("true", "1", "yes")
        else:
            raw[section][key] = value

    return raw


def _parse_peers(raw_peers: list[dict]) -> list[PeerConfig]:
    """Parse peer config entries."""
    peers = []
    for p in raw_peers:
        peers.append(
            PeerConfig(
                name=p.get("name", ""),
                url=p.get("url", ""),
                tier=p.get("tier", ""),
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


def _parse_plugins(raw_plugins: dict[str, dict]) -> dict[str, PluginEntry]:
    """Parse builtin plugin config entries."""
    entries = {}
    for plugin_id, cfg in raw_plugins.items():
        enabled = cfg.pop("enabled", False) if isinstance(cfg, dict) else False
        settings = cfg if isinstance(cfg, dict) else {}
        entries[plugin_id] = PluginEntry(enabled=enabled, settings=settings)
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
    )

    refresh = RefreshConfig(
        stats_interval=int(refresh_raw.get("stats_interval", 5)),
        services_interval=int(refresh_raw.get("services_interval", 30)),
        fleet_interval=int(refresh_raw.get("fleet_interval", 15)),
        plugins_interval=int(refresh_raw.get("plugins_interval", 60)),
    )

    plugins = PluginsConfig(
        enabled=bool(plugins_raw.get("enabled", True)),
        directory=plugins_raw.get("directory", "/plugins"),
        builtin=_parse_plugins(plugins_raw.get("builtin", {})),
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
        print(f"[buoy] Loading config from {config_path}")
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        print("[buoy] No config file found, using defaults")
        raw = {}

    # Apply environment variable overrides
    raw = _apply_env_overrides(raw)

    # Build typed config
    config = _build_config(raw)

    # CLI demo flag overrides everything
    if demo:
        config.features.demo_mode = True

    return config
