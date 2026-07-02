"""Tests for the Buoy configuration system."""

import yaml

from buoy.config import (
    _apply_env_overrides,
    _apply_plugin_env_overrides,
    _build_config,
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


class TestPluginEnvOverrides:
    """BUOY_PLUGINS_BUILTIN_<PLUGIN>_<KEY> overrides plugins.builtin.<plugin>.<key>."""

    def test_override_empty_secret(self, monkeypatch):
        """Basic case: env var sets an empty api_key from YAML."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_PLANE_API_KEY", "xyz")
        raw = {"plugins": {"builtin": {"plane": {"enabled": True, "api_key": ""}}}}
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["plane"]["api_key"] == "xyz"

    def test_override_nonempty_yaml_value(self, monkeypatch):
        """Env var overrides a non-empty YAML value."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_GITHUB_TOKEN", "ghp_new")
        raw = {"plugins": {"builtin": {"github": {"enabled": True, "token": "ghp_old"}}}}
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["github"]["token"] == "ghp_new"

    def test_underscore_in_plugin_id_and_key(self, monkeypatch):
        """Plugin id with underscore (uptime_kuma) + simple key (url)."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_UPTIME_KUMA_URL", "http://kuma:3001")
        raw = {"plugins": {"builtin": {"uptime_kuma": {"enabled": True, "url": ""}}}}
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["uptime_kuma"]["url"] == "http://kuma:3001"

    def test_int_coercion_for_existing_int_key(self, monkeypatch):
        """endpoint_id is an int in YAML; env value should be coerced to int."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_PORTAINER_ENDPOINT_ID", "42")
        raw = {"plugins": {"builtin": {"portainer": {"enabled": True, "endpoint_id": 1}}}}
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["portainer"]["endpoint_id"] == 42
        assert isinstance(result["plugins"]["builtin"]["portainer"]["endpoint_id"], int)

    def test_bool_coercion_for_existing_bool_key(self, monkeypatch):
        """verify_ssl is bool in YAML; env value should be coerced to bool."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_PORTAINER_VERIFY_SSL", "false")
        raw = {"plugins": {"builtin": {"portainer": {"enabled": True, "verify_ssl": True}}}}
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["portainer"]["verify_ssl"] is False

    def test_key_absent_in_yaml_stored_as_string(self, monkeypatch):
        """Key not present in YAML dict defaults to string storage."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_LOKI_TOKEN", "secret-token")
        raw = {"plugins": {"builtin": {"loki": {"enabled": True, "url": "http://loki:3100"}}}}
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["loki"]["token"] == "secret-token"

    def test_plugin_not_in_yaml_creates_entry(self, monkeypatch):
        """Plugin id known from builtin set but absent in YAML gets an entry created."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_JELLYFIN_API_KEY", "jf_key")
        raw = {}  # no plugins section at all
        result = _apply_env_overrides(raw)
        assert result["plugins"]["builtin"]["jellyfin"]["api_key"] == "jf_key"

    def test_unresolvable_env_var_skipped(self, monkeypatch, capsys):
        """Env var that can't be mapped to a plugin id emits a warning and is skipped."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_UNKNOWNXXX_SECRET", "val")
        raw = {}
        result = _apply_env_overrides(raw)
        # The unknown plugin should not be injected
        assert "unknownxxx" not in result.get("plugins", {}).get("builtin", {})
        captured = capsys.readouterr()
        assert "Warning" in captured.err

    def test_proxmox_token_secret_underscore_key(self, monkeypatch):
        """token_secret (underscore in key) resolves correctly for proxmox."""
        monkeypatch.setenv(
            "BUOY_PLUGINS_BUILTIN_PROXMOX_TOKEN_SECRET",
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        raw = {
            "plugins": {
                "builtin": {
                    "proxmox": {
                        "enabled": True,
                        "token_id": "user@pam!tok",
                        "token_secret": "",
                    }
                }
            }
        }
        result = _apply_env_overrides(raw)
        assert (
            result["plugins"]["builtin"]["proxmox"]["token_secret"]
            == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        )

    def test_end_to_end_build_config(self, monkeypatch):
        """Full pipeline: env var flows through _build_config into PluginEntry.settings."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_PLANE_API_KEY", "live-key")
        raw = {
            "plugins": {
                "builtin": {
                    "plane": {"enabled": True, "api_key": "", "url": "https://plane.example.com"}
                }
            }
        }
        raw = _apply_env_overrides(raw)
        config = _build_config(raw)
        assert config.plugins.builtin["plane"].settings["api_key"] == "live-key"

    def test_direct_apply_plugin_env_overrides(self, monkeypatch):
        """_apply_plugin_env_overrides can be called directly."""
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_IMMICH_API_KEY", "immich-secret")
        raw = {"plugins": {"builtin": {"immich": {"enabled": True, "api_key": ""}}}}
        result = _apply_plugin_env_overrides(raw)
        assert result["plugins"]["builtin"]["immich"]["api_key"] == "immich-secret"

    def test_existing_env_overrides_unaffected(self, monkeypatch):
        """Standard BUOY_NODE_NAME env override still works alongside plugin overrides."""
        monkeypatch.setenv("BUOY_NODE_NAME", "test-node")
        monkeypatch.setenv("BUOY_PLUGINS_BUILTIN_GITHUB_TOKEN", "tok")
        raw = {"plugins": {"builtin": {"github": {"enabled": True, "token": ""}}}}
        result = _apply_env_overrides(raw)
        assert result["node"]["name"] == "test-node"
        assert result["plugins"]["builtin"]["github"]["token"] == "tok"
