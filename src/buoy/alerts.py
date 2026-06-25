"""Alert engine — threshold detection and notification dispatch.

Monitors collected metrics and fires alerts when thresholds are breached.
Alerts are pushed via WebSocket to the frontend and optionally sent to
external webhooks (Discord, Slack, generic HTTP).
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

# Default thresholds (can be overridden in config in the future)
DEFAULT_THRESHOLDS = {
    "cpu": {"warn": 80, "crit": 95, "duration": 60},       # % for 60s
    "memory": {"warn": 85, "crit": 95, "duration": 60},    # % for 60s
    "disk": {"warn": 80, "crit": 90, "duration": 0},       # % immediate
    "temp": {"warn": 75, "crit": 85, "duration": 30},      # °C for 30s
}


@dataclass
class Alert:
    """An active or recently fired alert."""

    metric: str
    level: str  # "warn" | "crit"
    value: float
    threshold: float
    message: str
    fired_at: float = field(default_factory=time.time)
    resolved_at: float | None = None

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "level": self.level,
            "value": self.value,
            "threshold": self.threshold,
            "message": self.message,
            "fired_at": self.fired_at,
            "resolved_at": self.resolved_at,
            "active": self.is_active,
        }


class AlertEngine:
    """Evaluates metrics against thresholds and manages alert lifecycle."""

    def __init__(self, config: BuoyConfig, broadcast_fn: Callable | None = None):
        self.config = config
        self._broadcast_fn = broadcast_fn
        self._active_alerts: dict[str, Alert] = {}
        self._breach_start: dict[str, float] = {}  # metric → first breach timestamp
        self._history: list[Alert] = []  # last 50 alerts

    @property
    def active_alerts(self) -> list[Alert]:
        return [a for a in self._active_alerts.values() if a.is_active]

    @property
    def alert_history(self) -> list[dict]:
        return [a.to_dict() for a in self._history[-50:]]

    async def evaluate(self, stats: dict):
        """Evaluate a stats snapshot against all thresholds."""
        metrics_to_check = {
            "cpu": stats.get("cpu", 0),
            "memory": (
                (stats.get("mem_used", 0) / stats.get("mem_total", 1)) * 100
                if stats.get("mem_total", 0) > 0
                else 0
            ),
            "disk": stats.get("disk_pct", 0),
            "temp": stats.get("temp", 0),
        }

        for metric, value in metrics_to_check.items():
            thresholds = DEFAULT_THRESHOLDS.get(metric, {})
            await self._check_metric(metric, value, thresholds)

    async def _check_metric(self, metric: str, value: float, thresholds: dict):
        """Check a single metric against its thresholds."""
        crit_threshold = thresholds.get("crit", 999)
        warn_threshold = thresholds.get("warn", 999)
        duration = thresholds.get("duration", 0)

        now = time.time()

        # Determine severity
        if value >= crit_threshold:
            level = "crit"
            threshold = crit_threshold
        elif value >= warn_threshold:
            level = "warn"
            threshold = warn_threshold
        else:
            # Below thresholds — resolve any active alert
            if metric in self._active_alerts:
                await self._resolve_alert(metric)
            self._breach_start.pop(metric, None)
            return

        # Duration check: only fire after sustained breach
        if duration > 0:
            if metric not in self._breach_start:
                self._breach_start[metric] = now
                return  # Not yet sustained long enough
            elif now - self._breach_start[metric] < duration:
                return  # Still within grace period

        # Fire or update alert
        if metric not in self._active_alerts:
            await self._fire_alert(metric, level, value, threshold)

    async def _fire_alert(self, metric: str, level: str, value: float, threshold: float):
        """Fire a new alert."""
        message = f"{metric.upper()} {level}: {value:.0f} (threshold: {threshold})"
        alert = Alert(
            metric=metric,
            level=level,
            value=value,
            threshold=threshold,
            message=message,
        )
        self._active_alerts[metric] = alert
        self._history.append(alert)

        # Broadcast via WebSocket
        if self._broadcast_fn:
            await self._broadcast_fn({
                "type": "alert",
                "level": level,
                "metric": metric,
                "message": message,
                "value": value,
            })

        # Send webhook (non-blocking)
        asyncio.create_task(self._send_webhooks(alert))

    async def _resolve_alert(self, metric: str):
        """Resolve an active alert."""
        alert = self._active_alerts.pop(metric, None)
        if alert:
            alert.resolved_at = time.time()
            # Broadcast resolution
            if self._broadcast_fn:
                await self._broadcast_fn({
                    "type": "alert_resolved",
                    "metric": metric,
                    "message": f"{metric.upper()} returned to normal",
                })

    async def _send_webhooks(self, alert: Alert):
        """Send alert to configured webhooks (Discord, Slack, generic)."""
        webhook_url = self.config.plugins.builtin.get("alerts", None)
        if not webhook_url:
            return

        # Generic webhook payload
        payload = json.dumps({
            "text": alert.message,
            "level": alert.level,
            "metric": alert.metric,
            "value": alert.value,
            "hostname": self.config.node.name,
            "timestamp": alert.fired_at,
        }).encode()

        try:
            req = urllib.request.Request(
                webhook_url if isinstance(webhook_url, str) else "",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # Best-effort, don't crash on webhook failure
