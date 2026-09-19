"""Gitea / Forgejo plugin — repos, open PRs, Actions queue/failures."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_HTTP_TIMEOUT = 5
_MAX_ACTION_REPOS = 3
_MAX_REPOS = 50
_QUEUED_STATUSES = {"waiting", "queued"}


class GiteaPlugin(Plugin):
    """Shows Gitea/Forgejo repo counts, open PRs, and Actions queue/failures."""

    manifest = PluginManifest(
        id="gitea",
        name="Gitea",
        icon="🍵",
        description="Gitea/Forgejo repos, open PRs, Actions",
        version="1.0.0",
        config_schema={
            "url": {"type": "string", "required": True},
            "token": {"type": "string", "required": True},
            "repos": {"type": "array", "default": []},
            "verify_ssl": {"type": "boolean", "default": True},
            "queue_warn_threshold": {"type": "integer", "default": 5},
        },
        refresh_interval=120,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        token = self.config.get("token", "")
        if not url or not token:
            return PanelData(status="disabled", summary="Not configured")

        base = url.rstrip("/") + "/api/v1"
        headers = {"Authorization": f"token {token}", "Accept": "application/json"}
        verify_ssl = self.config.get("verify_ssl", True)
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        repos = self.config.get("repos") or []
        queue_warn_threshold = self.config.get("queue_warn_threshold", 5)

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._collect_sync, base, headers, ctx, repos, queue_warn_threshold
            )
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def _get_json(self, url: str, headers: dict, ctx) -> dict | list:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            return json.loads(resp.read())

    def _get_json_with_total(self, url: str, headers: dict, ctx) -> tuple[dict | list, int | None]:
        """Like _get_json, but also returns the true total from X-Total-Count.

        Gitea/Forgejo's paginated list endpoints cap each page at `limit`
        entries but report the real total in the `X-Total-Count` response
        header — without it, a page's length is just the page size, not
        the total count.
        """
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT, context=ctx) as resp:
            body = json.loads(resp.read())
            total_header = resp.headers.get("X-Total-Count") if resp.headers else None
        total = int(total_header) if total_header and total_header.isdigit() else None
        return body, total

    def _collect_sync(
        self, base: str, headers: dict, ctx, repos: list[str], queue_warn_threshold: int
    ) -> PanelData:
        try:
            repo_list, total_repos = self._get_json_with_total(
                f"{base}/user/repos?limit={_MAX_REPOS}", headers, ctx
            )
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return PanelData(status="error", summary="Auth failed")
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

        repo_count = total_repos if total_repos is not None else len(repo_list)
        pr_counter_sum = sum(r.get("open_pr_counter", 0) for r in repo_list)

        open_prs: list[dict] = []
        pr_count = pr_counter_sum
        try:
            pr_search, total_prs = self._get_json_with_total(
                f"{base}/repos/issues/search?type=pulls&state=open&limit=10", headers, ctx
            )
            issues = pr_search if isinstance(pr_search, list) else pr_search.get("data", [])
            for issue in issues:
                open_prs.append(
                    {
                        "title": issue.get("title", ""),
                        "repo": (issue.get("repository") or {}).get("full_name", ""),
                        "url": issue.get("html_url", ""),
                    }
                )
            pr_count = total_prs if total_prs is not None else len(issues)
        except urllib.error.HTTPError:
            pass  # older instance without the search endpoint — fall back to the counter

        queued = 0
        action_failures = 0
        failed_runs: list[dict] = []
        actions_supported = False
        for full_name in repos[:_MAX_ACTION_REPOS]:
            try:
                tasks = self._get_json(
                    f"{base}/repos/{full_name}/actions/tasks?limit=20", headers, ctx
                )
            except urllib.error.HTTPError:
                continue
            except Exception:
                continue

            if not isinstance(tasks, dict):
                continue

            actions_supported = True
            for run in tasks.get("workflow_runs", []):
                status = run.get("status", "")
                conclusion = run.get("conclusion", "")
                if status in _QUEUED_STATUSES:
                    queued += 1
                if conclusion == "failure" or status == "failure":
                    action_failures += 1
                    failed_runs.append(
                        {
                            "repo": full_name,
                            "name": run.get("name", ""),
                            "branch": run.get("head_branch", ""),
                        }
                    )

        if action_failures > 0:
            status = "error"
        elif queued > queue_warn_threshold:
            status = "warn"
        else:
            status = "ok"

        summary = f"{repo_count} repos · {pr_count} open PR{'s' if pr_count != 1 else ''}"
        if action_failures:
            summary += f" · {action_failures} failed"
        if queued:
            summary += f" · {queued} queued"

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "repo_count": repo_count,
                "pr_count": pr_count,
                "open_prs": open_prs,
                "queued": queued,
                "action_failures": action_failures,
                "failed_runs": failed_runs,
                "actions_supported": actions_supported,
            },
        )

    def demo_data(self) -> PanelData:
        open_prs = [
            {"title": "fix: retry flaky upload", "repo": "acme/infra", "url": "#"},
            {"title": "feat: add health endpoint", "repo": "acme/api", "url": "#"},
        ]
        return PanelData(
            status="warn",
            summary="12 repos · 2 open PRs · 1 queued",
            detail={
                "repo_count": 12,
                "pr_count": 2,
                "open_prs": open_prs,
                "queued": 1,
                "action_failures": 0,
                "failed_runs": [],
                "actions_supported": True,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        repo_count = d.get("repo_count", 0)
        pr_count = d.get("pr_count", 0)
        queued = d.get("queued", 0)
        failed_runs = d.get("failed_runs") or []
        open_prs = d.get("open_prs") or []

        if not repo_count and not pr_count and not queued and not failed_runs:
            return [panel.text("All clear", status="dim")]

        blocks: list[dict] = []
        kv_rows = [
            {"label": "Repos", "value": str(repo_count), "status": None},
            {"label": "Open PRs", "value": str(pr_count), "status": None},
        ]
        if failed_runs:
            kv_rows.append(
                {"label": "Failed runs", "value": str(len(failed_runs)), "status": "error"}
            )
        if queued:
            kv_rows.append({"label": "Queued", "value": str(queued), "status": "info"})
        blocks.append(panel.keyvalue(kv_rows))

        if open_prs:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(
                            pr.get("title", ""),
                            secondary=pr.get("repo", ""),
                            status="info",
                            href=pr.get("url"),
                        )
                        for pr in open_prs
                    ]
                )
            )

        if failed_runs:
            blocks.append(
                panel.badges(
                    [
                        panel.badge(
                            f"{r.get('repo', '')}: {r.get('name', '')}", status="error", dot=False
                        )
                        for r in failed_runs
                    ]
                )
            )

        return blocks
