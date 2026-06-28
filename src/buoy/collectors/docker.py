"""Docker collector — container discovery, stats, inspect, logs, restart.

Uses the Docker CLI via async subprocess. Validates all container names
before passing to shell commands.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


def _valid_name(name: str) -> bool:
    return bool(_CONTAINER_NAME_RE.match(name)) and len(name) <= 128


class DockerCollector:
    """Collects Docker container information via CLI."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._available: bool | None = None

    async def _run(self, *args: str, timeout: float = 10) -> tuple[int, str, str]:
        """Run a docker command and return (returncode, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
        except TimeoutError:
            return 1, "", "timeout"
        except FileNotFoundError:
            return 1, "", "docker not found"
        except Exception as e:
            return 1, "", str(e)

    async def is_available(self) -> bool:
        """Check if Docker CLI is accessible."""
        if self._available is None:
            code, _, _ = await self._run("info", "--format", "{{.ID}}", timeout=5)
            self._available = code == 0
        return self._available

    async def list_containers(self) -> list[dict]:
        """List running containers with name and host port."""
        code, stdout, _ = await self._run("ps", "--format", "{{.Names}}\t{{.Ports}}")
        if code != 0 or not stdout:
            return []

        containers = []
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            name = parts[0].strip()
            ports_str = parts[1].strip() if len(parts) > 1 else ""

            host_port = self._parse_first_port(ports_str)
            containers.append({"name": name, "host_port": host_port})

        return containers

    async def collect_summary(self) -> dict:
        """Collect container count and list for the stats endpoint."""
        containers = await self.list_containers()
        return {
            "containers": len(containers),
            "containers_list": [{"name": c["name"]} for c in containers],
        }

    async def inspect_container(self, name: str) -> dict:
        """Get detailed info for a single container."""
        if not _valid_name(name):
            return {"error": "invalid container name"}

        # Basic inspect
        code, stdout, stderr = await self._run(
            "inspect",
            "--format",
            '{"status":"{{.State.Status}}","started":"{{.State.StartedAt}}",'
            '"image":"{{.Config.Image}}","restart_count":{{.RestartCount}},'
            '"pid":{{.State.Pid}},"image_created":"{{.Created}}"}',
            name,
        )

        if code != 0:
            return {"error": stderr or "container not found"}

        try:
            info = json.loads(stdout)
        except json.JSONDecodeError:
            return {"error": "failed to parse inspect output"}

        info["name"] = name

        # Resource usage (docker stats --no-stream)
        stats_code, stats_out, _ = await self._run(
            "stats",
            "--no-stream",
            "--format",
            '{"cpu_pct":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}",'
            '"mem_pct":"{{.MemPerc}}","net_io":"{{.NetIO}}","block_io":"{{.BlockIO}}"}',
            name,
            timeout=15,
        )

        if stats_code == 0 and stats_out:
            try:
                info["resources"] = json.loads(stats_out)
            except json.JSONDecodeError:
                pass

        # Ports
        ports_code, ports_out, _ = await self._run("port", name, timeout=5)
        info["ports"] = ports_out if ports_code == 0 else ""

        return info

    async def get_logs(self, name: str, tail: int = 30) -> dict:
        """Get last N lines of container logs."""
        if not _valid_name(name):
            return {"error": "invalid container name"}

        code, stdout, stderr = await self._run(
            "logs",
            "--tail",
            str(tail),
            "--timestamps",
            name,
            timeout=5,
        )

        lines = (stdout + "\n" + stderr).strip().split("\n") if (stdout or stderr) else []
        return {"container": name, "lines": lines[-tail:]}

    async def restart_container(self, name: str) -> dict:
        """Restart a container by name."""
        if not _valid_name(name):
            return {"success": False, "error": "invalid container name"}

        code, _, stderr = await self._run("restart", name, timeout=30)
        if code == 0:
            return {"success": True, "container": name}
        return {"success": False, "error": stderr or "restart failed"}

    async def list_container_states(self) -> list[dict]:
        """Return name/status/restart_count for all containers (running + stopped)."""
        # Get all container IDs (including stopped)
        code, stdout, _ = await self._run("ps", "-aq", "--no-trunc")
        if code != 0 or not stdout:
            return []

        ids = [i for i in stdout.split("\n") if i.strip()]
        if not ids:
            return []

        # Batch inspect: one call for all IDs
        inspect_code, inspect_out, _ = await self._run(
            "inspect",
            "--format",
            '{"name":"{{.Name}}","status":"{{.State.Status}}","restart_count":{{.RestartCount}}}',
            *ids,
        )
        if inspect_code != 0 or not inspect_out:
            return []

        states = []
        for line in inspect_out.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                name = obj.get("name", "").lstrip("/")
                if name:
                    states.append(
                        {
                            "name": name,
                            "status": obj.get("status", "unknown"),
                            "restart_count": int(obj.get("restart_count", 0)),
                        }
                    )
            except (json.JSONDecodeError, ValueError):
                continue
        return states

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_first_port(ports_str: str) -> int | None:
        """Extract the first host-bound port from Docker ports string."""
        if not ports_str:
            return None

        for mapping in ports_str.split(","):
            mapping = mapping.strip()
            if "->" in mapping:
                left = mapping.split("->")[0]
                if ":" in left:
                    port_str = left.rsplit(":", 1)[1]
                else:
                    port_str = left
                try:
                    return int(port_str)
                except ValueError:
                    continue
        return None
