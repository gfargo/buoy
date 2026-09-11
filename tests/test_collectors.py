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


class TestDiskCollectorRootPercentConsistency:
    """Tests for _root_disk_percent() agreeing with _all_mounts() (BUG-25).

    The headline gauge (collect_summary()'s disk_pct) used to read this
    process's own container rootfs via shutil.disk_usage("/"), while the
    detail panel's mount table (collect_detail()'s mounts) read the host's
    view via nsenter. The two could legitimately disagree.
    """

    @pytest.mark.asyncio
    async def test_root_percent_matches_all_mounts_root_entry(self):
        """The gauge must read its value from the same mount list the
        detail panel uses, not take an independent (and possibly
        container-scoped) measurement."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        host_mounts = [
            {
                "fs": "/dev/sda1",
                "size": "500G",
                "used": "450G",
                "avail": "50G",
                "pct": 90,
                "mount": "/",
            },
            {
                "fs": "/dev/sdb1",
                "size": "4T",
                "used": "1T",
                "avail": "3T",
                "pct": 25,
                "mount": "/mnt/storage",
            },
        ]

        with patch.object(coll, "_all_mounts", new=AsyncMock(return_value=host_mounts)):
            pct = await coll._root_disk_percent()

        # The host's root usage (90%), not whatever this test process's own
        # shutil.disk_usage("/") happens to report.
        assert pct == 90

    @pytest.mark.asyncio
    async def test_root_percent_falls_back_when_no_root_mount_present(self):
        """If the resolved mount list has no "/" entry at all (unexpected,
        but possible), fall back to this process's own root usage rather
        than crashing or silently reporting 0."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        mounts_without_root = [
            {
                "fs": "/dev/sdb1",
                "size": "4T",
                "used": "1T",
                "avail": "3T",
                "pct": 25,
                "mount": "/data",
            }
        ]

        with patch.object(coll, "_all_mounts", new=AsyncMock(return_value=mounts_without_root)):
            pct = await coll._root_disk_percent()

        assert 0 <= pct <= 100

    @pytest.mark.asyncio
    async def test_collect_summary_and_collect_detail_agree(self):
        """End-to-end: collect_summary()'s disk_pct and collect_detail()'s
        "/" mount entry must come from the same underlying data."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        host_mounts = [
            {
                "fs": "/dev/sda1",
                "size": "500G",
                "used": "450G",
                "avail": "50G",
                "pct": 90,
                "mount": "/",
            }
        ]

        with (
            patch.object(coll, "_nsenter_mounts", new=AsyncMock(return_value=host_mounts)),
            patch.object(coll, "_nvme_smart", new=AsyncMock(return_value=None)),
            patch.object(
                coll, "_disk_io", new=AsyncMock(return_value={"read_gb": 0, "write_gb": 0})
            ),
        ):
            summary = await coll.collect_summary()
            detail = await coll.collect_detail()

        root_entry = next(m for m in detail["mounts"] if m["mount"] == "/")
        assert summary["disk_pct"] == root_entry["pct"] == 90


class TestDiskCollectorMountsCache:
    """Tests for _all_mounts()' 5s TTL cache (mirrors DockerCollector's
    list_containers cache — nsenter+df is a real subprocess spawn, and
    _all_mounts() is now on the hot path via _root_disk_percent())."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_uses_cache(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(return_value=[{"mount": "/", "pct": 42}])

        first = await coll._all_mounts()
        second = await coll._all_mounts()

        assert first == second
        coll._nsenter_mounts.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(return_value=[{"mount": "/", "pct": 42}])

        await coll._all_mounts()
        coll._mounts_cache_ts -= 6  # simulate TTL expiry (_MOUNTS_CACHE_TTL = 5.0)
        await coll._all_mounts()

        assert coll._nsenter_mounts.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_only_fetch_once(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(return_value=[{"mount": "/", "pct": 42}])

        results = await asyncio.gather(coll._all_mounts(), coll._all_mounts(), coll._all_mounts())

        assert all(r == results[0] for r in results)
        coll._nsenter_mounts.assert_called_once()


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


class TestDiskCollectorIo:
    """Tests for the real DiskCollector's /proc/diskstats-based I/O totals.

    Regression coverage for BUG-26: _disk_io() used to match only an exact
    device-name allowlist ("nvme0n1", "sda", "mmcblk0"), so a VM
    (vda/xvda), a second SATA disk (sdb), or an additional NVMe drive
    reported 0 GB read/write regardless of actual activity.
    """

    ONE_GB_SECTORS = 2097152  # 1 GiB / 512-byte sectors

    @staticmethod
    def _diskstat_line(major, minor, name, read_sectors, write_sectors):
        return f"{major} {minor} {name} 100 0 {read_sectors} 100 200 0 {write_sectors} 200 0 0 0\n"

    async def _run(self, lines: list[str]) -> dict:
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data="".join(lines))):
            return await coll._disk_io()

    @pytest.mark.asyncio
    async def test_vm_virtio_disk_is_recognized(self):
        """A VM's virtio disk (vda) used to report 0 GB — it's not in the
        old exact-match allowlist at all."""
        lines = [self._diskstat_line(252, 0, "vda", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS)]
        result = await self._run(lines)
        assert result == {"read_gb": 1.0, "write_gb": 1.0}

    @pytest.mark.asyncio
    async def test_xen_virtio_disk_is_recognized(self):
        lines = [self._diskstat_line(202, 0, "xvda", self.ONE_GB_SECTORS, 0)]
        result = await self._run(lines)
        assert result == {"read_gb": 1.0, "write_gb": 0.0}

    @pytest.mark.asyncio
    async def test_second_sata_disk_is_summed_with_first(self):
        """A second real disk (sdb) used to be invisible — only the exact
        name "sda" matched. Both disks' I/O must be summed, and their
        partitions excluded (already counted via the whole device)."""
        lines = [
            self._diskstat_line(8, 0, "sda", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS // 2),
            self._diskstat_line(8, 1, "sda1", 1_000_000, 500_000),  # partition, excluded
            self._diskstat_line(8, 16, "sdb", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS // 2),
            self._diskstat_line(8, 17, "sdb1", 1_000_000, 500_000),  # partition, excluded
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 2.0, "write_gb": 1.0}

    @pytest.mark.asyncio
    async def test_additional_nvme_drive_is_summed(self):
        """A second NVMe drive (nvme1n1) used to be invisible — only
        "nvme0n1" matched exactly."""
        lines = [
            self._diskstat_line(259, 0, "nvme0n1", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS),
            self._diskstat_line(259, 1, "nvme0n1p1", 500_000, 500_000),  # partition, excluded
            self._diskstat_line(259, 2, "nvme1n1", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS),
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 2.0, "write_gb": 2.0}

    @pytest.mark.asyncio
    async def test_mmcblk_partition_excluded_from_whole_device(self):
        lines = [
            self._diskstat_line(179, 0, "mmcblk0", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS),
            self._diskstat_line(179, 1, "mmcblk0p1", 500_000, 500_000),  # partition, excluded
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 1.0, "write_gb": 1.0}

    @pytest.mark.asyncio
    async def test_virtual_devices_excluded(self):
        """loop/dm/md devices are excluded — their I/O is already accounted
        for on the physical device(s) underneath, so counting them too
        would double-count."""
        lines = [
            self._diskstat_line(7, 0, "loop0", 1_000_000, 0),
            self._diskstat_line(253, 0, "dm-0", 1_000_000, 1_000_000),
            self._diskstat_line(9, 0, "md0", 1_000_000, 1_000_000),
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 0, "write_gb": 0}


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
        config.network.peers = [
            PeerConfig(name="harbor", url="https://harbor.local", verify_ssl=False)
        ]
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
    async def test_measure_latency_runs_peers_concurrently(self):
        """BUG-34 regression: measuring N peers must take ~one peer's latency,
        not N times that — each peer used to be awaited serially inside a
        plain `for` loop, so slow/offline peers could make the whole poll
        overrun its own refresh interval."""
        import time
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        per_peer_delay = 0.2
        num_peers = 4
        config = self._make_net_config(
            [(f"peer{i}", f"http://peer{i}.local") for i in range(num_peers)]
        )
        coll = NetworkCollector(config)

        async def slow_ping(_peer_name):
            await asyncio.sleep(per_peer_delay)
            return 1.0

        with patch.object(coll, "_tailscale_ping", new=AsyncMock(side_effect=slow_ping)):
            start = time.monotonic()
            results = await coll.measure_latency()
            elapsed = time.monotonic() - start

        assert len(results) == num_peers
        # Serial execution would take num_peers * per_peer_delay (~0.8s); a
        # generous ceiling well under that catches a regression to the old
        # sequential loop without being flaky under CI scheduling jitter.
        assert elapsed < per_peer_delay * (num_peers / 2)

    @pytest.mark.asyncio
    async def test_measure_latency_per_peer_override_wins(self):
        """Per-peer verify_ssl=False wins in measure_latency() HTTP fallback."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector
        from buoy.config import PeerConfig

        config = self._make_net_config()
        config.network.verify_ssl = True
        config.network.peers = [
            PeerConfig(name="harbor", url="https://harbor.local", verify_ssl=False)
        ]
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


class TestSystemCollectorMemory:
    """Tests for the real SystemCollector's /proc/meminfo-based memory reads.

    Regression coverage for BUG-29 (used = MemTotal - MemFree - Buffers -
    Cached overstated usage vs free/htop, since it ignores SReclaimable and
    Shmem) and BUG-30 (memory detail's top_processes was hardcoded empty).
    """

    MEMINFO = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         1024000 kB\n"
        "MemAvailable:    9000000 kB\n"
        "Buffers:          500000 kB\n"
        "Cached:          6000000 kB\n"
        "SwapTotal:       2048000 kB\n"
        "SwapFree:        1500000 kB\n"
    )

    def test_used_matches_total_minus_available(self):
        """used should track MemAvailable, not the free/buffers/cached formula
        that ignores reclaimable slab and shared memory."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=self.MEMINFO)):
            used_gb, total_gb = coll._read_memory()

        # 16384000 - 9000000 = 7384000 kB ≈ 7.0 GB, not the ~8.9 GB the old
        # (MemFree+Buffers+Cached) formula would have produced.
        assert used_gb == pytest.approx(7.0, abs=0.01)
        assert total_gb == pytest.approx(15.6, abs=0.01)

    def test_detail_used_mb_matches_total_minus_available(self):
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=self.MEMINFO)):
            detail = coll._read_memory_detail()

        assert detail["used_mb"] == detail["total_mb"] - detail["available_mb"]

    def test_falls_back_to_memfree_when_memavailable_missing(self):
        """Very old kernels don't expose MemAvailable at all."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        meminfo = "MemTotal:       16384000 kB\nMemFree:         1024000 kB\n"
        coll = SystemCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=meminfo)):
            used_gb, total_gb = coll._read_memory()

        assert used_gb == pytest.approx(14.6, abs=0.01)
        assert total_gb == pytest.approx(15.6, abs=0.01)

    @pytest.mark.asyncio
    async def test_collect_detail_populates_top_processes(self):
        """collect_detail() must wire memory.top_processes from
        _top_processes_by("mem") instead of leaving it hardcoded empty."""
        from unittest.mock import AsyncMock, mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        coll._is_linux = True  # exercise the real (non-fallback) path regardless of test host OS
        fake_top = [{"pid": 1, "cpu": 0.1, "mem": 42.0, "cmd": "buoy"}]
        top_processes_mock = AsyncMock(return_value=fake_top)

        with (
            patch("builtins.open", mock_open(read_data=self.MEMINFO)),
            patch.object(coll, "_top_processes_by", new=top_processes_mock),
            patch.object(coll, "_read_cpu_detail", new=AsyncMock(return_value={})),
        ):
            detail = await coll.collect_detail()

        assert detail["memory"]["top_processes"] == fake_top
        top_processes_mock.assert_awaited_once_with("mem")


class TestSystemCollectorCpu:
    """Tests for the real SystemCollector's /proc/stat-based CPU sampling.

    Regression coverage for BUG-31: _read_cpu() used to block for a fixed
    100ms on every call (two samples taken 100ms apart back-to-back) instead
    of keeping the previous sample on the instance and computing the delta
    across calls.
    """

    @staticmethod
    def _stat_line(user, nice, system, idle, iowait=0, irq=0, softirq=0, steal=0):
        return f"cpu  {user} {nice} {system} {idle} {iowait} {irq} {softirq} {steal} 0 0\n"

    @pytest.mark.asyncio
    async def test_first_call_returns_zero_with_no_prior_sample(self):
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stat = self._stat_line(100, 0, 50, 800)
        with patch("builtins.open", mock_open(read_data=stat)):
            result = await coll._read_cpu()

        assert result == 0

    @pytest.mark.asyncio
    async def test_second_call_computes_delta_against_previous_sample(self):
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        first = self._stat_line(100, 0, 50, 800)
        second = self._stat_line(150, 0, 70, 850)

        with patch("builtins.open", mock_open(read_data=first)):
            await coll._read_cpu()
        with patch("builtins.open", mock_open(read_data=second)):
            result = await coll._read_cpu()

        # idle_delta = 850-800 = 50, total_delta = 1070-950 = 120
        # 100 * (1 - 50/120) = 58.33 -> int() truncates to 58
        assert result == 58

    @pytest.mark.asyncio
    async def test_does_not_sleep(self):
        """The old implementation awaited asyncio.sleep(0.1) on every call;
        the fix removes that fixed blocking window entirely."""
        from unittest.mock import AsyncMock, mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stat = self._stat_line(100, 0, 50, 800)
        sleep_mock = AsyncMock()
        with (
            patch("builtins.open", mock_open(read_data=stat)),
            patch("asyncio.sleep", sleep_mock),
        ):
            await coll._read_cpu()

        sleep_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_total_delta_returns_zero(self):
        """Two identical samples (e.g. calls close enough together that the
        jiffy counters haven't moved) must not raise ZeroDivisionError."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stat = self._stat_line(100, 0, 50, 800)

        with patch("builtins.open", mock_open(read_data=stat)):
            await coll._read_cpu()
            result = await coll._read_cpu()

        assert result == 0


class TestSystemCollectorTemperature:
    """Tests for the real SystemCollector's CPU temperature detection (BUG-28).

    thermal_zone0 is the CPU on a Raspberry Pi but is frequently `acpitz`,
    a wifi radio, or a battery sensor on x86 — _read_temperature() used to
    always read zone0 by a fixed index regardless of what it actually was,
    reporting the wrong sensor (or 0°C, indistinguishable from a real cold
    reading) on non-Pi hardware.
    """

    @staticmethod
    def _patch_sysfs(hwmon: dict | None = None, thermal: dict | None = None):
        """Fake /sys/class/hwmon and /sys/class/thermal trees.

        hwmon: {"hwmon0": {"name": "coretemp", "temp1_input": "45000"}, ...}
        thermal: {"thermal_zone0": {"type": "cpu-thermal", "temp": "42000"}, ...}
        """
        import io
        from unittest.mock import patch

        hwmon = hwmon or {}
        thermal = thermal or {}

        files: dict[str, str] = {}
        for entry, attrs in hwmon.items():
            for fname, content in attrs.items():
                files[f"/sys/class/hwmon/{entry}/{fname}"] = content
        for entry, attrs in thermal.items():
            for fname, content in attrs.items():
                files[f"/sys/class/thermal/{entry}/{fname}"] = content

        def fake_listdir(path):
            if path == "/sys/class/hwmon":
                return sorted(hwmon.keys())
            if path == "/sys/class/thermal":
                return sorted(thermal.keys())
            raise FileNotFoundError(path)

        def fake_open(path, *args, **kwargs):
            if path in files:
                return io.StringIO(files[path])
            raise FileNotFoundError(path)

        return (
            patch("os.listdir", side_effect=fake_listdir),
            patch("builtins.open", side_effect=fake_open),
        )

    def test_prefers_coretemp_hwmon_over_thermal_zone(self):
        """A coretemp hwmon driver wins even if thermal_zone0 exists with an
        unrelated type — hwmon is checked before any thermal zone scan."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            hwmon={"hwmon0": {"name": "coretemp", "temp1_input": "45000"}},
            thermal={"thermal_zone0": {"type": "iwlwifi_1", "temp": "30000"}},
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 45

    def test_falls_back_to_cpu_thermal_zone_type_when_no_hwmon(self):
        """Pi-style: no relevant hwmon driver, but a zone typed cpu-thermal
        exists — found by type, regardless of its index."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={
                "thermal_zone0": {"type": "iwlwifi_1", "temp": "30000"},
                "thermal_zone1": {"type": "cpu-thermal", "temp": "52000"},
            },
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 52

    def test_falls_back_to_acpitz_as_last_resort(self):
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={"thermal_zone0": {"type": "acpitz", "temp": "48000"}},
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 48

    def test_prefers_specific_zone_type_over_acpitz(self):
        """acpitz is a best-effort last resort — a more specific CPU zone
        type wins if both are present on the same host."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={
                "thermal_zone0": {"type": "acpitz", "temp": "40000"},
                "thermal_zone1": {"type": "x86_pkg_temp", "temp": "55000"},
            },
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 55

    def test_returns_none_when_no_matching_sensor_anywhere(self):
        """A wifi/battery-only thermal zone with no matching hwmon must not
        be silently reported as 0°C — that's indistinguishable from a real
        (very cold) reading."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={"thermal_zone0": {"type": "iwlwifi_1", "temp": "30000"}},
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp is None

    def test_returns_none_when_hwmon_path_does_not_exist(self):
        """A restrictive container without /sys/class/hwmon mounted must not
        raise — falls through to the thermal zone scan."""
        from unittest.mock import patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with patch("os.listdir", side_effect=FileNotFoundError):
            temp = coll._read_hwmon_cpu_temp()

        assert temp is None
