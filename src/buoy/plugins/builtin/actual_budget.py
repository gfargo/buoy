"""Actual Budget plugin — monthly spend vs budget summary via HTTP adapter."""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date

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

    def frontend_js(self) -> str | None:
        return """
function render_actual_budget(data) {
  if (!data.detail || data.status === 'disabled') return '<div style="font-size:0.6rem;color:var(--text-dim)">Not configured</div>';
  const d = data.detail;
  const color = d.pct > 90 ? 'var(--amber)' : 'var(--cyan)';
  const cats = (d.categories || []).filter(c => c.budgeted > 0);
  const rows = cats.slice(0, 8).map(c => {
    const cp = c.budgeted > 0 ? Math.min(100, Math.round((c.spent / c.budgeted) * 100)) : 0;
    const cc = cp > 90 ? 'var(--red)' : 'var(--text-dim)';
    return '<div style="display:flex;justify-content:space-between;font-size:0.5rem;margin-bottom:0.2rem"><span style="color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">' + c.name + '</span><span style="color:' + cc + '">' + cp + '%</span></div>';
  }).join('');
  return '<div style="padding:0.4rem 0"><div style="height:6px;background:var(--border);border-radius:3px;overflow:hidden;margin-bottom:0.4rem"><div style="height:100%;width:' + Math.min(100, d.pct) + '%;background:' + color + ';border-radius:3px"></div></div><div style="font-size:0.55rem;color:var(--text-dim);margin-bottom:0.5rem">' + d.month + ' · $' + d.spent.toFixed(2) + ' of $' + d.budgeted.toFixed(2) + '</div>' + rows + '</div>';
}
"""
