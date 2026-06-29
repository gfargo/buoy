"""TLS certificate expiry plugin — days remaining per cert in a directory."""

from __future__ import annotations

import ssl
import time
from pathlib import Path

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


def _days_remaining(expiry_epoch: float, now: float) -> int:
    """Return whole days until expiry (negative if already expired)."""
    return int((expiry_epoch - now) / 86400)


def _parse_cert(path: Path) -> float | None:
    """Return the notAfter epoch for the first cert in *path*, or None on failure."""
    try:
        info = ssl._ssl._test_decode_cert(str(path))  # noqa: SLF001
        not_after = info.get("notAfter")
        if not_after:
            return ssl.cert_time_to_seconds(not_after)
    except Exception:  # noqa: BLE001
        pass
    return None


class CertExpiryPlugin(Plugin):
    """Shows TLS certificate expiry in days for certs in a configured directory."""

    manifest = PluginManifest(
        id="cert_expiry",
        name="Certs",
        icon="🔐",
        description="TLS certificate days remaining",
        version="1.0.0",
        config_schema={
            "cert_dir": {"type": "string", "default": "/etc/caddy/certs"},
            "warn_days": {"type": "integer", "default": 30},
            "critical_days": {"type": "integer", "default": 7},
        },
        refresh_interval=3600,
    )

    async def collect(self) -> PanelData:
        cert_dir = Path(self.config.get("cert_dir", "/etc/caddy/certs"))
        warn_days = int(self.config.get("warn_days", 30))
        critical_days = int(self.config.get("critical_days", 7))

        if not cert_dir.is_dir():
            return PanelData(status="disabled", summary="Not configured")

        cert_files = [
            p for p in cert_dir.iterdir() if p.suffix in {".crt", ".pem"} and p.is_file()
        ]
        if not cert_files:
            return PanelData(status="disabled", summary="Not configured")

        now = time.time()
        certs = []
        for path in sorted(cert_files):
            expiry = _parse_cert(path)
            if expiry is None:
                continue
            days = _days_remaining(expiry, now)
            if days < critical_days:
                cert_status = "error"
            elif days <= warn_days:
                cert_status = "warn"
            else:
                cert_status = "ok"
            # Use filename stem as display name (drop .crt/.pem suffix)
            name = path.stem
            certs.append({"name": name, "days": days, "status": cert_status})

        if not certs:
            return PanelData(status="disabled", summary="Not configured")

        # Aggregate: worst status wins
        if any(c["status"] == "error" for c in certs):
            overall = "error"
        elif any(c["status"] == "warn" for c in certs):
            overall = "warn"
        else:
            overall = "ok"

        if len(certs) == 1:
            summary = f"{certs[0]['name']}: {certs[0]['days']}d"
        else:
            soonest = min(certs, key=lambda c: c["days"])
            summary = f"{len(certs)} certs · soonest {soonest['days']}d"

        return PanelData(status=overall, summary=summary, detail={"certs": certs})

    def frontend_js(self) -> str | None:
        return """
function render_cert_expiry(data) {
  const certs = (data.detail && data.detail.certs) || [];
  if (!certs.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No certs found</div>';
  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.55rem">';
  certs.forEach(c => {
    const color = c.status === 'ok' ? 'var(--green)' : c.status === 'warn' ? 'var(--amber)' : 'var(--red)';
    const dot = '<span style="color:' + color + '">●</span>';
    const label = c.days < 0 ? 'expired' : c.days + 'd remaining';
    html += '<tr>';
    html += '<td style="padding:0.15rem 0.3rem;color:var(--text)">' + c.name + '</td>';
    html += '<td style="padding:0.15rem 0.3rem;color:' + color + '">' + label + '</td>';
    html += '<td style="padding:0.15rem 0.3rem">' + dot + '</td>';
    html += '</tr>';
  });
  html += '</table>';
  return html;
}
"""
