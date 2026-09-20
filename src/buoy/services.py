"""Service discovery — finds local Docker services and resolves URLs."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

_PINNED_GROUP = "Pinned"


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


def _match_keys(ctr: dict) -> tuple[str, ...]:
    """The key(s) a Docker container may be referenced by in `services.pinned`
    /`services.order`: its Compose service label (e.g. "redis" for
    `plane-plane-redis-1`) *and* its full container name. Checking only one
    would silently ignore config written against the other — `_resolve_override`
    already falls back from service label to full name for `services.overrides`,
    so `pinned`/`order` match the same way instead of requiring the caller to
    guess which form a given container resolves to.
    """
    service = ctr.get("service")
    name = ctr["name"]
    if service and service != name:
        return (service, name)
    return (name,)


def _best_rank(rank_map: dict, keys: tuple[str, ...], default: int) -> int:
    """The lowest configured rank among `keys` that appears in `rank_map`, or
    `default` if none do."""
    ranks = [rank_map[k] for k in keys if k in rank_map]
    return min(ranks) if ranks else default


def _resolve_override(overrides: dict, ctr: dict):
    """Look up a service override for `ctr`, keyed the same way `hidden` matches.

    Compose containers are keyed by their `com.docker.compose.service` label
    (e.g. "redis" for `plane-plane-redis-1`) so overrides configured with the
    bare service name apply the same way `hidden` entries do. Falls back to
    the full container name for containers Compose didn't label.
    """
    service = ctr.get("service")
    if service and service in overrides:
        return overrides[service]
    return overrides.get(ctr["name"])


def _resolve_group(override, ctr: dict) -> str:
    """Resolve a Docker-discovered service's group: an explicit override wins,
    else the container's discovered project label, else ungrouped."""
    if override and override.group:
        return override.group
    return ctr.get("project") or ""


def _sort_services(entries: list[dict], config: BuoyConfig) -> list[dict]:
    """Order `entries` (each already carrying `key` — a tuple of candidate
    match keys — `group`, `pinned`) into final display order: pinned first
    (in `services.pinned` order), then by group (per `services.group_order`,
    else alphabetical, ungrouped last), then within a group by
    `services.order`, else discovery order.

    A single stable sort with a composite key — rather than multiple passes
    or a dict-based regroup — so entries with equal rank keep their relative
    discovery order instead of being scrambled.
    """
    pinned_rank = {key: i for i, key in enumerate(config.services.pinned)}
    group_rank = {name: i for i, name in enumerate(config.services.group_order)}
    within_rank = {key: i for i, key in enumerate(config.services.order)}
    unranked_group = len(group_rank)
    unranked_within = len(within_rank)

    def sort_key(item: tuple[int, dict]):
        index, entry = item
        group = entry["group"]
        if entry["pinned"]:
            return (-1, "", _best_rank(pinned_rank, entry["key"], len(pinned_rank)), index)
        return (
            group_rank.get(group, unranked_group if group else unranked_group + 1),
            group.casefold(),
            _best_rank(within_rank, entry["key"], unranked_within),
            index,
        )

    ordered = sorted(enumerate(entries), key=sort_key)
    result = []
    for _, entry in ordered:
        entry = dict(entry)
        entry.pop("key", None)
        result.append(entry)
    return result


async def discover_services(
    config: BuoyConfig, is_tailscale: bool, collector=None, health: dict | None = None
) -> dict:
    """Discover local services from Docker and build the full response.

    Args:
        collector: Optional pre-built Docker collector to reuse (avoids a
            redundant `docker ps` and preserves its caches). Defaults to a
            fresh `DockerCollector`.
        health: Optional map of static-service name -> health check result
            (as produced by `StaticHealthChecker.check_all()`), applied to
            `services.static` entries. Never fetched here — this function
            does no network I/O of its own.

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
    pinned_keys = set(config.services.pinned)

    # Build local services from discovered containers
    local_services = []
    for ctr in containers:
        name = ctr.get("name", "")
        if any(matches(ctr) for matches in hidden_matchers):
            continue

        override = _resolve_override(overrides, ctr)
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

        keys = _match_keys(ctr)
        pinned = any(k in pinned_keys for k in keys)
        group = _PINNED_GROUP if pinned else _resolve_group(override, ctr)

        local_services.append(
            {
                "name": display_name,
                "desc": desc,
                "icon": icon,
                "url": url,
                "source": "docker",
                "status": None,
                "group": group,
                "pinned": pinned,
                "key": keys,
            }
        )

    # Static (non-Docker) services and bookmarks — not subject to
    # services.hidden/overrides, which only apply to Docker discovery.
    for entry in config.services.static:
        h = (health or {}).get(entry.name) or {}
        keys = (entry.name,)
        pinned = entry.name in pinned_keys
        group = _PINNED_GROUP if pinned else entry.group

        local_services.append(
            {
                "name": entry.name,
                "desc": entry.desc,
                "icon": entry.icon,
                "url": entry.url,
                "source": "static",
                "status": h.get("status"),
                "latency_ms": h.get("latency_ms"),
                "group": group,
                "pinned": pinned,
                "key": keys,
            }
        )

    local_services = _sort_services(local_services, config)

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
