"""Tests for the Gitea/Forgejo plugin."""

import json
import ssl
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _cm(payload):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: json.dumps(payload).encode())
    cm.__exit__ = lambda s, *a: None
    return cm


def _dispatch(payloads):
    """Return a urlopen side_effect that routes by URL fragment.

    A payload that is an Exception instance is raised instead of returned,
    so tests can simulate a 404/unreachable endpoint per-URL.
    """

    def _side_effect(req, *a, **kw):
        url = req.full_url
        for fragment, payload in payloads.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return _cm(payload)
        raise AssertionError(f"unexpected URL {url}")

    return _side_effect


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def _repo(full_name, open_pr_counter=0):
    return {"full_name": full_name, "open_pr_counter": open_pr_counter}


def _issue(title, repo, url="https://git.example.com/a/b/pulls/1"):
    return {"title": title, "repository": {"full_name": repo}, "html_url": url}


def _run(name="ci", status="success", conclusion="success", branch="main"):
    return {"name": name, "status": status, "conclusion": conclusion, "head_branch": branch}


class TestGiteaPlugin:
    def _make_plugin(self, config=None):
        from buoy.plugins.builtin.gitea import GiteaPlugin

        plugin = GiteaPlugin()
        plugin.configure(
            config if config is not None else {"url": "https://git.example.com", "token": "tok"}
        )
        return plugin

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        plugin = self._make_plugin({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_missing_token_returns_disabled(self):
        plugin = self._make_plugin({"url": "https://git.example.com", "token": ""})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_healthy_instance_ok(self):
        plugin = self._make_plugin(
            {"url": "https://git.example.com", "token": "tok", "repos": ["a/b"]}
        )
        payloads = {
            "/user/repos": [_repo("a/b"), _repo("c/d")],
            "issues/search": [_issue("fix bug", "a/b")],
            "actions/tasks": {"workflow_runs": [_run()]},
        }
        with patch("urllib.request.urlopen", side_effect=_dispatch(payloads)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["repo_count"] == 2
        assert result.detail["pr_count"] == 1

    @pytest.mark.asyncio
    async def test_failed_action_run_errors(self):
        plugin = self._make_plugin(
            {"url": "https://git.example.com", "token": "tok", "repos": ["a/b"]}
        )
        payloads = {
            "/user/repos": [_repo("a/b")],
            "issues/search": [],
            "actions/tasks": {"workflow_runs": [_run(status="failure", conclusion="failure")]},
        }
        with patch("urllib.request.urlopen", side_effect=_dispatch(payloads)):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.detail["action_failures"] == 1

    @pytest.mark.asyncio
    async def test_queue_backlog_warns(self):
        plugin = self._make_plugin(
            {
                "url": "https://git.example.com",
                "token": "tok",
                "repos": ["a/b"],
                "queue_warn_threshold": 1,
            }
        )
        payloads = {
            "/user/repos": [_repo("a/b")],
            "issues/search": [],
            "actions/tasks": {
                "workflow_runs": [
                    _run(status="waiting", conclusion=""),
                    _run(status="queued", conclusion=""),
                ]
            },
        }
        with patch("urllib.request.urlopen", side_effect=_dispatch(payloads)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["queued"] == 2

    @pytest.mark.asyncio
    async def test_actions_404_degrades_gracefully(self):
        plugin = self._make_plugin(
            {"url": "https://git.example.com", "token": "tok", "repos": ["a/b"]}
        )
        payloads = {
            "/user/repos": [_repo("a/b")],
            "issues/search": [],
            "actions/tasks": _http_error(404),
        }
        with patch("urllib.request.urlopen", side_effect=_dispatch(payloads)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["actions_supported"] is False

    @pytest.mark.asyncio
    async def test_issue_search_404_falls_back_to_open_pr_counter(self):
        plugin = self._make_plugin({"url": "https://git.example.com", "token": "tok"})
        payloads = {
            "/user/repos": [_repo("a/b", open_pr_counter=3), _repo("c/d", open_pr_counter=2)],
            "issues/search": _http_error(404),
        }
        with patch("urllib.request.urlopen", side_effect=_dispatch(payloads)):
            result = await plugin.collect()

        assert result.detail["pr_count"] == 5

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_render_produces_keyvalue_and_pr_list(self):
        plugin = self._make_plugin()
        payloads = {
            "/user/repos": [_repo("a/b")],
            "issues/search": [_issue("fix bug", "a/b", url="https://git.example.com/a/b/pulls/1")],
        }
        with patch("urllib.request.urlopen", side_effect=_dispatch(payloads)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "keyvalue"
        list_block = next(b for b in blocks if b["type"] == "list")
        assert list_block["items"][0]["href"] == "https://git.example.com/a/b/pulls/1"

    def test_render_empty_state(self):
        plugin = self._make_plugin()
        from buoy.plugins.protocol import PanelData

        data = PanelData(
            status="ok",
            summary="0 repos · 0 open PRs",
            detail={
                "repo_count": 0,
                "pr_count": 0,
                "open_prs": [],
                "queued": 0,
                "action_failures": 0,
                "failed_runs": [],
                "actions_supported": True,
            },
        )
        blocks = plugin.render(data)
        assert blocks == [{"type": "text", "value": "All clear", "status": "dim"}]

    @pytest.mark.asyncio
    async def test_verify_ssl_false_passes_unverified_context(self):
        plugin = self._make_plugin(
            {"url": "https://git.example.com", "token": "tok", "verify_ssl": False}
        )
        payloads = {"/user/repos": [], "issues/search": []}
        captured = {}

        def _side_effect(req, *a, **kw):
            captured["context"] = kw.get("context")
            return _dispatch(payloads)(req, *a, **kw)

        with patch("urllib.request.urlopen", side_effect=_side_effect):
            await plugin.collect()

        assert captured["context"] is not None
        assert captured["context"].verify_mode == ssl.CERT_NONE
