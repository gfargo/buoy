"""
Example Buoy plugin — use this as a template for custom plugins.

Place this file (or any .py file) in the plugins/ directory and enable it
in buoy.yaml under plugins.directory.
"""

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class ExamplePlugin(Plugin):
    """A minimal example plugin that returns a static message."""

    manifest = PluginManifest(
        id="example",
        name="Example",
        icon="🔌",
        description="A minimal example plugin",
        version="1.0.0",
        config_schema={},
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        return PanelData(
            status="ok",
            summary="Plugin is working",
            detail={"message": "Hello from the example plugin!"},
        )
