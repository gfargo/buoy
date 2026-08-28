"""Central logging bootstrap for Buoy.

Configures a handler on the root logger once per process; subsequent calls
(e.g. ``--dev`` forcing DEBUG after an earlier bootstrap at the configured
level) only adjust the ``buoy`` logger namespace's level, so reload/import
churn never adds duplicate handlers.
"""

from __future__ import annotations

import logging

_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the ``buoy`` namespace at *level*.

    Invalid level names fall back to ``INFO`` rather than raising, so a
    typo'd ``buoy.yaml``/``BUOY_LOG_LEVEL`` value can't crash startup.
    """
    global _configured

    resolved = logging.getLevelName(str(level).upper())
    if not isinstance(resolved, int):
        resolved = logging.INFO

    if not _configured:
        logging.basicConfig(
            level=resolved,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        _configured = True

    logging.getLogger("buoy").setLevel(resolved)
