"""Keep docs/grafana/*.{json,yml} in sync with the Prometheus exporter and alert engine.

The dashboard and rule files under docs/grafana/ are hand-written, not
generated, so nothing enforces that a metric name they reference actually
exists in PrometheusExporterPlugin.format_metrics(), or that an alert
threshold still matches buoy.alerts.DEFAULT_THRESHOLDS. These tests derive
the allowed metric names from the exporter itself and fail the build the
moment either drifts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from buoy.alerts import DEFAULT_THRESHOLDS
from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAFANA_DIR = REPO_ROOT / "docs" / "grafana"

METRIC_RE = re.compile(r"\bbuoy_[a-z0-9_]+\b")


def _exporter_metric_names() -> set[str]:
    """Derive the full set of metric names the exporter can emit, from code."""
    stats = {
        "hostname": "test-host",
        "cpu": 42.0,
        "mem_used": 4.0,
        "mem_total": 8.0,
        "temp": 55.0,
        "disk_pct": 67.0,
        "containers": 3,
        "uptime_s": 12345,
        "nvme": {"temp": 38, "wear_pct": 2},
    }
    output = PrometheusExporterPlugin.format_metrics(stats)
    return set(re.findall(r"# TYPE (buoy_[a-z0-9_]+) gauge", output))


def _load_dashboard() -> dict:
    return json.loads((GRAFANA_DIR / "buoy.json").read_text())


def _load_rule_file(name: str) -> dict:
    return yaml.safe_load((GRAFANA_DIR / name).read_text())


def _dashboard_panel_exprs(dashboard: dict):
    for panel in dashboard.get("panels", []):
        for target in panel.get("targets", []):
            if "expr" in target:
                yield target["expr"]


def test_dashboard_is_valid_json():
    dashboard = _load_dashboard()
    assert dashboard["title"]
    assert dashboard["uid"]
    assert dashboard["schemaVersion"]
    assert dashboard["panels"]


def test_dashboard_metrics_exist_in_exporter():
    exporter_names = _exporter_metric_names()
    assert exporter_names, "expected at least one metric name from format_metrics()"

    dashboard = _load_dashboard()
    referenced = set()
    for expr in _dashboard_panel_exprs(dashboard):
        referenced.update(METRIC_RE.findall(expr))

    assert referenced, "expected at least one buoy_* metric referenced in dashboard panels"
    unknown = referenced - exporter_names
    assert not unknown, f"dashboard references metrics the exporter never emits: {unknown}"


def test_dashboard_uses_datasource_variable():
    dashboard = _load_dashboard()
    variables = dashboard["templating"]["list"]
    ds_vars = [v for v in variables if v.get("type") == "datasource"]
    assert ds_vars, "expected a datasource-type template variable"
    ds_var_name = ds_vars[0]["name"]

    for panel in dashboard["panels"]:
        ds = panel.get("datasource")
        if ds is None:
            continue
        uid = ds.get("uid", "")
        assert uid == f"${{{ds_var_name}}}", (
            f"panel {panel.get('title')!r} hardcodes datasource uid {uid!r} "
            "instead of using the datasource variable"
        )


def test_host_variable_uses_always_present_metric():
    always_present = {"buoy_uptime_seconds", "buoy_disk_used_percent", "buoy_containers_running"}
    dashboard = _load_dashboard()
    host_vars = [v for v in dashboard["templating"]["list"] if v.get("name") == "host"]
    assert host_vars, "expected a 'host' template variable"

    query = host_vars[0]["query"]
    referenced = set(METRIC_RE.findall(query))
    assert referenced & always_present, (
        f"host variable query {query!r} must reference an always-emitted metric "
        f"from {always_present}"
    )


def test_rule_files_parse_and_reference_real_metrics():
    exporter_names = _exporter_metric_names()
    recorded_names = set()

    for filename in ("recording-rules.yml", "alert-rules.yml"):
        doc = _load_rule_file(filename)
        assert "groups" in doc
        for group in doc["groups"]:
            for rule in group["rules"]:
                is_record = "record" in rule
                is_alert = "alert" in rule
                assert is_record != is_alert, (
                    f"rule in {filename} must be exactly one of record/alert"
                )
                if is_record:
                    recorded_names.add(rule["record"])

    allowed = exporter_names | recorded_names | {"up"}
    for filename in ("recording-rules.yml", "alert-rules.yml"):
        doc = _load_rule_file(filename)
        for group in doc["groups"]:
            for rule in group["rules"]:
                referenced = set(METRIC_RE.findall(rule["expr"]))
                unknown = referenced - allowed
                assert not unknown, (
                    f"{filename} rule {rule.get('record') or rule.get('alert')} "
                    f"references unknown metrics: {unknown}"
                )


def _threshold_from_expr(expr: str) -> float:
    match = re.search(r">\s*([0-9.]+)", expr)
    assert match, f"couldn't find a numeric threshold in expr: {expr}"
    return float(match.group(1))


def test_alert_thresholds_match_alert_engine():
    doc = _load_rule_file("alert-rules.yml")
    alerts_by_name = {
        rule["alert"]: rule for group in doc["groups"] for rule in group["rules"] if "alert" in rule
    }

    expected = {
        "BuoyCPUHigh": DEFAULT_THRESHOLDS["cpu"]["warn"],
        "BuoyCPUCritical": DEFAULT_THRESHOLDS["cpu"]["crit"],
        "BuoyMemoryHigh": DEFAULT_THRESHOLDS["memory"]["warn"],
        "BuoyMemoryCritical": DEFAULT_THRESHOLDS["memory"]["crit"],
        "BuoyDiskHigh": DEFAULT_THRESHOLDS["disk"]["warn"],
        "BuoyDiskCritical": DEFAULT_THRESHOLDS["disk"]["crit"],
        "BuoyTempHigh": DEFAULT_THRESHOLDS["temp"]["warn"],
        "BuoyTempCritical": DEFAULT_THRESHOLDS["temp"]["crit"],
    }

    for alert_name, expected_value in expected.items():
        assert alert_name in alerts_by_name, f"missing alert rule: {alert_name}"
        actual_value = _threshold_from_expr(alerts_by_name[alert_name]["expr"])
        assert actual_value == expected_value, (
            f"{alert_name} threshold {actual_value} != DEFAULT_THRESHOLDS value {expected_value}"
        )


def test_readme_links_grafana_doc():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "docs/grafana/README.md" in readme
