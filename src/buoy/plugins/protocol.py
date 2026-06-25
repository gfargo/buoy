"""Plugin protocol — base class and data types for Buoy plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginManifest:
    """Metadata about a plugin."""

    id: str
    name: str
    icon: str = ""
    description: str = ""
    version: str = "0.0.0"
    config_schema: dict[str, Any] = field(default_factory=dict)
    refresh_interval: int = 60  # seconds


@dataclass
class PanelData:
    """Data returned by a plugin's collect() method.

    Attributes:
        status: ok | warn | error | disabled
        summary: Short text shown in compact view (e.g., "3 notifications")
        detail: Arbitrary dict passed to the frontend renderer
    """

    status: str = "ok"
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


class Plugin:
    """Base class for Buoy plugins.

    Subclass this and implement `collect()` to create a plugin.
    Optionally override `setup()`, `teardown()`, and `frontend_js()`.
    """

    manifest: PluginManifest = PluginManifest(id="base", name="Base Plugin")
    config: dict[str, Any] = {}

    def configure(self, config: dict[str, Any]) -> None:
        """Called with the plugin's config section from buoy.yaml."""
        self.config = config

    async def setup(self) -> None:
        """Called once on startup. Use for connection pooling, auth checks, etc."""
        pass

    async def teardown(self) -> None:
        """Called on graceful shutdown."""
        pass

    async def collect(self) -> PanelData:
        """Called on each refresh cycle. Return data for the frontend.

        Must be implemented by subclasses.
        """
        raise NotImplementedError("Plugins must implement collect()")

    def frontend_js(self) -> str | None:
        """Optional: return JS that renders this plugin's panel.

        If None, the default key-value renderer is used.
        The JS should define a function: render_{manifest.id}(data) → HTML string.
        """
        return None
