"""Tests for the declarative panel spec builders (buoy.plugins.panel)."""

from __future__ import annotations

from buoy.plugins import panel


class TestText:
    def test_defaults(self):
        assert panel.text("hello") == {"type": "text", "value": "hello", "status": None}

    def test_with_status(self):
        assert panel.text("uh oh", status="error") == {
            "type": "text",
            "value": "uh oh",
            "status": "error",
        }


class TestKeyvalue:
    def test_tuples(self):
        block = panel.keyvalue([("Label", "Value"), ("Other", "42")])
        assert block == {
            "type": "keyvalue",
            "rows": [
                {"label": "Label", "value": "Value", "status": None},
                {"label": "Other", "value": "42", "status": None},
            ],
        }

    def test_dict_rows_with_status(self):
        block = panel.keyvalue([{"label": "Disk errors", "value": "YES", "status": "error"}])
        assert block["rows"] == [{"label": "Disk errors", "value": "YES", "status": "error"}]

    def test_empty(self):
        assert panel.keyvalue([]) == {"type": "keyvalue", "rows": []}


class TestTable:
    def test_builds_rows_of_cells(self):
        rows = [[panel.cell("sda", status="ok"), panel.cell(42)]]
        block = panel.table(["Device", "Temp"], rows)
        assert block == {
            "type": "table",
            "columns": ["Device", "Temp"],
            "rows": [
                [
                    {"value": "sda", "status": "ok", "truncate": False, "mono": False},
                    {"value": 42, "status": None, "truncate": False, "mono": False},
                ]
            ],
        }

    def test_cell_truncate(self):
        c = panel.cell("a very long message", truncate=True)
        assert c["truncate"] is True

    def test_cell_mono(self):
        c = panel.cell("a1b2c3", mono=True)
        assert c["mono"] is True
        assert panel.cell("plain")["mono"] is False


class TestBadges:
    def test_badge_defaults(self):
        b = panel.badge("web-1")
        assert b == {"label": "web-1", "status": None, "dot": True}

    def test_badge_no_dot(self):
        b = panel.badge("lib", dot=False)
        assert b["dot"] is False

    def test_badges_wraps_items(self):
        items = [panel.badge("a", status="ok"), panel.badge("b", status="error")]
        assert panel.badges(items) == {"type": "badges", "items": items}


class TestBar:
    def test_defaults(self):
        assert panel.bar(42.5) == {"type": "bar", "pct": 42.5, "label": "", "status": None}

    def test_with_label_and_status(self):
        block = panel.bar(90, label="90% of budget", status="warn")
        assert block == {"type": "bar", "pct": 90, "label": "90% of budget", "status": "warn"}


class TestSparkline:
    def test_values_passed_through(self):
        block = panel.sparkline([1.0, 2.0, 3.0], status="info")
        assert block == {"type": "sparkline", "values": [1.0, 2.0, 3.0], "status": "info"}


class TestList:
    def test_list_item_defaults(self):
        item = panel.list_item("Title")
        assert item == {"primary": "Title", "secondary": "", "status": None, "href": None}

    def test_list_item_with_link(self):
        item = panel.list_item(
            "Add tests", secondary="gfargo/buoy", href="https://example.com/pr/1"
        )
        assert item["href"] == "https://example.com/pr/1"

    def test_list_wraps_items(self):
        items = [panel.list_item("a"), panel.list_item("b")]
        assert panel.list_(items) == {"type": "list", "items": items}
