"""Actual Budget plugin — monthly spend vs budget summary via HTTP adapter."""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class ActualBudgetPlugin(Plugin):
    """Shows current-month spend vs budget from an Actual Budget HTTP adapter."""

    manifest = PluginManifest(
        id="actual_budget",
        name="Budget",
        icon="💰",
        description="Monthly spend vs budget from Actual Budget",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "api_key": {"type": "string", "required": True},
            "budget_sync_id": {"type": "string", "required": True},
        },
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        api_key = self.config.get("api_key", "")
        budget_sync_id = self.config.get("budget_sync_id", "")

        if not all([url, api_key, budget_sync_id]):
            return PanelData(status="disabled", summary="Not configured")

        month = date.today().strftime("%Y-%m")

        try:
            api_url = f"{url.rstrip('/')}/v1/budgets/{budget_sync_id}/months/{month}"
            req = urllib.request.Request(
                api_url,
                headers={"x-api-key": api_key, "Accept": "application/json"},
            )
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_peer = False

            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                data = json.loads(resp.read())

            # Actual stores amounts as integer milliunits (1/1000 of currency unit)
            # actual-http-api returns them in the same format
            groups = data.get("categoryGroups", data.get("category_groups", []))
            total_spent = 0
            total_budgeted = 0
            categories = []

            for group in groups:
                for cat in group.get("categories", []):
                    budgeted = cat.get("budgeted", 0) or 0
                    spent = abs(cat.get("spent", 0) or 0)
                    total_budgeted += budgeted
                    total_spent += spent
                    if budgeted or spent:
                        categories.append(
                            {
                                "name": cat.get("name", ""),
                                "spent": round(spent / 1000, 2),
                                "budgeted": round(budgeted / 1000, 2),
                            }
                        )

            spent_dollars = round(total_spent / 1000, 2)
            budgeted_dollars = round(total_budgeted / 1000, 2)
            pct = round((total_spent / total_budgeted) * 100) if total_budgeted > 0 else 0

            status = "warn" if pct > 90 else "ok"
            summary = f"${spent_dollars:,.2f} / ${budgeted_dollars:,.2f} ({pct}%)"

            return PanelData(
                status=status,
                summary=summary,
                detail={
                    "month": month,
                    "spent": spent_dollars,
                    "budgeted": budgeted_dollars,
                    "pct": pct,
                    "categories": categories,
                },
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def demo_data(self) -> PanelData:
        month = date.today().strftime("%Y-%m")
        categories = [
            {"name": "Groceries", "spent": 412.30, "budgeted": 500.0},
            {"name": "Dining Out", "spent": 187.50, "budgeted": 150.0},
            {"name": "Utilities", "spent": 220.0, "budgeted": 250.0},
            {"name": "Transportation", "spent": 96.20, "budgeted": 200.0},
        ]
        spent = round(sum(c["spent"] for c in categories), 2)
        budgeted = round(sum(c["budgeted"] for c in categories), 2)
        pct = round((spent / budgeted) * 100) if budgeted else 0
        return PanelData(
            status="ok",
            summary=f"${spent:,.2f} / ${budgeted:,.2f} ({pct}%)",
            detail={
                "month": month,
                "spent": spent,
                "budgeted": budgeted,
                "pct": pct,
                "categories": categories,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        if not data.detail or data.status == "disabled":
            return [panel.text("Not configured", status="dim")]

        d = data.detail
        pct = d.get("pct", 0)
        bar_status = "warn" if pct > 90 else "info"
        label = f"{d.get('month', '')} · ${d.get('spent', 0):.2f} of ${d.get('budgeted', 0):.2f}"
        blocks: list[dict] = [panel.bar(pct, label=label, status=bar_status)]

        cats = [c for c in (d.get("categories") or []) if c.get("budgeted", 0) > 0]
        rows = []
        for c in cats[:8]:
            budgeted = c.get("budgeted", 0)
            cat_pct = min(100, round((c.get("spent", 0) / budgeted) * 100)) if budgeted > 0 else 0
            rows.append(
                {
                    "label": c.get("name", ""),
                    "value": f"{cat_pct}%",
                    "status": "error" if cat_pct > 90 else "dim",
                }
            )
        if rows:
            blocks.append(panel.keyvalue(rows))
        return blocks
