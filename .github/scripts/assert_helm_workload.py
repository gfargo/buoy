"""Assert the rendered Buoy workload uses the expected image and version label."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("kind", choices=("Deployment", "DaemonSet"))
    parser.add_argument("image")
    parser.add_argument("version_label")
    args = parser.parse_args()

    documents = [
        document
        for document in yaml.safe_load_all(args.manifest.read_text())
        if isinstance(document, dict) and document.get("kind") == args.kind
    ]
    if len(documents) != 1:
        parser.error(f"expected one {args.kind}, found {len(documents)}")

    workload = documents[0]
    actual_label = workload["metadata"]["labels"]["app.kubernetes.io/version"]
    containers = workload["spec"]["template"]["spec"]["containers"]
    buoy_containers = [container for container in containers if container.get("name") == "buoy"]
    if len(buoy_containers) != 1:
        parser.error(f"expected one buoy container, found {len(buoy_containers)}")

    actual_image = buoy_containers[0]["image"]
    if actual_image != args.image:
        parser.error(f"expected image {args.image!r}, found {actual_image!r}")
    if actual_label != args.version_label:
        parser.error(f"expected version label {args.version_label!r}, found {actual_label!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
