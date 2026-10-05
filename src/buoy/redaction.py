"""Shared secret-redaction helpers.

Split out of ``server.py`` so ``buoy.plugins.loader`` can redact per-key
config values (schema-driven, via ``is_secret_key``) without importing the
server module and creating a loader -> server import cycle.
"""

from __future__ import annotations

from typing import Any

_SECRET_KEY_FRAGMENTS = {"token", "password", "secret", "key"}


def is_secret_key(key: str, meta: dict[str, Any] | None = None) -> bool:
    """Return True if *key* should be treated as a secret.

    A schema's explicit ``"secret"`` flag wins; otherwise falls back to the
    key-name heuristic used by ``redact_secrets()``.
    """
    if isinstance(meta, dict) and "secret" in meta:
        return bool(meta["secret"])
    return any(frag in key.lower() for frag in _SECRET_KEY_FRAGMENTS)


def redact_secrets(obj: Any) -> Any:
    """Recursively replace secret-bearing string values with a redaction marker.

    Only string values are redacted (booleans/ints with "key" in the name are left alone).
    """
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***"
            if isinstance(v, str) and v and is_secret_key(k)
            else redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_secrets(item) for item in obj]
    return obj
