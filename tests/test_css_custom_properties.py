"""CI guard: every CSS custom property referenced in the codebase must be
defined in at least one theme file.

Scans:
  - static/css/**/*.css (excluding static/css/themes/, which only defines
    properties) — every stylesheet shipped with the app, not just buoy.css,
    so a new component sheet is covered automatically.
  - src/buoy/plugins/builtin/*.py (inline JS in frontend_js() methods)

Out of scope: user-contributed plugins loaded from an external
`config.plugins.directory` at runtime. Those live outside the repo, so they
can't be statically scanned in CI — plugin authors are responsible for only
using custom properties defined in static/css/themes/.

Definitions are collected from all static/css/themes/*.css files.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Repo root is two levels up from this test file (tests/ → project root)
REPO_ROOT = Path(__file__).parent.parent

CSS_DIR = REPO_ROOT / "static" / "css"
THEMES_DIR = CSS_DIR / "themes"
PLUGINS_DIR = REPO_ROOT / "src" / "buoy" / "plugins" / "builtin"

# Match var(--foo) or var(--foo, fallback) — capture the property name only
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)")

# Match CSS custom property declarations: --foo: value;
_DEF_RE = re.compile(r"(--[\w-]+)\s*:")


def _collect_referenced(path: Path) -> set[str]:
    """Return all CSS custom property names referenced via var() in *path*."""
    text = path.read_text(encoding="utf-8")
    return set(_VAR_RE.findall(text))


def _collect_defined(path: Path) -> set[str]:
    """Return all CSS custom property names declared in *path*."""
    text = path.read_text(encoding="utf-8")
    return set(_DEF_RE.findall(text))


# ---------------------------------------------------------------------------
# Collect all definitions across every theme
# ---------------------------------------------------------------------------


def _all_defined_vars() -> set[str]:
    defined: set[str] = set()
    for theme_file in THEMES_DIR.glob("*.css"):
        defined |= _collect_defined(theme_file)
    return defined


# ---------------------------------------------------------------------------
# Collect all references from core CSS + plugin inline JS
# ---------------------------------------------------------------------------


def _all_referenced_vars() -> dict[str, set[str]]:
    """Return {var_name: {source_file, ...}} for every reference found."""
    refs: dict[str, set[str]] = {}

    def _add(var: str, source: str) -> None:
        refs.setdefault(var, set()).add(source)

    for css_file in CSS_DIR.rglob("*.css"):
        if THEMES_DIR in css_file.parents:
            continue
        for var in _collect_referenced(css_file):
            _add(var, css_file.relative_to(REPO_ROOT).as_posix())

    for py_file in PLUGINS_DIR.glob("*.py"):
        for var in _collect_referenced(py_file):
            _add(var, py_file.name)

    return refs


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_all_referenced_css_vars_are_defined() -> None:
    """Every var(--x) used in buoy.css or plugin inline JS must be defined in
    at least one theme file under static/css/themes/."""
    defined = _all_defined_vars()
    refs = _all_referenced_vars()

    undefined = {var: sources for var, sources in refs.items() if var not in defined}

    if undefined:
        lines = [
            "The following CSS custom properties are referenced but never defined in any theme:",
        ]
        for var in sorted(undefined):
            sources = ", ".join(sorted(undefined[var]))
            lines.append(f"  {var}  (used in: {sources})")
        lines.append(
            "\nFix: add the property to every theme in static/css/themes/ or update"
            " the reference to use an existing property."
        )
        raise AssertionError("\n".join(lines))
