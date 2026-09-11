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
    When enabled (``plugins.builtin.prometheus_exporter.enabled=true``),
    ``create_app()`` registers the ``/metrics`` route.  The route is absent
    entirely when the plugin is disabled, so the endpoint is never reachable
    on installs that haven't opted in.  ``/metrics`` is also included in
    ``PROTECTED_PATHS``, so it is always rate-limited and is auth-gated
    whenever ``auth.enabled=true``.
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

    def demo_data(self) -> PanelData:
        """No I/O either way — same data as collect()."""
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

        # cpu/mem_used/mem_total/temp are None on platforms where SystemCollector
        # can't read them at all (non-Linux fallback stats, BUG-33; no CPU temp
        # sensor identified, BUG-28) — omit those metric lines entirely rather
        # than crash on float(None) or emit a literal "None" (invalid Prometheus
        # exposition format), matching the existing optional-NVMe-block pattern
        # below.
        cpu = stats.get("cpu")
        if cpu is not None:
            lines.append("# HELP buoy_cpu_percent CPU usage percentage")
            lines.append("# TYPE buoy_cpu_percent gauge")
            lines.append(f'buoy_cpu_percent{{host="{host}"}} {cpu}')

        mem_used = stats.get("mem_used")
        if mem_used is not None:
            lines.append("# HELP buoy_memory_used_bytes Memory used in bytes")
            lines.append("# TYPE buoy_memory_used_bytes gauge")
            mem_bytes = int(float(mem_used) * 1073741824)
            lines.append(f'buoy_memory_used_bytes{{host="{host}"}} {mem_bytes}')

        mem_total = stats.get("mem_total")
        if mem_total is not None:
            lines.append("# HELP buoy_memory_total_bytes Memory total in bytes")
            lines.append("# TYPE buoy_memory_total_bytes gauge")
            mem_total_bytes = int(float(mem_total) * 1073741824)
            lines.append(f'buoy_memory_total_bytes{{host="{host}"}} {mem_total_bytes}')

        temp = stats.get("temp")
        if temp is not None:
            lines.append("# HELP buoy_temperature_celsius CPU temperature")
            lines.append("# TYPE buoy_temperature_celsius gauge")
            lines.append(f'buoy_temperature_celsius{{host="{host}"}} {temp}')

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
