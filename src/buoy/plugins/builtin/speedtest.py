"""Speedtest plugin — periodic internet speed tests with trend tracking.

Runs speedtest-cli in a background task (not in collect()) because a speed test
can exceed the 30-second collect() timeout enforced by the loader.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_HISTORY_PATHS = [Path("/data/speedtest_history.json"), Path("speedtest_history.json")]
_MAX_HISTORY = 100


class SpeedtestPlugin(Plugin):
    """Runs internet speed tests on a long interval and tracks download/upload/ping trends."""

    manifest = PluginManifest(
        id="speedtest",
        name="Speedtest",
        icon="🌐",
        description="Periodic internet speed tests with trend tracking",
        version="1.0.0",
        config_schema={
            "interval_hours": {"type": "number", "default": 6},
            "server_id": {"type": "string"},
        },
        refresh_interval=60,
    )

    def __init__(self):
        super().__init__()
        self._history: list[dict] = []
        self._task: asyncio.Task | None = None

    async def setup(self) -> None:
        self._load_history()
        self._task = asyncio.create_task(self._loop())

    async def teardown(self) -> None:
        if self._task:
            self._task.cancel()

    async def collect(self) -> PanelData:
        """Return latest cached result instantly; never blocks on a subprocess."""
        if not self._history:
            return PanelData(status="ok", summary="Measuring…", detail={})

        latest = self._history[-1]
        if not latest.get("ok"):
            return PanelData(
                status="error",
                summary="Last test failed",
                detail={"history": self._history[-20:], "error": latest.get("error", "")},
            )

        status = self._compute_status()
        dl = latest.get("download_mbps", 0.0)
        ul = latest.get("upload_mbps", 0.0)
        ping = latest.get("ping_ms", 0.0)
        summary = f"↓ {dl:.0f} Mbps · ↑ {ul:.0f} Mbps · {ping:.0f} ms"

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "latest": latest,
                "history": self._history[-20:],
                "baseline_mbps": self._baseline(),
            },
        )

    # ── Background task ────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        interval_secs = self.config.get("interval_hours", 6) * 3600

        # Run immediately on startup if history is empty or stale
        if not self._history or (time.time() - self._history[-1].get("ts", 0)) > interval_secs:
            await self._run_and_save()

        while True:
            await asyncio.sleep(interval_secs)
            await self._run_and_save()

    async def _run_and_save(self) -> None:
        entry = await self._run_test()
        self._history.append(entry)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]
        self._save_history()

    async def _run_test(self) -> dict:
        """Run speedtest-cli --json and return a normalised result dict."""
        ts = time.time()
        server_id = self.config.get("server_id")

        cmd = ["speedtest-cli", "--json"]
        if server_id:
            cmd += ["--server", str(server_id)]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
            data = json.loads(stdout.decode())
            return {
                "ts": ts,
                # speedtest-cli reports bits/s; convert to Mbps
                "download_mbps": data["download"] / 1e6,
                "upload_mbps": data["upload"] / 1e6,
                "ping_ms": data["ping"],
                "server": data.get("server", {}).get("name", ""),
                "ok": True,
            }
        except FileNotFoundError:
            return {"ts": ts, "download_mbps": 0.0, "upload_mbps": 0.0, "ping_ms": 0.0, "server": "", "ok": False, "error": "speedtest-cli not found"}
        except Exception as exc:
            return {"ts": ts, "download_mbps": 0.0, "upload_mbps": 0.0, "ping_ms": 0.0, "server": "", "ok": False, "error": str(exc)}

    # ── Status & baseline ──────────────────────────────────────────────────────

    def _baseline(self) -> float:
        """Median download (Mbps) over the last 10 successful tests."""
        samples = [e["download_mbps"] for e in self._history if e.get("ok")]
        if not samples:
            return 0.0
        return statistics.median(samples[-10:])

    def _compute_status(self) -> str:
        if not self._history:
            return "ok"
        latest = self._history[-1]
        if not latest.get("ok"):
            return "error"
        baseline = self._baseline()
        if baseline > 0 and latest["download_mbps"] < baseline * 0.5:
            return "warn"
        return "ok"

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_history(self) -> None:
        for path in _HISTORY_PATHS:
            try:
                raw = json.loads(path.read_text())
                if isinstance(raw, list):
                    self._history = raw[-_MAX_HISTORY:]
                    return
            except Exception:
                pass

    def _save_history(self) -> None:
        for path in _HISTORY_PATHS:
            try:
                path.write_text(json.dumps(self._history))
                return
            except Exception:
                pass

    # ── Frontend ───────────────────────────────────────────────────────────────

    def frontend_js(self) -> str | None:
        return """
function render_speedtest(data) {
  const detail = data.detail || {};
  const latest = detail.latest || {};
  const history = detail.history || [];
  const baseline = detail.baseline_mbps || 0;

  if (!latest || !latest.ok) {
    const msg = (latest && latest.error) ? latest.error : 'Measuring…';
    return '<div style="font-size:0.6rem;color:var(--text-dim);padding:0.4rem">' + msg + '</div>';
  }

  const dl = (latest.download_mbps || 0).toFixed(0);
  const ul = (latest.upload_mbps || 0).toFixed(0);
  const ping = (latest.ping_ms || 0).toFixed(0);
  const server = latest.server || '';

  // Sparkline bars for download history
  const dlPoints = history.filter(e => e.ok).map(e => e.download_mbps || 0);
  let sparkHtml = '';
  if (dlPoints.length > 1) {
    const max = Math.max(...dlPoints) || 1;
    const H = 20;
    const bw = Math.max(3, Math.floor(120 / dlPoints.length));
    const statusColor = data.status === 'warn' ? 'var(--amber)' : 'var(--cyan)';
    sparkHtml = '<svg width="' + (dlPoints.length * bw) + '" height="' + H + '" style="display:block;margin-bottom:0.3rem">';
    dlPoints.forEach(function(v, i) {
      const h = Math.max(2, Math.round((v / max) * H));
      const fill = (i === dlPoints.length - 1) ? statusColor : 'var(--border)';
      sparkHtml += '<rect x="' + (i * bw) + '" y="' + (H - h) + '" width="' + (bw - 1) + '" height="' + h + '" fill="' + fill + '"/>';
    });
    sparkHtml += '</svg>';
  }

  let html = '<div style="padding:0.4rem 0.5rem;font-size:0.6rem">';
  html += sparkHtml;
  html += '<div style="display:flex;gap:1rem">';
  html += '<span style="color:var(--text)">↓ ' + dl + ' Mbps</span>';
  html += '<span style="color:var(--text)">↑ ' + ul + ' Mbps</span>';
  html += '<span style="color:var(--text-dim)">⏱ ' + ping + ' ms</span>';
  html += '</div>';
  if (server) {
    html += '<div style="color:var(--text-dim);margin-top:0.2rem;font-size:0.55rem">' + server + '</div>';
  }
  if (baseline > 0) {
    html += '<div style="color:var(--text-dim);margin-top:0.2rem;font-size:0.55rem">baseline: ' + baseline.toFixed(0) + ' Mbps</div>';
  }
  html += '</div>';
  return html;
}
"""
