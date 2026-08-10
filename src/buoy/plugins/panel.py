"""Declarative panel spec — the preferred way for plugins to describe their UI.

Plugins that implement ``render()`` return a list of these block dicts instead
of a raw HTML/JS string. The frontend (``static/js/panel.js``) turns them into
HTML, escaping every value itself — plugin authors never touch markup and
can't introduce an XSS via untrusted data (peer names, log lines, PR titles,
...).

Status is always one of the semantic strings below (or ``None``), never a raw
CSS color — the frontend owns the status → color mapping so all plugins stay
visually consistent and themeable.
"""

from __future__ import annotations

from typing import Any

Status = str | None  # "ok" | "warn" | "error" | "dim" | "info" | None


def text(value: str, status: Status = None) -> dict[str, Any]:
    """A single line of text — empty states, short notes."""
    return {"type": "text", "value": value, "status": status}


def keyvalue(rows: list[tuple[str, str] | dict[str, Any]]) -> dict[str, Any]:
    """Label/value pairs, one per row. Accepts (label, value) tuples or dicts."""
    return {"type": "keyvalue", "rows": [_kv_row(r) for r in rows]}


def _kv_row(row: tuple[str, str] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        return {
            "label": row.get("label", ""),
            "value": row.get("value", ""),
            "status": row.get("status"),
        }
    label, value = row
    return {"label": label, "value": value, "status": None}


def cell(value: Any, status: Status = None, truncate: bool = False) -> dict[str, Any]:
    """A single table cell. Use inside `table()` rows for per-cell styling."""
    return {"value": value, "status": status, "truncate": truncate}


def table(columns: list[str], rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """A table. Each row is a list of `cell()` dicts, one per column."""
    return {"type": "table", "columns": columns, "rows": rows}


def badges(items: list[dict[str, Any]]) -> dict[str, Any]:
    """A wrapped row of small pill badges, each with an optional status dot."""
    return {"type": "badges", "items": items}


def badge(label: str, status: Status = None, dot: bool = True) -> dict[str, Any]:
    return {"label": label, "status": status, "dot": dot}


def bar(pct: float, label: str = "", status: Status = None) -> dict[str, Any]:
    """A horizontal progress bar (0-100) with an optional caption below it."""
    return {"type": "bar", "pct": pct, "label": label, "status": status}


def sparkline(values: list[float], status: Status = None) -> dict[str, Any]:
    """A small bar-chart sparkline; the frontend highlights the last value."""
    return {"type": "sparkline", "values": values, "status": status}


def list_(items: list[dict[str, Any]]) -> dict[str, Any]:
    """A vertical list of items, each with a primary line and optional secondary/link."""
    return {"type": "list", "items": items}


def list_item(
    primary: str,
    secondary: str = "",
    status: Status = None,
    href: str | None = None,
) -> dict[str, Any]:
    return {"primary": primary, "secondary": secondary, "status": status, "href": href}
