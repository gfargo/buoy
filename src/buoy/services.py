"""Service discovery — finds local Docker services and resolves URLs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


async def discover_services(config: BuoyConfig, is_tailscale: bool) -> dict:
    """Discover local services from Docker and build the full response.

    Returns:
        Dict with 'local', 'network', 'hostname', 'tailscale', 'tailnet_domain' keys.
    """
    from buoy.collectors.docker import DockerCollector

    collector = DockerCollector(config)
    containers = await collector.list_containers()

    hidden = set(config.services.hidden)
    overrides = config.services.overrides
    hostname = config.node.name
    tailnet = config.network.tailnet_domain

    # Build local services from discovered containers
    local_services = []
    for ctr in containers:
        name = ctr.get("name", "")
        if name in hidden:
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

        local_services.append({
            "name": display_name,
            "desc": desc,
            "icon": icon,
            "url": url,
        })

    # Build network services from config peers
    network_services = []
    for peer in config.network.peers:
        if peer.name == hostname:
            continue
        network_services.append({
            "name": peer.name,
            "url": peer.url,
            "tier": peer.tier,
            "host": peer.name,
        })

    return {
        "local": local_services,
        "network": network_services,
        "hostname": hostname,
        "tailscale": is_tailscale,
        "tailnet_domain": tailnet,
    }
