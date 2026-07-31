"""Every built-in plugin's manifest.id must be documented in README.md and buoy.yaml.example."""

import importlib
import inspect
import pkgutil
from pathlib import Path

import buoy.plugins.builtin as builtin_pkg
from buoy.plugins.protocol import Plugin

REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_plugin_class(module):
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, Plugin) and obj is not Plugin:
            return obj
    return None


def _builtin_plugin_ids():
    ids = []
    for _, module_path, _ispkg in pkgutil.iter_modules(
        builtin_pkg.__path__, builtin_pkg.__name__ + "."
    ):
        module_name = module_path.rsplit(".", 1)[-1]
        if module_name.startswith("_"):
            continue
        module = importlib.import_module(module_path)
        plugin_class = _find_plugin_class(module)
        assert plugin_class is not None, f"No Plugin subclass found in {module_path}"
        ids.append(plugin_class.manifest.id)
    return ids


def test_every_builtin_plugin_documented_in_readme_and_yaml_example():
    ids = _builtin_plugin_ids()
    assert len(ids) > 0, "expected at least one built-in plugin"

    readme = (REPO_ROOT / "README.md").read_text()
    yaml_example = (REPO_ROOT / "buoy.yaml.example").read_text()

    missing_from_readme = [i for i in ids if i not in readme]
    missing_from_yaml = [i for i in ids if i not in yaml_example]

    assert not missing_from_readme, (
        f"Built-in plugin id(s) missing from README.md: {missing_from_readme}"
    )
    assert not missing_from_yaml, (
        f"Built-in plugin id(s) missing from buoy.yaml.example: {missing_from_yaml}"
    )
