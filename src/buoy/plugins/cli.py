"""`buoy plugin` CLI — list/inspect discoverable plugins, install new ones via pip."""

from __future__ import annotations

import subprocess
import sys

from buoy.plugins.loader import PluginManager


def cmd_list(config) -> int:
    """Print every discoverable plugin: source, id, name, version, enabled state."""
    plugins = sorted(PluginManager.discover_all(config), key=lambda p: p["id"])
    if not plugins:
        print("No plugins discovered.")
        return 0

    rows = [
        ("SOURCE", "ID", "NAME", "VERSION", "ENABLED"),
        *(
            (p["source"], p["id"], p["name"], p["version"], "yes" if p["enabled"] else "no")
            for p in plugins
        ),
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))
    return 0


def cmd_info(config, plugin_id: str) -> int:
    """Print the manifest for a single plugin id."""
    plugins = {p["id"]: p for p in PluginManager.discover_all(config)}
    plugin = plugins.get(plugin_id)
    if plugin is None:
        print(f"No plugin found with id '{plugin_id}'", file=sys.stderr)
        return 1

    print(f"id:               {plugin['id']}")
    print(f"name:             {plugin['name']}")
    print(f"icon:             {plugin['icon']}")
    print(f"description:      {plugin['description']}")
    print(f"version:          {plugin['version']}")
    print(f"source:           {plugin['source']}")
    print(f"enabled:          {'yes' if plugin['enabled'] else 'no'}")
    print(f"refresh_interval: {plugin['refresh_interval']}")
    print("config_schema:")
    if plugin["config_schema"]:
        for key, meta in plugin["config_schema"].items():
            print(f"  {key}: {meta}")
    else:
        print("  (none)")
    return 0


def cmd_install(config, spec: str) -> int:
    """Install a package via pip, then report which plugin(s) newly appeared."""
    before = {p["id"] for p in PluginManager.discover_all(config)}

    result = subprocess.run([sys.executable, "-m", "pip", "install", spec])
    if result.returncode != 0:
        return result.returncode

    after = PluginManager.discover_all(config)
    new_ids = {p["id"] for p in after} - before
    if new_ids:
        print(f"\nRegistered plugin(s): {', '.join(sorted(new_ids))}")
        print("Enable each in buoy.yaml under plugins.builtin.<id>.enabled: true")
    else:
        print(
            "\nInstall succeeded but no new 'buoy.plugins' entry point was detected. "
            'Check that the package declares one under [project.entry-points."buoy.plugins"].'
        )
    return 0
