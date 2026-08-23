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
        status: ok | warn | error | disabled | pending
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
    config: dict[str, Any]

    def __init__(self) -> None:
        """Initialise per-instance config dict.

        Subclasses that override __init__ should call super().__init__() so
        that self.config is always bound before configure() is called.
        """
        self.config = {}

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

    def demo_data(self) -> PanelData:
        """Sample data used in demo mode, in place of setup()/collect().

        Must not perform any I/O (network, subprocess, filesystem). Override
        this to return realistic sample data shaped like what collect()
        produces, so render(demo_data()) works without raising. The base
        implementation is a safe generic fallback for plugins that don't
        override it.
        """
        return PanelData(status="ok", summary="Demo data", detail={"demo": True})

    def render(self, data: PanelData) -> list[dict[str, Any]] | None:
        """Preferred rendering path: return a declarative panel spec.

        Build the list from the helpers in ``buoy.plugins.panel`` (``text``,
        ``table``, ``keyvalue``, ``badges``, ``bar``, ``sparkline``, ``list_``).
        Trusted, escaping frontend code (``static/js/panel.js``) turns the
        spec into HTML, so no plugin-authored markup ever reaches the page.
        If None, ``frontend_js()`` (if any) or the default renderer is used.
        """
        return None

    def frontend_js(self) -> str | None:
        """Deprecated escape hatch: return JS that renders this plugin's panel.

        Prefer ``render()``. This JS is executed via `new Function()` on the
        frontend and must build its own HTML, which is the root cause behind
        SEC-3/SEC-6 — plugins must escape every value themselves and no
        strict Content-Security-Policy is possible while it's in use.
        If None, ``render()``/the default renderer is used.
        The JS should define a function: render_{manifest.id}(data) → HTML string.
        """
        return None
