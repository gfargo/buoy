"""Version consistency across package, runtime, CLI, release, and Helm surfaces."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

import buoy
import buoy._version as version_module
import buoy.server as server
from buoy.config import BuoyConfig, FeaturesConfig
from buoy.server import create_app

REPO_ROOT = Path(__file__).parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHART = REPO_ROOT / "deploy/helm/buoy/Chart.yaml"
HELM_DIR = CHART.parent


def _project_version() -> str:
    with PYPROJECT.open("rb") as pyproject_file:
        return tomllib.load(pyproject_file)["project"]["version"]


def test_source_package_and_installed_metadata_versions_match():
    expected = _project_version()

    assert version_module.VERSION == expected
    assert buoy.__version__ == expected
    assert importlib.metadata.version("buoy") == expected


def test_resolver_prefers_source_pyproject(tmp_path, monkeypatch):
    source_pyproject = tmp_path / "pyproject.toml"
    source_pyproject.write_text('[project]\nname = "buoy"\nversion = "9.8.7"\n')
    monkeypatch.setattr(version_module, "_distribution_version", lambda _name: "1.0.0")

    assert version_module._resolve_version(source_pyproject) == "9.8.7"


def test_resolver_falls_back_to_installed_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(version_module, "_distribution_version", lambda _name: "7.6.5")

    assert version_module._resolve_version(tmp_path / "missing.toml") == "7.6.5"


def test_resolver_has_safe_unknown_fallback(tmp_path, monkeypatch):
    def missing_distribution(_name):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(version_module, "_distribution_version", missing_distribution)

    assert version_module._resolve_version(tmp_path / "missing.toml") == "0+unknown"


def test_cli_version_exits_without_starting_server():
    result = subprocess.run(
        [sys.executable, "-m", "buoy", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    assert result.stdout.strip() == f"buoy {_project_version()}"
    assert result.stderr == ""


def test_health_and_deploy_info_report_canonical_version(monkeypatch):
    config = BuoyConfig()
    config.features = FeaturesConfig(websocket=False, history=False, demo_mode=True)
    app = create_app(config)

    async def unavailable_subprocess(*_args, **_kwargs):
        raise FileNotFoundError

    with TestClient(app) as client:
        monkeypatch.setattr(server.asyncio, "create_subprocess_exec", unavailable_subprocess)
        health = client.get("/api/health")
        deploy_info = client.get("/api/deploy-info")

    assert health.status_code == 200
    assert deploy_info.status_code == 200
    assert health.json()["version"] == version_module.VERSION
    assert deploy_info.json()["version"] == version_module.VERSION


def test_release_manifest_and_helm_versions_match_project():
    expected = _project_version()
    manifest = json.loads((REPO_ROOT / ".release-please-manifest.json").read_text())
    chart = yaml.safe_load(CHART.read_text())
    release_config = json.loads((REPO_ROOT / "release-please-config.json").read_text())
    extra_files = release_config["packages"]["."]["extra-files"]

    assert manifest["."] == expected
    assert str(chart["version"]) == expected
    assert str(chart["appVersion"]) == expected
    assert extra_files == [{"type": "generic", "path": "deploy/helm/buoy/Chart.yaml"}]
    assert CHART.read_text().count("x-release-please-version") == 2


@pytest.mark.parametrize("tag_prefix", ["buoy-v", "v"])
def test_release_version_script_normalizes_and_verifies_supported_tags(tag_prefix):
    script = REPO_ROOT / ".github/scripts/release_version.py"
    expected = _project_version()
    result = subprocess.run(
        [sys.executable, str(script), f"{tag_prefix}{expected}", str(PYPROJECT)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )

    assert result.stdout.strip() == f"v{expected}"
    assert result.stderr == ""


def test_release_version_script_rejects_mismatch_and_malformed_tags(tmp_path):
    script = REPO_ROOT / ".github/scripts/release_version.py"
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "buoy"\nversion = "9.8.7"\n')

    mismatch = subprocess.run(
        [sys.executable, str(script), "buoy-v1.2.3", str(pyproject)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    malformed = subprocess.run(
        [sys.executable, str(script), "buoy-latest", str(pyproject)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert mismatch.returncode == 1
    assert "does not match package version" in mismatch.stderr
    assert malformed.returncode == 1
    assert "Unsupported release tag" in malformed.stderr


def test_release_workflow_invokes_version_validator_for_tags():
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text()

    assert 'if [[ "$GITHUB_REF" == refs/tags/* ]]' in workflow
    assert 'python .github/scripts/release_version.py "$GITHUB_REF_NAME" pyproject.toml' in workflow


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_helm_renders_canonical_and_safe_overridden_workload_versions():
    expected = _project_version()
    subprocess.run(["helm", "lint", str(HELM_DIR)], check=True, capture_output=True, text=True)

    default_render = subprocess.run(
        ["helm", "template", "buoy", str(HELM_DIR), "--show-only", "templates/workload.yaml"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    override_render = subprocess.run(
        [
            "helm",
            "template",
            "buoy",
            str(HELM_DIR),
            "--show-only",
            "templates/workload.yaml",
            "--set-string",
            "image.tag=_canary",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    long_tag = "a" * 70
    daemonset_render = subprocess.run(
        [
            "helm",
            "template",
            "buoy",
            str(HELM_DIR),
            "--show-only",
            "templates/workload.yaml",
            "--set",
            "workloadKind=daemonset",
            "--set-string",
            f"image.tag={long_tag}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    default_workload = yaml.safe_load(default_render)
    override_workload = yaml.safe_load(override_render)
    daemonset_workload = yaml.safe_load(daemonset_render)

    assert default_workload["kind"] == "Deployment"
    assert default_workload["spec"]["template"]["spec"]["containers"][0]["image"] == (
        f"ghcr.io/gfargo/buoy:{expected}"
    )
    assert default_workload["metadata"]["labels"]["app.kubernetes.io/version"] == expected

    assert override_workload["spec"]["template"]["spec"]["containers"][0]["image"] == (
        "ghcr.io/gfargo/buoy:_canary"
    )
    assert override_workload["metadata"]["labels"]["app.kubernetes.io/version"] == "canary"

    assert daemonset_workload["kind"] == "DaemonSet"
    assert daemonset_workload["spec"]["template"]["spec"]["containers"][0]["image"] == (
        f"ghcr.io/gfargo/buoy:{long_tag}"
    )
    assert daemonset_workload["metadata"]["labels"]["app.kubernetes.io/version"] == "a" * 63
