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

    def demo_data(self) -> PanelData:
        """Sample data used when Buoy runs with --demo. Must not perform any I/O.

        Called instead of setup()/collect() in demo mode. Optional — the base
        Plugin class already provides a generic fallback — but overriding it
        lets your panel show something realistic in a demo/screenshot.
        """
        return PanelData(
            status="ok",
            summary="Plugin is working (demo)",
            detail={"message": "Hello from the example plugin!"},
        )
