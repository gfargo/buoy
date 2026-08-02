"""Service discovery — finds local Docker services and resolves URLs."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


def _hidden_matcher(pattern: str):
    """Build a predicate matching `pattern` against a discovered container.

    Compose names containers `<project>-<service>-<n>` (or the legacy
    `<project>_<service>_<n>`), not the bare service name, so a plain equality
    check against e.g. "redis" never matches "plane-plane-redis-1". Rather than
    guessing at name segmentation (ambiguous once project or service names
    contain hyphens themselves), match against the container's own
    `com.docker.compose.service` label when Docker reports one — that's the
    exact service name Compose assigned, with no risk of matching the project
    segment or a substring of a compound service name. Falls back to exact
    full-name equality for containers Compose didn't label. Patterns containing
    glob characters (`*`, `?`, `[`) are matched with fnmatch against the full
    container name for advanced use.
    """
    if not pattern:
        # Empty string would match every unlabeled (non-Compose) container
        # because Docker reports service="" for them; skip silently.
        return lambda ctr: False
    if any(ch in pattern for ch in "*?["):
        return lambda ctr: fnmatch(ctr["name"], pattern)
    return lambda ctr: ctr.get("service") == pattern or ctr["name"] == pattern


async def discover_services(config: BuoyConfig, is_tailscale: bool, collector=None) -> dict:
    """Discover local services from Docker and build the full response.

    Args:
        collector: Optional pre-built Docker collector to reuse (avoids a
            redundant `docker ps` and preserves its caches). Defaults to a
            fresh `DockerCollector`.

    Returns:
        Dict with 'local', 'network', 'hostname', 'tailscale', 'tailnet_domain' keys.
    """
    if collector is None:
        from buoy.collectors.docker import DockerCollector

        collector = DockerCollector(config)
    containers = await collector.list_containers()

    hidden_matchers = [_hidden_matcher(pattern) for pattern in config.services.hidden]
    overrides = config.services.overrides
    hostname = config.node.name
    tailnet = config.network.tailnet_domain

    # Build local services from discovered containers
    local_services = []
    for ctr in containers:
        name = ctr.get("name", "")
        if any(matches(ctr) for matches in hidden_matchers):
            continue

        override = overrides.get(name)
        display_name = override.name if override and override.name else name
        icon = override.icon if override else ""
        desc = override.desc if override else ""

        # Determine URL
        port = override.port if override else None
        path = override.path if override else ""

        if not port:
            port = ctr.get("host_port")

        if port:
            if is_tailscale and tailnet:
                url = f"https://{hostname}.{tailnet}:{port}{path}"
            else:
                url = f"http://localhost:{port}{path}"
        else:
            url = ""

        local_services.append(
            {
                "name": display_name,
                "desc": desc,
                "icon": icon,
                "url": url,
            }
        )

    # Build network services from config peers
    network_services = []
    for peer in config.network.peers:
        if peer.name == hostname:
            continue
        network_services.append(
            {
                "name": peer.name,
                "url": peer.url,
                "tier": peer.tier,
                "host": peer.name,
            }
        )

    return {
        "local": local_services,
        "network": network_services,
        "hostname": hostname,
        "tailscale": is_tailscale,
        "tailnet_domain": tailnet,
    }


async def top_services(
    config: BuoyConfig, is_tailscale: bool, limit: int = 5, collector=None
) -> list[dict]:
    """Return the first `limit` local services that have a URL.

    Used by /api/stats so fleet cards can render service link pills.
    """
    data = await discover_services(config, is_tailscale, collector=collector)
    return [
        {"name": s["name"], "icon": s["icon"], "url": s["url"]} for s in data["local"] if s["url"]
    ][:limit]
