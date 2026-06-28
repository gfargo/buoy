"""Image update checker — compares local container image digests against registry.

Runs on a long interval (default 6h) to avoid registry rate limits.
Results are cached in-memory and exposed via the stats API as update_status
on each container entry.

Status values:
  up_to_date       — local digest matches registry
  update_available — registry has a newer digest
  unknown          — registry unreachable, auth failure, or other error
  skipped          — local build, <none> image, or @sha256-pinned ref
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

# Validate image ref components (no shell metacharacters)
_IMAGE_REF_RE = re.compile(r"^[a-zA-Z0-9][\w.\-/:@]*$")

_OCI_ACCEPT = ",".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def _valid_ref(ref: str) -> bool:
    return bool(ref) and bool(_IMAGE_REF_RE.match(ref)) and len(ref) <= 512


def _parse_ref(image: str) -> dict | None:
    """Parse image ref into {registry, repo, tag}.

    Returns None for refs that should be skipped:
    - digest-pinned (@sha256:...)
    - <none> or empty
    - refs without a registry and containing no slash (ambiguous local build)
    """
    if not image or image == "<none>" or image.startswith("sha256:"):
        return None

    # Strip @sha256:... digest pins — skip, can't reliably compare
    if "@sha256:" in image:
        return None

    # Split tag
    tag = "latest"
    if ":" in image.rsplit("/", 1)[-1]:
        image, tag = image.rsplit(":", 1)

    # Determine registry
    parts = image.split("/")
    if len(parts) >= 2 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        registry = parts[0]
        repo = "/".join(parts[1:])
    else:
        # Docker Hub implicit
        registry = "registry-1.docker.io"
        repo = "/".join(parts) if len(parts) > 1 else f"library/{parts[0]}"

    return {"registry": registry, "repo": repo, "tag": tag}


async def _run(*args: str, timeout: float = 10) -> tuple[int, str]:
    """Run a command, return (returncode, stdout)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode().strip()
    except Exception:
        return 1, ""


async def _local_digest(image: str) -> str | None:
    """Get the manifest digest for a locally pulled image via docker inspect."""
    if not _valid_ref(image):
        return None
    code, out = await _run("docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}")
    if code != 0 or not out:
        return None
    try:
        digests: list[str] = json.loads(out)
        # RepoDigests entries look like "repo@sha256:abc..."
        for d in digests:
            if "@sha256:" in d:
                return d.split("@sha256:", 1)[1]
    except (json.JSONDecodeError, IndexError):
        pass
    return None


def _docker_hub_token(repo: str) -> str | None:
    """Fetch an anonymous bearer token for Docker Hub."""
    url = (
        f"https://auth.docker.io/token?service=registry.docker.io"
        f"&scope=repository:{repo}:pull"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
            return json.loads(r.read())["token"]
    except Exception:
        return None


def _ghcr_token(repo: str) -> str | None:
    """Fetch an anonymous bearer token for GHCR."""
    url = f"https://ghcr.io/token?scope=repository:{repo}:pull"
    try:
        with urllib.request.urlopen(url, timeout=10)  as r:  # noqa: S310
            return json.loads(r.read())["token"]
    except Exception:
        return None


async def _remote_digest(ref: dict) -> str | None:
    """Fetch the manifest-list digest from the registry (HEAD request).

    Returns the Docker-Content-Digest header value (sha256 hex), or None.
    """
    registry = ref["registry"]
    repo = ref["repo"]
    tag = ref["tag"]

    # Resolve bearer token for known registries
    token: str | None = None
    loop = asyncio.get_event_loop()
    try:
        if registry == "registry-1.docker.io":
            token = await loop.run_in_executor(None, _docker_hub_token, repo)
        elif registry == "ghcr.io":
            token = await loop.run_in_executor(None, _ghcr_token, repo)
        # Other registries: try unauthenticated first

        url = f"https://{registry}/v2/{repo}/manifests/{tag}"
        req = urllib.request.Request(url, method="HEAD")  # noqa: S310
        req.add_header("Accept", _OCI_ACCEPT)
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        def _head() -> str | None:
            try:
                with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
                    return r.headers.get("Docker-Content-Digest", "").removeprefix("sha256:")
            except urllib.error.HTTPError:
                return None
            except Exception:
                return None

        digest = await loop.run_in_executor(None, _head)
        return digest or None
    except Exception:
        return None


class ImageUpdateChecker:
    """Checks running container images against their registries."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def _docker_image_refs(self) -> list[dict]:
        """List running containers with their image refs."""
        code, out = await _run(
            "docker", "ps", "--format", "{{.Names}}\t{{.Image}}"
        )
        if code != 0 or not out:
            return []
        refs = []
        for line in out.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                name, image = parts[0].strip(), parts[1].strip()
                if name and image:
                    refs.append({"container": name, "image": image})
        return refs

    async def check_all(self) -> dict[str, dict]:
        """Check all running containers; returns {container_name: status_dict}."""
        import time

        containers = await self._docker_image_refs()
        if not containers:
            return {}

        # Dedupe images so we only query each registry ref once
        image_to_ref: dict[str, dict | None] = {}
        for c in containers:
            img = c["image"]
            if img not in image_to_ref:
                image_to_ref[img] = _parse_ref(img)

        # Gather local digests and remote digests concurrently (per unique image)
        unique_images = list(image_to_ref.keys())

        local_digests = await asyncio.gather(
            *[_local_digest(img) for img in unique_images],
            return_exceptions=True,
        )

        remote_digests = await asyncio.gather(
            *[
                _remote_digest(image_to_ref[img])
                if image_to_ref[img] is not None else _noop()
                for img in unique_images
            ],
            return_exceptions=True,
        )

        # Build per-image status map
        image_status: dict[str, str] = {}
        now = time.time()
        for i, img in enumerate(unique_images):
            ref = image_to_ref[img]
            if ref is None:
                image_status[img] = "skipped"
                continue

            local = local_digests[i] if not isinstance(local_digests[i], Exception) else None
            remote = remote_digests[i] if not isinstance(remote_digests[i], Exception) else None

            if remote is None:
                image_status[img] = "unknown"
            elif local is None:
                image_status[img] = "unknown"
            elif local == remote:
                image_status[img] = "up_to_date"
            else:
                image_status[img] = "update_available"

        # Assemble per-container result
        result: dict[str, dict] = {}
        for c in containers:
            img = c["image"]
            result[c["container"]] = {
                "status": image_status.get(img, "unknown"),
                "image": img,
                "checked_at": now,
            }
        return result


async def _noop() -> None:
    return None
