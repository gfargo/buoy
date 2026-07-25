"""Prometheus exporter plugin — exposes /metrics in Prometheus text format.

Unlike other plugins, this one doesn't render a panel. Instead, it registers
a /metrics route that Prometheus can scrape. The data comes from the same
collectors that power the dashboard — zero additional overhead.
"""

from __future__ import annotations

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class PrometheusExporterPlugin(Plugin):
    """Exposes a /metrics endpoint in Prometheus exposition format.

    This plugin is special: it doesn't have a frontend panel.
    The server.py checks for this plugin and adds the /metrics route.
    """

    manifest = PluginManifest(
        id="prometheus_exporter",
        name="Prometheus",
        icon="📈",
        description="Exposes /metrics for Prometheus scraping",
        version="1.0.0",
        config_schema={},
        refresh_interval=9999,  # Doesn't self-refresh; metrics are pulled on demand
    )

    async def collect(self) -> PanelData:
        """This plugin doesn't produce panel data."""
        return PanelData(status="ok", summary="/metrics active")

    @staticmethod
    def _escape_label_value(value: str) -> str:
        """Escape a Prometheus label value per the exposition format spec.

        Escaping order matters: backslash must be escaped before the others.
        """
        return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    @staticmethod
    def format_metrics(stats: dict) -> str:
        """Format collected stats as Prometheus text exposition.

        Args:
            stats: The combined stats dict from system + docker + disk collectors.

        Returns:
            Prometheus-format text.
        """
        lines = []
        host = PrometheusExporterPlugin._escape_label_value(stats.get("hostname", ""))
        lines.append("# HELP buoy_cpu_percent CPU usage percentage")
        lines.append("# TYPE buoy_cpu_percent gauge")
        lines.append(f'buoy_cpu_percent{{host="{host}"}} {stats.get("cpu", 0)}')

        lines.append("# HELP buoy_memory_used_bytes Memory used in bytes")
        lines.append("# TYPE buoy_memory_used_bytes gauge")
        mem_bytes = int(float(stats.get("mem_used", 0)) * 1073741824)
        lines.append(f'buoy_memory_used_bytes{{host="{host}"}} {mem_bytes}')

        lines.append("# HELP buoy_memory_total_bytes Memory total in bytes")
        lines.append("# TYPE buoy_memory_total_bytes gauge")
        mem_total_bytes = int(float(stats.get("mem_total", 0)) * 1073741824)
        lines.append(f'buoy_memory_total_bytes{{host="{host}"}} {mem_total_bytes}')

        lines.append("# HELP buoy_temperature_celsius CPU temperature")
        lines.append("# TYPE buoy_temperature_celsius gauge")
        lines.append(f'buoy_temperature_celsius{{host="{host}"}} {stats.get("temp", 0)}')

        lines.append("# HELP buoy_disk_used_percent Root disk usage percentage")
        lines.append("# TYPE buoy_disk_used_percent gauge")
        lines.append(f'buoy_disk_used_percent{{host="{host}"}} {stats.get("disk_pct", 0)}')

        lines.append("# HELP buoy_containers_running Number of running Docker containers")
        lines.append("# TYPE buoy_containers_running gauge")
        lines.append(f'buoy_containers_running{{host="{host}"}} {stats.get("containers", 0)}')

        lines.append("# HELP buoy_uptime_seconds System uptime in seconds")
        lines.append("# TYPE buoy_uptime_seconds gauge")
        uptime = stats.get("uptime_s")
        if uptime is None:
            uptime = stats.get("uptime_h", 0) * 3600 + stats.get("uptime_m", 0) * 60
        lines.append(f'buoy_uptime_seconds{{host="{host}"}} {uptime}')

        # NVMe metrics (if available)
        nvme = stats.get("nvme")
        if nvme:
            lines.append("# HELP buoy_nvme_temperature_celsius NVMe temperature")
            lines.append("# TYPE buoy_nvme_temperature_celsius gauge")
            lines.append(f'buoy_nvme_temperature_celsius{{host="{host}"}} {nvme.get("temp", 0)}')

            lines.append("# HELP buoy_nvme_wear_percent NVMe wear percentage")
            lines.append("# TYPE buoy_nvme_wear_percent gauge")
            lines.append(f'buoy_nvme_wear_percent{{host="{host}"}} {nvme.get("wear_pct", 0)}')

        lines.append("")
        return "\n".join(lines)
