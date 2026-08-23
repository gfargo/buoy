"""Tests for Buoy collectors (using mocked data / demo collectors)."""

import asyncio

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.demo import DemoDiskCollector, DemoDockerCollector, DemoSystemCollector


def _make_config(name="test-node"):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.network = NetworkConfig()
    config.features = FeaturesConfig()
    return config


class TestDemoSystemCollector:
    """Tests for the demo system collector (mock data)."""

    @pytest.mark.asyncio
    async def test_collect_returns_hostname(self):
        config = _make_config("demo-pi")
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert data["hostname"] == "demo-pi"

    @pytest.mark.asyncio
    async def test_collect_has_required_fields(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()

        required = [
            "hostname",
            "cpu",
            "mem_used",
            "mem_total",
            "temp",
            "uptime_h",
            "uptime_m",
            "uptime_s",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_cpu_in_range(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert 0 <= data["cpu"] <= 100

    @pytest.mark.asyncio
    async def test_temp_in_range(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert 0 <= data["temp"] <= 100

    @pytest.mark.asyncio
    async def test_nvme_data_present(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert "nvme" in data
        assert data["nvme"]["wear_pct"] >= 0

    @pytest.mark.asyncio
    async def test_collect_detail_structure(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect_detail()
        assert "cpu" in data
        assert "memory" in data
        assert "cores" in data["cpu"]
        assert "top_processes" in data["cpu"]
        assert len(data["cpu"]["top_processes"]) == 5


class TestDemoDockerCollector:
    """Tests for the demo Docker collector."""

    @pytest.mark.asyncio
    async def test_list_containers(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        containers = await coll.list_containers()
        assert len(containers) > 0
        assert "name" in containers[0]

    @pytest.mark.asyncio
    async def test_collect_summary(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.collect_summary()
        assert data["containers"] > 0
        assert len(data["containers_list"]) > 0

    @pytest.mark.asyncio
    async def test_inspect_container(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.inspect_container("grafana")
        assert data["name"] == "grafana"
        assert data["status"] == "running"
        assert "resources" in data
        # cpu_pct carries its own % suffix (from docker stats / demo); the
        # frontend must NOT append another one — otherwise the UI shows "1.23%%".
        assert data["resources"]["cpu_pct"].endswith("%"), (
            "cpu_pct must already include the '%' suffix so the frontend can "
            "render it directly without appending another '%'"
        )

    @pytest.mark.asyncio
    async def test_get_logs(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.get_logs("grafana")
        assert data["container"] == "grafana"
        assert len(data["lines"]) > 0

    @pytest.mark.asyncio
    async def test_restart_container(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.restart_container("grafana")
        assert data["success"] is True


class TestDemoDiskCollector:
    """Tests for the demo disk collector."""

    @pytest.mark.asyncio
    async def test_collect_summary(self):
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_summary()
        assert "disk_pct" in data
        assert 0 <= data["disk_pct"] <= 100

    @pytest.mark.asyncio
    async def test_collect_detail(self):
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_detail()
        assert "mounts" in data
        assert len(data["mounts"]) > 0
        assert "pct" in data["mounts"][0]


class TestDockerContainerNameValidation:
    """Test container name validation in the real Docker collector."""

    def test_valid_names(self):
        from buoy.collectors.docker import _valid_name

        assert _valid_name("grafana") is True
        assert _valid_name("my-container_1.0") is True
        assert _valid_name("plane-api-1") is True

    def test_invalid_names(self):
        from buoy.collectors.docker import _valid_name

        assert _valid_name("") is False
        assert _valid_name("-starts-with-dash") is False
        assert _valid_name("../../etc/passwd") is False
        assert _valid_name("a" * 200) is False
        assert _valid_name("has spaces") is False
        assert _valid_name("has;semicolon") is False


class TestDockerListContainersCache:
    """Tests for DockerCollector.list_containers() 5s TTL cache (SPEC §8.2)."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_uses_cache(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._fetch_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])

        first = await coll.list_containers()
        second = await coll.list_containers()

        assert first == second
        coll._fetch_containers.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._fetch_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])

        await coll.list_containers()
        coll._containers_cache_ts -= 6  # simulate TTL expiry
        await coll.list_containers()

        assert coll._fetch_containers.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_only_fetch_once(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._fetch_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])

        results = await asyncio.gather(
            coll.list_containers(), coll.list_containers(), coll.list_containers()
        )

        assert all(r == results[0] for r in results)
        coll._fetch_containers.assert_called_once()


class TestDiskCollectorLocalMounts:
    """Tests for the real DiskCollector's nsenter-less /proc/mounts fallback."""

    def test_filters_virtual_fs_and_dedupes_bind_mounts(self, tmp_path):
        """Virtual filesystems are skipped, and bind mounts of a real mount
        point (e.g. Docker's per-container /etc/hosts) collapse to the
        shortest (real) path on that device instead of listing both."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        bind_dir = real_dir / "bind"
        bind_dir.mkdir()

        proc_mounts = (
            "overlay / overlay rw 0 0\n"
            "tmpfs /dev tmpfs rw 0 0\n"
            f"/dev/sda1 {real_dir} ext4 rw 0 0\n"
            f"/dev/sda1 {bind_dir} ext4 rw 0 0\n"
        )

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert len(mounts) == 1
        assert mounts[0]["mount"] == str(real_dir)

    @pytest.mark.parametrize(
        "device",
        ["nas.local:/export", "//nas.local/share"],
        ids=["nfs", "cifs"],
    )
    def test_keeps_network_filesystem_mounts(self, tmp_path, device):
        """NFS/CIFS device fields (e.g. "host:/export", "//host/share")
        don't start with "/", but they're real mounts and must not be
        dropped from the reported list."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        net_dir = tmp_path / "net"
        net_dir.mkdir()

        proc_mounts = f"{device} {net_dir} nfs4 rw 0 0\n"

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert [m["mount"] for m in mounts] == [str(net_dir)]
        assert mounts[0]["fs"] == device

    def test_falls_back_to_root_when_nothing_real_found(self):
        """If every /proc/mounts line is virtual (e.g. an overlay-rooted
        container with no other real mount), fall back to root usage."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        proc_mounts = "overlay / overlay rw 0 0\ntmpfs /dev tmpfs rw 0 0\n"

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert len(mounts) == 1
        assert mounts[0]["mount"] == "/"


class TestDiskCollectorNvme:
    """Tests for real DiskCollector NVMe SMART path."""

    @pytest.mark.asyncio
    async def test_nvme_smart_returns_none_when_unavailable(self):
        """_nvme_smart returns None gracefully when nsenter and smartctl are absent."""
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        # Both nsenter and direct smartctl calls raise FileNotFoundError
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("smartctl not found"),
        ):
            result = await coll._nvme_smart()

        assert result is None

    @pytest.mark.asyncio
    async def test_demo_disk_nvme_in_summary(self):
        """DemoDiskCollector always returns nvme data in collect_summary."""
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_summary()
        assert "nvme" in data
        nvme = data["nvme"]
        assert "temp" in nvme
        assert "wear_pct" in nvme
        assert "power_hours" in nvme
        assert "read" in nvme
        assert "written" in nvme


class TestNetworkLatency:
    """Tests for NetworkCollector tailscale ping and HTTP fallback."""

    def _make_net_config(self, peers=None):
        from buoy.config import PeerConfig

        config = _make_config("compass")
        if peers:
            config.network.peers = [PeerConfig(name=n, url=u) for n, u in peers]
        return config

    def _mock_proc(self, returncode, stdout):
        from unittest.mock import AsyncMock, MagicMock

        proc = MagicMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        return proc

    @pytest.mark.asyncio
    async def test_tailscale_ping_parses_latency(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config()
        coll = NetworkCollector(config)
        proc = self._mock_proc(0, b"pong from compass (100.64.67.98) via DERP(nyc) in 2.1ms\n")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await coll._tailscale_ping("compass")

        assert result == 2.1

    @pytest.mark.asyncio
    async def test_tailscale_ping_returns_none_on_failure(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config()
        coll = NetworkCollector(config)
        proc = self._mock_proc(1, b"")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await coll._tailscale_ping("compass")

        assert result is None

    @pytest.mark.asyncio
    async def test_measure_latency_falls_back_to_http(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "http://harbor.local")])
        coll = NetworkCollector(config)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client):
                results = await coll.measure_latency()

        assert len(results) == 1
        assert results[0]["online"] is True
        assert results[0]["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_measure_latency_self_node(self):
        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("compass", "http://compass.local")])
        coll = NetworkCollector(config)
        results = await coll.measure_latency()

        assert results == [{"name": "compass", "latency_ms": 0, "online": True}]

    # --- verify_ssl propagation tests ---

    def _make_mock_client(self, status_code=200):
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json = MagicMock(return_value={})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        return mock_client

    @pytest.mark.asyncio
    async def test_collect_uses_verify_true_by_default(self):
        """collect() passes verify=True to AsyncClient when network.verify_ssl defaults."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        # Default: network.verify_ssl=True, peer.verify_ssl=None
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await coll.collect()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is True

    @pytest.mark.asyncio
    async def test_collect_uses_network_verify_false(self):
        """collect() passes verify=False when network.verify_ssl=False."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        config.network.verify_ssl = False
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await coll.collect()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_collect_per_peer_override_wins(self):
        """Per-peer verify_ssl=False wins over network.verify_ssl=True."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector
        from buoy.config import PeerConfig

        config = self._make_net_config()
        config.network.verify_ssl = True
        config.network.peers = [PeerConfig(name="harbor", url="https://harbor.local", verify_ssl=False)]
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await coll.collect()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_measure_latency_http_fallback_verify_true(self):
        """HTTP fallback in measure_latency() uses verify=True by default."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
                await coll.measure_latency()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is True

    @pytest.mark.asyncio
    async def test_measure_latency_http_fallback_verify_false(self):
        """HTTP fallback in measure_latency() uses verify=False when network.verify_ssl=False."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        config.network.verify_ssl = False
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
                await coll.measure_latency()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_measure_latency_per_peer_override_wins(self):
        """Per-peer verify_ssl=False wins in measure_latency() HTTP fallback."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector
        from buoy.config import PeerConfig

        config = self._make_net_config()
        config.network.verify_ssl = True
        config.network.peers = [PeerConfig(name="harbor", url="https://harbor.local", verify_ssl=False)]
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
                await coll.measure_latency()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False


class TestDockerListContainerStates:
    """Tests for DockerCollector.list_container_states()."""

    def _make_proc(self, returncode, stdout):
        from unittest.mock import AsyncMock, MagicMock

        proc = MagicMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout.encode(), b""))
        return proc

    @pytest.mark.asyncio
    async def test_parses_running_and_stopped_containers(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)

        ps_proc = self._make_proc(0, "abc123\ndef456\n")
        inspect_lines = (
            '{"name":"/grafana","status":"running","restart_count":0}\n'
            '{"name":"/redis","status":"exited","restart_count":3}\n'
        )
        inspect_proc = self._make_proc(0, inspect_lines)

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ps_proc if call_count == 1 else inspect_proc

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)):
            result = await coll.list_container_states()

        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"grafana", "redis"}

        grafana = next(r for r in result if r["name"] == "grafana")
        assert grafana["status"] == "running"
        assert grafana["restart_count"] == 0

        redis = next(r for r in result if r["name"] == "redis")
        assert redis["status"] == "exited"
        assert redis["restart_count"] == 3

    @pytest.mark.asyncio
    async def test_strips_leading_slash_from_name(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)

        ps_proc = self._make_proc(0, "abc123\n")
        inspect_proc = self._make_proc(
            0, '{"name":"/my-container","status":"running","restart_count":0}\n'
        )

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ps_proc if call_count == 1 else inspect_proc

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)):
            result = await coll.list_container_states()

        assert result[0]["name"] == "my-container"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_containers(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        ps_proc = self._make_proc(0, "")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=ps_proc)):
            result = await coll.list_container_states()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_ps_failure(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        ps_proc = self._make_proc(1, "")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=ps_proc)):
            result = await coll.list_container_states()

        assert result == []


class TestDemoDockerListContainerStates:
    """Tests for DemoDockerCollector.list_container_states()."""

    @pytest.mark.asyncio
    async def test_returns_states_for_all_demo_containers(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        states = await coll.list_container_states()
        assert len(states) > 0
        for s in states:
            assert "name" in s
            assert "status" in s
            assert "restart_count" in s
            assert isinstance(s["restart_count"], int)

    @pytest.mark.asyncio
    async def test_all_demo_containers_are_running(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        states = await coll.list_container_states()
        for s in states:
            assert s["status"] == "running"
