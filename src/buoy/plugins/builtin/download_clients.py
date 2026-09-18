"""Download clients plugin — qBittorrent / Transmission / SABnzbd / NZBGet queues.

Supports any number of clients across the four backends in one config list;
each client is probed independently and reported as its own row, with the
dashboard card aggregating worst-status-wins across all of them (mirrors the
``databases`` plugin's multi-target pattern).

Secrets (``password``/``api_key``) are never echoed back in ``PanelData`` —
probe failures are mapped to a small set of generic, categorized error
strings (see ``_sanitize_error``) instead of the raw exception text, since
driver/HTTP errors can embed credentials or full URLs.
"""

from __future__ import annotations

import asyncio
import base64
import http.cookiejar
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_QBIT_QUEUED_STATES = {"queuedDL", "queuedUP"}
_QBIT_INACTIVE_STATES = {"pausedDL", "pausedUP", "error", "missingFiles"}

_TRANSMISSION_ACTIVE_STATUSES = {2, 4, 6}  # checking, downloading, seeding
_TRANSMISSION_QUEUED_STATUSES = {1, 3, 5}  # check wait, download wait, seed wait


def _worst(statuses: list[str]) -> str:
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    return "ok"


def _sanitize_error(message: str) -> str:
    """Map a raw exception message to a short, credential-free reason.

    Driver/HTTP errors can embed a full request URL (which may carry an
    ``apikey=`` query param or a ``user:pass@host`` netloc), so the original
    message is never returned — only one of these fixed, generic strings.
    """
    lower = message.lower()
    if "authentication failed" in lower or "auth failed" in lower:
        return "authentication failed"
    if "401" in lower or "403" in lower or "password" in lower:
        return "authentication failed"
    if "timeout" in lower or "timed out" in lower:
        return "timed out"
    if (
        "refused" in lower
        or "unreachable" in lower
        or "name or service" in lower
        or "nodename" in lower
        or "no route" in lower
    ):
        return "unreachable"
    return "connection failed"


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_secret(client: dict[str, Any], key: str, env_key: str) -> str:
    env_name = client.get(env_key)
    if env_name:
        return os.environ.get(str(env_name), "")
    return str(client.get(key, "") or "")


def _fmt_rate(bytes_s: float) -> str:
    if bytes_s >= 1024**3:
        return f"{bytes_s / 1024**3:.1f} GB/s"
    if bytes_s >= 1024**2:
        return f"{bytes_s / 1024**2:.1f} MB/s"
    if bytes_s >= 1024:
        return f"{bytes_s / 1024:.1f} KB/s"
    return f"{bytes_s:.0f} B/s"


def _fmt_eta(eta: Any) -> str:
    """Format a seconds-remaining value; unknown/infinite sentinels render as '—'."""
    try:
        seconds = int(eta)
    except (TypeError, ValueError):
        return "—"
    if seconds < 0 or seconds >= 8_640_000:
        return "—"
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _qbit_counts(torrents: dict[str, Any]) -> tuple[int, int, list[dict[str, Any]]]:
    active = 0
    queued = 0
    items: list[dict[str, Any]] = []
    for t in torrents.values():
        state = t.get("state", "")
        if state in _QBIT_QUEUED_STATES:
            queued += 1
        elif state not in _QBIT_INACTIVE_STATES:
            active += 1
        items.append(
            {
                "name": t.get("name", ""),
                "progress": round(_safe_float(t.get("progress", 0.0)) * 100, 1),
                "speed": int(t.get("dlspeed", 0) or 0),
                "eta": t.get("eta"),
            }
        )
    return active, queued, items


def _transmission_counts(torrents: list[dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    active = 0
    queued = 0
    items: list[dict[str, Any]] = []
    for t in torrents:
        status = t.get("status", 0)
        if status in _TRANSMISSION_ACTIVE_STATUSES:
            active += 1
        elif status in _TRANSMISSION_QUEUED_STATUSES:
            queued += 1
        items.append(
            {
                "name": t.get("name", ""),
                "progress": round(_safe_float(t.get("percentDone", 0.0)) * 100, 1),
                "speed": int(t.get("rateDownload", 0) or 0),
                "eta": t.get("eta"),
            }
        )
    return active, queued, items


def _sabnzbd_counts(slots: list[dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    active = 0
    queued = 0
    items: list[dict[str, Any]] = []
    for s in slots:
        status = str(s.get("status") or "").lower()
        if status == "downloading":
            active += 1
        else:
            queued += 1
        items.append(
            {
                "name": s.get("filename", ""),
                "progress": _safe_float(s.get("percentage", 0)),
                "speed": None,
                "eta": None,
            }
        )
    return active, queued, items


def _nzbget_counts(groups: list[dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    active = 0
    queued = 0
    items: list[dict[str, Any]] = []
    for g in groups:
        status = str(g.get("Status") or "").upper()
        if "DOWNLOAD" in status:
            active += 1
        else:
            queued += 1
        file_size = _safe_float(g.get("FileSizeMB", 0))
        remaining = _safe_float(g.get("RemainingSizeMB", 0))
        progress = round(((file_size - remaining) / file_size) * 100, 1) if file_size else 0.0
        items.append(
            {
                "name": g.get("NZBFilename") or g.get("NZBName", ""),
                "progress": progress,
                "speed": None,
                "eta": None,
            }
        )
    return active, queued, items


class DownloadClientsPlugin(Plugin):
    """Shows active/queued downloads, speeds, ratio, and free disk space per client."""

    manifest = PluginManifest(
        id="download_clients",
        name="Downloads",
        icon="📥",
        description="qBittorrent / Transmission / SABnzbd / NZBGet queue, speeds, disk",
        version="1.0.0",
        config_schema={
            "clients": {"type": "array", "default": []},
            "timeout_s": {"type": "number", "default": 8},
            "verify_ssl": {"type": "boolean", "default": True},
            "disk_warn_gb": {"type": "number", "default": 50},
            "disk_critical_gb": {"type": "number", "default": 10},
            "max_rows": {"type": "integer", "default": 10},
        },
        refresh_interval=60,
    )

    def __init__(self) -> None:
        super().__init__()
        # Cached Transmission X-Transmission-Session-Id per client name, so
        # each collect() cycle doesn't pay for a fresh 409 round-trip.
        self._transmission_session_ids: dict[str, str] = {}

    async def collect(self) -> PanelData:
        raw_clients = self.config.get("clients") or []
        clients = [c for c in raw_clients if isinstance(c, dict)]
        if not clients:
            return PanelData(status="disabled", summary="Not configured")

        timeout_s = float(self.config.get("timeout_s", 8))
        verify_ssl = self.config.get("verify_ssl", True)
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        results = await asyncio.gather(
            *(self._probe(c, timeout_s, ctx) for c in clients), return_exceptions=True
        )

        rows = []
        for client, result in zip(clients, results, strict=True):
            if isinstance(result, BaseException):
                name = str(client.get("name") or client.get("type") or "client")
                rows.append(self._error_row(name, str(client.get("type", "")), result))
            else:
                rows.append(result)

        return self._make_panel(rows)

    async def _probe(self, client: dict[str, Any], timeout_s: float, ctx) -> dict[str, Any]:
        name = str(client.get("name") or client.get("type") or "client")
        c_type = str(client.get("type", ""))
        dispatch = {
            "qbittorrent": self._probe_qbittorrent,
            "transmission": self._probe_transmission,
            "sabnzbd": self._probe_sabnzbd,
            "nzbget": self._probe_nzbget,
        }
        fn = dispatch.get(c_type)
        if fn is None:
            return self._error_row(name, c_type, ValueError(f"unknown type {c_type!r}"))

        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, client, timeout_s, ctx), timeout=timeout_s
            )
        except TimeoutError:
            return self._error_row(name, c_type, TimeoutError("timed out"))
        except Exception as e:  # noqa: BLE001
            return self._error_row(name, c_type, e)

    @staticmethod
    def _error_row(name: str, c_type: str, exc: BaseException) -> dict[str, Any]:
        return {
            "name": name,
            "type": c_type,
            "status": "error",
            "error": _sanitize_error(str(exc)),
            "active": 0,
            "queued": 0,
            "dl_bytes_s": 0,
            "ul_bytes_s": 0,
            "ratio": None,
            "free_bytes": None,
            "items": [],
        }

    # ------------------------------------------------------------------
    # qBittorrent
    # ------------------------------------------------------------------

    def _probe_qbittorrent(self, client: dict[str, Any], timeout_s: float, ctx) -> dict[str, Any]:
        name = str(client.get("name") or "qbittorrent")
        url = str(client.get("url") or "").rstrip("/")
        username = str(client.get("username") or "")
        password = _resolve_secret(client, "password", "password_env")

        handlers: list[Any] = [urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        opener = urllib.request.build_opener(*handlers)

        if username:
            body = urllib.parse.urlencode({"username": username, "password": password}).encode()
            req = urllib.request.Request(
                f"{url}/api/v2/auth/login",
                data=body,
                headers={
                    "Referer": url,
                    "Origin": url,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                method="POST",
            )
            with opener.open(req, timeout=timeout_s) as resp:
                login_result = resp.read().decode("utf-8", errors="replace")
            if login_result.strip() != "Ok.":
                raise RuntimeError("authentication failed")

        req = urllib.request.Request(
            f"{url}/api/v2/sync/maindata", headers={"Accept": "application/json"}
        )
        with opener.open(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read())

        state = data.get("server_state") or {}
        torrents = data.get("torrents") or {}
        active, queued, items = _qbit_counts(torrents)

        return {
            "name": name,
            "type": "qbittorrent",
            "status": "ok",
            "error": "",
            "active": active,
            "queued": queued,
            "dl_bytes_s": int(state.get("dl_info_speed", 0) or 0),
            "ul_bytes_s": int(state.get("up_info_speed", 0) or 0),
            "ratio": _safe_float(state.get("global_ratio", 0.0)),
            "free_bytes": int(state.get("free_space_on_disk", 0) or 0),
            "items": items,
        }

    # ------------------------------------------------------------------
    # Transmission
    # ------------------------------------------------------------------

    def _probe_transmission(self, client: dict[str, Any], timeout_s: float, ctx) -> dict[str, Any]:
        name = str(client.get("name") or "transmission")
        url = str(client.get("url") or "").rstrip("/")
        username = str(client.get("username") or "")
        password = _resolve_secret(client, "password", "password_env")

        headers = {"Content-Type": "application/json"}
        if username:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        session_id = self._transmission_session_ids.get(name, "")

        def _call(method: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
            nonlocal session_id
            payload = json.dumps({"method": method, "arguments": arguments or {}}).encode()
            req_headers = dict(headers)
            if session_id:
                req_headers["X-Transmission-Session-Id"] = session_id
            req = urllib.request.Request(
                f"{url}/transmission/rpc", data=payload, headers=req_headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    new_sid = e.headers.get("X-Transmission-Session-Id") if e.headers else None
                    if new_sid:
                        session_id = new_sid
                        self._transmission_session_ids[name] = new_sid
                        req_headers["X-Transmission-Session-Id"] = new_sid
                        retry_req = urllib.request.Request(
                            f"{url}/transmission/rpc",
                            data=payload,
                            headers=req_headers,
                            method="POST",
                        )
                        with urllib.request.urlopen(
                            retry_req, timeout=timeout_s, context=ctx
                        ) as resp:
                            return json.loads(resp.read())
                raise

        stats = _call("session-stats").get("arguments") or {}
        torrents_resp = (
            _call(
                "torrent-get",
                {
                    "fields": [
                        "name",
                        "status",
                        "percentDone",
                        "rateDownload",
                        "rateUpload",
                        "uploadRatio",
                        "eta",
                    ]
                },
            ).get("arguments")
            or {}
        )
        torrents = torrents_resp.get("torrents") or []

        session = _call("session-get").get("arguments") or {}
        download_dir = session.get("download-dir", "")
        free_bytes = None
        if download_dir:
            free_resp = _call("free-space", {"path": download_dir}).get("arguments") or {}
            free_bytes = free_resp.get("size-bytes")

        active, queued, items = _transmission_counts(torrents)

        cumulative = stats.get("cumulative-stats") or {}
        downloaded = cumulative.get("downloadedBytes", 0)
        uploaded = cumulative.get("uploadedBytes", 0)
        ratio = (uploaded / downloaded) if downloaded else None

        return {
            "name": name,
            "type": "transmission",
            "status": "ok",
            "error": "",
            "active": active,
            "queued": queued,
            "dl_bytes_s": int(stats.get("downloadSpeed", 0) or 0),
            "ul_bytes_s": int(stats.get("uploadSpeed", 0) or 0),
            "ratio": ratio,
            "free_bytes": int(free_bytes) if free_bytes is not None else None,
            "items": items,
        }

    # ------------------------------------------------------------------
    # SABnzbd
    # ------------------------------------------------------------------

    def _probe_sabnzbd(self, client: dict[str, Any], timeout_s: float, ctx) -> dict[str, Any]:
        name = str(client.get("name") or "sabnzbd")
        url = str(client.get("url") or "").rstrip("/")
        api_key = _resolve_secret(client, "api_key", "api_key_env")

        query = urllib.parse.urlencode({"mode": "queue", "output": "json", "apikey": api_key})
        req = urllib.request.Request(f"{url}/api?{query}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
            data = json.loads(resp.read())

        queue = data.get("queue")
        if queue is None:
            raise RuntimeError("authentication failed")

        slots = queue.get("slots") or []
        active, queued, items = _sabnzbd_counts(slots)

        status_text = str(queue.get("status") or "").lower()
        dl_bytes_s = _safe_float(queue.get("kbpersec", 0)) * 1024
        free_bytes = _safe_float(queue.get("diskspace1", 0)) * (1024**3)

        return {
            "name": name,
            "type": "sabnzbd",
            "status": "warn" if status_text == "paused" else "ok",
            "error": "",
            "active": active,
            "queued": queued,
            "dl_bytes_s": int(dl_bytes_s),
            "ul_bytes_s": 0,
            "ratio": None,
            "free_bytes": int(free_bytes),
            "items": items,
        }

    # ------------------------------------------------------------------
    # NZBGet
    # ------------------------------------------------------------------

    def _probe_nzbget(self, client: dict[str, Any], timeout_s: float, ctx) -> dict[str, Any]:
        name = str(client.get("name") or "nzbget")
        url = str(client.get("url") or "").rstrip("/")
        username = str(client.get("username") or "")
        password = _resolve_secret(client, "password", "password_env")

        headers = {"Content-Type": "application/json"}
        if username:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        def _rpc(method: str, params: list[Any] | None = None) -> Any:
            payload = json.dumps({"method": method, "params": params or [], "id": 1}).encode()
            req = urllib.request.Request(
                f"{url}/jsonrpc", data=payload, headers=headers, method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout_s, context=ctx) as resp:
                body = json.loads(resp.read())
            if body.get("error"):
                raise RuntimeError("authentication failed")
            return body.get("result")

        status = _rpc("status") or {}
        groups = _rpc("listgroups") or []

        active, queued, items = _nzbget_counts(groups)

        free_mb = status.get("FreeDiskSpaceMB")
        if free_mb is not None:
            free_bytes = int(_safe_float(free_mb) * 1024 * 1024)
        else:
            lo = status.get("FreeDiskSpaceLo", 0) or 0
            hi = status.get("FreeDiskSpaceHi", 0) or 0
            free_bytes = int(hi * (2**32) + lo)

        paused = bool(status.get("DownloadPaused", False))

        return {
            "name": name,
            "type": "nzbget",
            "status": "warn" if paused else "ok",
            "error": "",
            "active": active,
            "queued": queued,
            "dl_bytes_s": int(status.get("DownloadRate", 0) or 0),
            "ul_bytes_s": 0,
            "ratio": None,
            "free_bytes": free_bytes,
            "items": items,
        }

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _make_panel(self, rows: list[dict[str, Any]]) -> PanelData:
        max_rows = int(self.config.get("max_rows", 10))
        disk_warn_gb = float(self.config.get("disk_warn_gb", 50))
        disk_critical_gb = float(self.config.get("disk_critical_gb", 10))

        for row in rows:
            row["items"] = row.get("items", [])[:max_rows]
            if row["status"] != "error":
                free_bytes = row.get("free_bytes")
                if free_bytes is not None:
                    free_gb = free_bytes / (1024**3)
                    if free_gb <= disk_critical_gb:
                        row["status"] = "error"
                    elif free_gb <= disk_warn_gb:
                        row["status"] = "warn"

        overall = _worst([r["status"] for r in rows])
        total_active = sum(r["active"] for r in rows)
        total_queued = sum(r["queued"] for r in rows)
        total_dl = sum(r["dl_bytes_s"] for r in rows)
        total_ul = sum(r["ul_bytes_s"] for r in rows)
        errored = [r for r in rows if r["status"] == "error" and r.get("error")]

        if len(rows) == 1 and errored:
            summary = f"{rows[0]['name']}: {rows[0]['error']}"
        else:
            parts = [f"{total_active} active"]
            if total_queued:
                parts.append(f"{total_queued} queued")
            if total_dl or total_ul:
                parts.append(f"↓{_fmt_rate(total_dl)} ↑{_fmt_rate(total_ul)}")
            if errored:
                plural = "s" if len(errored) != 1 else ""
                parts.append(f"{len(errored)} client{plural} unreachable")
            summary = " · ".join(parts)

        return PanelData(
            status=overall,
            summary=summary,
            detail={
                "clients": rows,
                "totals": {
                    "active": total_active,
                    "queued": total_queued,
                    "dl_bytes_s": total_dl,
                    "ul_bytes_s": total_ul,
                },
            },
        )

    def demo_data(self) -> PanelData:
        rows = [
            {
                "name": "qbit",
                "type": "qbittorrent",
                "status": "ok",
                "error": "",
                "active": 2,
                "queued": 1,
                "dl_bytes_s": 12_500_000,
                "ul_bytes_s": 1_200_000,
                "ratio": 1.85,
                "free_bytes": 420 * 1024**3,
                "items": [
                    {"name": "Ubuntu 24.04 LTS", "progress": 64.0, "speed": 8_000_000, "eta": 300},
                    {
                        "name": "Debian netinst",
                        "progress": 12.0,
                        "speed": 4_500_000,
                        "eta": 1800,
                    },
                ],
            },
            {
                "name": "sab",
                "type": "sabnzbd",
                "status": "ok",
                "error": "",
                "active": 1,
                "queued": 2,
                "dl_bytes_s": 30_000_000,
                "ul_bytes_s": 0,
                "ratio": None,
                "free_bytes": 15 * 1024**3,
                "items": [
                    {"name": "some.linux.iso", "progress": 88.0, "speed": None, "eta": None},
                ],
            },
        ]
        return self._make_panel(rows)

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        rows = d.get("clients") or []
        if not rows:
            return [panel.text("No downloads", status="dim")]

        totals = d.get("totals") or {}
        ratios = [r["ratio"] for r in rows if r.get("ratio") is not None]
        kv_rows: list[dict[str, Any]] = [
            {"label": "Active", "value": str(totals.get("active", 0))},
            {"label": "Queued", "value": str(totals.get("queued", 0))},
            {
                "label": "Download",
                "value": _fmt_rate(totals.get("dl_bytes_s", 0)),
                "status": "info",
            },
            {"label": "Upload", "value": _fmt_rate(totals.get("ul_bytes_s", 0)), "status": "info"},
        ]
        if ratios:
            kv_rows.append({"label": "Ratio", "value": f"{sum(ratios) / len(ratios):.2f}"})

        blocks: list[dict] = [panel.keyvalue(kv_rows)]

        max_rows = int(self.config.get("max_rows", 10))
        table_rows = []
        for r in rows:
            if r["status"] == "error":
                table_rows.append(
                    [
                        panel.cell(r["name"]),
                        panel.cell(r.get("type", "")),
                        panel.cell(r.get("error", "error"), status="error"),
                        panel.cell("—"),
                        panel.cell("—"),
                    ]
                )
                continue
            for it in r.get("items", []):
                speed = it.get("speed")
                table_rows.append(
                    [
                        panel.cell(it.get("name", ""), truncate=True),
                        panel.cell(r["name"]),
                        panel.cell(f"{it.get('progress', 0):.0f}%"),
                        panel.cell(_fmt_rate(speed) if speed is not None else "—"),
                        panel.cell(_fmt_eta(it.get("eta"))),
                    ]
                )
        table_rows = table_rows[:max_rows]

        if table_rows:
            blocks.append(panel.table(["Name", "Client", "Progress", "Speed", "ETA"], table_rows))
        else:
            blocks.append(panel.text("No downloads", status="dim"))

        return blocks
