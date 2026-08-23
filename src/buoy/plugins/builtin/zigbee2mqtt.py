"""Zigbee2MQTT plugin — coordinator/network health and per-device link quality.

Deliberately reads the Zigbee network layer directly via MQTT rather than
through Home Assistant (or any other automation platform) — this plugin has
no opinion on what, if anything, consumes the network besides Buoy. See
`home_assistant.py` for entity/automation-level health instead; the two are
independent and don't need each other running.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import paho.mqtt.client as mqtt

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

logger = logging.getLogger(__name__)

_STALE_SECONDS = 600  # a device with no message in 10min is flagged, not dropped
_LQ_WARN = 50
_LQ_ERROR = 20


class Zigbee2MqttPlugin(Plugin):
    """Shows Zigbee2MQTT bridge/coordinator status and per-device link quality.

    Connects once in setup() and keeps a persistent MQTT subscription alive
    for the plugin's lifetime (paho's own background network thread via
    loop_start()) rather than reconnecting on every collect() — bridge/info,
    bridge/state, and bridge/devices are all published retained, so a single
    long-lived subscription sees every update as it happens; collect() just
    reads the cache built by on_message, no I/O per refresh cycle.
    """

    manifest = PluginManifest(
        id="zigbee2mqtt",
        name="Zigbee2MQTT",
        icon="📡",
        description="Coordinator status, network health, per-device link quality",
        version="1.0.0",
        config_schema={
            "host": {"type": "string", "required": True},
            "port": {"type": "integer", "default": 1883},
            "username": {"type": "string", "default": ""},
            "password": {
                "type": "string",
                "default": "",
                "env": "BUOY_PLUGIN_ZIGBEE2MQTT_PASSWORD",
            },
            "base_topic": {"type": "string", "default": "zigbee2mqtt"},
        },
        refresh_interval=30,
    )

    def __init__(self) -> None:
        super().__init__()
        self._client: mqtt.Client | None = None
        self._bridge_state: str | None = None
        self._bridge_info: dict[str, Any] = {}
        self._devices: list[dict[str, Any]] = []
        self._device_live: dict[str, dict[str, Any]] = {}  # friendly_name -> last message + seen_at
        self._connect_error: str | None = None

    async def setup(self) -> None:
        host = self.config.get("host", "")
        if not host:
            return  # collect() reports "disabled" — nothing to connect to

        base_topic = self.config.get("base_topic", "zigbee2mqtt")
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        username = self.config.get("username", "")
        if username:
            client.username_pw_set(username, self.config.get("password", ""))

        client.on_connect = self._make_on_connect(base_topic)
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        try:
            client.connect_async(host, int(self.config.get("port", 1883)), keepalive=30)
            client.loop_start()
        except Exception as e:
            self._connect_error = str(e)
            logger.warning("zigbee2mqtt: connect failed: %s", e)
            return

        self._client = client

    async def teardown(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    def _make_on_connect(self, base_topic: str):
        def _on_connect(client, userdata, flags, reason_code, properties=None):
            if reason_code != 0:
                self._connect_error = str(reason_code)
                logger.warning("zigbee2mqtt: connect refused: %s", reason_code)
                return
            self._connect_error = None
            client.subscribe(f"{base_topic}/bridge/state")
            client.subscribe(f"{base_topic}/bridge/info")
            client.subscribe(f"{base_topic}/bridge/devices")
            client.subscribe(f"{base_topic}/+")

        return _on_connect

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            self._connect_error = f"disconnected: {reason_code}"

    def _on_message(self, client, userdata, msg) -> None:
        base_topic = self.config.get("base_topic", "zigbee2mqtt")
        suffix = (
            msg.topic[len(base_topic) + 1 :]
            if msg.topic.startswith(base_topic + "/")
            else msg.topic
        )

        try:
            payload = json.loads(msg.payload)
        except (ValueError, UnicodeDecodeError):
            return

        if suffix == "bridge/state":
            self._bridge_state = payload.get("state") if isinstance(payload, dict) else str(payload)
        elif suffix == "bridge/info":
            self._bridge_info = payload if isinstance(payload, dict) else {}
        elif suffix == "bridge/devices":
            self._devices = payload if isinstance(payload, list) else []
        elif "/" not in suffix and suffix:
            # A per-device state topic (zigbee2mqtt/<friendly_name>) — bridge/*
            # is excluded above, everything else at this level is a device.
            if isinstance(payload, dict):
                self._device_live[suffix] = {**payload, "_seen_at": time.time()}

    async def collect(self) -> PanelData:
        if not self.config.get("host"):
            return PanelData(status="disabled", summary="Not configured")

        if self._connect_error:
            return PanelData(
                status="error", summary="MQTT unreachable", detail={"error": self._connect_error}
            )

        if self._bridge_state is None and not self._bridge_info:
            return PanelData(status="warn", summary="Waiting for bridge data")

        if self._bridge_state != "online":
            return PanelData(
                status="error",
                summary=f"Bridge {self._bridge_state or 'unknown'}",
                detail={"bridge_state": self._bridge_state},
            )

        devices = [d for d in self._devices if d.get("type") != "Coordinator"]
        coordinator = self._bridge_info.get("coordinator", {})
        network = self._bridge_info.get("network", {})
        permit_join = bool(self._bridge_info.get("permit_join"))

        rows = []
        worst_lq: int | None = None
        stale_count = 0
        now = time.time()
        for d in devices:
            name = d.get("friendly_name", d.get("ieee_address", "?"))
            live = self._device_live.get(name)
            linkquality = live.get("linkquality") if live else None
            seen_at = live.get("_seen_at") if live else None
            stale = seen_at is None or (now - seen_at) > _STALE_SECONDS
            if stale:
                stale_count += 1
            if isinstance(linkquality, int) and (worst_lq is None or linkquality < worst_lq):
                worst_lq = linkquality
            rows.append(
                {
                    "name": name,
                    "type": d.get("type", "?"),
                    "linkquality": linkquality,
                    "stale": stale,
                    "interview_ok": d.get("interview_state", "SUCCESSFUL") == "SUCCESSFUL",
                    "disabled": bool(d.get("disabled")),
                }
            )

        problem_count = sum(1 for r in rows if not r["interview_ok"] or r["disabled"]) + stale_count
        summary = f"{len(devices)} device{'s' if len(devices) != 1 else ''}"
        if worst_lq is not None:
            summary += f" · weakest {worst_lq}"
        if problem_count:
            summary += f" · {problem_count} need attention"

        status = "ok"
        if worst_lq is not None and worst_lq < _LQ_ERROR:
            status = "error"
        elif problem_count or (worst_lq is not None and worst_lq < _LQ_WARN):
            status = "warn"

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "coordinator_type": coordinator.get("type"),
                "coordinator_firmware": (coordinator.get("meta") or {}).get("revision"),
                "channel": network.get("channel"),
                "pan_id": network.get("pan_id"),
                "permit_join": permit_join,
                "device_count": len(devices),
                "devices": sorted(
                    rows, key=lambda r: (r["linkquality"] is None, r["linkquality"] or 0)
                ),
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        if data.status == "disabled":
            return [panel.text("Not configured", status="dim")]
        if data.status == "error" and "bridge_state" in d:
            return [panel.text(f"Bridge {d['bridge_state']}", status="error")]
        if data.status == "error" and "error" in d:
            return [panel.text(d["error"], status="error")]
        if not d:
            return [panel.text("Waiting for bridge data", status="dim")]

        blocks: list[dict] = [
            panel.keyvalue(
                [
                    ("Coordinator", d.get("coordinator_type") or "unknown"),
                    ("Firmware", d.get("coordinator_firmware") or "unknown"),
                    ("Channel", str(d.get("channel") or "?")),
                    (
                        "Permit join",
                        "open" if d.get("permit_join") else "closed",
                    ),
                    ("Devices", str(d.get("device_count", 0))),
                ]
            )
        ]

        devices = d.get("devices") or []
        if devices:
            rows = []
            for dev in devices:
                lq = dev["linkquality"]
                lq_status = (
                    "dim"
                    if lq is None
                    else "error"
                    if lq < _LQ_ERROR
                    else "warn"
                    if lq < _LQ_WARN
                    else "ok"
                )
                name_status = "error" if (not dev["interview_ok"] or dev["disabled"]) else None
                rows.append(
                    [
                        panel.cell(dev["name"], status=name_status),
                        panel.cell(dev["type"], status="dim"),
                        panel.cell(lq if lq is not None else "—", status=lq_status),
                        panel.cell(
                            "stale" if dev["stale"] else "live",
                            status="warn" if dev["stale"] else "dim",
                        ),
                    ]
                )
            blocks.append(panel.table(["Device", "Type", "Link quality", "Seen"], rows))

        return blocks
