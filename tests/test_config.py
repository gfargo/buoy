"""Tests for the Buoy configuration system."""

import pytest
import yaml

from buoy.config import (
    ConfigError,
    _apply_env_overrides,
    _build_config,
    _warn_unknown_keys,
    load_config,
)


class TestConfigDefaults:
    """Config with no file or env should return sensible defaults."""

    def test_default_node_name(self):
        config = _build_config({})
        assert config.node.name == "buoy"

    def test_default_port(self):
        config = _build_config({})
        assert config.network.listen_port == 8090

    def test_default_theme(self):
        config = _build_config({})
        assert config.theme.preset == "terminal"

    def test_default_auth_disabled(self):
        config = _build_config({})
        assert config.auth.enabled is False

    def test_default_features(self):
        config = _build_config({})
        assert config.features.websocket is True
        assert config.features.history is False
        assert config.features.demo_mode is False
        assert config.features.night_mode == "auto"
        assert config.features.image_updates is False

    def test_default_refresh(self):
        config = _build_config({})
        assert config.refresh.stats_interval == 5
        assert config.refresh.fleet_interval == 15
        assert config.refresh.image_updates_interval == 21600

    def test_default_logging_level(self):
        config = _build_config({})
        assert config.logging.level == "INFO"


class TestConfigFromYAML:
    """Config loaded from a YAML dict."""

    def test_node_name(self):
        config = _build_config({"node": {"name": "compass"}})
        assert config.node.name == "compass"

    def test_network_peers(self):
        raw = {
            "network": {
                "peers": [
                    {"name": "harbor", "url": "https://harbor.example.ts.net", "tier": "1A"},
                    {"name": "watch", "url": "https://watch.example.ts.net", "tier": "2"},
                ]
            }
        }
        config = _build_config(raw)
        assert len(config.network.peers) == 2
        assert config.network.peers[0].name == "harbor"
        assert config.network.peers[1].url == "https://watch.example.ts.net"

    def test_services_hidden(self):
        raw = {"services": {"hidden": ["redis", "postgres"]}}
        config = _build_config(raw)
        assert "redis" in config.services.hidden
        assert "postgres" in config.services.hidden

    def test_services_overrides(self):
        raw = {
            "services": {"overrides": {"grafana": {"name": "Grafana", "icon": "📊", "port": 3000}}}
        }
        config = _build_config(raw)
        assert "grafana" in config.services.overrides
        assert config.services.overrides["grafana"].name == "Grafana"
        assert config.services.overrides["grafana"].port == 3000

    def test_auth_enabled(self):
        raw = {"auth": {"enabled": True, "type": "token", "token": "secret123"}}
        config = _build_config(raw)
        assert config.auth.enabled is True
        assert config.auth.token == "secret123"

    def test_network_allowed_origins(self):
        raw = {"network": {"allowed_origins": ["https://harbor.example.ts.net"]}}
        config = _build_config(raw)
        assert config.network.allowed_origins == ["https://harbor.example.ts.net"]

    def test_network_allowed_origins_default_empty(self):
        config = _build_config({})
        assert config.network.allowed_origins == []

    def test_plugins_builtin(self):
        raw = {
            "plugins": {
                "builtin": {
                    "github": {"enabled": True, "token": "ghp_xxx"},
                }
            }
        }
        config = _build_config(raw)
        assert config.plugins.builtin["github"].enabled is True
        assert config.plugins.builtin["github"].settings["token"] == "ghp_xxx"

    def test_plugins_builtin_refresh_interval_override(self):
        raw = {
            "plugins": {
                "builtin": {
                    "github": {"enabled": True, "refresh_interval": 600, "token": "ghp_xxx"},
                }
            }
        }
        config = _build_config(raw)
        assert config.plugins.builtin["github"].refresh_interval == 600
        # Must not leak into settings passed to the plugin's configure().
        assert "refresh_interval" not in config.plugins.builtin["github"].settings
        assert config.plugins.builtin["github"].settings["token"] == "ghp_xxx"

    def test_plugins_builtin_refresh_interval_defaults_to_none(self):
        raw = {"plugins": {"builtin": {"github": {"enabled": True}}}}
        config = _build_config(raw)
        assert config.plugins.builtin["github"].refresh_interval is None

    def test_plugins_user(self):
        raw = {
            "plugins": {
                "user": {
                    "weather": {"url": "https://api.example.com"},
                }
            }
        }
        config = _build_config(raw)
        assert config.plugins.user["weather"].settings["url"] == "https://api.example.com"

    def test_plugins_user_defaults_empty(self):
        config = _build_config({})
        assert config.plugins.user == {}

    def test_plugins_user_defaults_enabled(self):
        """User (drop-in) plugins are opt-out: no `enabled` key means enabled."""
        raw = {"plugins": {"user": {"weather": {"url": "https://api.example.com"}}}}
        config = _build_config(raw)
        assert config.plugins.user["weather"].enabled is True

    def test_plugins_user_explicit_disabled(self):
        raw = {"plugins": {"user": {"weather": {"enabled": False}}}}
        config = _build_config(raw)
        assert config.plugins.user["weather"].enabled is False

    def test_plugins_builtin_defaults_disabled(self):
        """Builtin plugins are opt-in: no `enabled` key means disabled."""
        raw = {"plugins": {"builtin": {"github": {"token": "ghp_xxx"}}}}
        config = _build_config(raw)
        assert config.plugins.builtin["github"].enabled is False

    def test_build_config_does_not_mutate_raw_dict(self):
        """_build_config must not pop keys off the caller's raw dict (BUG-17):
        building twice from the same raw dict must yield the same result."""
        raw = {"plugins": {"builtin": {"github": {"enabled": True, "token": "ghp_xxx"}}}}
        _build_config(raw)
        assert raw["plugins"]["builtin"]["github"] == {"enabled": True, "token": "ghp_xxx"}

        config = _build_config(raw)
        assert config.plugins.builtin["github"].enabled is True
        assert config.plugins.builtin["github"].settings["token"] == "ghp_xxx"

    def test_theme_preset_light(self):
        config = _build_config({"theme": {"preset": "light"}})
        assert config.theme.preset == "light"

    def test_theme_preset_solarized(self):
        config = _build_config({"theme": {"preset": "solarized"}})
        assert config.theme.preset == "solarized"

    def test_theme_preset_nord(self):
        config = _build_config({"theme": {"preset": "nord"}})
        assert config.theme.preset == "nord"

    def test_theme_preset_high_contrast(self):
        config = _build_config({"theme": {"preset": "high-contrast"}})
        assert config.theme.preset == "high-contrast"

    def test_theme_custom_vars(self):
        raw = {"theme": {"preset": "terminal", "custom": {"bg": "#ff0000", "amber": "#00ff00"}}}
        config = _build_config(raw)
        assert config.theme.custom["bg"] == "#ff0000"
        assert config.theme.custom["amber"] == "#00ff00"

    def test_logging_level_from_yaml(self):
        config = _build_config({"logging": {"level": "DEBUG"}})
        assert config.logging.level == "DEBUG"


class TestEnvOverrides:
    """Environment variables override YAML values."""

    def test_node_name_override(self, monkeypatch):
        monkeypatch.setenv("BUOY_NODE_NAME", "harbor")
        raw = {"node": {"name": "compass"}}
        result = _apply_env_overrides(raw)
        assert result["node"]["name"] == "harbor"

    def test_port_override(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_LISTEN_PORT", "9090")
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["network"]["listen_port"] == 9090

    def test_auth_token_override(self, monkeypatch):
        monkeypatch.setenv("BUOY_AUTH_TOKEN", "my-secret")
        raw = {"auth": {"enabled": True}}
        result = _apply_env_overrides(raw)
        assert result["auth"]["token"] == "my-secret"

    def test_bool_coercion(self, monkeypatch):
        monkeypatch.setenv("BUOY_FEATURES_DEMO_MODE", "true")
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["features"]["demo_mode"] is True

    def test_bool_coercion_false(self, monkeypatch):
        monkeypatch.setenv("BUOY_FEATURES_WEBSOCKET", "false")
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["features"]["websocket"] is False

    def test_image_updates_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_FEATURES_IMAGE_UPDATES", "true")
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["features"]["image_updates"] is True

    def test_image_updates_interval_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_REFRESH_IMAGE_UPDATES_INTERVAL", "3600")
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["refresh"]["image_updates_interval"] == 3600

    def test_allowed_origins_env_comma_split(self, monkeypatch):
        monkeypatch.setenv(
            "BUOY_NETWORK_ALLOWED_ORIGINS",
            "https://harbor.example.ts.net, https://watch.example.ts.net",
        )
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["network"]["allowed_origins"] == [
            "https://harbor.example.ts.net",
            "https://watch.example.ts.net",
        ]

    def test_allowed_origins_env_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_ALLOWED_ORIGINS", "https://harbor.example.ts.net")
        raw = {"network": {"allowed_origins": ["https://old.example.ts.net"]}}
        result = _apply_env_overrides(raw)
        config = _build_config(result)
        assert config.network.allowed_origins == ["https://harbor.example.ts.net"]

    def test_theme_preset_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_THEME_PRESET", "nord")
        raw = {"theme": {"preset": "terminal"}}
        result = _apply_env_overrides(raw)
        assert result["theme"]["preset"] == "nord"

    def test_alerts_webhook_url_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_ALERTS_WEBHOOK_URL", "https://hooks.example/test")
        raw = {}
        result = _apply_env_overrides(raw)
        assert result["alerts"]["webhook_url"] == "https://hooks.example/test"

    def test_alerts_webhook_url_env_builds_config(self, monkeypatch):
        monkeypatch.setenv("BUOY_ALERTS_WEBHOOK_URL", "https://hooks.example/test")
        raw = _apply_env_overrides({})
        config = _build_config(raw)
        assert config.alerts.webhook_url == "https://hooks.example/test"

    def test_log_level_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_LOG_LEVEL", "DEBUG")
        raw = _apply_env_overrides({})
        assert raw["logging"]["level"] == "DEBUG"

    def test_log_level_env_builds_config(self, monkeypatch):
        monkeypatch.setenv("BUOY_LOG_LEVEL", "WARNING")
        raw = _apply_env_overrides({})
        config = _build_config(raw)
        assert config.logging.level == "WARNING"

    def test_port_override_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_LISTEN_PORT", "eighty-ninety")
        with pytest.raises(ConfigError) as exc_info:
            _apply_env_overrides({})
        assert "BUOY_NETWORK_LISTEN_PORT" in str(exc_info.value)
        assert "eighty-ninety" in str(exc_info.value)

    def test_port_override_empty_string_raises(self, monkeypatch):
        """An explicitly-set but empty env var is an invalid int, not "unset"."""
        monkeypatch.setenv("BUOY_NETWORK_LISTEN_PORT", "")
        with pytest.raises(ConfigError) as exc_info:
            _apply_env_overrides({})
        assert "BUOY_NETWORK_LISTEN_PORT" in str(exc_info.value)

    def test_stats_interval_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_REFRESH_STATS_INTERVAL", "10")
        result = _apply_env_overrides({})
        assert result["refresh"]["stats_interval"] == 10

    def test_services_interval_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_REFRESH_SERVICES_INTERVAL", "45")
        result = _apply_env_overrides({})
        assert result["refresh"]["services_interval"] == 45

    def test_fleet_interval_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_REFRESH_FLEET_INTERVAL", "20")
        result = _apply_env_overrides({})
        assert result["refresh"]["fleet_interval"] == 20

    def test_plugins_interval_env(self, monkeypatch):
        monkeypatch.setenv("BUOY_REFRESH_PLUGINS_INTERVAL", "90")
        result = _apply_env_overrides({})
        assert result["refresh"]["plugins_interval"] == 90


class TestLoadConfig:
    """Integration test for the full load_config flow."""

    def test_load_from_file(self, tmp_path):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(yaml.dump({"node": {"name": "test-node"}}))

        config = load_config(path=str(config_file))
        assert config.node.name == "test-node"

    def test_load_missing_file_uses_defaults(self):
        config = load_config(path="/nonexistent/buoy.yaml")
        assert config.node.name == "buoy"

    def test_demo_flag_overrides_config(self, tmp_path):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(yaml.dump({"features": {"demo_mode": False}}))

        config = load_config(path=str(config_file), demo=True)
        assert config.features.demo_mode is True

    def test_empty_yaml_uses_defaults(self, tmp_path):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text("")

        config = load_config(path=str(config_file))
        assert config.node.name == "buoy"
        assert config.network.listen_port == 8090

    def test_allowed_origins_from_yaml(self, tmp_path):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(
            yaml.dump({"network": {"allowed_origins": ["https://harbor.example.ts.net"]}})
        )

        config = load_config(path=str(config_file))
        assert config.network.allowed_origins == ["https://harbor.example.ts.net"]

    def test_allowed_origins_from_env(self, tmp_path, monkeypatch):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(yaml.dump({"node": {"name": "compass"}}))
        monkeypatch.setenv("BUOY_NETWORK_ALLOWED_ORIGINS", "https://harbor.example.ts.net")

        config = load_config(path=str(config_file))
        assert config.network.allowed_origins == ["https://harbor.example.ts.net"]

    def test_plugins_interval_from_env(self, tmp_path, monkeypatch):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(yaml.dump({"node": {"name": "compass"}}))
        monkeypatch.setenv("BUOY_REFRESH_PLUGINS_INTERVAL", "120")

        config = load_config(path=str(config_file))
        assert config.refresh.plugins_interval == 120

    def test_invalid_port_env_raises_config_error(self, tmp_path, monkeypatch):
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(yaml.dump({"node": {"name": "compass"}}))
        monkeypatch.setenv("BUOY_NETWORK_LISTEN_PORT", "eighty-ninety")

        with pytest.raises(ConfigError):
            load_config(path=str(config_file))


class TestBuildConfigIntGuards:
    """_build_config raises a named ConfigError for malformed YAML-sourced
    ints instead of a bare ValueError/TypeError traceback (issue: config:
    guard unguarded int() casts on YAML-sourced values in _build_config)."""

    def test_invalid_yaml_listen_port_raises_config_error(self):
        with pytest.raises(ConfigError) as exc_info:
            _build_config({"network": {"listen_port": "eighty-ninety"}})
        assert "network.listen_port" in str(exc_info.value)
        assert "eighty-ninety" in str(exc_info.value)

    def test_invalid_yaml_refresh_interval_raises_config_error(self):
        with pytest.raises(ConfigError) as exc_info:
            _build_config({"refresh": {"stats_interval": "soon"}})
        assert "refresh.stats_interval" in str(exc_info.value)

    def test_non_scalar_yaml_int_raises_config_error(self):
        """A list/dict where an int is expected is a TypeError from int(),
        not a ValueError — both must surface as a clean ConfigError."""
        with pytest.raises(ConfigError):
            _build_config({"network": {"listen_port": [8090]}})

    def test_invalid_plugin_refresh_interval_raises_config_error(self):
        with pytest.raises(ConfigError) as exc_info:
            _build_config({"plugins": {"builtin": {"smart_disk": {"refresh_interval": "often"}}}})
        assert "plugins.smart_disk.refresh_interval" in str(exc_info.value)

    def test_yaml_string_int_still_coerces(self):
        """A quoted numeric string (e.g. `listen_port: "8080"`) is valid YAML
        and must still work, not just native YAML ints."""
        config = _build_config({"network": {"listen_port": "8080"}})
        assert config.network.listen_port == 8080


class TestWarnUnknownKeys:
    """Unknown config keys warn but don't fail (BUG-24 / SPEC §3.3).

    _build_config's plain .get() calls silently ignore a typo'd key like
    `noed: {nmae: x}` — the node still starts, just not configured the way
    the typo intended, with no diagnostic anywhere. _warn_unknown_keys logs
    a warning instead of leaving that completely silent.
    """

    def test_unknown_top_level_section_warns(self, caplog):
        with caplog.at_level("WARNING", logger="buoy.config"):
            _warn_unknown_keys({"noed": {"name": "x"}})

        assert any("noed" in r.message for r in caplog.records)

    def test_unknown_key_within_known_section_warns(self, caplog):
        with caplog.at_level("WARNING", logger="buoy.config"):
            _warn_unknown_keys({"node": {"nmae": "x"}})

        assert any("node.nmae" in r.message for r in caplog.records)

    def test_valid_minimal_config_warns_about_nothing(self, caplog):
        with caplog.at_level("WARNING", logger="buoy.config"):
            _warn_unknown_keys({"node": {"name": "compass"}})

        assert caplog.records == []

    def test_does_not_false_positive_on_freeform_nested_structures(self, caplog):
        """services.overrides.<service-name>, plugins.builtin.<plugin-id>,
        and a network.peers entry are all user-defined maps/lists one level
        below a recognized field — their own contents must never be checked
        against BuoyConfig's dataclass fields."""
        raw = {
            "network": {"peers": [{"name": "harbor", "url": "https://harbor.local", "tier": "x"}]},
            "services": {"overrides": {"grafana": {"icon": "📊", "desc": "dashboards"}}},
            "plugins": {"builtin": {"smart_disk": {"enabled": True, "drives": ["/dev/sda"]}}},
        }

        with caplog.at_level("WARNING", logger="buoy.config"):
            _warn_unknown_keys(raw)

        assert caplog.records == []

    def test_realistic_full_config_warns_about_nothing(self, caplog):
        """A config touching every section with real-world keys must not
        produce any false-positive warnings."""
        raw = {
            "node": {"name": "compass", "tier": "primary", "role": "server"},
            "network": {
                "tailnet_domain": "example.ts.net",
                "listen_port": 8090,
                "base_path": "/buoy",
                "peers": [{"name": "harbor", "url": "https://harbor.local"}],
                "allowed_origins": ["https://harbor.example.ts.net"],
                "trusted_proxies": ["10.0.0.1"],
                "verify_ssl": True,
            },
            "services": {"hidden": ["internal-tool"], "overrides": {}},
            "theme": {"preset": "nord", "custom": {}},
            "auth": {"enabled": True, "type": "token", "token": "secret"},
            "features": {"websocket": True, "demo_mode": False},
            "refresh": {"stats_interval": 5, "fleet_interval": 15},
            "plugins": {"enabled": True, "directory": "/plugins", "builtin": {}, "user": {}},
            "alerts": {"webhook_url": "https://example.com/hook"},
            "logging": {"level": "DEBUG"},
        }

        with caplog.at_level("WARNING", logger="buoy.config"):
            _warn_unknown_keys(raw)

        assert caplog.records == []

    def test_load_config_warns_on_typo_without_raising(self, tmp_path, caplog):
        """End-to-end: a typo'd config file still starts (matching the
        existing "everything has sensible defaults" behavior) but the
        typo is no longer completely silent."""
        config_file = tmp_path / "buoy.yaml"
        config_file.write_text(yaml.dump({"noed": {"nmae": "compass"}}))

        with caplog.at_level("WARNING", logger="buoy.config"):
            config = load_config(path=str(config_file))

        assert config.node.name == "buoy"  # typo'd section had no effect, as before
        assert any("noed" in r.message for r in caplog.records)

    def test_empty_config_warns_about_nothing(self, caplog):
        with caplog.at_level("WARNING", logger="buoy.config"):
            _warn_unknown_keys({})

        assert caplog.records == []


class TestNetworkVerifySsl:
    """Tests for network.verify_ssl and per-peer verify_ssl config."""

    # --- defaults ---

    def test_network_verify_ssl_default_true(self):
        config = _build_config({})
        assert config.network.verify_ssl is True

    def test_peer_verify_ssl_default_none(self):
        raw = {"network": {"peers": [{"name": "harbor", "url": "https://harbor.example.ts.net"}]}}
        config = _build_config(raw)
        assert config.network.peers[0].verify_ssl is None

    # --- YAML overrides ---

    def test_network_verify_ssl_false_from_yaml(self):
        raw = {"network": {"verify_ssl": False}}
        config = _build_config(raw)
        assert config.network.verify_ssl is False

    def test_network_verify_ssl_true_explicit(self):
        raw = {"network": {"verify_ssl": True}}
        config = _build_config(raw)
        assert config.network.verify_ssl is True

    def test_per_peer_verify_ssl_false(self):
        raw = {
            "network": {
                "peers": [
                    {"name": "harbor", "url": "https://harbor.local", "verify_ssl": False},
                ]
            }
        }
        config = _build_config(raw)
        assert config.network.peers[0].verify_ssl is False

    def test_per_peer_verify_ssl_true(self):
        raw = {
            "network": {
                "peers": [
                    {"name": "harbor", "url": "https://harbor.local", "verify_ssl": True},
                ]
            }
        }
        config = _build_config(raw)
        assert config.network.peers[0].verify_ssl is True

    def test_per_peer_without_verify_ssl_stays_none(self):
        """A peer without verify_ssl must stay None (inherit, not default-False)."""
        raw = {
            "network": {
                "peers": [
                    {"name": "harbor", "url": "https://harbor.local"},
                ]
            }
        }
        config = _build_config(raw)
        assert config.network.peers[0].verify_ssl is None

    # --- env override ---

    def test_env_verify_ssl_false(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_VERIFY_SSL", "false")
        raw = _apply_env_overrides({})
        config = _build_config(raw)
        assert config.network.verify_ssl is False

    def test_env_verify_ssl_true(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_VERIFY_SSL", "true")
        raw = _apply_env_overrides({})
        config = _build_config(raw)
        assert config.network.verify_ssl is True

    def test_env_verify_ssl_zero_is_false(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_VERIFY_SSL", "0")
        raw = _apply_env_overrides({})
        config = _build_config(raw)
        assert config.network.verify_ssl is False

    def test_env_verify_ssl_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("BUOY_NETWORK_VERIFY_SSL", "false")
        raw = _apply_env_overrides({"network": {"verify_ssl": True}})
        config = _build_config(raw)
        assert config.network.verify_ssl is False
