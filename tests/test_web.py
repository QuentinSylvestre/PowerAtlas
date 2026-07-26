"""Tests for web module."""

import asyncio
import datetime as dt
import json
import logging
import os
import re
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from power_atlas.data import Session
from power_atlas.web import app
from power_atlas import launcher


@pytest.fixture
def client():
    """TestClient with default Origin header for same-origin guard.

    The base URL is loopback because ``_ALLOWED_HOSTS`` only admits real
    loopback names; TestClient's own default (``http://testserver``) is a
    single-label host an attacker can win on the local network, so it must not
    be allowlisted just to make this suite pass.
    """
    c = TestClient(app, base_url="http://127.0.0.1")
    # Patch the post method to add Origin by default
    _original_post = c.post
    def _post_with_origin(*args, **kwargs):
        headers = kwargs.get("headers", {})
        if "Origin" not in headers and "origin" not in headers:
            headers["Origin"] = "http://127.0.0.1"
            kwargs["headers"] = headers
        return _original_post(*args, **kwargs)
    c.post = _post_with_origin
    return c


def test_post_rejected_for_non_loopback_host():
    """DNS-rebinding defense: a POST arriving with a non-loopback Host is refused (403),
    even when the Origin matches the (attacker-controlled) Host."""
    rebind_client = TestClient(app, base_url="http://evil.com")
    resp = rebind_client.post(
        "/api/pin-folder",
        json={"folder": "C:\\projects\\myapp"},
        headers={"Origin": "http://evil.com"},
    )
    assert resp.status_code == 403


def _make_session(title="test session", cwd="C:\\projects\\myapp", **kwargs):
    defaults = dict(
        session_id="sess-1", title=title, cwd=cwd,
        created_at="2026-06-17T10:00:00", updated_at="2026-06-17T12:00:00",
        first_prompt="hello world", last_prompt="fix the bug",
        last_reply_tail="Done, fixed.",
    )
    defaults.update(kwargs)
    return Session(**defaults)


def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "hx-get" in resp.text
    assert "skeleton-card" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = [_make_session(cwd=workspace)]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert workspace in resp.text or Path(workspace).name in resp.text
    assert "1</span>" in resp.text or "card-count" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_empty(mock_discover, mock_providers, mock_config, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    mock_providers.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "No workspaces found" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_stale(mock_discover, mock_providers, mock_sessions, client):
    mock_discover.return_value = [("C:\\nonexistent\\path\\xyz", 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = [_make_session(cwd="C:\\nonexistent\\path\\xyz")]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "stale" in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_error(mock_discover, mock_providers, client):
    mock_discover.side_effect = RuntimeError("db unavailable")
    mock_providers.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "Error" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_filters(mock_discover, mock_config, client, tmp_path):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    workspace = str(tmp_path)
    mock_discover.return_value = [
        (workspace, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
        ("C:\\other\\project", 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]

    resp = client.get(f"/search?q={Path(workspace).name}")
    assert resp.status_code == 200
    assert Path(workspace).name in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_no_results(mock_discover, mock_config, client, tmp_path):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "", "kiro-cli")]

    resp = client.get("/search?q=zzzznotfound")
    assert resp.status_code == 200
    assert "No results" in resp.text


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_settings(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "-a --verbose",
        "color": "",
        "enabled": True,
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert saved.provider_settings["kiro-cli"]["default_args"] == "-a --verbose"
    assert saved.provider_settings["kiro-cli"]["enabled"] is True


@patch("power_atlas.web.load_config")
def test_get_provider_settings(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a", "color": "", "enabled": True},
    })

    resp = client.get("/api/provider/kiro-cli")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "kiro-cli"
    assert body["default_args"] == "-a"
    assert body["enabled"] is True


@patch("power_atlas.web.load_config")
def test_get_provider_settings_default(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.get("/api/provider/claude-code")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "claude-code"
    assert body["default_args"] == ""
    assert body["enabled"] is True


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces")
def test_session_row_shows_all_fields(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [_make_session(
        cwd=workspace, title="my title",
        first_prompt="first question", last_prompt="last question",
        last_reply_tail="final answer",
    )]

    resp = client.get("/partials/sessions", params={"cwd": workspace})
    assert "my title" in resp.text
    assert "first question" in resp.text
    assert "last question" in resp.text or "final answer" in resp.text  # new template shows last_reply not last_prompt
    assert "final answer" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_folder_empty_sessions(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 0, "", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = []

    resp = client.get("/partials/workspaces")
    assert "Loading" in resp.text or "workspace-card" in resp.text



@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_pin_session(mock_sessions, mock_config, mock_save, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_sessions.return_value = []
    resp = client.post("/api/pin-session", json={"session_id": "sess-1"},
                       headers={"X-Workspace": "C:\\app", "Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "sess-1" in saved.pinned_sessions


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_unpin_session(mock_sessions, mock_config, mock_save, client):
    from power_atlas.config import Config
    mock_config.return_value = Config(pinned_sessions=["sess-1", "sess-2"])
    mock_sessions.return_value = []
    resp = client.post("/api/unpin-session", json={"session_id": "sess-1"},
                       headers={"X-Workspace": "C:\\app", "Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "sess-1" not in saved.pinned_sessions


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_folders_merged(mock_discover, mock_sessions, mock_config, mock_providers, client, tmp_path):
    from power_atlas.config import Config
    workspace = str(tmp_path)
    pinned = "C:\\my-pinned-workspace"
    mock_config.return_value = Config(pinned_folders=[pinned])
    mock_discover.return_value = [(workspace, 0, "", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Pinned workspace appears even though not in discovery results (0 sessions)
    assert pinned in resp.text or "my-pinned-workspace" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_pinned_workspaces_provider_filter(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Provider filter on unified workspaces panel shows only matching pinned workspaces."""
    from power_atlas.config import Config
    ws_kiro = str(tmp_path / "kiro-proj")
    ws_claude = str(tmp_path / "claude-proj")
    mock_config.return_value = Config(pinned_folders=[ws_kiro, ws_claude])
    mock_discover.return_value = [
        (ws_kiro, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws_claude, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Filter by kiro-cli — only kiro workspace should appear
    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    assert "kiro-proj" in resp.text
    assert "claude-proj" not in resp.text

    # Filter by claude-code — only claude workspace should appear
    resp = client.get("/partials/workspaces?provider=claude-code")
    assert resp.status_code == 200
    assert "claude-proj" in resp.text
    assert "kiro-proj" not in resp.text

    # No filter (all) — both should appear
    resp = client.get("/partials/workspaces?provider=all")
    assert resp.status_code == 200
    assert "kiro-proj" in resp.text
    assert "claude-proj" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces")
def test_pinned_sessions_sorted_first(mock_discover, mock_sessions, mock_config, client, tmp_path):
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(pinned_sessions=["sess-2"])
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [
        _make_session(session_id="sess-1", title="unpinned", cwd=workspace),
        _make_session(session_id="sess-2", title="pinned", cwd=workspace),
    ]
    resp = client.get("/partials/sessions", params={"cwd": workspace})
    assert resp.status_code == 200
    # Pinned should appear before unpinned
    assert resp.text.index("pinned") < resp.text.index("unpinned")


# --- Phase 1: Simplified pin/unpin and provider=all sessions ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_pin_folder_simple(mock_load, mock_save, client):
    """Pin folder API accepts just folder path (no provider)."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/pin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert "C:\\projects\\myapp" in saved.pinned_folders


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_pin_folder_no_duplicate(mock_load, mock_save, client):
    """Pin folder API does not duplicate already-pinned paths."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\projects\\myapp"])
    resp = client.post("/api/pin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_unpin_folder_simple(mock_load, mock_save, client):
    """Unpin folder API removes path from list."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\projects\\myapp", "C:\\other"])
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert "C:\\projects\\myapp" not in saved.pinned_folders
    assert "C:\\other" in saved.pinned_folders


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_unpin_folder_not_present(mock_load, mock_save, client):
    """Unpin folder API is no-op for non-pinned path."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\other"])
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\nonexistent"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    mock_save.assert_not_called()


@patch("power_atlas.web.data.PROVIDERS")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_partials_sessions_provider_all(mock_get_sessions, mock_config, mock_providers, client, tmp_path):
    """provider=all merges sessions from all providers sorted by updated_at."""
    from power_atlas.config import Config
    from unittest.mock import MagicMock

    workspace = str(tmp_path)
    mock_config.return_value = Config()

    # Create mock providers
    kiro_mod = MagicMock()
    kiro_mod.is_available.return_value = True
    claude_mod = MagicMock()
    claude_mod.is_available.return_value = True

    mock_providers.items.return_value = [("kiro-cli", kiro_mod), ("claude-code", claude_mod)]

    # get_sessions returns different sessions per provider
    def side_effect(cwd, prov):
        if prov == "kiro-cli":
            return [_make_session(session_id="k1", title="kiro session", updated_at="2026-06-17T14:00:00")]
        elif prov == "claude-code":
            return [_make_session(session_id="c1", title="claude session", updated_at="2026-06-17T15:00:00")]
        return []

    mock_get_sessions.side_effect = side_effect

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "all"})
    assert resp.status_code == 200
    # Both sessions present
    assert "kiro session" in resp.text
    assert "claude session" in resp.text
    # Claude session (newer) should appear first
    assert resp.text.index("claude session") < resp.text.index("kiro session")


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_partials_sessions_single_provider(mock_get_sessions, mock_config, client, tmp_path):
    """provider=kiro-cli still works (single provider mode)."""
    from power_atlas.config import Config

    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_get_sessions.return_value = [
        _make_session(session_id="k1", title="kiro only", cwd=workspace),
    ]

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "kiro-cli"})
    assert resp.status_code == 200
    assert "kiro only" in resp.text


class TestSaveSettingAllowlist:
    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_unknown_key(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "__class__", "value": "evil"},
                           headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "unknown" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_terminal_command(self, mock_load, mock_save, client):
        """terminal_command is no longer a valid setting — profile edits go through /api/launch-profile/save."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "terminal_command", "value": "wt.exe"},
                           headers={"Origin": "http://127.0.0.1"})
        body = resp.json()
        assert body["ok"] is False
        assert "unknown" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_wrong_type(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "port", "value": "not_int"},
                           headers={"Origin": "http://127.0.0.1"})
        body = resp.json()
        assert body["ok"] is False
        assert "type" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_accepts_valid_setting(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "peek_hotkey", "value": "ctrl+shift+x"},
                           headers={"Origin": "http://127.0.0.1"})
        body = resp.json()
        assert body["ok"] is True
        mock_save.assert_called_once()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_valid(mock_load, mock_save, client):
    """Valid port value is accepted and saved."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 8080}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert saved.port == 8080


@patch("power_atlas.web.load_config")
def test_save_setting_port_bool_rejected(mock_load, client):
    """Boolean value for port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": True}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.load_config")
def test_save_setting_port_out_of_range(mock_load, client):
    """Out-of-range port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 99999}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_zero_accepted(mock_load, mock_save, client):
    """Port 0 (random mode) is accepted."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 0}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is True


# --- Phase 4: session-tail endpoint ---


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="hello user")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_returns_messages(mock_tail, mock_first, mock_cache, client):
    mock_tail.return_value = ["message one", "message two"]
    mock_cache.get.return_value = [
        Session(session_id="sess-1", title="My Session", cwd="C:\\Projects\\myapp",
                created_at="", updated_at="", first_prompt="", last_prompt="", last_reply_tail=""),
    ]
    resp = client.get("/partials/session-tail?sid=sess-1&cwd=C%3A%5CProjects%5Cmyapp")
    assert resp.status_code == 200
    assert "message one" in resp.text
    assert "message two" in resp.text
    assert "tail-line" in resp.text
    assert "tail-header" in resp.text
    assert "tail-workspace" in resp.text
    assert "myapp" in resp.text
    assert "tail-label" in resp.text
    assert "My Session" in resp.text


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="hello user")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_graceful_no_cache(mock_tail, mock_first, mock_cache, client):
    """When session is not in cache, title is empty but tooltip still renders."""
    mock_tail.return_value = ["agent reply"]
    mock_cache.get.return_value = None  # Cache miss
    resp = client.get("/partials/session-tail?sid=sess-1&cwd=C%3A%5CProjects%5Cmyapp")
    assert resp.status_code == 200
    assert "agent reply" in resp.text
    assert "tail-workspace" in resp.text  # workspace name from Path(cwd).name still shows
    assert "myapp" in resp.text
    assert "tail-title" not in resp.text  # no title when not in cache


@patch("power_atlas.web.data.get_first_prompt", return_value="")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_empty(mock_tail, mock_first, client):
    mock_tail.return_value = []
    resp = client.get("/partials/session-tail?sid=sess-1")
    assert resp.status_code == 200
    assert "tail-empty" in resp.text
    assert "No recent output" in resp.text



# --- Phase 3: custom launcher CRUD ---


@patch("power_atlas.web.icons.extract_icon")
@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_launcher_create(mock_load, mock_save, mock_extract, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/launcher/create", json={
        "name": "Dev Server", "command": "npm", "custom_args": "start", "cwd": "C:\\proj", "color": "#ef4444"
    })
    assert resp.status_code == 200
    assert "created" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert len(saved.custom_launchers) == 1
    assert saved.custom_launchers[0]["name"] == "Dev Server"
    assert saved.custom_launchers[0]["id"]  # UUID generated


@patch("power_atlas.web.icons.remove_icon")
@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_launcher_delete(mock_load, mock_save, mock_remove_icon, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[{"id": "abc", "name": "x", "command": "y"}])
    resp = client.post("/api/launcher/delete", json={"id": "abc"})
    assert resp.status_code == 200
    assert "deleted" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert len(saved.custom_launchers) == 0


@patch("power_atlas.web.launcher.launch_custom")
@patch("power_atlas.web.load_config")
def test_launcher_run(mock_load, mock_launch, client, tmp_path):
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))
    resp = client.post("/api/launcher/run", json={
        "name": "test", "command": "npm", "custom_args": "start", "cwd": str(tmp_path)
    })
    assert resp.status_code == 200
    assert "started" in resp.text.lower()
    mock_launch.assert_called_once()


@patch("power_atlas.web.icons.has_icon", return_value=False)
@patch("power_atlas.web.load_config")
def test_launcher_icon_fallback_terminal(mock_load, mock_has, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[{"id": "abc", "terminal": True, "command": "kiro-cli"}])
    resp = client.get("/api/launcher-icon/abc")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert "polyline" in resp.text  # terminal icon has polyline


@patch("power_atlas.web.icons.has_icon", return_value=False)
@patch("power_atlas.web.load_config")
def test_launcher_icon_fallback_app(mock_load, mock_has, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[{"id": "xyz", "terminal": False, "command": "app.exe"}])
    resp = client.get("/api/launcher-icon/xyz")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert "circle" in resp.text  # app icon has circle


@patch("power_atlas.web.icons.icon_path")
@patch("power_atlas.web.icons.has_icon", return_value=True)
def test_launcher_icon_serves_png(mock_has, mock_path, client, tmp_path):
    # Create a fake PNG file
    fake_png = tmp_path / "test.png"
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    mock_path.return_value = fake_png
    resp = client.get("/api/launcher-icon/abc")
    assert resp.status_code == 200
    assert "image/png" in resp.headers["content-type"]


# --- Phase 2: Provider tabs and filtering ---


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_provider_filter(mock_discover, mock_providers, client, tmp_path):
    """Filtering by provider shows only groups containing that provider, but preserves multi-provider info."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 3, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    # Cards no longer have data-provider; verify the workspace is rendered with the provider icon
    assert 'provider--kiro-cli' in resp.text
    assert 'workspace-card' in resp.text
    # Always discovers all providers, then filters post-grouping
    mock_discover.assert_any_call(provider=None)


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_all_tab(mock_discover, mock_providers, client, tmp_path):
    """All tab shows cards from all providers interleaved."""
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    ws3 = str(tmp_path / "proj3")
    mock_discover.return_value = [
        (ws1, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws2, 1, "2026-01-01T00:00:00Z", "claude-code"),
        (ws3, 1, "2026-01-03T00:00:00Z", "kiro-ide"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code", "kiro-ide"]

    resp = client.get("/partials/workspaces?provider=all")
    assert resp.status_code == 200
    # Cards no longer have data-provider; verify all provider icons appear
    assert 'provider--kiro-cli' in resp.text
    assert 'provider--claude-code' in resp.text
    assert 'provider--kiro-ide' in resp.text
    # Verify discover was called with provider=None (all)
    mock_discover.assert_any_call(provider=None)


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tab_hidden_single_provider(mock_discover, mock_providers, client, tmp_path):
    """When only one provider available, no tab bar rendered in partials (tabs now static in index.html)."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Tab bar no longer rendered inline by partials_workspaces (moved to static HTML)
    assert "provider-tabs" not in resp.text
    assert "provider-filter" not in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tab_shown_multiple_providers(mock_discover, mock_providers, client, tmp_path):
    """When multiple providers available, /api/available-providers returns them for the static filter."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Tab bar no longer rendered inline by partials_workspaces
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "provider-tabs" not in resp.text

    # Instead, /api/available-providers returns the provider list for JS-rendered filter
    resp = client.get("/api/available-providers")
    assert resp.status_code == 200
    providers = resp.json()
    names = [p["name"] for p in providers]
    assert "kiro-cli" in names
    assert "claude-code" in names
    assert all("display" in p and "color" in p for p in providers)


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_has_data_provider(mock_discover, mock_providers, client, tmp_path):
    """Workspace cards no longer have data-provider; they show provider icons and colored border."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "claude-code")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # data-provider removed; card is workspace-level now
    assert 'data-provider=' not in resp.text
    # Provider color shown via gradient span (solid color for single provider)
    assert "provider-gradient" in resp.text
    assert "#c2590f" in resp.text
    # Provider icon badge
    assert "provider-icon-badge" in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_has_provider_icon_img(mock_discover, mock_providers, client, tmp_path):
    """Workspace cards include provider icon img tag with fallback badge."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'provider-icon-badge' in resp.text
    assert 'src="/api/launcher-icon/provider--kiro-cli"' in resp.text
    assert 'provider-badge-fallback' in resp.text
    # Title now uses the display name
    assert 'title="kiro-cli"' in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_uses_user_configured_color(mock_discover, mock_providers, mock_config, client, tmp_path):
    """When user sets a custom color in provider_settings, workspace card uses it."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "", "color": "#ff0000", "enabled": True},
    })
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "#ff0000" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_empty_provider_tab_shows_helper(mock_discover, mock_providers, mock_config, client):
    """When a filtered provider has no results, a helper message is shown."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    mock_providers.return_value = ["kiro-cli", "claude-code", "kiro-ide"]

    resp = client.get("/partials/workspaces?provider=claude-code")
    assert resp.status_code == 200
    assert "No Claude Code sessions found" in resp.text

    resp = client.get("/partials/workspaces?provider=kiro-ide")
    assert resp.status_code == 200
    assert "No Kiro IDE sessions found" in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_active_tab_class(mock_discover, mock_providers, client, tmp_path):
    """Provider filter is now client-side (via /api/available-providers); tabs no longer in partials."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Request kiro-cli filtered view — no tab bar in response (tabs are static HTML now)
    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    assert "provider-tabs" not in resp.text
    # The endpoint still filters correctly by provider
    assert "workspace-card" in resp.text


# --- Phase 4: Selection-aware launcher batch ---


@patch("power_atlas.web.launcher.launch_custom_batch")
@patch("power_atlas.web.load_config")
def test_launcher_run_batch_endpoint(mock_load, mock_batch, client, tmp_path):
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    lid = "test-launcher-id"
    mock_load.return_value = Config(custom_launchers=[{
        "id": lid, "name": "Dev", "command": "npm", "custom_args": "start",
        "cwd": "", "env": {}, "terminal": True, "use_selected_workspaces": True,
    }])
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_batch.return_value = [
        LaunchResult(True, None, ws1),
        LaunchResult(True, None, ws2),
    ]
    resp = client.post("/api/launcher/run-batch", json={"id": lid, "workspaces": [ws1, ws2]})
    assert resp.status_code == 200
    assert "Launched 2" in resp.text
    mock_batch.assert_called_once()
    call_kwargs = mock_batch.call_args
    assert call_kwargs[1]["workspaces"] == [ws1, ws2]


@patch("power_atlas.web.load_config")
def test_launcher_run_batch_not_found(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[])
    resp = client.post("/api/launcher/run-batch", json={"id": "nonexistent", "workspaces": ["C:\\proj"]})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


# --- Phase 5: Provider settings and default_args ---


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_launch_uses_provider_default_args(mock_load, mock_launch, client, tmp_path):
    """Launch endpoint passes default_args from provider_settings to launch_session."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a --verbose", "color": "", "enabled": True},
    })
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    mock_launch.assert_called_once()
    call_kwargs = mock_launch.call_args[1]
    assert call_kwargs["default_args"] == "-a --verbose"


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_launch_no_provider_settings_uses_empty_default_args(mock_load, mock_launch, client, tmp_path):
    """Launch endpoint passes empty default_args when no provider_settings configured."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    call_kwargs = mock_launch.call_args[1]
    assert call_kwargs["default_args"] == ""


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_disabled_provider_hidden_from_tabs(mock_discover, mock_config, mock_providers, client, tmp_path):
    """Disabling a provider via provider_settings hides it from the tab bar."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(provider_settings={
        "claude-code": {"default_args": "", "color": "", "enabled": False},
    })
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # claude-code tab should not be rendered
    assert "provider=claude-code" not in resp.text
    # kiro-cli tab should still be there (but single provider = no tabs)
    # With only one enabled provider, no tab bar at all
    assert "provider-tabs" not in resp.text


# --- Phase 3 (unification): Provider-launcher tiles in grid ---


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_partials_launchers_provider_tiles_before_custom(mock_load, mock_providers, client):
    """Provider tiles render before custom launchers in the grid."""
    from power_atlas.config import Config
    mock_load.return_value = Config(
        custom_launchers=[{"id": "custom-1", "name": "My Script", "command": "python",
                           "custom_args": "", "cwd": "", "env": {}, "color": "",
                           "terminal": True, "use_selected_workspaces": False}],
        provider_settings={"kiro-cli": {"default_args": "-a", "color": "", "enabled": True}},
    )
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    # Provider tile comes first
    kiro_pos = resp.text.find('data-id="provider--kiro-cli"')
    custom_pos = resp.text.find('data-id="custom-1"')
    assert kiro_pos >= 0, "Provider tile not found"
    assert custom_pos >= 0, "Custom tile not found"
    assert kiro_pos < custom_pos, "Provider tile should appear before custom tile"


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_provider_tile_has_correct_data_id(mock_load, mock_providers, client):
    """Provider tile has data-id='provider--kiro-cli'."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    assert 'data-id="provider--kiro-cli"' in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_disabled_provider_not_in_launcher_grid(mock_load, mock_providers, client):
    """Disabled providers don't appear in the launcher grid."""
    from power_atlas.config import Config
    mock_load.return_value = Config(
        provider_settings={"kiro-cli": {"default_args": "", "color": "", "enabled": False}},
    )
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    assert 'provider--kiro-cli' not in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tab_bar_no_gear_icons(mock_discover, mock_providers, client, tmp_path):
    """Tab bar no longer has gear icons (no tab-gear class)."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "tab-gear" not in resp.text


@patch("power_atlas.web.launcher.launch_custom_batch")
@patch("power_atlas.web.load_config")
def test_launcher_run_batch_passes_workspace_arg_for_non_terminal(mock_load, mock_batch, client):
    """Non-terminal selection-aware launchers pass pass_workspace_arg=True."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config(
        custom_launchers=[{"id": "gui-1", "name": "IDE", "command": "code",
                           "custom_args": "", "cwd": "", "env": {}, "color": "",
                           "terminal": False, "use_selected_workspaces": True}],
    )
    mock_batch.return_value = [LaunchResult(True, None, "/tmp")]
    resp = client.post("/api/launcher/run-batch", json={"id": "gui-1", "workspaces": ["/tmp"]})
    assert resp.status_code == 200
    mock_batch.assert_called_once()
    call_kwargs = mock_batch.call_args
    assert call_kwargs.kwargs.get("pass_workspace_arg") is True or call_kwargs[1].get("pass_workspace_arg") is True


# --- Phase 3: 3-provider gradient + resume button UX ---


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_three_provider_gradient_has_all_colors(mock_discover, mock_providers, client, tmp_path):
    """Workspace card with 3 providers renders gradient with all three colors and gradient-3plus class."""
    workspace = str(tmp_path)
    mock_discover.return_value = [
        (workspace, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
        (workspace, 1, "2026-01-01T00:00:00Z", "claude-code"),
        (workspace, 1, "2026-01-01T00:00:00Z", "kiro-ide"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code", "kiro-ide"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # All three provider colors present
    assert "#7138cc" in resp.text  # kiro-cli
    assert "#c2590f" in resp.text  # claude-code
    assert "#8b5cf6" in resp.text  # kiro-ide
    # Gradient class for 3+ providers
    assert "gradient-3plus" in resp.text
    # Multi-provider class present
    assert "multi-provider" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_resume_button_kiro_ide_tooltip(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    """Kiro IDE sessions show 'Open workspace in Kiro IDE' tooltip; others show 'Resume session'."""
    from power_atlas.data import Session
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-ide")]
    mock_providers.return_value = ["kiro-ide"]
    mock_sessions.return_value = [Session(
        session_id="test-ide-session",
        title="Test IDE Session",
        cwd=workspace,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        first_prompt="Hello",
        last_prompt="",
        last_reply_tail="",
    )]

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "kiro-ide"})
    assert resp.status_code == 200
    assert 'title="Open workspace in Kiro IDE"' in resp.text
    assert 'aria-label="Open in Kiro IDE"' in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_resume_button_terminal_provider_tooltip(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    """Terminal providers (kiro-cli, claude-code) show 'Resume session' tooltip."""
    from power_atlas.data import Session
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = [Session(
        session_id="test-cli-session",
        title="Test CLI Session",
        cwd=workspace,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        first_prompt="Hello",
        last_prompt="",
        last_reply_tail="",
    )]

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "kiro-cli"})
    assert resp.status_code == 200
    assert 'title="Resume session"' in resp.text
    assert 'aria-label="Resume"' in resp.text


# --- /api/settings endpoint tests ---


@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_api_settings_returns_expected_keys(mock_load, mock_autostart, client):
    """GET /api/settings returns 200 with all expected keys (no terminal_command)."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_autostart.return_value = False

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    expected_keys = {"active_launch_profile", "launch_profiles", "peek_hotkey", "port", "default_directory", "provider_settings", "custom_launchers", "autostart"}
    assert set(body.keys()) == expected_keys
    assert body["autostart"] is False
    assert "terminal_command" not in body


@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_api_settings_reflects_config_values(mock_load, mock_autostart, client):
    """GET /api/settings reflects pre-populated config values."""
    from power_atlas.config import Config, LaunchProfile
    mock_load.return_value = Config(
        launch_profiles=[LaunchProfile(id="prod", name="Production", terminal_command="wt.exe")],
        active_launch_profile="prod",
        peek_hotkey="ctrl+shift+z",
        port=8080,
        provider_settings={
            "kiro-cli": {"default_args": "-a", "color": "#ff0000", "enabled": True},
        },
        custom_launchers=[{"name": "my-launcher", "command": "echo hi"}],
    )
    mock_autostart.return_value = True

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_launch_profile"] == "prod"
    assert len(body["launch_profiles"]) == 1
    assert body["launch_profiles"][0]["name"] == "Production"
    assert body["launch_profiles"][0]["terminal_command"] == "wt.exe"
    assert body["peek_hotkey"] == "ctrl+shift+z"
    assert body["port"] == 8080
    assert body["provider_settings"]["kiro-cli"]["default_args"] == "-a"
    assert body["provider_settings"]["kiro-cli"]["color"] == "#ff0000"
    assert body["custom_launchers"] == [{"name": "my-launcher", "command": "echo hi"}]
    assert body["autostart"] is True


@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_api_settings_autostart_exception_returns_false(mock_load, mock_autostart, client):
    """GET /api/settings returns autostart=False when is_enabled() raises."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_autostart.side_effect = RuntimeError("registry unavailable")

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["autostart"] is False


# --- Phase 3 (Launch Profiles): Same-origin guard tests ---


@pytest.fixture
def raw_client():
    """TestClient WITHOUT default Origin header, for testing the guard itself."""
    return TestClient(app, base_url="http://127.0.0.1")


class TestSameOriginGuard:
    def test_origin_null_rejected(self, raw_client):
        """Origin: null is always rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Origin": "null"})
        assert resp.status_code == 403
        assert "Forbidden" in resp.json()["error"]

    def test_both_absent_rejected(self, raw_client):
        """Request with neither Origin nor Referer is rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080})
        assert resp.status_code == 403

    def test_mismatched_origin_rejected(self, raw_client):
        """Origin from different host is rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Origin": "http://evil.example.com"})
        assert resp.status_code == 403

    def test_mismatched_referer_rejected(self, raw_client):
        """Referer from different host (no Origin) is rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Referer": "http://evil.example.com/path"})
        assert resp.status_code == 403

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_valid_origin_accepted(self, mock_load, mock_save, raw_client):
        """Matching Origin header passes the guard."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_valid_referer_only_accepted(self, mock_load, mock_save, raw_client):
        """Matching Referer (without Origin) passes the guard."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Referer": "http://127.0.0.1/some/page"})
        assert resp.status_code == 200

    def test_guard_applies_to_multiple_endpoints(self, raw_client):
        """Multiple POST endpoints are all protected by the guard."""
        endpoints = [
            "/api/pin-folder",
            "/api/unpin-folder",
            "/api/pin-session",
            "/api/unpin-session",
            "/api/launch",
            "/api/provider/save",
            "/api/launch-profile/activate",
            "/api/launch-profile/save",
            "/api/launch-profile/delete",
        ]
        for endpoint in endpoints:
            resp = raw_client.post(endpoint, json={},
                                   headers={"Origin": "null"})
            assert resp.status_code == 403, f"{endpoint} should reject Origin: null"


class TestAcpNavigationGuard:
    """``GET /acp`` is state-changing, which the guard's POST-only scope was
    justified by it not being.

    Rendering the page seeds a socket that sends ``subscribe``, is answered
    ``unknown_session``, and sends ``load`` — which reaches ``ensure_started``
    and spawns ``kiro-cli acp -a``, trust-all-tools. A cross-origin top-level
    navigation was therefore enough to start an agent with no user gesture.
    """

    def test_the_dashboard_row_action_still_works(self, raw_client):
        """``location.href = '/acp?sid=…'`` is a same-origin top-level
        navigation: **no** ``Origin`` at all, because browsers attach it only
        to navigations that are not GET/HEAD, plus a same-origin ``Referer``.
        Copying the POST rule verbatim would have refused this."""
        resp = raw_client.get("/acp?sid=abc", headers={
            "Referer": "http://127.0.0.1/", "Sec-Fetch-Site": "same-origin"})
        assert resp.status_code == 200

    def test_a_bookmark_or_a_typed_address_still_works(self, raw_client):
        """Neither header, which is what a user-initiated load sends. This is
        the case a rule demanding an Origin would break."""
        resp = raw_client.get("/acp", headers={"Sec-Fetch-Site": "none"})
        assert resp.status_code == 200

    def test_a_client_that_sends_no_fetch_metadata_still_works(self, raw_client):
        resp = raw_client.get("/acp")
        assert resp.status_code == 200

    def test_a_cross_origin_fetch_is_refused(self, raw_client):
        resp = raw_client.get("/acp?sid=abc", headers={
            "Origin": "http://evil.example.com", "Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403
        assert _ACP_TOKEN not in resp.text

    def test_a_cross_origin_referer_is_refused(self, raw_client):
        resp = raw_client.get("/acp?sid=abc",
                              headers={"Referer": "https://evil.example.com/x"})
        assert resp.status_code == 403

    def test_a_cross_site_navigation_with_no_referrer_is_refused(self, raw_client):
        """One ``Referrer-Policy: no-referrer`` on the attacker's page strips
        the only header an Origin/Referer rule could have caught this with,
        leaving it indistinguishable from a bookmark. ``Sec-Fetch-Site`` is set
        by the browser and page content cannot influence it."""
        resp = raw_client.get("/acp?sid=abc",
                              headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403
        assert _ACP_TOKEN not in resp.text

    def test_origin_null_is_refused(self, raw_client):
        resp = raw_client.get("/acp", headers={"Origin": "null"})
        assert resp.status_code == 403

    @patch("power_atlas.web.autostart.is_enabled")
    @patch("power_atlas.web.load_config")
    def test_the_rule_is_scoped_to_acp_rather_than_every_get(
            self, mock_load, mock_autostart, raw_client):
        """Every other GET here only reads, and browsers omit Origin on
        ordinary navigations — widening the rule to all of them would refuse
        traffic that has no way to declare itself."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        mock_autostart.return_value = False
        resp = raw_client.get("/api/settings", headers={
            "Referer": "https://evil.example.com/x",
            "Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 200


class TestHostAllowlistCoversGetRequests:
    """The Host allowlist used to live inside the guard's ``method == "POST"``
    branch, so no GET route was ever Host-checked. A rebound page is same-origin
    with what it fetches, so it could read workspace paths, session titles and
    settings straight out of the response bodies."""

    @pytest.mark.parametrize("host", ["evil.com", "testserver"])
    @pytest.mark.parametrize("path", [
        "/", "/partials/workspaces", "/api/settings", "/search?q=a",
        "/api/tags", "/partials/all-sessions",
    ])
    def test_get_rejected_for_non_loopback_host(self, raw_client, path, host):
        resp = raw_client.get(path, headers={"Host": host})
        assert resp.status_code == 403, f"GET {path} should reject Host: {host}"
        assert resp.json() == {"error": "Forbidden"}

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:4915", "localhost", "[::1]:4915"])
    @patch("power_atlas.web.autostart.is_enabled")
    @patch("power_atlas.web.load_config")
    def test_api_settings_served_on_loopback(self, mock_load, mock_autostart, raw_client, host):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        mock_autostart.return_value = False
        resp = raw_client.get("/api/settings", headers={"Host": host})
        assert resp.status_code == 200, f"GET /api/settings should accept Host: {host}"

    @patch("power_atlas.web.load_config")
    @patch("power_atlas.web.data.available_providers")
    @patch("power_atlas.web.data.discover_workspaces_with_counts")
    def test_partials_workspaces_served_on_loopback(self, mock_discover, mock_providers,
                                                    mock_config, raw_client):
        from power_atlas.config import Config
        mock_config.return_value = Config()
        mock_discover.return_value = []
        mock_providers.return_value = []
        resp = raw_client.get("/partials/workspaces", headers={"Host": "localhost"})
        assert resp.status_code == 200


# --- ACP surface: DNS-rebinding and token-check regressions ---

from starlette.websockets import WebSocketDisconnect
from power_atlas.web import (
    _ACP_TOKEN, _acp_token_ok, _host_allowed, _ws_origin_ok)


class TestSingleLabelHostRejected:
    """``testserver`` was allowlisted so this suite would pass, which made every
    guarded route reachable from a rebound single-label host."""

    def test_post_rejected_for_single_label_host(self, raw_client):
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Host": "testserver", "Origin": "http://testserver"})
        assert resp.status_code == 403

    def test_acp_page_rejected_for_non_loopback_host(self, raw_client):
        """End-to-end: no rebound Host reaches the token, whichever check stops
        it. Which one actually did is a question this cannot answer, because the
        middleware runs first — see ``TestAcpInlineHostCheck`` for the route's
        own check, tested with the middleware out of the way."""
        for host in ("testserver", "evil.com"):
            resp = raw_client.get("/acp", headers={"Host": host})
            assert resp.status_code == 403, f"GET /acp should reject Host: {host}"
            assert _ACP_TOKEN not in resp.text

    def test_acp_page_served_on_loopback(self, raw_client):
        resp = raw_client.get("/acp")
        assert resp.status_code == 200
        assert _ACP_TOKEN in resp.text


# --- Host header: parsed here, never taken from starlette's URL ---

from fastapi.middleware.asyncexitstack import AsyncExitStackMiddleware

# ``app`` minus every middleware except the one FastAPI's own route handler
# asserts on. Routing and the endpoint run; ``same_origin_guard`` does not, so a
# route's own checks can be tested without the middleware answering ahead of
# them. Wrapping the router rather than reaching into the built stack keeps this
# to one documented FastAPI class, which fails loudly if it ever moves.
_ROUTER_ONLY = AsyncExitStackMiddleware(app.router)


def _raw_asgi(asgi_app, path: str, raw_headers: list[tuple[bytes, bytes]],
              method: str = "GET") -> tuple[int, bytes]:
    """Call an ASGI app with exactly the headers given, byte for byte.

    Both HTTP clients within reach synthesise ``Host`` from the URL they are
    handed and will not send a request without one, so "no Host at all" and
    "two Host headers" are unreachable through them — yet an HTTP/1.0 client
    produces the first by accident and a raw socket sends either without
    complaint. This builds the scope uvicorn would build instead.

    ``asgi_app`` selects the entry point: ``app`` runs ``same_origin_guard``,
    ``_ROUTER_ONLY`` skips it and reaches a route's own checks, which is
    the only way to tell the two apart.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 4915),
        "app": app,
        "state": {},
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(asgi_app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent
                    if m["type"] == "http.response.body")
    return status, body


# Every one of these must be refused. The trailing note on each names the way
# `request.url.hostname` — the value both Host checks used to read — mishandled
# it. Starlette discards a Host that fails its `_HOST_RE` and rebuilds the URL
# from `scope["server"]`, so a rejected Host read back as plain loopback.
_HOSTILE_HOSTS = [
    "evil.com",                  # ordinary rebinding target
    "a_b.evil.com",              # underscore is absent from `_HOST_RE`, so this
    "evil_1.attacker.com",       # read back as 127.0.0.1 and was served
    "[evil",                     # unmatched bracket: `urlsplit` raised
    "[::1",                      # ValueError, i.e. an unauthenticated 500
    "[::1]extra",                # bytes trailing the closing bracket
    "[]",                        # bracketed empty name
    "evil.com@127.0.0.1:4915",   # userinfo: `urlsplit` keeps the last "@" part
    "evil.com@localhost",
    "127.0.0.1:4915.evil.com",   # a hostname smuggled in as the port
    "127.0.0.1:",                # empty port
    "127.0.0.1:1:2",             # two ports
    "::1",                       # bare IPv6, which HTTP requires bracketed
    "localhost.evil.com",        # allowlisted name as a label of another domain
    "testserver",                # single-label name, winnable over LLMNR/mDNS
]

# One route per kind of thing a rebound page could take: the dashboard (which
# embeds `custom_launchers`, whose `env` holds cleartext credentials), the ACP
# token's delivery page, a JSON API, and the static mount — the last because
# `StaticFiles` sits behind the same middleware and was answering 500.
_GUARDED_PATHS = ["/", "/acp", "/api/settings", "/static/style.css"]


class TestHostHeaderIsParsedNotTrusted:
    @pytest.mark.parametrize("host", _HOSTILE_HOSTS)
    @pytest.mark.parametrize("path", _GUARDED_PATHS)
    def test_hostile_host_is_forbidden(self, path, host):
        status, body = _raw_asgi(app, path, [(b"host", host.encode())])
        assert status == 403, f"GET {path} with Host: {host} answered {status}"
        assert b"Forbidden" in body
        assert _ACP_TOKEN.encode() not in body

    @pytest.mark.parametrize("path", _GUARDED_PATHS)
    def test_absent_host_is_forbidden(self, path):
        """A Host-less request used to read as loopback: with no Host header at
        all, starlette's fallback to ``scope["server"]`` is the *only* thing
        left, so ``url.hostname`` was 127.0.0.1 by construction."""
        status, body = _raw_asgi(app, path, [])
        assert status == 403, f"GET {path} without a Host answered {status}"
        assert _ACP_TOKEN.encode() not in body

    @pytest.mark.parametrize("order", [
        [b"127.0.0.1", b"evil.com"],
        [b"evil.com", b"127.0.0.1"],
    ])
    def test_duplicate_host_is_forbidden(self, order):
        """Two Host headers is a smuggling shape, never a browser: which one is
        authoritative differs between hops, so neither is trusted."""
        status, _ = _raw_asgi(app, "/", [(b"host", h) for h in order])
        assert status == 403

    @pytest.mark.parametrize("host", [
        "127.0.0.1", "127.0.0.1:4915", "127.0.0.1:8080",
        "localhost", "localhost:8080", "LOCALHOST", "LocalHost:8080",
        "[::1]", "[::1]:4915",
    ])
    def test_loopback_forms_accepted(self, host):
        status, _ = _raw_asgi(app, "/api/last-refresh", [(b"host", host.encode())])
        assert status == 200, f"Host: {host} is loopback and must be served"

    @pytest.mark.parametrize("raw", _HOSTILE_HOSTS + [
        None, "", "   ", "[", "]", ":", "@", "]:80", "\x00", "\U0001f600",
        "127.0.0.1:٤٩١٥",  # non-ASCII digits: `str.isdigit()` calls these a port
    ])
    def test_unparseable_host_is_rejected_never_raised(self, raw):
        assert _host_allowed(raw) is False


class TestAcpInlineHostCheck:
    """``GET /acp`` repeats the Host check the middleware already runs, because
    it is the ACP token's only delivery vehicle and a narrowing of the
    middleware must not silently un-protect it.

    Every other test of that route goes through the middleware, which answers
    first — so deleting the inline check left the whole suite green. These probe
    ``_ROUTER_ONLY``: routing and the endpoint run, ``same_origin_guard`` does
    not, and the inline check is the only thing that can produce a 403.
    """

    def test_router_alone_serves_acp_on_loopback(self):
        """Anchors the three below: without this, a 403 from the router could
        just as well mean the route never ran."""
        status, body = _raw_asgi(_ROUTER_ONLY, "/acp", [(b"host", b"127.0.0.1:4915")])
        assert status == 200
        assert _ACP_TOKEN.encode() in body

    @pytest.mark.parametrize("host", [
        "evil.com", "a_b.evil.com", "[::1", "evil.com@127.0.0.1", "testserver",
    ])
    def test_inline_check_refuses_without_the_middleware(self, host):
        status, body = _raw_asgi(_ROUTER_ONLY, "/acp", [(b"host", host.encode())])
        assert status == 403, f"/acp served Host: {host} with no middleware above it"
        assert _ACP_TOKEN.encode() not in body

    def test_inline_check_refuses_absent_host(self):
        status, body = _raw_asgi(_ROUTER_ONLY, "/acp", [])
        assert status == 403
        assert _ACP_TOKEN.encode() not in body


class TestWsOriginUnaffectedByTheHostFallback:
    """``_ws_origin_ok`` deliberately keeps reading ``ws.url`` and is *not*
    switched to ``_host_allowed``.

    Middleware never sees an upgrade request, so this is the WebSocket's whole
    defense — but it is already safe under the same fallback, because both
    halves come from ``ws.url``. When Starlette discards an unparseable Host the
    netloc collapses to loopback along with the hostname, so the expected origin
    stops matching the attacker's ``Origin`` in the same step. Reading the raw
    Host for the allowlist while leaving the expected origin on ``ws.url`` is
    what would break it: the two would then disagree, and a rebound host would
    satisfy a loopback allowlist while matching its own origin.
    """

    @staticmethod
    def _ws(host: str | None, origin: str):
        from starlette.websockets import WebSocket
        headers = [(b"origin", origin.encode())]
        if host is not None:
            headers.insert(0, (b"host", host.encode()))
        return WebSocket({
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
            "path": "/ws/acp", "raw_path": b"/ws/acp", "query_string": b"",
            "root_path": "", "headers": headers,
            "client": ("127.0.0.1", 54321), "server": ("127.0.0.1", 4915),
        }, receive=None, send=None)

    @pytest.mark.parametrize("host", ["a_b.evil.com", "evil_1.attacker.com"])
    def test_underscore_host_still_fails_its_own_origin(self, host):
        """The exact bypass shape: Host that Starlette discards, Origin that
        matches it. The discarded Host takes the netloc with it."""
        assert _ws_origin_ok(self._ws(host, f"http://{host}")) is False

    def test_loopback_host_with_attacker_origin_rejected(self):
        assert _ws_origin_ok(self._ws("127.0.0.1:4915", "http://evil.com")) is False

    def test_loopback_host_with_matching_origin_accepted(self):
        """Positive control: the rejections above are not a broken helper."""
        assert _ws_origin_ok(self._ws("127.0.0.1:4915", "http://127.0.0.1:4915")) is True

    def test_absent_and_malformed_host_do_not_raise(self):
        """Whatever the verdict, an unparseable Host must not become a traceback
        on the handshake path."""
        assert _ws_origin_ok(self._ws(None, "http://evil.com")) is False
        assert _ws_origin_ok(self._ws("[::1", "http://evil.com")) is False


class _FakeWs:
    """A socket whose only behaviour is to fail the way the writer must survive."""

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, text: str) -> None:
        raise self._failure

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


class TestAcpWriterTeardown:
    """Every writer exit must leave the socket closed, not merely deregistered.

    The routine-disconnect arm caught ``RuntimeError`` and retired with an empty
    reason, which skipped the close — leaving a socket the server still held
    open with no writer behind it, silently swallowing every frame queued at it.
    That is precisely the zombie ``_retire`` was added to remove, surviving in
    the one arm that reaches it most often.
    """

    @pytest.mark.parametrize("failure, expected_code", [
        # Routine: the peer went away mid-send. Closes with "going away".
        (RuntimeError('Cannot call "send" once a close message has been sent.'), 1001),
        (ConnectionResetError(), 1001),
        # Unexpected: a bug in a frame we built. Closes with 1011.
        (ValueError("unserialisable frame"), 1011),
    ])
    def test_writer_exit_closes_and_deregisters(self, failure, expected_code):
        from power_atlas import acp as acp_mod

        async def run():
            ws = _FakeWs(failure)
            conn = acp_mod._Connection(ws)
            acp_mod._registry.connections.add(conn)
            try:
                conn.send({"type": "meta", "payload": {}})
                await conn._write_loop()
            finally:
                acp_mod._registry.connections.discard(conn)
            return ws, conn

        ws, conn = asyncio.run(run())
        assert ws.closed, "the writer exited leaving the socket open"
        assert ws.closed[0][0] == expected_code
        assert conn not in acp_mod._registry.connections

    def test_close_failure_does_not_escape_the_writer(self):
        """A peer that is genuinely gone makes ``close()`` raise too. The writer
        is a bare task: an exception here would surface only as a stray
        "Task exception was never retrieved"."""
        from power_atlas import acp as acp_mod

        class _DeadWs(_FakeWs):
            async def close(self, code: int = 1000, reason: str = "") -> None:
                raise RuntimeError("socket already closed")

        async def run():
            conn = acp_mod._Connection(_DeadWs(ConnectionResetError()))
            acp_mod._registry.connections.add(conn)
            try:
                conn.send({"type": "meta", "payload": {}})
                await conn._write_loop()
            finally:
                acp_mod._registry.connections.discard(conn)

        asyncio.run(run())


class TestAcpTokenCheck:
    @pytest.mark.parametrize("supplied", [
        "", "é", "é" * 60, "�", "A" * 4000, "not-the-token",
    ])
    def test_wrong_token_rejected_without_raising(self, supplied):
        assert _acp_token_ok(supplied) is False

    def test_correct_token_accepted(self):
        assert _acp_token_ok(_ACP_TOKEN) is True

    def test_non_ascii_token_closes_socket(self, raw_client):
        """``?t=%C3%A9`` URL-decodes to a non-ASCII str, which ``compare_digest``
        used to reject with TypeError — an unauthenticated 500 on the auth path."""
        with pytest.raises(WebSocketDisconnect) as exc:
            with raw_client.websocket_connect("/ws/acp?t=%C3%A9"):
                pass
        assert exc.value.code == 1008


# --- ACP phase 4: prompt, streaming fan-out, and reconnect replay ---


class _SinkWs:
    """A socket that accepts everything, so a test can read what was queued."""

    def __init__(self) -> None:
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, text: str) -> None:
        pass

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _queued(conn) -> list[dict]:
    """Everything sitting in a connection's outbound queue, in order.

    The queue holds ``(weight, frame)``; the weight is the send side's byte
    budget bookkeeping and no test asserts on it directly.
    """
    frames = []
    while True:
        try:
            frames.append(conn._out.get_nowait()[1])
        except asyncio.QueueEmpty:
            return frames


@pytest.fixture
def acp_session():
    """A live ACP session on the real supervisor singleton, torn down after.

    The registry and supervisor are module globals; leaving either dirty would
    make the next test's ``subscribe`` answer from another test's state.
    """
    from power_atlas import acp as acp_mod

    sid = "sess-phase4"
    acp_mod._supervisor.sessions[sid] = {
        "cwd": r"C:\scratch", "created": 0.0, "models": {}, "modes": {}}
    acp_mod._supervisor.history[sid] = acp_mod._History()
    try:
        yield acp_mod, sid
    finally:
        acp_mod._supervisor.sessions.pop(sid, None)
        acp_mod._supervisor.history.pop(sid, None)
        acp_mod._supervisor.inflight.discard(sid)
        for conn in tuple(acp_mod._registry.connections):
            acp_mod._registry.detach(conn)
        acp_mod._registry.connections.clear()
        acp_mod._registry.subscribers.clear()


class TestAcpServerTypeGuard:
    """``SERVER_TYPES`` was declared in phase 3a and never consulted.

    Every call site passes a literal, so a type it refuses is a typo — and an
    unrecognised frame does not fail loudly on the page, it renders as a line
    of raw JSON in the transport log that reads like agent noise.
    """

    def test_undeclared_type_raises(self):
        from power_atlas import acp as acp_mod
        with pytest.raises(ValueError):
            acp_mod.envelope("chunkk", {"text": "x"})

    def test_every_declared_type_is_accepted(self):
        """Positive control: the guard rejects typos, not the real vocabulary."""
        from power_atlas import acp as acp_mod
        for type_ in acp_mod.SERVER_TYPES:
            assert acp_mod.envelope(type_)["type"] == type_

    def test_error_frame_still_builds(self):
        from power_atlas import acp as acp_mod
        frame = acp_mod.error_frame("bad_payload", "no", "s1")
        assert frame == {"type": "error", "sessionId": "s1",
                         "payload": {"code": "bad_payload", "message": "no"}}


class TestAcpHistoryBuffer:
    """The ring buffer is bounded twice, and both bounds must announce a drop.

    A ``deque`` discards its oldest entry in silence, so a replay degraded from
    "the conversation" to "its last N events" is indistinguishable from a
    complete one unless something records that it happened.
    """

    def _chunk(self, acp_mod, text):
        return acp_mod.envelope("chunk", {"role": "agent", "text": text}, "s1")

    def test_count_bound_holds_and_reports_the_drop(self):
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        for i in range(acp_mod.HISTORY_MAXLEN + 25):
            history.append(self._chunk(acp_mod, "e%d" % i))
        events = history.events()
        assert len(events) == acp_mod.HISTORY_MAXLEN
        assert history.truncated is True
        # The oldest went and the newest stayed — a ring, not a prefix.
        assert events[0]["payload"]["text"] == "e25"
        assert events[-1]["payload"]["text"] == "e%d" % (acp_mod.HISTORY_MAXLEN + 24)

    def test_under_the_cap_nothing_is_marked_truncated(self):
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        for i in range(acp_mod.HISTORY_MAXLEN):
            history.append(self._chunk(acp_mod, "e%d" % i))
        assert len(history.events()) == acp_mod.HISTORY_MAXLEN
        assert history.truncated is False

    def test_byte_budget_evicts_before_the_count_would(self):
        """Few but enormous events are the case the count bound does not cover:
        HISTORY_MAXLEN of them at the reader's line ceiling is hundreds of MB."""
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        big = "x" * (acp_mod.HISTORY_MAX_BYTES // 4)
        for _ in range(12):
            history.append(self._chunk(acp_mod, big))
        assert len(history.events()) < acp_mod.HISTORY_MAXLEN
        assert len(history.events()) <= 5
        assert history.truncated is True

    def test_the_budget_is_bytes_and_not_characters(self):
        """``len()`` on a ``str`` counts characters: an astral-plane buffer
        accounted at 2,087,398 was measured holding 8.24 MB. Each character
        below is one ``len()`` unit and four UTF-8 bytes, so a character count
        would fit eight of these under a budget that holds two."""
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        big = "\U0001f600" * (acp_mod.HISTORY_MAX_BYTES // 4)
        assert len(big) * 4 == len(big.encode("utf-8")) == acp_mod.HISTORY_MAX_BYTES
        history.append(self._chunk(acp_mod, big))
        assert history.truncated is False
        history.append(self._chunk(acp_mod, big))
        assert len(history.events()) == 1
        assert history.truncated is True

    def test_nested_payload_strings_are_weighed(self):
        """A top-level-only walk priced a ``history`` frame — and, from this
        phase, a ``tool_call`` carrying a command — at the envelope allowance
        however large it really was."""
        from power_atlas import acp as acp_mod
        nested = acp_mod.envelope("history", {"events": [
            acp_mod.envelope("chunk", {"role": "agent", "text": "z" * 5000})]})
        assert acp_mod._frame_weight(nested) > 5000

    def test_one_oversized_event_is_never_evicted_to_empty(self):
        """A reload finding an empty replay reads as a lost conversation; a
        suffix of one event reads as what it is."""
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        history.append(self._chunk(acp_mod, "y" * (acp_mod.HISTORY_MAX_BYTES * 2)))
        assert len(history.events()) == 1


class TestAcpSendQueueByteBound:
    """The send queue kept the defect the history buffer shed.

    ``SEND_QUEUE_MAXSIZE`` bounds frames, not bytes, and nothing caps a queued
    frame below ``MAX_AGENT_LINE_BYTES`` — so one socket could hold 256 MiB and
    ``MAX_CONNECTIONS`` of them 2 GiB, against 256 KiB in the other direction.
    """

    def _conn(self, acp_mod):
        return acp_mod._Connection(_SinkWs())

    def test_a_stalled_socket_cannot_pin_unbounded_memory(self):
        from power_atlas import acp as acp_mod
        conn = self._conn(acp_mod)
        big = "q" * (256 * 1024)
        for _ in range(acp_mod.SEND_QUEUE_MAXSIZE):
            conn.send(acp_mod.envelope("chunk", {"role": "agent", "text": big}))
        assert conn._overflowed is True
        assert conn._queued_bytes <= acp_mod.SEND_QUEUE_MAX_BYTES
        # The count bound alone would have accepted every one of them.
        assert conn._out.qsize() < acp_mod.SEND_QUEUE_MAXSIZE

    def test_ordinary_traffic_never_trips_the_byte_bound(self):
        """Positive control: a full queue of streamed chunks is normal."""
        from power_atlas import acp as acp_mod
        conn = self._conn(acp_mod)
        for _ in range(acp_mod.SEND_QUEUE_MAXSIZE):
            conn.send(acp_mod.envelope("chunk", {"role": "agent", "text": "tick"}))
        assert conn._overflowed is False
        assert conn._out.qsize() == acp_mod.SEND_QUEUE_MAXSIZE

    def test_a_lone_frame_over_the_budget_is_still_delivered(self):
        """Refusing it onto an empty queue would leave the writer parked on
        ``get()`` with an overflow flag it never wakes up to read."""
        from power_atlas import acp as acp_mod
        conn = self._conn(acp_mod)
        conn.send(acp_mod.envelope(
            "chunk", {"role": "agent",
                      "text": "w" * (acp_mod.SEND_QUEUE_MAX_BYTES + 4096)}))
        assert conn._overflowed is False
        assert conn._out.qsize() == 1

    def test_the_writer_releases_the_bytes_it_has_sent(self):
        """Without this the budget is a lifetime total and every long-lived
        socket eventually refuses to send anything at all."""
        from power_atlas import acp as acp_mod

        async def drive():
            conn = self._conn(acp_mod)
            conn.start()
            conn.send(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "u" * (1024 * 1024)}))
            assert conn._queued_bytes > 1024 * 1024
            await conn.drain()
            await conn.stop()
            return conn

        conn = asyncio.run(drive())
        assert conn._queued_bytes == 0
        assert conn._overflowed is False

    def test_a_full_byte_budget_replay_does_not_retire_its_own_socket(
            self, acp_session):
        """The bound must sit above the largest frame the server itself builds:
        a ``history`` replay carries up to ``HISTORY_MAX_BYTES`` of text."""
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        chunk = "h" * 64 * 1024
        while not history.truncated:
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": chunk}, sid))

        conn = self._conn(acp_mod)
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert conn._overflowed is False
        assert [f["type"] for f in _queued(conn)] == [
            "session", "history_truncated", "history"]


class TestAcpReplayOnSubscribe:
    def test_replay_is_one_frame_and_cannot_overflow_the_socket(self, acp_session):
        """``SEND_QUEUE_MAXSIZE`` is 256 and a full queue retires the socket, so
        an event-per-frame replay of a full buffer would kill the very socket
        the replay exists to serve — and only for sessions worth replaying."""
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        for i in range(acp_mod.HISTORY_MAXLEN):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)

        frames = _queued(conn)
        assert conn._overflowed is False
        assert [f["type"] for f in frames] == ["session", "history"]
        events = frames[1]["payload"]["events"]
        assert len(events) == acp_mod.HISTORY_MAXLEN
        assert events[0]["payload"]["text"] == "e0"
        assert events[-1]["payload"]["text"] == "e%d" % (acp_mod.HISTORY_MAXLEN - 1)

    def test_replay_carries_the_recorded_conversation_in_order(self, acp_session):
        acp_mod, sid = acp_session
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "user", "text": "hello"}, sid))
        acp_mod._emit(sid, acp_mod.envelope("meta", {"turn": "start"}, sid))
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "hi "}, sid))
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "there"}, sid))

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)

        frames = _queued(conn)
        events = frames[-1]["payload"]["events"]
        assert [(e["type"], e["payload"].get("text") or e["payload"].get("turn"))
                for e in events] == [
            ("chunk", "hello"), ("meta", "start"),
            ("chunk", "hi "), ("chunk", "there")]

    def test_truncated_marker_precedes_the_replay(self, acp_session):
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        for i in range(acp_mod.HISTORY_MAXLEN + 1):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)

        assert [f["type"] for f in _queued(conn)] == [
            "session", "history_truncated", "history"]

    def test_intact_buffer_emits_no_truncation_marker(self, acp_session):
        """Positive control for the marker: it must mean something."""
        acp_mod, sid = acp_session
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "only"}, sid))
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert [f["type"] for f in _queued(conn)] == ["session", "history"]

    def test_a_live_event_after_subscribe_is_not_also_replayed(self, acp_session):
        """Attach and replay are atomic because ``_handle_subscribe`` never
        awaits; an event broadcast afterwards must therefore arrive exactly
        once, live, and not a second time inside the history frame."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "live"}, sid))

        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["session", "history", "chunk"]
        assert frames[1]["payload"]["events"] == []
        assert frames[2]["payload"]["text"] == "live"

    def test_subscribe_is_synchronous_by_construction(self):
        """The atomicity above is "no ``await`` between ``attach`` and the
        ``history`` frame", and nothing enforced it: converting this function
        to ``async def`` together with the tests that drive it would leave
        every frame-ordering assertion passing."""
        import inspect
        from power_atlas import acp as acp_mod
        assert not inspect.iscoroutinefunction(acp_mod._handle_subscribe)
        # Its only call site, for the same reason: a coroutine `_dispatch`
        # would put a suspension point either side of the call.
        assert not inspect.iscoroutinefunction(acp_mod._dispatch)

    def test_subscribe_reports_a_turn_the_server_still_holds(self, acp_session):
        """The page's only other source is a replayed ``meta {"turn":
        "start"}`` — a frame the ring buffer is built to evict."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.inflight.add(sid)
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["turnActive"] is True

    def test_subscribe_reports_an_idle_session(self, acp_session):
        """Positive control: the flag must mean something."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["turnActive"] is False

    def test_the_turn_flag_survives_a_buffer_that_evicted_its_marker(
            self, acp_session):
        """The whole point: a turn emitting more than ``HISTORY_MAXLEN`` chunks
        replays with no ``turn: start`` left in it."""
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        history.append(acp_mod.envelope("meta", {"turn": "start"}, sid))
        for i in range(acp_mod.HISTORY_MAXLEN):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))
        acp_mod._supervisor.inflight.add(sid)

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        replayed = frames[-1]["payload"]["events"]
        assert not any(e["payload"].get("turn") == "start" for e in replayed)
        assert frames[0]["payload"]["turnActive"] is True

    def test_an_unknown_session_leaves_a_server_side_trace(self, acp_session,
                                                           caplog):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            acp_mod._handle_subscribe(conn, "no-such-session")
        assert any("unknown_session" in r.getMessage() for r in caplog.records)


class TestAcpNotificationFanout:
    def test_agent_message_chunk_reaches_subscribers_and_the_buffer(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)

        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk",
                # Measured shape for kiro-cli 2.14.2: one object, not a list.
                "content": {"type": "text", "text": "partial"}}},
        })

        assert _queued(conn) == [{
            "type": "chunk", "sessionId": sid,
            "payload": {"role": "agent", "text": "partial"}}]
        assert acp_mod._supervisor.history[sid].events()[0][
            "payload"]["text"] == "partial"

    def test_chunk_for_another_session_does_not_cross_over(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": "someone-else", "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "not yours"}}},
        })
        assert _queued(conn) == []


class TestAcpToolCallVisibility:
    """Under ``-a`` there is no permission gate, and the accepted justification
    for removing it was a human watching the run. A tool call that reaches only
    a log file — one the app writes on a single launch path — is not something
    anyone is watching: a ``shell`` call was observed reading and writing a
    session store file outside its own session's cwd, unseen.
    """

    def _attached(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def _notify(self, acp_mod, sid, update):
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": update},
        })

    def test_a_tool_call_reaches_the_page_and_the_replay_buffer(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t1",
            "title": "Set the tab title", "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "Get-Content ~/.kiro/sessions/cli/x.json"},
        })

        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["tool_call"]
        assert frames[0]["payload"] == {
            "toolCallId": "t1", "title": "Set the tab title",
            "kind": "execute", "status": "pending",
            "command": "Get-Content ~/.kiro/sessions/cli/x.json"}
        # Recorded too, or a reload would show a conversation with no sign
        # that anything ran during it.
        assert acp_mod._supervisor.history[sid].events() == frames

    def test_an_update_arrives_as_its_own_declared_type(self, acp_session):
        """``tool_call_update`` is ACP's name; ``tool_update`` is the wire type
        this module declared for it in ``SERVER_TYPES``."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call_update", "toolCallId": "t1",
            "status": "completed"})
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["tool_update"]
        assert frames[0]["payload"]["status"] == "completed"
        assert frames[0]["payload"]["toolCallId"] == "t1"

    def test_the_command_is_logged_as_well_as_forwarded(self, acp_session, caplog):
        """The log line is the trace that outlives the tab. The test it
        replaces asserted only that forwarding did *not* happen, while its
        name claimed it covered the logging."""
        acp_mod, sid = acp_session
        self._attached(acp_mod, sid)
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"):
            self._notify(acp_mod, sid, {
                "sessionUpdate": "tool_call", "toolCallId": "t9",
                "title": "shell", "kind": "execute", "status": "in_progress",
                "rawInput": {"command": "Remove-Item -Recurse C:/tmp"}})
        messages = [r.getMessage() for r in caplog.records]
        assert any("ACP tool tool_call" in m and "t9" in m
                   and "Remove-Item -Recurse C:/tmp" in m for m in messages), messages

    def test_a_long_command_is_clipped_and_says_so(self, acp_session):
        """A command clipped to look complete would be worse than none, so the
        bound travels with the frame and the page states it."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        command = "echo " + "a" * (acp_mod.MAX_TOOL_INPUT_CHARS * 2)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t2",
            "title": "shell", "rawInput": {"command": command}})
        payload = _queued(conn)[0]["payload"]
        assert payload["commandTruncated"] is True
        assert payload["commandLength"] == len(command)
        assert payload["command"] == command[:acp_mod.MAX_TOOL_INPUT_CHARS]

    def test_a_short_command_carries_no_truncation_marker(self, acp_session):
        """Positive control: the marker must mean something."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t3",
            "rawInput": {"command": "dir"}})
        assert "commandTruncated" not in _queued(conn)[0]["payload"]

    def test_an_input_with_no_command_key_is_still_reported(self, acp_session):
        """``rawInput`` has no schema and differs per tool. "A tool ran and we
        cannot say what it did" is the state this rendering removes."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t4",
            "title": "fs_write", "rawInput": {"mode": "create", "bytes": 42}})
        assert _queued(conn)[0]["payload"]["command"] == (
            '{"bytes": 42, "mode": "create"}')

    def test_non_string_fields_do_not_reach_the_page_untyped(self, acp_session):
        """Every field is agent-authored and none is schema-checked on the way
        in; the renderer reads these as text and nothing else."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": {"nested": 1},
            "title": ["shell"], "kind": 7, "status": None})
        payload = _queued(conn)[0]["payload"]
        assert payload["toolCallId"] == ""
        assert payload["title"] == ""
        assert payload["kind"] == ""
        assert payload["status"] == ""

    def test_a_tool_call_for_another_session_does_not_cross_over(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, "someone-else", {
            "sessionUpdate": "tool_call", "toolCallId": "t5", "title": "shell"})
        assert _queued(conn) == []
        assert acp_mod._supervisor.history[sid].events() == []


class TestAcpPromptDispatch:
    def _conn(self, acp_mod, sid=None):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        if sid:
            acp_mod._registry.attach(conn, sid)
        return conn

    @pytest.mark.parametrize("payload, code", [
        ({}, "bad_payload"),
        ({"prompt": ""}, "bad_payload"),
        ({"prompt": "   \n "}, "bad_payload"),
        ({"prompt": 17}, "bad_payload"),
        ({"prompt": ["hi"]}, "bad_payload"),
        ({"prompt": {"text": "hi"}}, "bad_payload"),
    ])
    def test_bad_payloads_are_refused_with_a_typed_frame(self, acp_session,
                                                         payload, code):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, sid, payload))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == code
        assert acp_mod._supervisor.history[sid].events() == []

    def test_missing_session_id_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, None, {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "bad_envelope"

    def test_unknown_session_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, "no-such-session",
                                           {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "unknown_session"

    def test_unattached_socket_is_refused(self, acp_session):
        """It would start a turn and then receive none of the stream it began,
        which on the page looks like an agent that never answered."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod)
        asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "not_subscribed"

    def test_second_prompt_during_a_turn_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "turn_in_progress"

    @pytest.mark.parametrize("attach, sid_arg, payload, code", [
        (True, None, {"prompt": "hi"}, "bad_envelope"),
        (True, "self", {"prompt": ""}, "bad_payload"),
        (True, "no-such-session", {"prompt": "hi"}, "unknown_session"),
        (False, "self", {"prompt": "hi"}, "not_subscribed"),
    ])
    def test_every_refusal_leaves_a_server_side_trace(self, acp_session, caplog,
                                                      attach, sid_arg, payload,
                                                      code):
        """``not_subscribed`` is what a reconnect subscribing with a stale
        session id looks like from the client, and it left no trace at all."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid if attach else None)
        target = sid if sid_arg == "self" else sid_arg
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            asyncio.run(acp_mod._handle_prompt(conn, target, payload))
        assert _queued(conn)[0]["payload"]["code"] == code
        assert any(code in r.getMessage() for r in caplog.records), \
            [r.getMessage() for r in caplog.records]

    def test_the_turn_in_progress_refusal_is_logged(self, acp_session, caplog):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert any("turn_in_progress" in r.getMessage() for r in caplog.records)

    def test_a_turn_streams_to_every_subscriber_and_is_recorded(self, acp_session):
        """The stub sits at the JSON-RPC transport, so the request this asserts
        is the one the supervisor really builds, and the chunk travels the real
        notification path while the request is still outstanding."""
        acp_mod, sid = acp_session
        calls = []

        async def fake_request(self, method, params,
                               timeout=acp_mod.REQUEST_TIMEOUT_SECONDS):
            calls.append((method, params, timeout))
            self._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "answer"}}},
            })
            return {"stopReason": "end_turn"}

        sup = acp_mod._supervisor
        conn_a = self._conn(acp_mod, sid)
        conn_b = self._conn(acp_mod, sid)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_prompt(conn_a, sid, {"prompt": "ping"}))

        assert calls == [("session/prompt",
                          {"sessionId": sid,
                           "prompt": [{"type": "text", "text": "ping"}]},
                          acp_mod.PROMPT_TIMEOUT_SECONDS)]
        expected = [
            ("chunk", {"role": "user", "text": "ping"}),
            ("meta", {"turn": "start"}),
            ("chunk", {"role": "agent", "text": "answer"}),
            ("meta", {"turn": "end", "stopReason": "end_turn"}),
        ]
        # Both tabs see the same turn, including the prompt neither of them
        # typed — that is what makes a second tab a view and not a rival.
        for conn in (conn_a, conn_b):
            assert [(f["type"], f["payload"]) for f in _queued(conn)] == expected
        assert [(e["type"], e["payload"])
                for e in sup.history[sid].events()] == expected
        assert sid not in sup.inflight

    def test_an_agent_failure_ends_the_turn_rather_than_hanging_it(self, acp_session):
        """The page derives "still answering" from the turn boundary, so a
        refused prompt that emitted no end would leave Send disabled forever."""
        acp_mod, sid = acp_session

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        conn = self._conn(acp_mod, sid)
        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "ping"}))

        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["chunk", "meta", "error", "meta"]
        assert frames[2]["payload"]["code"] == "agent_timeout"
        assert frames[3]["payload"] == {"turn": "end", "stopReason": "error"}
        assert sid not in acp_mod._supervisor.inflight


class _BlockReader:
    """A pipe that hands out pre-set blocks, one per ``read1``."""

    def __init__(self, blocks, on_read=None) -> None:
        self._blocks = list(blocks)
        self._on_read = on_read
        self.sizes: list[int] = []

    def read1(self, size):
        self.sizes.append(size)
        if self._on_read is not None:
            self._on_read()
        return self._blocks.pop(0) if self._blocks else b""


class _StdoutOf:
    def __init__(self, reader) -> None:
        self.buffer = reader


class _FakeProc:
    def __init__(self, reader) -> None:
        self.stdout = _StdoutOf(reader)
        self.pid = -1

    def poll(self):
        return 0


def _run_reader(acp_mod, blocks, on_read=None):
    """Drive the real reader loop over ``blocks`` and return what it posted."""
    posted = []
    sup = acp_mod._Supervisor()
    sup._post = lambda fn, *args: posted.append((fn.__name__, args))
    reader = _BlockReader(blocks, on_read)
    sup._reader_loop(_FakeProc(reader))
    return [args[0] for name, args in posted if name == "_on_message"], reader


class TestAcpReaderLineCap:
    """The agent→client direction had no size cap at all.

    ``for line in proc.stdout`` — and ``readline()``, which is the same thing —
    accumulates until a newline arrives however long that takes. Phase 4 is
    when it becomes live, because tool output under ``-a`` arrives on it.
    """

    def test_lines_split_across_blocks_are_reassembled(self):
        from power_atlas import acp as acp_mod
        msgs, _ = _run_reader(acp_mod, [b'{"id":1}\n{"id"', b':2}\n'])
        assert msgs == [{"id": 1}, {"id": 2}]

    def test_a_multibyte_character_split_across_blocks_survives(self):
        """Bytes are accumulated and decoded only at the newline, so a UTF-8
        sequence straddling a block boundary is not two replacement chars."""
        from power_atlas import acp as acp_mod
        msgs, _ = _run_reader(acp_mod, [b'{"t":"\xc3', b'\xa9"}\n'])
        assert msgs == [{"t": "\u00e9"}]

    def test_an_overlong_line_is_dropped_and_the_channel_continues(self):
        from power_atlas import acp as acp_mod
        with patch.object(acp_mod, "MAX_AGENT_LINE_BYTES", 64):
            msgs, _ = _run_reader(acp_mod, [
                b'{"first":1}\n',
                b'{"junk":"' + b"z" * 500,          # no newline: over the cap
                b'more junk still no newline' * 10,  # the tail is swallowed too
                b'"}\n{"after":2}\n',
            ])
        # Only the tail of the rejected line was discarded — not the good line
        # that shared its final block, which is what makes this a cap and not a
        # channel reset.
        assert msgs == [{"first": 1}, {"after": 2}]

    def test_the_cap_is_not_simply_dropping_everything(self):
        """Positive control: a line just under the cap still gets through."""
        from power_atlas import acp as acp_mod
        payload = ('{"t":"' + "z" * 40 + '"}').encode()
        assert len(payload) < 64
        with patch.object(acp_mod, "MAX_AGENT_LINE_BYTES", 64):
            msgs, _ = _run_reader(acp_mod, [payload + b"\n"])
        assert len(msgs) == 1

    def test_reads_are_bounded_blocks_delivered_as_they_arrive(self):
        """A buffered ``read(n)`` loops until it has all n bytes, so it would
        hold every chunk of a streamed answer until 64 KiB had accumulated.
        ``read1`` returns one OS read, which is what makes streaming visible."""
        from power_atlas import acp as acp_mod
        seen_at_each_read = []
        posted = []
        sup = acp_mod._Supervisor()
        sup._post = lambda fn, *args: posted.append(fn.__name__)
        reader = _BlockReader(
            [b'{"id":1}\n', b'{"id":2}\n'],
            on_read=lambda: seen_at_each_read.append(
                posted.count("_on_message")))
        sup._reader_loop(_FakeProc(reader))

        assert reader.sizes and set(reader.sizes) == {acp_mod.READ_BLOCK_BYTES}
        # By the second read the first message had already been delivered.
        assert seen_at_each_read[:2] == [0, 1]

    def test_non_json_noise_is_tolerated(self):
        from power_atlas import acp as acp_mod
        msgs, _ = _run_reader(acp_mod, [b'banner text\n\n{"id":1}\n'])
        assert msgs == [{"id": 1}]


# --- ACP phase 4 fixes: CSP, wire encoding, subscribe throttle ---


class TestAcpContentSecurityPolicy:
    """``/acp`` renders agent-authored prose and agent-authored tool commands,
    and the agent behind it runs trust-all-tools. Until now the only control on
    that was the page's own no-innerHTML discipline — a rule every future line
    has to keep. A nonce policy is the control that does not depend on it.
    """

    def _policy(self, resp) -> str:
        header = resp.headers.get("content-security-policy")
        assert header, "GET /acp served no Content-Security-Policy at all"
        return header

    def _nonce(self, policy: str) -> str:
        match = re.search(r"script-src 'nonce-([^']+)'", policy)
        assert match, "the policy is not nonce-based: %s" % policy
        return match.group(1)

    def test_the_page_carries_a_nonce_policy(self, raw_client):
        resp = raw_client.get("/acp")
        assert resp.status_code == 200
        policy = self._policy(resp)
        self._nonce(policy)
        # `'unsafe-inline'` would permit both an injected <script> and an
        # <img onerror=…>, which is the whole vector this exists against.
        assert "'unsafe-inline'" not in policy
        assert "'unsafe-eval'" not in policy
        for directive in ("default-src 'self'", "object-src 'none'",
                          "base-uri 'none'", "frame-ancestors 'none'"):
            assert directive in policy, "%r missing from %s" % (directive, policy)

    def test_the_header_nonce_is_the_one_on_the_page(self, raw_client):
        """A mismatched nonce is a blank page, and it passes every assertion
        that only checks the header exists."""
        resp = raw_client.get("/acp")
        nonce = self._nonce(self._policy(resp))
        # The page's own inline block, which is the one the policy would blank.
        assert '<script nonce="%s">' % nonce in resp.text
        # And the shared <head> tag, which would otherwise log a violation on a
        # page whose console has to stay readable.
        assert ('<script nonce="%s" src="/static/htmx.min.js">' % nonce) in resp.text
        tags = re.findall(r"<script\b[^>]*>", resp.text)
        assert len(tags) == 2, "a script tag was added without a nonce: %s" % tags

    def test_the_nonce_is_regenerated_per_response(self, raw_client):
        first = self._nonce(self._policy(raw_client.get("/acp")))
        second = self._nonce(self._policy(raw_client.get("/acp")))
        assert first != second

    def test_connect_src_admits_the_socket_the_page_opens(self, raw_client):
        """The easiest way to break this page with a CSP. ``/ws/acp`` is what
        the whole feature runs on, and every server-side test still passes when
        ``connect-src`` forbids it."""
        resp = raw_client.get("/acp", headers={"Host": "127.0.0.1:4915"})
        assert resp.status_code == 200
        match = re.search(r"connect-src ([^;]+)", self._policy(resp))
        assert match, "no connect-src directive at all"
        sources = match.group(1).split()
        assert "ws://127.0.0.1:4915" in sources
        assert "wss://127.0.0.1:4915" in sources

    def test_the_policy_names_the_host_actually_served(self, raw_client):
        """A hardcoded port would be wrong on most launches: config.py defaults
        `port` to 0 and the OS assigns one."""
        resp = raw_client.get("/acp", headers={"Host": "localhost:53119"})
        assert "ws://localhost:53119" in self._policy(resp)

    def test_no_policy_leaks_onto_the_dashboard(self, raw_client):
        """base.html is shared. index.html carries substantial inline script and
        static/htmx.min.js binds at DOMContentLoaded, so this policy would risk
        the dashboard for no gain — it renders no agent-authored text."""
        resp = raw_client.get("/")
        assert resp.status_code == 200
        assert "content-security-policy" not in resp.headers
        # Rendered, not assumed: the shared template must be byte-identical
        # where it was touched.
        assert '<script src="/static/htmx.min.js"></script>' in resp.text


class _EncodingWs:
    """A socket that encodes what it is handed, the way the transport does.

    ``_SinkWs`` accepts any ``str``. uvicorn's websockets layer encodes to UTF-8
    before framing, and that encode is where a lone surrogate raises — inside
    ``_write_loop``, whose catch-all then retires the socket.
    """

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text.encode("utf-8"))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _write_through(acp_mod, *frames):
    """Put frames through the real writer task; returns ``(ws, conn)``."""

    async def drive():
        ws = _EncodingWs()
        conn = acp_mod._Connection(ws)
        conn.start()
        for frame in frames:
            conn.send(frame)
        await conn.drain()
        await conn.stop()
        return ws, conn

    return asyncio.run(drive())


class TestAcpWireEncoding:
    """The outbound serializer defaulted to ``ensure_ascii=True``, so every
    non-ASCII character the agent produced left as a 6- or 12-byte escape.
    """

    def test_non_ascii_reaches_the_wire_as_utf8(self):
        from power_atlas import acp as acp_mod
        text = "héllo — 🌍" * 200
        frame = acp_mod.envelope("chunk", {"role": "agent", "text": text})
        ws, _ = _write_through(acp_mod, frame)
        assert ws.closed == []
        assert len(ws.sent) == 1
        assert b"\\u" not in ws.sent[0]
        assert text.encode("utf-8") in ws.sent[0]
        # The saving, measured rather than asserted as an inequality that a
        # one-byte difference would satisfy.
        assert len(ws.sent[0]) < 0.6 * len(json.dumps(frame).encode("utf-8"))

    def test_a_lone_surrogate_does_not_retire_a_healthy_socket(self):
        """``json.loads`` turns a ``\\ud800`` escape in the agent's own output
        into a lone surrogate, and UTF-8 cannot represent one. Unguarded, that
        encode raises where the writer's catch-all takes the socket down."""
        from power_atlas import acp as acp_mod
        text = json.loads(r'"\ud800 tail"')
        frame = acp_mod.envelope("chunk", {"role": "agent", "text": text})
        ws, _ = _write_through(acp_mod, frame)
        assert ws.closed == [], "an encodable frame retired the socket"
        assert len(ws.sent) == 1, "the frame never reached the wire"
        assert json.loads(ws.sent[0].decode("utf-8"))["payload"]["text"] == text

    def test_the_fallback_is_per_frame_and_not_per_socket(self):
        """Positive control: a poisoned frame must not send the rest of the
        stream back to the escaped form for the socket's lifetime."""
        from power_atlas import acp as acp_mod
        poisoned = acp_mod.envelope(
            "chunk", {"role": "agent", "text": json.loads(r'"\udfff"')})
        ordinary = acp_mod.envelope("chunk", {"role": "agent", "text": "🌍" * 50})
        ws, _ = _write_through(acp_mod, poisoned, ordinary)
        assert ws.closed == []
        assert len(ws.sent) == 2
        assert b"\\u" in ws.sent[0]
        assert b"\\u" not in ws.sent[1]

    def test_the_byte_budget_now_prices_what_the_wire_carries(self):
        """The send queue and the history buffer both charge UTF-8 bytes. While
        the wire was escaped they bounded roughly a third of the memory they
        were sizing."""
        from power_atlas import acp as acp_mod
        frame = acp_mod.envelope("chunk", {"role": "agent", "text": "🌍" * 5000})
        wire = len(acp_mod._dumps_frame(frame).encode("utf-8"))
        assert wire <= acp_mod._frame_weight(frame) * 1.1


class TestAcpSubscribeThrottle:
    """A ~60-byte ``subscribe`` rebuilds the whole replay buffer — up to
    HISTORY_MAX_BYTES — into one ``history`` frame, and ``_dispatch`` applied no
    throttle at all, so SEND_QUEUE_MAXSIZE of them could be queued on the event
    loop that also serves the dashboard.
    """

    def _fill(self, acp_mod, sid, events=50):
        history = acp_mod._supervisor.history[sid]
        for i in range(events):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))

    def _conn(self, acp_mod):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        return conn

    def test_a_legitimate_reload_is_never_throttled(self, acp_session):
        """The page sends exactly one ``subscribe`` per socket, from onopen."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        assert [f["type"] for f in _queued(conn)] == ["session", "history"]

    def test_a_repeat_is_refused_with_a_typed_error(self, acp_session, caplog):
        """Typed rather than silent: silence on this page is indistinguishable
        from a server that stopped answering."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "subscribe_throttled"
        assert any("throttled" in r.getMessage() for r in caplog.records)

    def test_the_replay_is_not_rebuilt_while_throttled(self, acp_session):
        """The re-serialisation is the cost this exists to stop, so a refused
        call must produce no ``history`` frame at all."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid, acp_mod.HISTORY_MAXLEN)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        for _ in range(20):
            acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        assert len(frames) == 20
        assert {f["type"] for f in frames} == {"error"}

    def test_a_fresh_socket_is_not_throttled_by_its_predecessor(self, acp_session):
        """A reconnect is a new socket, and throttling it would break the one
        recovery path the page has."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        acp_mod._handle_subscribe(self._conn(acp_mod), sid)
        second = self._conn(acp_mod)
        acp_mod._handle_subscribe(second, sid)
        assert [f["type"] for f in _queued(second)] == ["session", "history"]

    def test_the_window_expires_rather_than_latching(self, acp_session):
        """Positive control: a rate, not a one-replay-per-socket cap."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        with patch.object(acp_mod, "SUBSCRIBE_MIN_INTERVAL_SECONDS", 0.0):
            acp_mod._handle_subscribe(conn, sid)
        assert [f["type"] for f in _queued(conn)] == ["session", "history"]

    def test_the_refusal_paths_keep_their_own_codes(self, acp_session):
        """The two cheap refusals sit above the throttle: each costs one small
        frame and the send queue already bounds them, so answering one of them
        ``subscribe_throttled`` would only make the page harder to read."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        acp_mod._handle_subscribe(conn, "no-such-session")
        assert _queued(conn)[0]["payload"]["code"] == "unknown_session"


# --- ACP phase 5: resume a session this process never created ---


async def _no_spawn(self):
    """``ensure_started`` without an agent. Every load test stubs the
    transport, so spawning a real one would only add ~1 s and a process tree."""
    return None


def _lock_time(when) -> str:
    """A lock timestamp in the shape kiro-cli writes: RFC 3339, nanoseconds."""
    return when.astimezone(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f") + "000Z"


@pytest.fixture
def acp_store(tmp_path, monkeypatch):
    """An empty kiro-cli session store, and a supervisor left clean after.

    The supervisor and registry are module globals: a load test that left a
    session registered would make the next test's ``subscribe`` answer from it.
    """
    from power_atlas import acp as acp_mod
    monkeypatch.setattr(acp_mod, "KIRO_SESSION_DIR", tmp_path)
    try:
        yield acp_mod, tmp_path
    finally:
        acp_mod._supervisor.sessions.clear()
        acp_mod._supervisor.history.clear()
        acp_mod._supervisor.inflight.clear()
        acp_mod._supervisor._reserved = 0
        for conn in tuple(acp_mod._registry.connections):
            acp_mod._registry.detach(conn)
        acp_mod._registry.connections.clear()
        acp_mod._registry.subscribers.clear()


def _acp_conn(acp_mod):
    conn = acp_mod._Connection(_SinkWs())
    acp_mod._registry.connections.add(conn)
    return conn


class TestAcpSessionIdValidation:
    """The id arrives from the browser, is joined into the kiro-cli session
    store to read a lock file, and is then sent to an agent running
    trust-all-tools. Nothing downstream re-checks it.
    """

    @pytest.mark.parametrize("sid", [
        "../../../../Windows/System32/config/SAM",
        "..",
        ".",
        "a/b",
        "a\\b",
        "C:\\Windows",
        "sess:1",
        "sess.1",
        "sess 1",
        "sess\x00",
        # `$` also matches just before a trailing newline, so the shared
        # pattern used with `match` accepts this one.
        "sess\n",
        "",
        "x" * 129,
    ])
    def test_hostile_shapes_are_rejected(self, sid):
        from power_atlas import acp as acp_mod
        assert not acp_mod._valid_session_id(sid)

    @pytest.mark.parametrize("sid", [None, 17, 1.5, ["a"], {"a": 1}, b"abc"])
    def test_non_strings_are_rejected(self, sid):
        from power_atlas import acp as acp_mod
        assert not acp_mod._valid_session_id(sid)

    @pytest.mark.parametrize("sid", [
        "001b4195-ee19-4633-b1b2-488574cad044",
        "sess_phase4",
        "x" * 128,
    ])
    def test_real_session_ids_are_accepted(self, sid):
        """Positive control: the guard refuses hostile shapes, not the ids the
        store is actually full of."""
        from power_atlas import acp as acp_mod
        assert acp_mod._valid_session_id(sid)

    def test_a_rejected_id_reaches_neither_a_path_nor_the_wire(self, acp_store):
        acp_mod, _ = acp_store
        touched = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder",
                          lambda sid: touched.append(("lock", sid))), \
                patch.object(acp_mod._Supervisor, "_request",
                             lambda *a, **k: touched.append(("wire",))):
            asyncio.run(acp_mod._handle_load(conn, "../../etc/passwd"))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "bad_session_id"
        assert touched == []

    def test_a_rejected_id_leaves_a_server_side_trace(self, acp_store, caplog):
        acp_mod, _ = acp_store
        conn = _acp_conn(acp_mod)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            asyncio.run(acp_mod._handle_load(conn, "a/b"))
        assert any("bad_session_id" in r.getMessage() for r in caplog.records)


class TestAcpLockPreflight:
    """A hint, never the gate. Measured on this machine's store: 803 lock
    files, 22 naming a pid that still exists — and all 22 were recycled pids
    (svchost, firefox, RuntimeBroker), each created weeks after its lock was
    written. A pre-flight resting on pid liveness alone would have refused 22
    loadable sessions and been wrong every time it fired.
    """

    def _lock(self, store, sid, **fields):
        (store / (sid + ".lock")).write_text(json.dumps(fields))

    def test_a_live_lock_names_its_holder(self, acp_store):
        acp_mod, store = acp_store
        # This test process: alive, and started before a lock written now.
        self._lock(store, "held", pid=os.getpid(),
                   started_at=_lock_time(dt.datetime.now(dt.timezone.utc)))
        assert acp_mod._lock_holder("held") == os.getpid()

    def test_a_recycled_pid_is_not_a_holder(self, acp_store):
        """The 22-of-803 case: the pid exists, but the process behind it
        started long after the lock was written, so it is not the holder."""
        acp_mod, store = acp_store
        self._lock(store, "stale", pid=os.getpid(),
                   started_at="2020-01-01T00:00:00.000000000Z")
        assert acp_mod._lock_holder("stale") is None

    def test_a_dead_pid_is_not_a_holder(self, acp_store):
        acp_mod, store = acp_store
        self._lock(store, "gone", pid=2 ** 31 - 1,
                   started_at=_lock_time(dt.datetime.now(dt.timezone.utc)))
        assert acp_mod._lock_holder("gone") is None

    def test_a_missing_lock_is_not_a_holder(self, acp_store):
        acp_mod, _ = acp_store
        assert acp_mod._lock_holder("never-locked") is None

    @pytest.mark.parametrize("body", [
        "not json at all",
        "[]",
        '{"pid": "4242", "started_at": "2026-01-01T00:00:00.0Z"}',
        '{"pid": -1, "started_at": "2026-01-01T00:00:00.0Z"}',
        '{"started_at": "2026-01-01T00:00:00.0Z"}',
        '{"pid": %d}' % os.getpid(),
        '{"pid": %d, "started_at": "whenever"}' % os.getpid(),
    ])
    def test_an_unreadable_lock_grants_nothing(self, acp_store, body):
        """A hint may only ever add a refusal. Every branch that cannot
        establish a holder has to fall through to the agent, which is the
        authority."""
        acp_mod, store = acp_store
        (store / "odd.lock").write_text(body)
        assert acp_mod._lock_holder("odd") is None

    def test_the_preflight_refuses_before_the_wire(self, acp_store):
        acp_mod, store = acp_store
        self._lock(store, "busy-0001", pid=os.getpid(),
                   started_at=_lock_time(dt.datetime.now(dt.timezone.utc)))
        wire = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          lambda *a, **k: wire.append(a)):
            asyncio.run(acp_mod._handle_load(conn, "busy-0001"))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "session_in_use"
        assert str(os.getpid()) in frames[0]["payload"]["message"]
        assert wire == []
        assert "busy-0001" not in acp_mod._supervisor.sessions


class TestAcpSessionLoad:
    """``session/load`` is what reaches a session this process never created —
    one made in a terminal, or before a restart.
    """

    def _stored(self, store, sid, cwd):
        (store / (sid + ".json")).write_text(
            json.dumps({"session_id": sid, "cwd": str(cwd), "title": "t"}))

    def _replay(self, acp_mod, sid, events, calls):
        """A ``session/load`` that answers the way the agent does: by replaying
        the conversation as notifications while the request is outstanding."""
        async def fake_request(self, method, params,
                               timeout=acp_mod.REQUEST_TIMEOUT_SECONDS):
            calls.append((method, params))
            for kind, text in events:
                self._on_notification({
                    "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": kind,
                        "content": {"type": "text", "text": text}}},
                })
            return {}
        return fake_request

    def test_the_replayed_conversation_arrives_as_one_history_frame(
            self, acp_store):
        """Both halves of the conversation, in order, coalesced. Frame per
        event would put a whole conversation on a queue that retires the socket
        at SEND_QUEUE_MAXSIZE — and only for sessions long enough to matter."""
        acp_mod, store = acp_store
        calls = []
        sid = "load-me-0001"
        self._stored(store, sid, store)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid, [
                              ("user_message_chunk", "what is 2+2"),
                              ("agent_message_chunk", "4"),
                          ], calls)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))

        assert calls == [(
            "session/load",
            {"sessionId": sid, "cwd": str(Path(store).resolve()),
             "mcpServers": []},
        )]
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert frames[0]["payload"] == {
            "pending": "load",
            "timeoutSeconds": acp_mod.REQUEST_TIMEOUT_SECONDS}
        assert frames[1]["payload"]["sessionId"] == sid
        assert frames[1]["payload"]["created"] is False
        assert [(e["type"], e["payload"])
                for e in frames[2]["payload"]["events"]] == [
            ("chunk", {"role": "user", "text": "what is 2+2"}),
            ("chunk", {"role": "agent", "text": "4"}),
        ]

    def test_a_replayed_tool_call_survives_the_load(self, acp_store):
        """Tool calls already forward and render; a loaded history full of them
        has to travel the same path rather than a second one."""
        acp_mod, store = acp_store
        sid = "load-tools-01"
        self._stored(store, sid, store)

        async def fake_request(self, method, params, timeout=None):
            self._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-1", "title": "shell", "kind": "execute",
                    "status": "completed",
                    "rawInput": {"command": "git status"}}},
            })
            return {}

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        events = _queued(conn)[2]["payload"]["events"]
        assert [e["type"] for e in events] == ["tool_call"]
        assert events[0]["payload"]["command"] == "git status"

    def test_the_replay_is_not_fanned_out_while_it_is_being_built(
            self, acp_store):
        """A socket left attached from an earlier life of the session would
        otherwise receive the conversation event by event."""
        acp_mod, store = acp_store
        sid = "load-me-0002"
        self._stored(store, sid, store)
        stale = _acp_conn(acp_mod)
        acp_mod._registry.attach(stale, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid,
                                       [("agent_message_chunk", "x")] * 5,
                                       [])), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert _queued(stale) == []
        assert stale.session_id is None

    def test_a_spoken_in_use_refusal_becomes_a_readable_message(
            self, acp_store):
        """The plan recorded the agent naming the holder itself, measured
        before it self-updated. Builds that still do are read as such."""
        acp_mod, store = acp_store
        sid = "load-me-0003"
        self._stored(store, sid, store)

        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected(
                "Session is active in another process (PID 4242) (code -32603)")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", refused), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta", "error"]
        assert frames[1]["payload"]["code"] == "session_in_use"
        assert "4242" in frames[1]["payload"]["message"]

    def test_a_silent_refusal_is_named_by_reading_the_lock_again(
            self, acp_store):
        """Measured on kiro-cli 2.14.2: a session held elsewhere is refused
        with a bare ``-32603 Internal error`` — no pid, and nothing to tell it
        from any other failure. The lock is the only thing left that can name
        the process. Also covers a lock taken after the pre-flight ran."""
        acp_mod, store = acp_store
        sid = "load-me-0009"
        self._stored(store, sid, store)
        first = []

        def one_shot_preflight(session_id):
            # The pre-flight sees nothing; the re-read after the failure does.
            if not first:
                first.append(session_id)
                return None
            return 4242

        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("Internal error (code -32603)")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", one_shot_preflight), \
                patch.object(acp_mod._Supervisor, "_request", refused), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert frames[1]["payload"]["code"] == "session_in_use"
        assert "4242" in frames[1]["payload"]["message"]

    def test_another_agent_error_keeps_its_own_code(self, acp_store):
        """Positive control: with no lock and no marker, a refusal keeps the
        code it came with rather than being reported as an occupied session."""
        acp_mod, store = acp_store
        sid = "load-me-0004"
        self._stored(store, sid, store)

        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("No such session (code -32602)")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", refused), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert _queued(conn)[1]["payload"]["code"] == "agent_error"

    def test_a_failed_load_registers_nothing(self, acp_store):
        """A half-loaded session left behind would count against MAX_SESSIONS
        and be answered by ``subscribe`` with an empty transcript."""
        acp_mod, store = acp_store
        sid = "load-me-0005"
        self._stored(store, sid, store)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert sid not in acp_mod._supervisor.sessions
        assert sid not in acp_mod._supervisor.history
        assert acp_mod._supervisor._reserved == 0
        assert _queued(conn)[1]["payload"]["code"] == "agent_timeout"

    def test_a_session_already_live_here_is_answered_from_the_buffer(
            self, acp_session):
        """A second agent-side replay would append the conversation to itself."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.history[sid].append(
            acp_mod.envelope("chunk", {"role": "agent", "text": "old"}, sid))
        wire = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          lambda *a, **k: wire.append(a)):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["session", "history"]
        assert len(frames[1]["payload"]["events"]) == 1
        assert wire == []

    def test_a_missing_store_entry_falls_back_to_the_neutral_cwd(
            self, acp_store, monkeypatch, tmp_path):
        """A workspace that has been moved or deleted does not make the
        conversation unreadable, so it must not make the load fail."""
        acp_mod, store = acp_store
        calls = []
        neutral = tmp_path / "neutral"
        neutral.mkdir()
        monkeypatch.setattr(acp_mod, "_neutral_cwd", lambda: neutral)
        sid = "load-me-0006"
        self._stored(store, sid, store / "deleted-workspace")
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid, [], calls)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert calls[0][1]["cwd"] == str(neutral)

    def test_a_load_is_never_throttled_by_an_earlier_replay(self, acp_store):
        """The throttle rations a buffer rebuild a client can ask for freely.
        This replay was paid for with an agent round-trip, and refusing it
        would leave a loaded session rendering nothing at all."""
        acp_mod, store = acp_store
        sid = "load-me-0008"
        self._stored(store, sid, store)
        acp_mod._supervisor.sessions["other"] = {"cwd": ""}
        acp_mod._supervisor.history["other"] = acp_mod._History()
        conn = _acp_conn(acp_mod)
        acp_mod._handle_subscribe(conn, "other")
        _queued(conn)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid,
                                       [("agent_message_chunk", "x")], [])), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert len(frames[2]["payload"]["events"]) == 1

    def test_the_session_cap_still_binds(self, acp_store):
        acp_mod, store = acp_store
        sid = "load-me-0007"
        self._stored(store, sid, store)
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        wire = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          lambda *a, **k: wire.append(a)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert _queued(conn)[1]["payload"]["code"] == "too_many_sessions"
        assert wire == []

    def test_dispatch_routes_load_off_the_synchronous_path(self, acp_store):
        """``_handle_subscribe`` must stay free of ``await``; ``load`` is the
        half that needs one, which is why it is a separate frame type."""
        acp_mod, _ = acp_store
        seen = []

        async def fake_load(conn, session_id):
            seen.append(session_id)

        async def drive():
            conn = _acp_conn(acp_mod)
            acp_mod._dispatch(conn, {"type": "load", "sessionId": "abc",
                                     "payload": {}})
            await asyncio.sleep(0)

        assert "load" in acp_mod.CLIENT_TYPES
        with patch.object(acp_mod, "_handle_load", fake_load):
            asyncio.run(drive())
        assert seen == ["abc"]
        assert not asyncio.iscoroutinefunction(acp_mod._handle_subscribe)


def _stored_session(store, sid):
    """The minimum a load needs from the kiro-cli store: a recoverable cwd."""
    (store / (sid + ".json")).write_text(
        json.dumps({"session_id": sid, "cwd": str(store)}))


class TestAcpLoadServesOneReplayToEverySocket:
    """A socket that asks for a session while its ``session/load`` is running.

    ``load_session`` has to register the session before the round-trip — the
    agent replays the conversation as notifications while the request is
    outstanding, and ``record`` drops frames for a session with no buffer — and
    ``_emit`` both records *and* broadcasts. So for the duration of the load the
    registry saw a live session and attached anything that asked, handing it the
    replay frame by frame: at 1200 events a second socket subscribing halfway
    queued SEND_QUEUE_MAXSIZE frames and set ``_overflowed``. One click reaches
    it, because the dashboard row action opens ``/acp?sid=…`` and two tabs on
    one session is a supported shape.

    Suppressing the broadcast for the duration is the trap, not the fix: that
    socket would receive a ``history`` frame of the events so far and then never
    see the rest, trading a retired socket — visible — for a silently truncated
    conversation.
    """

    # Above SEND_QUEUE_MAXSIZE (256), which is the number that decides this:
    # at 256 queued frames `_Connection.send` sets `_overflowed` and the writer
    # retires the socket.
    EVENTS = 300

    def _replay_with(self, sid, count, halfway):
        """A ``session/load`` that replays ``count`` events and runs ``halfway``
        in the middle of them — where another socket's frame would land."""
        async def fake_request(self, method, params, timeout=None):
            for i in range(count):
                if i == count // 2:
                    halfway()
                self._on_notification({
                    "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "e%d" % i}}},
                })
            return {}
        return fake_request

    def test_a_socket_subscribing_mid_load_gets_one_frame_and_the_whole_tail(
            self, acp_store):
        acp_mod, store = acp_store
        sid = "load-mid-0001"
        _stored_session(store, sid)
        loader = _acp_conn(acp_mod)
        second = _acp_conn(acp_mod)

        with patch.object(
                acp_mod._Supervisor, "_request",
                self._replay_with(
                    sid, self.EVENTS,
                    lambda: acp_mod._handle_subscribe(second, sid))), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(loader, sid))

        frames = _queued(second)
        # No `chunk` of its own anywhere: every event arrived inside the replay.
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert frames[0]["payload"]["pending"] == "load"
        events = frames[2]["payload"]["events"]
        # Both halves of the property. The head proves the replay was not
        # started late; the tail proves the events broadcast after this socket
        # asked were not merely dropped.
        assert len(events) == self.EVENTS
        assert events[0]["payload"]["text"] == "e0"
        assert events[-1]["payload"]["text"] == "e%d" % (self.EVENTS - 1)
        assert second._overflowed is False
        assert second.session_id == sid

    def test_the_socket_that_asked_is_served_the_same_replay(self, acp_store):
        """Positive control: parking the other socket must not cost this one
        the answer it is waiting for."""
        acp_mod, store = acp_store
        sid = "load-mid-0002"
        _stored_session(store, sid)
        loader = _acp_conn(acp_mod)
        second = _acp_conn(acp_mod)
        with patch.object(
                acp_mod._Supervisor, "_request",
                self._replay_with(
                    sid, 4, lambda: acp_mod._handle_subscribe(second, sid))), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(loader, sid))
        frames = _queued(loader)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert len(frames[2]["payload"]["events"]) == 4

    def test_nothing_is_attached_while_the_load_runs(self, acp_store):
        """The invariant the ``_registry.loading`` key carries, asserted where
        it holds rather than only through its consequences."""
        acp_mod, store = acp_store
        sid = "load-mid-0003"
        _stored_session(store, sid)
        seen = []
        second = _acp_conn(acp_mod)

        def halfway():
            acp_mod._handle_subscribe(second, sid)
            seen.append(set(acp_mod._registry.subscribers.get(sid, ())))

        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay_with(sid, 4, halfway)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(_acp_conn(acp_mod), sid))
        assert seen == [set()]


class TestAcpConcurrentLoad:
    """The second ``load`` for one session used to be refused
    ``AgentRejected("That session is already loaded.")`` — relabelled
    ``session_in_use`` with "exit that one first", against a page whose
    ``loadTried`` guard then stopped it retrying on that socket. Nothing
    subscribed it to the buffer the refusal's own comment called the better
    answer.
    """

    async def _drive(self, acp_mod, sid, winner, loser):
        first = asyncio.ensure_future(acp_mod._handle_load(winner, sid))
        for _ in range(100):
            if sid in acp_mod._registry.loading:
                break
            await asyncio.sleep(0)
        assert sid in acp_mod._registry.loading, "the load never claimed it"
        await acp_mod._handle_load(loser, sid)
        await first

    def test_the_second_load_is_served_the_first_ones_replay(self, acp_store):
        acp_mod, store = acp_store
        sid = "load-race-001"
        _stored_session(store, sid)
        winner, loser = _acp_conn(acp_mod), _acp_conn(acp_mod)

        async def fake_request(self, method, params, timeout=None):
            self._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello"}}},
            })
            return {}

        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(self._drive(acp_mod, sid, winner, loser))

        frames = _queued(loser)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert frames[2]["payload"]["events"][0]["payload"]["text"] == "hello"
        assert loser.session_id == sid

    def test_only_one_round_trip_is_spent(self, acp_store):
        """A second agent-side replay would append the whole conversation to
        itself, which is why the loser waits rather than asking again."""
        acp_mod, store = acp_store
        sid = "load-race-002"
        _stored_session(store, sid)
        calls = []

        async def fake_request(self, method, params, timeout=None):
            calls.append(method)
            return {}

        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(self._drive(acp_mod, sid, _acp_conn(acp_mod),
                                    _acp_conn(acp_mod)))
        assert calls == ["session/load"]

    def test_a_failure_reaches_everyone_who_waited(self, acp_store):
        """A waiter left holding a pending label is the failure mode parking
        them introduces, so the failure path has to reach them too."""
        acp_mod, store = acp_store
        sid = "load-race-003"
        _stored_session(store, sid)
        loser = _acp_conn(acp_mod)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(self._drive(acp_mod, sid, _acp_conn(acp_mod), loser))
        frames = _queued(loser)
        assert [f["type"] for f in frames] == ["meta", "error"]
        assert frames[1]["payload"]["code"] == "agent_timeout"


class TestAcpLoadSlotAccounting:
    """``new_session`` records only after its round-trip, so its reservation and
    its record never overlap. ``load_session`` must record first, and holding
    the reservation as well counted the loading session twice: reproduced with
    one pre-existing session, where a concurrent ``new`` was refused
    ``too_many_sessions`` while only two existed. MAX_SESSIONS is 3.
    """

    def test_an_in_flight_load_takes_one_slot_not_two(self, acp_store):
        acp_mod, store = acp_store
        sid = "load-slot-001"
        _stored_session(store, sid)
        acp_mod._supervisor.sessions["already-live"] = {"cwd": ""}
        acp_mod._supervisor.history["already-live"] = acp_mod._History()
        created = []

        async def fake_request(self, method, params, timeout=None):
            if method == "session/load":
                # A `new` landing while the load is outstanding. It is the third
                # slot of three and must not be refused.
                created.append(await self.new_session(str(store)))
                return {}
            return {"sessionId": "brand-new-01"}

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))

        assert [f["type"] for f in _queued(conn)] == [
            "meta", "session", "history"]
        assert created == [{"sessionId": "brand-new-01", "cwd": str(store)}]
        assert acp_mod._supervisor._reserved == 0
        assert len(acp_mod._supervisor.sessions) == acp_mod.MAX_SESSIONS

    def test_a_failed_load_releases_its_slot(self, acp_store):
        """Positive control: the release moved, it did not disappear."""
        acp_mod, store = acp_store
        sid = "load-slot-002"
        _stored_session(store, sid)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(_acp_conn(acp_mod), sid))
        assert acp_mod._supervisor._reserved == 0
        assert acp_mod._supervisor.sessions == {}


class TestAcpLockReadIsBoundedAndOffTheLoop:
    """The lock directory is written by the agent running trust-all-tools, so a
    lock file's size is not ours to assume, and ``MemoryError`` is in no caught
    set on this path. One line away, ``_stored_session_cwd`` already reads a
    bounded prefix through ``asyncio.to_thread``.
    """

    def _padded(self, pad):
        return " " * pad + json.dumps({
            "pid": os.getpid(),
            "started_at": _lock_time(dt.datetime.now(dt.timezone.utc))})

    def test_a_lock_is_not_read_past_the_cap(self, acp_store):
        """The pid here sits beyond LOCK_MAX_BYTES, so a bounded read cannot
        see it and an unbounded one can."""
        acp_mod, store = acp_store
        (store / "huge.lock").write_text(self._padded(acp_mod.LOCK_MAX_BYTES))
        assert acp_mod._lock_holder("huge") is None

    def test_the_same_lock_inside_the_cap_still_names_its_holder(self, acp_store):
        """Positive control: the cap refused above, not the padding itself."""
        acp_mod, store = acp_store
        (store / "small.lock").write_text(self._padded(16))
        assert acp_mod._lock_holder("small") == os.getpid()

    def test_the_lock_is_read_off_the_event_loop(self, acp_store):
        """Twice per load — the pre-flight and the re-read after a failure —
        each a file read plus a psutil query."""
        acp_mod, store = acp_store
        sid = "load-thread-1"
        _stored_session(store, sid)
        threads = []

        def spy(session_id):
            threads.append(threading.current_thread().ident)
            return None

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod, "_lock_holder", spy), \
                patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(_acp_conn(acp_mod), sid))
        assert len(threads) == 2
        assert threading.main_thread().ident not in threads


class TestAcpLoadFailureAttribution:
    """``_load_failure`` preferred the re-read lock over the actual exception,
    unconditionally and for every ``AcpError`` subclass. A load that timed out
    on our side was reported as "Session is active in another process (PID n) …
    exit that one first" — and the pid it named was PowerAtlas's own agent,
    because ``session/load`` makes the agent write that lock itself.
    """

    def _one_shot(self, holder):
        """A pre-flight that sees nothing and a re-read that sees ``holder``."""
        seen = []

        def _lock_holder(session_id):
            seen.append(session_id)
            return None if len(seen) == 1 else holder
        return _lock_holder

    def _refuse_with(self, exc_factory):
        async def refused(self, method, params, timeout=None):
            raise exc_factory()
        return refused

    def test_a_lock_naming_our_own_agent_is_not_a_holder(
            self, acp_store, monkeypatch):
        """Confirmed against the real store: ``73a40df3….lock`` was written at
        14:10 naming pid 21452, while that session's ``.json`` was created at
        10:36 — so the lock came from a load, not from a creation. With the load
        then failing on our side, every retry was refused at the pre-flight for
        the life of the agent process, naming a process the operator cannot
        exit because it is ours."""
        acp_mod, store = acp_store

        class _OurAgent:
            pid = os.getpid()

        (store / "ours.lock").write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": _lock_time(dt.datetime.now(dt.timezone.utc))}))
        assert acp_mod._lock_holder("ours") == os.getpid()
        monkeypatch.setattr(acp_mod._supervisor, "_proc", _OurAgent())
        assert acp_mod._lock_holder("ours") is None

    @pytest.mark.parametrize("factory, code, fragment", [
        (lambda: __import__("power_atlas.acp", fromlist=["x"]).AgentTimeout(
            "The agent did not answer 'session/load' within 90s."),
         "agent_timeout", "within 90s"),
        (lambda: __import__("power_atlas.acp", fromlist=["x"]).AgentDied(
            "The agent stopped answering; its channel closed."),
         "agent_died", "channel closed"),
    ])
    def test_a_local_failure_keeps_its_own_code(
            self, acp_store, factory, code, fragment):
        """A timeout or a dead agent says nothing about who holds the session.
        Relabelling them ``session_in_use`` hid the failure that did happen
        behind advice about a process that was not there."""
        acp_mod, store = acp_store
        sid = "load-attr-001"
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", self._one_shot(4242)), \
                patch.object(acp_mod._Supervisor, "_request",
                             self._refuse_with(factory)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        payload = _queued(conn)[1]["payload"]
        assert payload["code"] == code
        assert fragment in payload["message"]
        assert "4242" not in payload["message"]

    def test_the_session_cap_is_not_reported_as_an_occupied_session(
            self, acp_store):
        acp_mod, store = acp_store
        sid = "load-attr-002"
        _stored_session(store, sid)
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", self._one_shot(4242)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        payload = _queued(conn)[1]["payload"]
        assert payload["code"] == "too_many_sessions"
        assert "4242" not in payload["message"]

    def test_a_named_holder_still_wins_over_an_agent_refusal(self, acp_store):
        """Positive control: the re-read is still what turns the one cause an
        operator can act on into a sentence — it is only its precedence over
        unrelated failures that was wrong."""
        acp_mod, store = acp_store
        sid = "load-attr-003"
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", self._one_shot(4242)), \
                patch.object(
                    acp_mod._Supervisor, "_request",
                    self._refuse_with(
                        lambda: acp_mod.AgentRejected(
                            "Internal error (code -32603)"))), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        payload = _queued(conn)[1]["payload"]
        assert payload["code"] == "session_in_use"
        assert "4242" in payload["message"]


class TestAcpUnattributedRefusal:
    """``_lock_holder`` returns ``None`` on eight conditions — psutil absent,
    the lock missing, unreadable, not a JSON object, no pid, an unparseable
    timestamp, a psutil error, the skew guard — and on kiro-cli 2.14.2 the
    agent contributes only a bare ``-32603 "Internal error"``. Every one of
    those eight therefore landed on a message saying neither what happened nor
    what to do, and a kiro-cli that changed its lock format would silently
    revert every in-use refusal to it.
    """

    def _refused(self, acp_mod, message):
        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected(message)
        return refused

    def _load(self, acp_mod, store, sid, message):
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._refused(acp_mod, message)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        return _queued(conn)[1]["payload"]

    def test_an_opaque_refusal_still_says_what_is_known(self, acp_store):
        acp_mod, store = acp_store
        payload = self._load(acp_mod, store, "load-opaque-1",
                             "Internal error (code -32603)")
        assert payload["code"] == "session_in_use"
        message = payload["message"]
        assert "-32603" in message
        # No pid is claimed, because none could be established.
        assert "PID" not in message
        # Both remedies, one of which is the only thing that frees a session.
        assert "kiro-cli" in message
        assert "restart PowerAtlas" in message

    def test_a_refusal_that_names_a_cause_is_passed_through(self, acp_store):
        """Positive control: only the code that names nothing is rewritten."""
        acp_mod, store = acp_store
        payload = self._load(acp_mod, store, "load-opaque-2",
                             "No such session (code -32602)")
        assert payload["code"] == "agent_error"
        assert payload["message"] == "No such session (code -32602)"

    def test_the_refusal_names_its_session_and_what_the_page_was_told(
            self, acp_store, caplog):
        """Both failed-load log lines omitted the session id, alone among this
        module's refusals, and one paired the *substituted* code with the
        *original* message — observed verbatim as ``[session_in_use] The agent
        did not answer 'session/load' within 90s``."""
        acp_mod, store = acp_store
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            self._load(acp_mod, store, "load-opaque-3",
                       "Internal error (code -32603)")
        line = [r.getMessage() for r in caplog.records
                if "session/load refused" in r.getMessage()]
        assert len(line) == 1
        assert "[session_in_use]" in line[0]
        assert "session=load-opaque-3" in line[0]
        assert "-32603" in line[0]
        # The agent's own words are kept, but as the agent's, not as ours.
        assert "(agent: Internal error (code -32603))" in line[0]


class TestAcpSessionCapMessage:
    """This message has been wrong in both directions. It said "close one
    first" while ``close`` answered ``not_implemented``; Phase 5 corrected it
    to name a PowerAtlas restart; Phase 6 built the close control and made the
    correction wrong the other way. Naming a remedy is not enough — the remedy
    has to be one the running build actually offers, and the cap is reachable
    by *browsing* three sessions from the dashboard.
    """

    def _fill(self, acp_mod):
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}

    def _assert_names_the_close_control(self, acp_mod, payload):
        assert payload["code"] == "too_many_sessions"
        text = payload["message"].lower()
        assert "close" in text
        # The two claims this message has actually shipped falsely, pinned by
        # their own words. A generic "mentions closing" assertion passes on
        # "nothing closes a session yet", which is the wording being retired.
        assert "nothing closes a session" not in text
        assert "only way to free one" not in text
        # The cheaper lever is named first: a restart releases every session,
        # including the two the operator was not trying to free.
        assert text.index("close") < text.index("restart")
        # Tied to the control actually being routed. `close` answering
        # `not_implemented` is exactly the state this message described
        # falsely for two phases, and no other test reads the message's truth.
        assert "close" in acp_mod.CLIENT_TYPES
        conn = _acp_conn(acp_mod)

        async def dispatch():
            acp_mod._dispatch(conn, {"type": "close", "sessionId": "nope",
                                     "payload": {}})
            await asyncio.gather(*acp_mod._tasks)

        asyncio.run(dispatch())
        codes = [f["payload"].get("code") for f in _queued(conn)]
        assert "not_implemented" not in codes, codes

    def test_the_new_path_names_a_remedy_that_exists(self, acp_store, tmp_path):
        acp_mod, _ = acp_store
        self._fill(acp_mod)
        conn = _acp_conn(acp_mod)
        asyncio.run(acp_mod._handle_new(conn, {"cwd": str(tmp_path)}))
        self._assert_names_the_close_control(acp_mod, _queued(conn)[1]["payload"])

    def test_the_load_path_names_a_remedy_that_exists(self, acp_store):
        acp_mod, store = acp_store
        _stored_session(store, "load-cap-0001")
        self._fill(acp_mod)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, "load-cap-0001"))
        self._assert_names_the_close_control(acp_mod, _queued(conn)[1]["payload"])


class TestAcpLoadPageRecovery:
    """After a failed load nothing on the page was pressable. ``Send`` is
    disabled, and ``Reconnect``/``Reload page`` are unhidden only from
    socket-close handlers that do not fire, because the socket is healthy — it
    is the session that is not. Enter was not disabled either: ``sendPrompt``
    gated only on ``sessionId``, so it reached the server and came back "It may
    belong to an earlier PowerAtlas process — create a new one", which is the
    one thing the user was avoiding by opening an existing session.
    """

    def _page(self) -> str:
        from power_atlas.web import templates
        return templates.env.loader.get_source(templates.env, "acp.html")[0]

    def test_enter_is_refused_before_the_wrong_advice_is_earned(self):
        src = self._page()
        body = src.split("function sendPrompt()", 1)[1].split("\n  function ", 1)[0]
        assert "if (loadFailed)" in body
        # Ahead of the `sessionId` gate, which is the one that used to let it
        # through: after a failed load `sessionId` is still set from ?sid=.
        assert body.index("if (loadFailed)") < body.index("if (!sessionId)")

    def test_a_failed_load_states_its_recovery_and_offers_a_control(self):
        src = self._page()
        body = src.split("function reportLoadFailure()", 1)[1].split(
            "\n  function ", 1)[0]
        assert "loadFailed = true" in body
        assert "reloadBtn.hidden = false" in body
        # In the transcript, not only the 120 px log strip.
        assert "addMessage('note'" in body
        assert "Reload the page" in body

    def test_the_error_handler_reaches_it_only_for_a_load(self):
        src = self._page()
        assert "if (failedLoad) reportLoadFailure();" in src
        assert "var failedLoad = loadingSid !== null" in src

    def test_a_session_frame_clears_the_flag_again(self):
        """A load that succeeds on a retry must not leave the page refusing to
        send to a session it is now subscribed to."""
        src = self._page()
        session_branch = src.split("if (type === 'session') {", 1)[1]
        assert "loadFailed = false;" in session_branch.split("return;", 1)[0]

    def test_the_send_button_is_disabled_by_the_flag(self):
        src = self._page()
        assert "sendBtn.disabled = active || !sessionId || loadFailed;" in src


class TestAcpLoadStatesItsCeiling:
    """A slow load and a wedged one look identical until the ceiling expires:
    the agent says nothing at all before the whole conversation, so an
    unchanging pill and a blank transcript is the entire signal for up to
    REQUEST_TIMEOUT_SECONDS — 90 seconds. The sibling ``new`` path sets an
    expectation ("the agent takes several seconds"); this one did not.
    """

    def test_the_pending_frame_carries_the_ceiling(self):
        from power_atlas import acp as acp_mod
        frame = acp_mod._load_pending_frame("s1")
        assert frame["type"] == "meta"
        assert frame["sessionId"] == "s1"
        assert frame["payload"] == {
            "pending": "load",
            "timeoutSeconds": acp_mod.REQUEST_TIMEOUT_SECONDS}

    def test_the_page_renders_the_ceiling_rather_than_hardcoding_one(self):
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        branch = src.split("payload.pending === 'load'", 1)[1].split(
            "return;", 1)[0]
        assert "payload.timeoutSeconds" in branch
        # In the transcript as well as the log strip: a blank transcript is
        # exactly what makes a wedged load unreadable.
        assert "addMessage('note'" in branch


class TestAcpDashboardRowAction:
    """The control has to live inside ``.session-actions``: that container is
    what the row's own ``onclick`` excludes, so anywhere else a click on it
    toggles multi-select instead of opening the session. The same collision is
    recorded at ``CLOSED_INVESTIGATIONS.md:90``.
    """

    def _row(self) -> str:
        from power_atlas.web import templates

        class _Session:
            session_id = "001b4195-ee19-4633-b1b2-488574cad044"
            title = "t"
            updated_at = "2026-07-26T10:00:00"
            first_prompt = ""
            last_reply_tail = ""

        return templates.get_template("partials/session_row.html").render(
            session=_Session(), cwd=r"C:\scratch", pinned_sessions=[],
            provider_name="kiro-cli", provider_color="", stale=False,
            status="closed", waiting_detail=("", ""))

    def test_the_action_sits_inside_the_excluded_container(self):
        html = self._row()
        actions = html.split('<div class="session-actions">', 1)
        assert len(actions) == 2, "the row lost its .session-actions block"
        assert "openInAcp(this)" in actions[1].split("</div>", 1)[0]

    def test_the_action_opens_acp_for_this_row(self):
        from power_atlas.web import templates
        index = templates.env.loader.get_source(templates.env, "index.html")[0]
        assert "function openInAcp(btn)" in index
        assert "'/acp?sid='+encodeURIComponent(row.dataset.sid)" in index

    def test_a_non_kiro_provider_gets_no_acp_action(self):
        """`/acp` speaks ACP to kiro-cli; no other provider has a session it
        could load, so offering the control would only ever fail."""
        from power_atlas.web import templates

        class _Session:
            session_id = "abc"
            title = "t"
            updated_at = ""
            first_prompt = ""
            last_reply_tail = ""

        html = templates.get_template("partials/session_row.html").render(
            session=_Session(), cwd="", pinned_sessions=[],
            provider_name="claude-code", provider_color="", stale=False,
            status="closed", waiting_detail=("", ""))
        assert "openInAcp" not in html


# --- ACP phase 6: cancel, close, and context-window telemetry ---


def _sent(acp_mod, method_calls):
    """A ``_write`` stand-in that records the JSON-RPC objects built for it.

    Stubbed at ``_write`` rather than at ``_request``/``_notify``: the shape
    under test is the *line* the supervisor puts on the pipe, and whether it
    carries an ``id`` is exactly what separates a request from a notification.
    """
    def write(self, obj):
        method_calls.append(obj)
        loop = self._loop
        # Answer our own request from the reader's side, the way the agent
        # would, so the awaiting future completes.
        if "id" in obj and loop is not None:
            loop.call_soon_threadsafe(
                self._on_response, {"id": obj["id"], "result": {}})
    return write


def _run_bound(acp_mod, factory):
    """Run a coroutine with the supervisor bound to the running loop.

    ``_request`` refuses outright when ``_loop`` is unset, so a test that only
    stubs ``_write`` never reaches the request it is checking. Binding here is
    what the real ``ensure_started`` does, and it lets ``_on_response`` match
    the future the same way the reader thread does.
    """
    async def run():
        acp_mod._supervisor._loop = asyncio.get_running_loop()
        try:
            return await factory()
        finally:
            acp_mod._supervisor._loop = None
            acp_mod._supervisor._pending.clear()
    return asyncio.run(run())


class TestAcpCancel:
    """A turn under ``-a`` can run tools for minutes, and until now nothing
    could end one. ``session/cancel`` is a **notification** on kiro-cli 2.14.2 —
    measured: a request would have parked the Stop button on a future the agent
    never answers.
    """

    def _conn(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def test_cancel_is_a_notification_naming_the_session(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert written == [{"jsonrpc": "2.0", "method": "session/cancel",
                            "params": {"sessionId": sid}}]
        # No id: a notification cannot be answered, and awaiting an answer
        # would hold the page for REQUEST_TIMEOUT_SECONDS after a cancellation
        # that has already happened.
        assert "id" not in written[0]

    def test_cancel_emits_no_turn_boundary_of_its_own(self, acp_session):
        """The outstanding ``session/prompt`` returns ``cancelled`` and the task
        awaiting it emits the boundary. A second one here would leave the
        transcript with two ends to one turn."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert _queued(conn) == []
        assert acp_mod._supervisor.history[sid].events() == []
        # And the session survives its own cancellation.
        assert sid in acp_mod._supervisor.sessions

    def test_cancel_without_a_turn_reaches_the_agent_not_at_all(
            self, acp_session, caplog):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        written = []
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"), \
                patch.object(acp_mod._Supervisor, "_write",
                             _sent(acp_mod, written)):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert written == []
        assert "not running a turn" in caplog.text

    def test_cancel_without_a_session_id_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)
        asyncio.run(acp_mod._handle_cancel(conn, None))
        assert _queued(conn)[0]["payload"]["code"] == "bad_envelope"

    def test_an_agent_failure_reaches_the_page_typed(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)

        def boom(self, obj):
            raise acp_mod.AgentDied("the agent is not running")

        with patch.object(acp_mod._Supervisor, "_write", boom), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert _queued(conn)[0]["payload"]["code"] == "agent_died"

    def test_the_page_shows_the_cancellation_and_reopens_send(self):
        """`stopReason: "cancelled"` is not `end_turn`, so the turn-end branch
        writes it into the transcript — the log strip is 120 px tall and is not
        replayed, so a cancellation reaching only it disappears on reload."""
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        branch = src.split("payload.turn === 'end'", 1)[1].split("return;", 1)[0]
        assert "addMessage('note', 'turn ended: ' + payload.stopReason)" in branch
        assert "setTurn(false)" in branch
        stop = src.split("stopBtn.addEventListener", 1)[1].split("});", 1)[0]
        assert "send('cancel'" in stop


class TestAcpSessionClose:
    """The lever the whole memory budget rests on: §4 and §6 accept ~306 MB a
    session on the strength of a close control existing.

    Measured on kiro-cli 2.14.2 — closing one session took the agent's tree from
    17 processes / 1045.5 MB to 12 / 792.1 MB, and removed the session's
    ``.lock``. ``session/close`` is **not** the method that does it: the agent
    answers that one ``-32601 Method not found``.
    """

    def _conn(self, acp_mod, sid=None):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        if sid:
            acp_mod._registry.attach(conn, sid)
        return conn

    def test_close_asks_the_agent_before_dropping_anything(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
        assert [(o["method"], o["params"]) for o in written] == [
            (acp_mod.CLOSE_METHOD, {"sessionId": sid})]
        assert acp_mod.CLOSE_METHOD == "_kiro.dev/session/terminate"
        assert sid not in acp_mod._supervisor.sessions

    def test_the_ring_buffer_goes_with_the_session(self, acp_session):
        """Keyed by session id and reachable from nowhere else, so a buffer left
        behind is up to HISTORY_MAX_BYTES resident for the app's lifetime."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        for i in range(50):
            acp_mod._emit(sid, acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))
        assert len(acp_mod._supervisor.history[sid]) == 50
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
        assert sid not in acp_mod._supervisor.history

    def test_a_failed_close_keeps_the_session(self, acp_session):
        """A kiro-cli without the extension method answers -32601. Dropping our
        own record then would report a memory saving that did not happen and
        leave ~5 processes unreachable for the agent's whole life."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)

        async def refuse(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("Method not found (code -32601)")

        with patch.object(acp_mod._Supervisor, "_request", refuse), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_close(conn, sid))
        assert sid in acp_mod._supervisor.sessions
        assert sid in acp_mod._supervisor.history
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "agent_error"
        assert sid not in acp_mod._supervisor.closing

    def test_every_watching_socket_is_told_and_detached(self, acp_session):
        """A second tab is holding a transcript that no longer has a session
        behind it, and a subscriber entry for a session that is gone would
        outlive every frame that could clear it."""
        acp_mod, sid = acp_session
        a = self._conn(acp_mod, sid)
        b = self._conn(acp_mod, sid)
        _queued(a), _queued(b)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(a, sid))
        for conn in (a, b):
            frames = _queued(conn)
            assert [f["type"] for f in frames] == ["session_closed"]
            assert frames[0]["payload"]["sessionId"] == sid
            assert conn.session_id is None
        assert sid not in acp_mod._registry.subscribers

    def test_a_socket_watching_another_session_keeps_it(self, acp_session):
        """The closing socket need not be attached to what it closes, and
        detaching it from what it *is* attached to would leave that tab
        receiving nothing."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.sessions["other"] = {"cwd": ""}
        acp_mod._supervisor.history["other"] = acp_mod._History()
        conn = self._conn(acp_mod, "other")
        _queued(conn)
        try:
            with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                    patch.object(acp_mod._Supervisor, "alive", lambda self: True):
                _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
            assert [f["type"] for f in _queued(conn)] == ["session_closed"]
            assert conn.session_id == "other"
        finally:
            acp_mod._supervisor.sessions.pop("other", None)
            acp_mod._supervisor.history.pop("other", None)

    def test_close_during_a_turn_is_refused(self, acp_session):
        """The outstanding `session/prompt` would sit on a session the agent no
        longer has for the whole of PROMPT_TIMEOUT_SECONDS."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)):
            asyncio.run(acp_mod._handle_close(conn, sid))
        assert written == []
        assert _queued(conn)[0]["payload"]["code"] == "turn_in_progress"
        assert sid in acp_mod._supervisor.sessions

    def test_close_during_a_load_is_refused(self, acp_session):
        """``_Registry.loading`` bars attachment for the whole of a
        ``session/load``; closing under one would strand the parked sockets."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._registry.loading[sid] = []
        _queued(conn)
        written = []
        try:
            with patch.object(acp_mod._Supervisor, "_write",
                              _sent(acp_mod, written)):
                asyncio.run(acp_mod._handle_close(conn, sid))
        finally:
            acp_mod._registry.loading.pop(sid, None)
        assert written == []
        assert _queued(conn)[0]["payload"]["code"] == "session_loading"
        assert sid in acp_mod._supervisor.sessions

    def test_an_unknown_session_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)):
            asyncio.run(acp_mod._handle_close(conn, "no-such-session"))
        # One frame and nothing on the wire: a refusal that reports and then
        # carries on reaches the agent anyway and answers the page twice, with
        # the second answer contradicting the first.
        assert [f["payload"]["code"] for f in _queued(conn)] == ["unknown_session"]
        assert written == []

    def test_a_second_close_cannot_overtake_the_first(self, acp_session):
        """Two `close` frames become two tasks. The second would be refused by
        an agent that no longer has the session, and reach the page as a failure
        to close something already closed."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)
        started = asyncio.Event()

        async def slow(self, method, params, timeout=None):
            started.set()
            await asyncio.sleep(0.05)
            return {}

        async def both():
            with patch.object(acp_mod._Supervisor, "_request", slow), \
                    patch.object(acp_mod._Supervisor, "alive",
                                 lambda self: True):
                first = asyncio.ensure_future(acp_mod._handle_close(conn, sid))
                await started.wait()
                await acp_mod._handle_close(conn, sid)
                await first

        asyncio.run(both())
        codes = [f["payload"].get("code") for f in _queued(conn)]
        assert "close_in_progress" in codes

    def test_the_closed_session_frees_a_slot(self, acp_session):
        """Which is the entire point: the cap is the only thing between one
        socket and memory exhaustion at ~306 MB a session."""
        acp_mod, sid = acp_session
        for i in range(acp_mod.MAX_SESSIONS - 1):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        conn = self._conn(acp_mod, sid)
        try:
            assert (len(acp_mod._supervisor.sessions)
                    >= acp_mod.MAX_SESSIONS)
            with patch.object(acp_mod._Supervisor, "_write",
                              _sent(acp_mod, [])), \
                    patch.object(acp_mod._Supervisor, "alive",
                                 lambda self: True):
                _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
            assert len(acp_mod._supervisor.sessions) < acp_mod.MAX_SESSIONS
        finally:
            for i in range(acp_mod.MAX_SESSIONS - 1):
                acp_mod._supervisor.sessions.pop("filler%d" % i, None)

    def test_the_page_clears_the_session_and_the_url(self):
        """A ?sid= left in place makes a reload re-adopt the session through
        `load` and spend the ~306 MB again — undoing the button."""
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        branch = src.split("type === 'session_closed'", 1)[1].split(
            "return;", 1)[0]
        assert "sessionId = null" in branch
        assert "history.replaceState(null, '', location.pathname)" in branch
        assert "setContext(null)" in branch

    def test_close_is_off_while_a_turn_runs(self):
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        body = src.split("function setTurn(active)", 1)[1].split("\n  }", 1)[0]
        assert "closeBtn.disabled = active || !sessionId" in body


class TestAcpContextWindow:
    """``_kiro.dev/metadata`` is kiro-private and arrives with **no**
    ``sessionUpdate`` field, so it is matched on the JSON-RPC method name —
    the opposite rule from tool calls, where one method carries six update
    kinds and only ``update.sessionUpdate`` separates them.
    """

    def _attached(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def _notify(self, acp_mod, sid, percent):
        acp_mod._supervisor._on_notification({
            "method": acp_mod.METADATA_METHOD,
            "params": {"sessionId": sid,
                       acp_mod.CONTEXT_PERCENT_KEY: percent,
                       "totalTokens": 12345},
        })

    def test_the_percentage_reaches_the_page(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        self._notify(acp_mod, sid, 5.8399)
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta"]
        # Rounded on the wire, so the number sent and the number rendered are
        # the same one. The agent sends four decimals.
        assert frames[0]["payload"] == {"contextPercent": 5.8}

    def test_it_is_matched_on_the_method_not_the_update(self, acp_session):
        """Keying off ``update.sessionUpdate`` — the tool-call rule — would drop
        every one of these, because they carry no ``update`` at all."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk",
                acp_mod.CONTEXT_PERCENT_KEY: 42.0,
                "content": {"type": "text", "text": "hi"}}},
        })
        assert [f["payload"].get("contextPercent") for f in _queued(conn)] == [None]

    def test_it_is_not_recorded_in_the_replay_buffer(self, acp_session):
        """A level, not an event: recording each would spend an eviction per
        turn on a number a later frame supersedes."""
        acp_mod, sid = acp_session
        self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, 7.0)
        assert acp_mod._supervisor.history[sid].events() == []

    def test_subscribe_carries_the_latest_reading(self, acp_session):
        """Which is why not recording it is safe: the buffer is built to evict,
        and this is the reconnecting socket's only other source."""
        acp_mod, sid = acp_session
        self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, 11.25)
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["contextPercent"] == 11.2

    def test_a_session_with_no_turn_yet_reports_null(self, acp_session):
        """Rather than 0%, which would draw an empty bar claiming a measurement
        that has not been taken."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["contextPercent"] is None

    @pytest.mark.parametrize("value", [
        None, "5.8", True, False, -1, 101, float("nan"), float("inf"),
        {"percent": 5}])
    def test_a_value_that_is_not_a_percentage_is_dropped(self, acp_session,
                                                         value):
        """This number ends up as a CSS width. ``True`` is the one that looks
        harmless: it is an ``int`` in Python and would round-trip as 1%."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        self._notify(acp_mod, sid, value)
        assert _queued(conn) == []
        assert "contextPercent" not in acp_mod._supervisor.sessions[sid]

    def test_a_reading_for_an_unknown_session_is_dropped(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        self._notify(acp_mod, "someone-else", 9.0)
        assert _queued(conn) == []

    def test_the_page_narrows_the_value_again_before_the_style_sink(self):
        """The one attribute sink on a page whose whole defence is that nothing
        agent-derived reaches one."""
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        body = src.split("function setContext(percent)", 1)[1].split(
            "\n  }", 1)[0]
        assert "typeof percent !== 'number'" in body
        assert "isFinite(percent)" in body
        assert "percent < 0 || percent > 100" in body
        assert "contextFill.style.width = percent + '%'" in body


class TestAcpDeclaredTypesAreRouted:
    """``cancel`` and ``close`` answered ``not_implemented`` for three phases
    while the page had no control for either. Now that both are routed, the
    fallback is a server bug rather than a phase boundary — and a control the
    page draws while the server silently ignores it is the worst of the three
    states.
    """

    def test_no_client_type_falls_through_to_not_implemented(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)

        async def dispatch():
            for type_ in sorted(acp_mod.CLIENT_TYPES):
                acp_mod._dispatch(conn, {"type": type_, "sessionId": sid,
                                         "payload": {"prompt": "x"}})
            await asyncio.gather(*acp_mod._tasks)

        with patch.object(acp_mod._Supervisor, "alive", lambda self: False):
            asyncio.run(dispatch())
        codes = [f["payload"].get("code") for f in _queued(conn)]
        assert "not_implemented" not in codes, codes


# --- Phase 3 (Launch Profiles): Profile endpoint tests ---


class TestLaunchProfileEndpoints:
    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_activate_valid_profile(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(
            launch_profiles=[LaunchProfile(id="default"), LaunchProfile(id="fast", name="Fast")],
            active_launch_profile="default",
        )
        resp = client.post("/api/launch-profile/activate", json={"id": "fast"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        saved = mock_save.call_args[0][0]
        assert saved.active_launch_profile == "fast"

    @patch("power_atlas.web.load_config")
    def test_activate_nonexistent_profile(self, mock_load, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
        resp = client.post("/api/launch-profile/activate", json={"id": "nonexistent"})
        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_save_new_profile(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
        resp = client.post("/api/launch-profile/save", json={
            "id": "__new__",
            "name": "My Custom Profile",
            "terminal_command": "wt.exe",
            "wt_profile": "PowerShell",
        })
        assert resp.status_code == 200
        assert "saved" in resp.text.lower()
        saved = mock_save.call_args[0][0]
        assert len(saved.launch_profiles) == 2
        assert saved.launch_profiles[1].name == "My Custom Profile"

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_save_update_existing(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile(id="default", name="Default")])
        resp = client.post("/api/launch-profile/save", json={
            "id": "default",
            "name": "Renamed",
            "terminal_command": "",
            "wt_profile": "PowerShell",
        })
        assert resp.status_code == 200
        assert "saved" in resp.text.lower()
        saved = mock_save.call_args[0][0]
        assert len(saved.launch_profiles) == 1
        assert saved.launch_profiles[0].name == "Renamed"

    @patch("power_atlas.web.load_config")
    def test_save_invalid_name_empty(self, mock_load, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
        resp = client.post("/api/launch-profile/save", json={
            "id": "__new__", "name": "", "terminal_command": "",
            "wt_profile": "PowerShell",
        })
        assert resp.status_code == 200
        assert "1-80" in resp.text

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_delete_profile(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(
            launch_profiles=[LaunchProfile(id="a", name="A"), LaunchProfile(id="b", name="B")],
            active_launch_profile="a",
        )
        resp = client.post("/api/launch-profile/delete", json={"id": "b"})
        assert resp.status_code == 200
        assert "deleted" in resp.text.lower()
        saved = mock_save.call_args[0][0]
        assert len(saved.launch_profiles) == 1
        assert saved.launch_profiles[0].id == "a"

    @patch("power_atlas.web.load_config")
    def test_delete_last_profile_rejected(self, mock_load, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile(id="only")])
        resp = client.post("/api/launch-profile/delete", json={"id": "only"})
        assert resp.status_code == 200
        assert "cannot delete" in resp.text.lower()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_delete_active_reassigns(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(
            launch_profiles=[LaunchProfile(id="a"), LaunchProfile(id="b")],
            active_launch_profile="a",
        )
        resp = client.post("/api/launch-profile/delete", json={"id": "a"})
        assert resp.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved.active_launch_profile == "b"


# --- Phase 3 (Launch Profiles): default_args validation ---


class TestDefaultArgsValidation:
    @patch("power_atlas.web.load_config")
    def test_default_args_control_chars_rejected(self, mock_load, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/provider/save", json={
            "provider": "kiro-cli",
            "default_args": "valid\x01evil",
            "color": "",
            "enabled": True,
        })
        assert resp.status_code == 200
        assert "control" in resp.text.lower()

    @patch("power_atlas.web.load_config")
    def test_default_args_too_long_rejected(self, mock_load, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/provider/save", json={
            "provider": "kiro-cli",
            "default_args": "x" * 257,
            "color": "",
            "enabled": True,
        })
        assert resp.status_code == 200
        assert "too long" in resp.text.lower()


# --- Phase 3 (Launch Profiles): Launch profile propagation ---


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_launch_uses_active_profile(mock_load, mock_launch, client, tmp_path):
    """Launch endpoint passes get_active_launch_profile(config) to launcher."""
    from power_atlas.config import Config, LaunchProfile
    from power_atlas.launcher import LaunchResult
    profile = LaunchProfile(id="custom", name="Custom", terminal_command="wt.exe")
    mock_load.return_value = Config(
        launch_profiles=[profile],
        active_launch_profile="custom",
    )
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    call_kwargs = mock_launch.call_args[1]
    assert call_kwargs["launch_profile"].terminal_command == "wt.exe"
    assert call_kwargs["launch_profile"].name == "Custom"


# --- Phase 3 (Launch Profiles): Batch warning aggregation ---


@patch("power_atlas.web.launcher.launch_batch")
@patch("power_atlas.web.load_config")
def test_launch_batch_warning_aggregation(mock_load, mock_batch, client, tmp_path):
    """When all launches succeed but some warn, aggregate into persistent warning toast."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_batch.return_value = [
        LaunchResult(True, None, str(tmp_path), warning="MCP-safe failed; launched directly"),
        LaunchResult(True, None, str(tmp_path)),
        LaunchResult(True, None, str(tmp_path), warning="MCP-safe failed; launched directly"),
    ]
    resp = client.post("/api/launch-batch", json={
        "sessions": [
            {"session_id": "s1", "workspace": str(tmp_path), "provider": "kiro-cli"},
            {"session_id": "s2", "workspace": str(tmp_path), "provider": "kiro-cli"},
            {"session_id": "s3", "workspace": str(tmp_path), "provider": "kiro-cli"},
        ],
    })
    assert resp.status_code == 200
    assert "2 launches used fallback" in resp.text
    assert "toast-persistent" in resp.text
    assert "toast-warning" in resp.text


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_single_launch_warning_persistent(mock_load, mock_launch, client, tmp_path):
    """Single launch with warning renders persistent warning toast."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path), warning="MCP-safe failed")

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    assert "MCP-safe failed" in resp.text
    assert "toast-persistent" in resp.text
    assert "toast-warning" in resp.text


# --- Phase 3 (Launch Profiles): Metacharacter profile name round-trip ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_profile_metacharacter_name_roundtrip(mock_load, mock_save, client):
    """Profile with HTML metacharacters in name survives save round-trip."""
    from power_atlas.config import Config, LaunchProfile
    mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
    xss_name = '<script>alert(1)</script>'
    resp = client.post("/api/launch-profile/save", json={
        "id": "__new__",
        "name": xss_name,
        "terminal_command": "",
        "wt_profile": "PowerShell",
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert len(saved.launch_profiles) == 2
    assert saved.launch_profiles[1].name == xss_name



# --- Phase 4 (findings fixes): disabled provider cards and unknown provider 404 ---


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_disabled_provider_hidden_from_cards(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Disabling a provider hides its workspace cards from the main listing."""
    from power_atlas.config import Config
    ws_kiro = str(tmp_path / "kiro-proj")
    ws_claude = str(tmp_path / "claude-proj")
    mock_config.return_value = Config(provider_settings={
        "claude-code": {"default_args": "", "color": "", "enabled": False},
    })
    mock_discover.return_value = [
        (ws_kiro, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws_claude, 3, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # kiro workspace should be visible
    assert "kiro-proj" in resp.text
    # claude workspace should be hidden because its only provider is disabled
    assert "claude-proj" not in resp.text


def test_get_provider_settings_unknown_404(client):
    """GET /api/provider/bogus returns 404 for unknown providers."""
    resp = client.get("/api/provider/bogus")
    assert resp.status_code == 404


# --- default_directory ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_settings_with_default_directory(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "-a",
        "color": "#ff0000",
        "enabled": True,
        "default_directory": "/home/user/work",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert saved.provider_settings["kiro-cli"]["default_directory"] == "/home/user/work"


@patch("power_atlas.web.load_config")
def test_get_provider_settings_includes_default_directory(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == ""


@patch("power_atlas.web.load_config")
def test_get_provider_settings_returns_saved_default_directory(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a", "color": "", "enabled": True, "default_directory": "/my/path"},
    })

    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert data["default_directory"] == "/my/path"


@patch("power_atlas.web.load_config")
def test_get_provider_settings_legacy_entry_gets_default_directory(mock_load, client):
    """Legacy provider settings without default_directory still return the field."""
    from power_atlas.config import Config
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a", "color": "", "enabled": True},
    })

    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == ""


@patch("power_atlas.web.load_config")
def test_settings_includes_default_directory(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(default_directory="/global/path")

    resp = client.get("/api/settings")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == "/global/path"


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_default_directory(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/save-setting", json={
        "key": "default_directory",
        "value": "/new/default/path",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    saved = mock_save.call_args[0][0]
    assert saved.default_directory == "/new/default/path"


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_default_directory_too_long(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/save-setting", json={
        "key": "default_directory",
        "value": "x" * 513,
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "too long" in body["error"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_default_directory_control_chars(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/save-setting", json={
        "key": "default_directory",
        "value": "/path/\x01bad",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "control characters" in body["error"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_directory_too_long(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "",
        "color": "",
        "enabled": True,
        "default_directory": "x" * 513,
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "too long" in resp.text.lower()
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_directory_control_chars(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "",
        "color": "",
        "enabled": True,
        "default_directory": "/path/\x01bad",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "control characters" in resp.text.lower()
    mock_save.assert_not_called()


# --- Phase 3 (panel restructure): All-sessions endpoint ---


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_basic(mock_paginated, mock_config, client, tmp_path):
    """Basic /partials/all-sessions returns session rows with workspace name."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [(_make_session(cwd=workspace, title="My Session"), "kiro-cli")],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "My Session" in resp.text
    assert tmp_path.name in resp.text  # workspace_name shown
    assert "session-row" in resp.text
    assert "load-more-btn" not in resp.text  # has_more=False


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_pagination(mock_paginated, mock_config, client, tmp_path):
    """Load more button rendered when has_more=True."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [(_make_session(cwd=workspace, title="Page 1 Session"), "kiro-cli")],
        True,
    )
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert "Page 1 Session" in resp.text
    assert "load-more-btn" in resp.text
    assert "loadMoreSessions(2)" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_provider_filter(mock_paginated, mock_config, client, tmp_path):
    """Provider filter is passed through to data layer."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [(_make_session(cwd=workspace, title="Claude Only"), "claude-code")],
        False,
    )
    resp = client.get("/partials/all-sessions?provider=claude-code")
    assert resp.status_code == 200
    assert "Claude Only" in resp.text
    # Verify provider filter was passed
    call_kwargs = mock_paginated.call_args[1]
    assert call_kwargs["provider"] == "claude-code"


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_search(mock_paginated, mock_config, client, tmp_path):
    """Search filter on all-sessions filters by title/prompt/cwd."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="s1", title="matching title"), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="s2", title="other session"), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions?q=matching")
    assert resp.status_code == 200
    assert "matching title" in resp.text
    assert "other session" not in resp.text
    assert "load-more-btn" not in resp.text  # Search disables pagination


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_empty(mock_paginated, mock_config, client):
    """Empty state when no sessions found."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "No sessions found" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_pinned_at_top(mock_paginated, mock_config, client, tmp_path):
    """Pinned sessions appear at top with pin icon."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(pinned_sessions=["pinned-sess"])
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="pinned-sess", title="Pinned One"), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="other-sess", title="Regular One"), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "Pinned One" in resp.text
    assert "Regular One" in resp.text
    # Pinned session has pin indicator
    assert "pinned-indicator" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_tag_filter(mock_paginated, mock_config, client, tmp_path):
    """Tag filter returns only sessions from matching workspaces."""
    from power_atlas.config import Config
    ws_tagged = str(tmp_path / "tagged-proj")
    ws_other = str(tmp_path / "other-proj")
    (tmp_path / "tagged-proj").mkdir()
    (tmp_path / "other-proj").mkdir()
    mock_config.return_value = Config(
        workspace_settings={ws_tagged: {"tags": ["frontend"], "color": ""}}
    )
    mock_paginated.return_value = (
        [
            (_make_session(cwd=ws_tagged, session_id="s1", title="Tagged Session"), "kiro-cli"),
            (_make_session(cwd=ws_other, session_id="s2", title="Other Session"), "kiro-cli"),
        ],
        True,
    )
    resp = client.get("/partials/all-sessions?tag=frontend")
    assert resp.status_code == 200
    assert "Tagged Session" in resp.text
    assert "Other Session" not in resp.text
    assert "load-more-btn" not in resp.text  # tag filter disables pagination


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_hidden_excluded_by_default(mock_paginated, mock_config, client, tmp_path):
    """Sessions from hidden workspaces are excluded by default."""
    from power_atlas.config import Config
    ws_hidden = str(tmp_path / "hidden-proj")
    ws_normal = str(tmp_path / "normal-proj")
    (tmp_path / "hidden-proj").mkdir()
    (tmp_path / "normal-proj").mkdir()
    mock_config.return_value = Config(
        workspace_settings={ws_hidden: {"tags": ["hidden"], "color": ""}}
    )
    mock_paginated.return_value = (
        [
            (_make_session(cwd=ws_hidden, session_id="s1", title="Hidden Session"), "kiro-cli"),
            (_make_session(cwd=ws_normal, session_id="s2", title="Normal Session"), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "Hidden Session" not in resp.text
    assert "Normal Session" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_time_filter(mock_paginated, mock_config, client, tmp_path):
    """Time filter returns only sessions from matching time bucket."""
    from power_atlas.config import Config
    from datetime import datetime, timedelta
    workspace = str(tmp_path)
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    old_iso = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="s1", title="Today Session", updated_at=today_iso), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="s2", title="Old Session", updated_at=old_iso), "kiro-cli"),
        ],
        True,
    )
    resp = client.get("/partials/all-sessions?time_filter=today")
    assert resp.status_code == 200
    assert "Today Session" in resp.text
    assert "Old Session" not in resp.text
    assert "load-more-btn" not in resp.text  # time filter disables pagination


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_time_grouped(mock_paginated, mock_config, client, tmp_path):
    """Sessions panel renders time-group headings."""
    from power_atlas.config import Config
    from datetime import datetime, timedelta
    workspace = str(tmp_path)
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    old_iso = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="s1", title="Today Session", updated_at=today_iso), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="s2", title="Old Session", updated_at=old_iso), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "group-heading" in resp.text
    assert "Today" in resp.text
    assert "Older" in resp.text
    # Empty group headings not rendered
    assert "Yesterday" not in resp.text
    assert "This week" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_pinned_above_time_groups(mock_paginated, mock_config, client, tmp_path):
    """Pinned sessions render above time-group headings with separator."""
    from power_atlas.config import Config
    from datetime import datetime
    workspace = str(tmp_path)
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    mock_config.return_value = Config(pinned_sessions=["pinned-sess"])
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="pinned-sess", title="Pinned One", updated_at=today_iso), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="other-sess", title="Regular One", updated_at=today_iso), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    # Pinned appears before time group headings
    pinned_pos = resp.text.index("Pinned One")
    sep_pos = resp.text.index("pinned-separator")
    heading_pos = resp.text.index("group-heading")
    regular_pos = resp.text.index("Regular One")
    assert pinned_pos < sep_pos < heading_pos < regular_pos


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_tag_empty_state(mock_paginated, mock_config, client, tmp_path):
    """Empty state shows tag-specific message."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    resp = client.get("/partials/all-sessions?tag=frontend")
    assert resp.status_code == 200
    assert "No sessions in workspaces tagged" in resp.text
    assert "frontend" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_time_filter_empty_state(mock_paginated, mock_config, client, tmp_path):
    """Empty state shows time-specific message."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    resp = client.get("/partials/all-sessions?time_filter=today")
    assert resp.status_code == 200
    assert "No sessions active" in resp.text
    assert "today" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspaces_includes_pinned_at_top(mock_discover, mock_config, client, tmp_path):
    """Unified workspaces endpoint shows pinned workspaces at top, non-pinned below."""
    from power_atlas.config import Config
    ws_pinned = str(tmp_path / "pinned-proj")
    ws_other = str(tmp_path / "other-proj")
    mock_config.return_value = Config(pinned_folders=[ws_pinned])
    mock_discover.return_value = [
        (ws_pinned, 3, "2026-01-01T00:00:00Z", "kiro-cli"),
        (ws_other, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
    ]
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Both workspaces appear
    assert "pinned-proj" in resp.text
    assert "other-proj" in resp.text
    # Pinned workspace appears before non-pinned
    assert resp.text.index("pinned-proj") < resp.text.index("other-proj")


# --- Phase 5: open-folder, launch-terminal, terminal tile ---


@patch("power_atlas.web.sys")
@patch("power_atlas.web.os.startfile", create=True)
def test_open_folder_valid_directory(mock_startfile, mock_sys, client, tmp_path):
    """POST /api/open-folder with valid dir returns success toast."""
    mock_sys.platform = "win32"
    folder = str(tmp_path)
    resp = client.post("/api/open-folder", json={"folder": folder})
    assert resp.status_code == 200
    assert "Opened" in resp.text
    assert tmp_path.name in resp.text
    assert "success" in resp.text
    mock_startfile.assert_called_once_with(folder)


def test_open_folder_nonexistent_path(client):
    """POST /api/open-folder with non-existent path returns error toast."""
    resp = client.post("/api/open-folder", json={"folder": "C:\\nonexistent\\xyz\\bogus"})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower() or "error" in resp.text.lower()


@patch("power_atlas.web.launcher.launch_terminal")
@patch("power_atlas.web.load_config")
def test_launch_terminal_success(mock_config, mock_launch, client, tmp_path):
    """POST /api/launch-terminal with valid workspace returns success toast."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    cwd = str(tmp_path)
    mock_config.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, cwd)
    resp = client.post("/api/launch-terminal", json={"workspace": cwd})
    assert resp.status_code == 200
    assert "Terminal opened" in resp.text
    assert tmp_path.name in resp.text
    assert "success" in resp.text
    mock_launch.assert_called_once()


@patch("power_atlas.web.load_config")
def test_launch_terminal_no_workspace_no_default(mock_config, client):
    """POST /api/launch-terminal with no workspace and no default_directory falls back to home dir."""
    from power_atlas.config import Config
    mock_config.return_value = Config(default_directory="")
    with patch("power_atlas.web.launcher.launch_terminal") as mock_launch:
        mock_launch.return_value = launcher.LaunchResult(True, None, str(Path.home()))
        resp = client.post("/api/launch-terminal", json={})
        assert resp.status_code == 200
        assert "success" in resp.text
        # Verify it was called with the home directory
        mock_launch.assert_called_once()
        assert mock_launch.call_args[0][0] == str(Path.home())


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_partials_launchers_includes_terminal_tile(mock_load, mock_providers, client):
    """Launcher grid includes the builtin terminal tile."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_providers.return_value = ["kiro-cli"]
    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    assert 'data-id="builtin--terminal"' in resp.text


@patch("power_atlas.web.sys")
@patch("power_atlas.web.os.startfile", create=True)
def test_open_folder_oserror(mock_startfile, mock_sys, client, tmp_path):
    """POST /api/open-folder returns error toast when os.startfile raises OSError."""
    mock_sys.platform = "win32"
    mock_startfile.side_effect = OSError("Permission denied")
    folder = str(tmp_path)
    resp = client.post("/api/open-folder", json={"folder": folder})
    assert resp.status_code == 200
    assert "Could not open folder" in resp.text
    assert "error" in resp.text


def test_open_folder_empty_path(client):
    """POST /api/open-folder with empty folder returns error toast."""
    resp = client.post("/api/open-folder", json={"folder": ""})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower() or "error" in resp.text.lower()


@patch("power_atlas.web.launcher.launch_terminal")
@patch("power_atlas.web.load_config")
def test_launch_terminal_failure_result(mock_config, mock_launch, client, tmp_path):
    """POST /api/launch-terminal returns error toast when launcher returns failure."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    cwd = str(tmp_path)
    mock_config.return_value = Config()
    mock_launch.return_value = LaunchResult(False, None, cwd, error="No terminal found")
    resp = client.post("/api/launch-terminal", json={"workspace": cwd})
    assert resp.status_code == 200
    assert "No terminal found" in resp.text
    assert "error" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_separator_present_when_both_groups(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Separator div appears between pinned and non-pinned workspace cards."""
    from power_atlas.config import Config
    pinned_ws = str(tmp_path / "pinned-proj")
    other_ws = str(tmp_path / "other-proj")
    mock_config.return_value = Config(pinned_folders=[pinned_ws])
    mock_discover.return_value = [
        (pinned_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (other_ws, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_separator_absent_when_only_pinned(mock_discover, mock_providers, mock_config, client, tmp_path):
    """No separator when all workspaces are pinned (no non-pinned group)."""
    from power_atlas.config import Config
    pinned_ws = str(tmp_path / "pinned-proj")
    mock_config.return_value = Config(pinned_folders=[pinned_ws])
    mock_discover.return_value = [
        (pinned_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_separator_absent_when_no_pinned(mock_discover, mock_providers, mock_config, client, tmp_path):
    """No separator when no workspaces are pinned."""
    from power_atlas.config import Config
    other_ws = str(tmp_path / "other-proj")
    mock_config.return_value = Config(pinned_folders=[])
    mock_discover.return_value = [
        (other_ws, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' not in resp.text



@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_session_pinned_separator_present(mock_paginated, mock_config, client, tmp_path):
    """Separator div appears between pinned and non-pinned sessions on page 1."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    pinned_session = _make_session(session_id="pinned-1", title="Pinned", cwd=workspace)
    other_session = _make_session(session_id="other-1", title="Other", cwd=workspace)
    mock_config.return_value = Config(pinned_sessions=["pinned-1"])
    mock_paginated.return_value = (
        [(pinned_session, "kiro-cli"), (other_session, "kiro-cli")],
        False,
    )
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_session_pinned_separator_absent_no_pinned(mock_paginated, mock_config, client, tmp_path):
    """No separator when no sessions are pinned."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    session = _make_session(session_id="s1", title="Regular", cwd=workspace)
    mock_config.return_value = Config(pinned_sessions=[])
    mock_paginated.return_value = (
        [(session, "kiro-cli")],
        False,
    )
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' not in resp.text



# --- Phase 2 (Workspace Tags): Workspace settings API ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_workspace_settings_save_roundtrip(mock_load, mock_save, client, tmp_path):
    """POST /api/workspace-settings/save persists tags and color to config."""
    from power_atlas.config import Config
    cwd = str(tmp_path)
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": cwd,
        "tags": ["frontend", "active"],
        "color": "#3b82f6",
    })
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert saved.workspace_settings[cwd]["tags"] == ["frontend", "active"]
    assert saved.workspace_settings[cwd]["color"] == "#3b82f6"


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_empty_cwd_rejected(mock_load, client):
    """Empty cwd is rejected with error toast."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": "",
        "tags": ["test"],
        "color": "",
    })
    assert resp.status_code == 200
    assert "invalid" in resp.text.lower() or "error" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_too_many_tags(mock_load, client, tmp_path):
    """More than 10 tags is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": str(tmp_path),
        "tags": ["t" + str(i) for i in range(11)],
        "color": "",
    })
    assert resp.status_code == 200
    assert "max 10" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_tag_too_long(mock_load, client, tmp_path):
    """Tag over 64 chars is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": str(tmp_path),
        "tags": ["x" * 65],
        "color": "",
    })
    assert resp.status_code == 200
    assert "invalid tag" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_tag_control_chars(mock_load, client, tmp_path):
    """Tag with control characters is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": str(tmp_path),
        "tags": ["bad\x01tag"],
        "color": "",
    })
    assert resp.status_code == 200
    assert "invalid tag" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_get_returns_defaults(mock_load, client, tmp_path):
    """GET /api/workspace-settings returns empty defaults for unknown workspace."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.get("/api/workspace-settings", params={"cwd": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["tags"] == []
    assert body["settings"]["color"] == ""
    assert body["all_tags"] == []


@patch("power_atlas.web.load_config")
def test_workspace_settings_get_returns_saved_data(mock_load, client, tmp_path):
    """GET /api/workspace-settings returns previously saved tags and color."""
    from power_atlas.config import Config
    cwd = str(tmp_path)
    mock_load.return_value = Config(
        workspace_settings={cwd: {"tags": ["dev", "active"], "color": "#ef4444"}},
        tag_settings={"archived": {"color": "#64748b"}},
    )
    resp = client.get("/api/workspace-settings", params={"cwd": cwd})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["tags"] == ["dev", "active"]
    assert body["settings"]["color"] == "#ef4444"
    # all_tags includes both workspace tags and tag_settings keys
    assert "dev" in body["all_tags"]
    assert "active" in body["all_tags"]
    assert "archived" in body["all_tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_workspace_settings_save_deduplicates_normalized_path(mock_load, mock_save, client, tmp_path):
    """Saving settings deduplicates paths that normalize to the same value."""
    from power_atlas.config import Config
    cwd_lower = str(tmp_path).lower()
    cwd_upper = str(tmp_path).upper()
    mock_load.return_value = Config(
        workspace_settings={cwd_lower: {"tags": ["old"], "color": ""}},
    )
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": cwd_upper,
        "tags": ["new"],
        "color": "#22c55e",
    })
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    # Old key should be removed, new key used
    assert cwd_lower not in saved.workspace_settings
    assert saved.workspace_settings[cwd_upper]["tags"] == ["new"]
    assert saved.workspace_settings[cwd_upper]["color"] == "#22c55e"



# --- Phase 3 (Workspace Tags): Color precedence ---


class TestResolveWorkspaceColor:
    """Test _resolve_workspace_color precedence: explicit > tag color > empty."""

    def test_explicit_workspace_color_wins(self):
        """Workspace with explicit color returns that color regardless of tags."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["active"], "color": "#ff0000"}},
            tag_settings={"active": {"color": "#00ff00"}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == "#ff0000"

    def test_tag_color_used_when_no_explicit_color(self):
        """Workspace with no explicit color but tagged with colored tag uses tag's color."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["frontend", "active"], "color": ""}},
            tag_settings={"frontend": {"color": "#3b82f6"}, "active": {"color": "#22c55e"}},
        )
        # First tag's color wins
        assert _resolve_workspace_color("C:\\proj", config) == "#3b82f6"

    def test_empty_when_no_color_and_no_colored_tags(self):
        """Workspace with no color and uncolored tags returns empty (provider gradient)."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["uncolored"], "color": ""}},
            tag_settings={"uncolored": {"color": ""}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == ""

    def test_empty_when_no_workspace_settings(self):
        """Unknown workspace returns empty (provider gradient)."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config()
        assert _resolve_workspace_color("C:\\unknown", config) == ""

    def test_skips_uncolored_tags_uses_first_colored(self):
        """Skips tags without color, uses first tag that has a color."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["nocolor", "hascolor"], "color": ""}},
            tag_settings={"nocolor": {"color": ""}, "hascolor": {"color": "#ef4444"}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == "#ef4444"

    def test_tag_not_in_tag_settings_skipped(self):
        """Tags not defined in tag_settings are skipped gracefully."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["undefined-tag", "defined"], "color": ""}},
            tag_settings={"defined": {"color": "#abc123"}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == "#abc123"


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_uses_workspace_color_over_provider(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspace with explicit color renders that color instead of provider gradient."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(
        workspace_settings={workspace: {"tags": [], "color": "#e11d48"}},
    )
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Workspace color is used instead of provider default
    assert "#e11d48" in resp.text
    assert "provider-gradient" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_uses_tag_color_when_no_explicit(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspace with no explicit color but colored tag uses tag's color."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(
        workspace_settings={workspace: {"tags": ["frontend"], "color": ""}},
        tag_settings={"frontend": {"color": "#3b82f6"}},
    )
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "#3b82f6" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_falls_through_to_provider_gradient(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspace with no color and no colored tags shows provider gradient."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(
        workspace_settings={workspace: {"tags": ["plain"], "color": ""}},
        tag_settings={"plain": {"color": ""}},
    )
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Provider color is used (kiro-cli default)
    assert "#7138cc" in resp.text



# --- Phase 5 (Workspace Tags): Filters, time bucketing, group-by, /api/tags ---


class TestTimeBucket:
    """Unit tests for the _time_bucket helper."""

    def test_empty_string_returns_before(self):
        from power_atlas.web import _time_bucket
        assert _time_bucket("") == "before"

    def test_invalid_iso_returns_before(self):
        from power_atlas.web import _time_bucket
        assert _time_bucket("not-a-date") == "before"

    def test_today_timestamp(self):
        from datetime import datetime, timezone
        from power_atlas.web import _time_bucket
        now = datetime.now(timezone.utc).isoformat()
        assert _time_bucket(now) == "today"

    def test_yesterday_timestamp(self):
        from datetime import datetime, timezone, timedelta
        from power_atlas.web import _time_bucket
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert _time_bucket(yesterday) == "yesterday"

    def test_this_week_timestamp(self):
        from datetime import datetime, timezone, timedelta, date
        from power_atlas.web import _time_bucket
        today = date.today()
        # Monday of this week
        monday = today - timedelta(days=today.weekday())
        if monday == today or monday == today - timedelta(days=1):
            # If today is Monday or Tuesday, skip — Monday/Tuesday map to today/yesterday
            pytest.skip("Can't test this_week on Mon/Tue without hitting today/yesterday")
        monday_dt = datetime(monday.year, monday.month, monday.day, 12, 0, 0, tzinfo=timezone.utc)
        assert _time_bucket(monday_dt.isoformat()) == "this_week"

    def test_old_timestamp_returns_before(self):
        from power_atlas.web import _time_bucket
        assert _time_bucket("2020-01-01T00:00:00Z") == "before"

    def test_z_suffix_handled(self):
        from datetime import datetime, timezone
        from power_atlas.web import _time_bucket
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _time_bucket(now) == "today"


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_hidden_tag_excluded_by_default(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspaces tagged 'hidden' are excluded from default view."""
    from power_atlas.config import Config
    visible_ws = str(tmp_path / "visible-proj")
    hidden_ws = str(tmp_path / "hidden-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (visible_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "visible-proj" in resp.text
    assert "hidden-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_hidden_tag_filter_reveals_hidden(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Selecting 'hidden' tag filter reveals only hidden workspaces."""
    from power_atlas.config import Config
    visible_ws = str(tmp_path / "visible-proj")
    hidden_ws = str(tmp_path / "hidden-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (visible_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?tag=hidden")
    assert resp.status_code == 200
    assert "hidden-proj" in resp.text
    assert "visible-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tag_filter_shows_matching_workspaces(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Tag filter shows only workspaces that have the selected tag."""
    from power_atlas.config import Config
    frontend_ws = str(tmp_path / "frontend-proj")
    backend_ws = str(tmp_path / "backend-proj")
    mock_config.return_value = Config(
        workspace_settings={
            frontend_ws: {"tags": ["frontend"], "color": ""},
            backend_ws: {"tags": ["backend"], "color": ""},
        },
    )
    mock_discover.return_value = [
        (frontend_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (backend_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?tag=frontend")
    assert resp.status_code == 200
    assert "frontend-proj" in resp.text
    assert "backend-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_time_filter_today(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Time filter 'today' shows only workspaces updated today."""
    from datetime import datetime, timezone
    from power_atlas.config import Config
    today_ws = str(tmp_path / "today-proj")
    old_ws = str(tmp_path / "old-proj")
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (today_ws, 1, now_iso, "kiro-cli"),
        (old_ws, 1, "2020-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?time_filter=today")
    assert resp.status_code == 200
    assert "today-proj" in resp.text
    assert "old-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tag_filter_empty_state(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Tag filter with no matches shows appropriate empty state."""
    from power_atlas.config import Config
    ws = str(tmp_path / "proj")
    mock_config.return_value = Config(
        workspace_settings={ws: {"tags": ["backend"], "color": ""}},
    )
    mock_discover.return_value = [(ws, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?tag=nonexistent")
    assert resp.status_code == 200
    assert "No workspaces with tag" in resp.text
    assert "nonexistent" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_time_filter_empty_state(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Time filter with no matches shows appropriate empty state."""
    from power_atlas.config import Config
    ws = str(tmp_path / "proj")
    mock_config.return_value = Config()
    mock_discover.return_value = [(ws, 1, "2020-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?time_filter=today")
    assert resp.status_code == 200
    assert "No workspaces active" in resp.text
    assert "today" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_default_time_grouping_renders_headings(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Default rendering always time-groups with Today/Yesterday/This week/Older headings."""
    from datetime import datetime, timezone, timedelta
    from power_atlas.config import Config
    today_ws = str(tmp_path / "today-proj")
    old_ws = str(tmp_path / "old-proj")
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (today_ws, 1, now_iso, "kiro-cli"),
        (old_ws, 1, "2020-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="group-heading"' in resp.text
    assert "Today" in resp.text
    assert "Older" in resp.text
    assert "today-proj" in resp.text
    assert "old-proj" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_filters_compose_with_provider(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Tag filter composes with provider filter (AND logic)."""
    from power_atlas.config import Config
    kiro_ws = str(tmp_path / "kiro-proj")
    claude_ws = str(tmp_path / "claude-proj")
    mock_config.return_value = Config(
        workspace_settings={
            kiro_ws: {"tags": ["active"], "color": ""},
            claude_ws: {"tags": ["active"], "color": ""},
        },
    )
    mock_discover.return_value = [
        (kiro_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (claude_ws, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Tag=active AND provider=kiro-cli — only kiro workspace
    resp = client.get("/partials/workspaces?tag=active&provider=kiro-cli")
    assert resp.status_code == 200
    assert "kiro-proj" in resp.text
    assert "claude-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_hidden_filter_applies_to_pinned(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Pinned workspaces tagged 'hidden' are also excluded from default view."""
    from power_atlas.config import Config
    hidden_pinned_ws = str(tmp_path / "hidden-pinned")
    visible_ws = str(tmp_path / "visible-proj")
    mock_config.return_value = Config(
        pinned_folders=[hidden_pinned_ws],
        workspace_settings={hidden_pinned_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (hidden_pinned_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (visible_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "hidden-pinned" not in resp.text
    assert "visible-proj" in resp.text


# --- /api/tags endpoint ---


@patch("power_atlas.web.load_config")
def test_api_tags_returns_tag_list(mock_load, client, tmp_path):
    """GET /api/tags returns all tags with colors and counts."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["frontend", "active"], "color": ""},
            ws2: {"tags": ["frontend", "backend"], "color": ""},
        },
        tag_settings={
            "frontend": {"color": "#3b82f6"},
            "archived": {"color": "#64748b"},
        },
    )
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    tags = resp.json()
    tag_map = {t["name"]: t for t in tags}
    assert "frontend" in tag_map
    assert tag_map["frontend"]["count"] == 2
    assert tag_map["frontend"]["color"] == "#3b82f6"
    assert "active" in tag_map
    assert tag_map["active"]["count"] == 1
    assert tag_map["active"]["color"] == ""
    assert "backend" in tag_map
    assert tag_map["backend"]["count"] == 1
    # tag_settings tag with 0 workspaces still appears
    assert "archived" in tag_map
    assert tag_map["archived"]["count"] == 0
    assert tag_map["archived"]["color"] == "#64748b"


@patch("power_atlas.web.load_config")
def test_api_tags_empty_when_no_tags(mock_load, client):
    """GET /api/tags returns empty list when no tags configured."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    assert resp.json() == []


# --- Search endpoint with filters ---


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_excludes_hidden_by_default(mock_discover, mock_config, client, tmp_path):
    """Search results exclude hidden workspaces by default."""
    from power_atlas.config import Config
    hidden_ws = str(tmp_path / "hidden-proj")
    visible_ws = str(tmp_path / "visible-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (visible_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    # Search for "proj" which matches both
    resp = client.get(f"/search?q=proj")
    assert resp.status_code == 200
    assert "visible-proj" in resp.text
    assert "hidden-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_with_tag_filter(mock_discover, mock_config, client, tmp_path):
    """Search results respect tag filter."""
    from power_atlas.config import Config
    frontend_ws = str(tmp_path / "frontend-proj")
    backend_ws = str(tmp_path / "backend-proj")
    mock_config.return_value = Config(
        workspace_settings={
            frontend_ws: {"tags": ["frontend"], "color": ""},
            backend_ws: {"tags": ["backend"], "color": ""},
        },
    )
    mock_discover.return_value = [
        (frontend_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (backend_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    resp = client.get("/search?q=proj&tag=frontend")
    assert resp.status_code == 200
    assert "frontend-proj" in resp.text
    assert "backend-proj" not in resp.text


def _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path):
    """Three same-named workspaces with different liveness and provider shapes.

    ``mixed-proj`` is registered under both providers but only claude-code is
    running in it, which is what separates a provider-scoped status query from
    an unscoped one.
    """
    from power_atlas.config import Config
    live_ws = str(tmp_path / "live-proj")
    dead_ws = str(tmp_path / "dead-proj")
    mixed_ws = str(tmp_path / "mixed-proj")
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (live_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (dead_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (mixed_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (mixed_ws, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_snap.return_value = _snapshot(live_cwds={
        ("kiro-cli", _normalize_path(live_ws)),
        ("claude-code", _normalize_path(mixed_ws)),
    })
    mock_sessions.return_value = []
    return live_ws, dead_ws, mixed_ws


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_with_status_filter(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """Search results respect status filter (regression: surplus arg 500'd the endpoint)."""
    _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path)

    resp = client.get("/search?q=proj&status=working")
    assert resp.status_code == 200
    assert "live-proj" in resp.text
    assert "mixed-proj" in resp.text
    assert "dead-proj" not in resp.text
    assert mock_snap.call_count == 1

    # status=live goes through the _LIVE_STATUSES branch of _status_matches.
    resp = client.get("/search?q=proj&status=live")
    assert resp.status_code == 200
    assert "live-proj" in resp.text
    assert "mixed-proj" in resp.text
    assert "dead-proj" not in resp.text
    assert mock_snap.call_count == 2

    # Provider-scoped: mixed-proj is a kiro-cli workspace whose only running
    # process is claude-code, so it survives this filter exactly when
    # _workspace_status is asked about every provider instead of kiro-cli.
    resp = client.get("/search?q=proj&provider=kiro-cli&status=working")
    assert resp.status_code == 200
    assert "live-proj" in resp.text
    assert "mixed-proj" not in resp.text
    assert "dead-proj" not in resp.text
    assert mock_snap.call_count == 3

    # status=all and the no-status path both skip the presence scan entirely.
    for url in ("/search?q=proj&status=all", "/search?q=proj"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert "live-proj" in resp.text
        assert "dead-proj" in resp.text
    assert mock_snap.call_count == 3


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_names_the_filter(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """A filter that removes every match says which filter, as /partials/workspaces does."""
    _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path)

    resp = client.get("/search?q=proj&status=errored")
    assert resp.status_code == 200
    assert "No errored workspaces right now." in resp.text
    assert "No results for" not in resp.text

    resp = client.get("/search?q=proj&tag=nope")
    assert resp.status_code == 200
    assert "No workspaces with tag" in resp.text and "nope" in resp.text

    resp = client.get("/search?q=proj&time_filter=yesterday")
    assert resp.status_code == 200
    assert "No workspaces active yesterday" in resp.text

    # A query that matches nothing at all still reports the query, not a
    # filter — including when a filter is active, since no filter is at fault.
    for url in ("/search?q=nothingmatchesthis",
                "/search?q=nothingmatchesthis&status=working",
                "/search?q=nothingmatchesthis&tag=nope"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert "No results for" in resp.text, url
        assert "workspaces right now" not in resp.text, url


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_when_hidden_tag_removes_every_match(
        mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """The default hidden-tag filter can empty a non-empty match set.

    That is the only route to the cascade's last branch: the query matched,
    but no explicit filter was named, so the wording falls back to the query.
    """
    from power_atlas.config import Config
    hidden_ws = str(tmp_path / "hidden-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [(hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_snap.return_value = _snapshot()
    mock_sessions.return_value = []

    resp = client.get("/search?q=proj")
    assert resp.status_code == 200
    assert "No results for" in resp.text and "proj" in resp.text
    assert "hidden-proj" not in resp.text
    assert "workspaces right now" not in resp.text
    # Asking for the hidden tag explicitly shows the same workspace.
    resp = client.get("/search?q=proj&tag=hidden")
    assert resp.status_code == 200
    assert "hidden-proj" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_names_the_provider(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """A query that only matches other providers' workspaces says so.

    /partials/workspaces answers "no sessions found — start one with claude",
    which would be a lie here: the provider may well have sessions, just none
    matching the query.
    """
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (str(tmp_path / "kiro-only-proj"), 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_snap.return_value = _snapshot()
    mock_sessions.return_value = []

    resp = client.get("/search?q=proj&provider=claude-code")
    assert resp.status_code == 200
    assert "No Claude Code results for" in resp.text
    assert "proj" in resp.text
    assert "start one with" not in resp.text

    # An unknown provider degrades to its raw name rather than erroring.
    resp = client.get("/search?q=proj&provider=bogus")
    assert resp.status_code == 200
    assert "No bogus results for" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_escapes_input(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """Filter values are reflected in the empty state, so they must be escaped."""
    _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path)

    resp = client.get("/search", params={"q": "proj", "status": "<b>x</b>"})
    assert resp.status_code == 200
    assert "<b>x</b>" not in resp.text
    assert "&lt;b&gt;x" in resp.text


# --- Phase 6 (Tag Color Management): /api/tag/save ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_save_persists_color(mock_load, mock_save, client, tmp_path):
    """POST /api/tag/save persists color to tag_settings in config."""
    from power_atlas.config import Config
    config = Config(tag_settings={})
    mock_load.return_value = config

    resp = client.post("/api/tag/save", json={"tag": "frontend", "color": "#3b82f6"})
    assert resp.status_code == 200
    assert "Tag color saved" in resp.text

    saved = mock_save.call_args[0][0]
    assert saved.tag_settings["frontend"] == {"color": "#3b82f6"}


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_save_empty_color_clears(mock_load, mock_save, client):
    """POST /api/tag/save with empty color clears the tag's color."""
    from power_atlas.config import Config
    config = Config(tag_settings={"frontend": {"color": "#3b82f6"}})
    mock_load.return_value = config

    resp = client.post("/api/tag/save", json={"tag": "frontend", "color": ""})
    assert resp.status_code == 200
    assert "Tag color saved" in resp.text

    saved = mock_save.call_args[0][0]
    assert saved.tag_settings["frontend"] == {"color": ""}


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_empty_name(mock_load, client):
    """POST /api/tag/save rejects empty tag name."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "", "color": "#ff0000"})
    assert resp.status_code == 200
    assert "Invalid tag name" in resp.text


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_long_name(mock_load, client):
    """POST /api/tag/save rejects tag name > 64 chars."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "a" * 65, "color": "#ff0000"})
    assert resp.status_code == 200
    assert "Invalid tag name" in resp.text


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_control_chars_in_name(mock_load, client):
    """POST /api/tag/save rejects tag name with control characters."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "bad\x01tag", "color": "#ff0000"})
    assert resp.status_code == 200
    assert "Invalid tag name" in resp.text


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_invalid_color(mock_load, client):
    """POST /api/tag/save rejects color with control characters."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "frontend", "color": "\x01red"})
    assert resp.status_code == 200
    assert "Invalid color value" in resp.text


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_save_appears_in_api_tags(mock_load, mock_save, client, tmp_path):
    """Saved tag color is reflected in subsequent /api/tags response."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    config = Config(
        workspace_settings={ws1: {"tags": ["frontend"], "color": ""}},
        tag_settings={"frontend": {"color": "#3b82f6"}},
    )
    mock_load.return_value = config

    resp = client.get("/api/tags")
    assert resp.status_code == 200
    tags = resp.json()
    tag_map = {t["name"]: t for t in tags}
    assert tag_map["frontend"]["color"] == "#3b82f6"


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_delete_removes_globally(mock_load, mock_save, client, tmp_path):
    """POST /api/tag/delete removes tag from tag_settings and all workspace assignments."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    config = Config(
        tag_settings={"frontend": {"color": "#3b82f6"}, "backend": {"color": "#10b981"}},
        workspace_settings={
            ws1: {"tags": ["frontend", "backend"], "color": ""},
            ws2: {"tags": ["frontend"], "color": ""},
        },
    )
    mock_load.return_value = config

    resp = client.post("/api/tag/delete", json={"tag": "frontend"})
    assert resp.status_code == 200
    assert "deleted" in resp.text
    assert "2 workspace" in resp.text

    saved = mock_save.call_args[0][0]
    assert "frontend" not in saved.tag_settings
    assert "backend" in saved.tag_settings
    assert "frontend" not in saved.workspace_settings[ws1]["tags"]
    assert "backend" in saved.workspace_settings[ws1]["tags"]
    assert "frontend" not in saved.workspace_settings[ws2]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_delete_hidden_protected(mock_load, mock_save, client):
    """POST /api/tag/delete rejects deletion of the 'hidden' tag."""
    from power_atlas.config import Config
    mock_load.return_value = Config(tag_settings={"hidden": {"color": ""}})

    resp = client.post("/api/tag/delete", json={"tag": "hidden"})
    assert resp.status_code == 200
    assert "Cannot delete" in resp.text
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_delete_nonexistent_succeeds(mock_load, mock_save, client):
    """POST /api/tag/delete succeeds gracefully for a tag that doesn't exist."""
    from power_atlas.config import Config
    config = Config(tag_settings={"existing": {"color": "#fff"}})
    mock_load.return_value = config

    resp = client.post("/api/tag/delete", json={"tag": "nonexistent"})
    assert resp.status_code == 200
    assert "not found" in resp.text
    mock_save.assert_not_called()


# --- Bulk workspace settings tests ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_adds_tags_to_multiple(mock_load, mock_save, client, tmp_path):
    """POST /api/workspace-settings/save-bulk adds a tag to multiple workspaces."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "project1")
    ws2 = str(tmp_path / "project2")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["existing"], "color": ""},
            ws2: {"tags": [], "color": "#aaa"},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws1, ws2],
        "tags_add": ["new-tag"],
        "tags_remove": [],
    })
    assert resp.status_code == 200
    assert "updated 2 workspace" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert "new-tag" in saved.workspace_settings[ws1]["tags"]
    assert "existing" in saved.workspace_settings[ws1]["tags"]
    assert "new-tag" in saved.workspace_settings[ws2]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_partial_success_10_tag_limit(mock_load, mock_save, client, tmp_path):
    """Bulk add returns warning when a workspace hits the 10-tag limit."""
    from power_atlas.config import Config
    ws_full = str(tmp_path / "full")
    ws_empty = str(tmp_path / "empty")
    mock_load.return_value = Config(
        workspace_settings={
            ws_full: {"tags": [f"t{i}" for i in range(10)], "color": ""},
            ws_empty: {"tags": [], "color": ""},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws_full, ws_empty],
        "tags_add": ["overflow"],
        "tags_remove": [],
    })
    assert resp.status_code == 200
    assert "warning" in resp.text.lower() or "10-tag limit" in resp.text.lower()
    assert "1 hit 10-tag limit" in resp.text
    saved = mock_save.call_args[0][0]
    # Full workspace should NOT have the new tag
    assert "overflow" not in saved.workspace_settings[ws_full]["tags"]
    # Empty workspace should have it
    assert "overflow" in saved.workspace_settings[ws_empty]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_removes_tags(mock_load, mock_save, client, tmp_path):
    """Bulk remove strips a tag from multiple workspaces."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "a")
    ws2 = str(tmp_path / "b")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["old", "keep"], "color": ""},
            ws2: {"tags": ["old"], "color": ""},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws1, ws2],
        "tags_add": [],
        "tags_remove": ["old"],
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "old" not in saved.workspace_settings[ws1]["tags"]
    assert "keep" in saved.workspace_settings[ws1]["tags"]
    assert "old" not in saved.workspace_settings[ws2]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_color_applies_to_all(mock_load, mock_save, client, tmp_path):
    """Bulk save sets color on all specified workspaces."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "x")
    ws2 = str(tmp_path / "y")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": [], "color": ""},
            ws2: {"tags": [], "color": "#old"},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws1, ws2],
        "tags_add": [],
        "tags_remove": [],
        "color": "#new123",
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert saved.workspace_settings[ws1]["color"] == "#new123"
    assert saved.workspace_settings[ws2]["color"] == "#new123"


@patch("power_atlas.web.load_config")
def test_bulk_save_add_remove_overlap_rejected(mock_load, client, tmp_path):
    """Overlapping tags_add and tags_remove returns error toast."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [str(tmp_path)],
        "tags_add": ["conflict"],
        "tags_remove": ["conflict"],
    })
    assert resp.status_code == 200
    assert "must not overlap" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_bulk_get_returns_multiple(mock_load, client, tmp_path):
    """POST /api/workspace-settings-bulk returns settings for multiple cwds."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["frontend"], "color": "#111"},
            ws2: {"tags": ["backend"], "color": "#222"},
        },
        tag_settings={"archived": {"color": "#999"}},
    )
    resp = client.post("/api/workspace-settings-bulk", json={
        "cwds": [ws1, ws2],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspaces"][ws1]["tags"] == ["frontend"]
    assert body["workspaces"][ws1]["color"] == "#111"
    assert body["workspaces"][ws2]["tags"] == ["backend"]
    assert body["workspaces"][ws2]["color"] == "#222"
    # all_tags includes workspace tags + tag_settings keys
    assert "frontend" in body["all_tags"]
    assert "backend" in body["all_tags"]
    assert "archived" in body["all_tags"]


# --- Live-session status: helpers, filtering, dots ---

from datetime import datetime, timezone, timedelta
from power_atlas import presence
from power_atlas.web import _session_status, _workspace_status, _status_matches
from power_atlas.data import _normalize_path


def _recent_iso(secs=5):
    return (datetime.now(timezone.utc) - timedelta(seconds=secs)).isoformat()


def _old_iso(mins=10):
    return (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()


def _snapshot(live_sids=(), live_cwds=(), sid_to_cwd=None, sid_status=None):
    return presence.Snapshot(set(live_sids), set(live_cwds), sid_to_cwd or {},
                             sid_status or {})


def test_status_matches_semantics():
    assert _status_matches("", "closed") is True          # no filter
    assert _status_matches("all", "waiting") is True
    assert _status_matches("live", "working") is True
    assert _status_matches("live", "waiting") is True
    assert _status_matches("live", "errored") is True
    assert _status_matches("live", "closed") is False
    assert _status_matches("working", "working") is True
    assert _status_matches("working", "waiting") is False


@patch("power_atlas.web.get_semantic_status")
def test_session_status_semantic_and_fallback(mock_semantic):
    """Semantic status is returned when available; waiting fallback when None."""
    from power_atlas.status_classifier import SemanticStatus
    live = _snapshot(live_sids={("claude-code", "s1")})
    recent_s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    closed_s = _make_session(session_id="s2", cwd="/w", updated_at=_recent_iso())

    # Semantic path: returns classifier result
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(live, recent_s, "claude-code") == "waiting"
    mock_semantic.return_value = SemanticStatus.ERRORED
    assert _session_status(live, recent_s, "claude-code") == "errored"

    # Fallback path: classifier returns None -> working (process running, can't classify)
    mock_semantic.return_value = None
    assert _session_status(live, recent_s, "claude-code") == "working"

    # Closed: not live at all
    assert _session_status(live, closed_s, "claude-code") == "closed"


@patch("power_atlas.status_classifier._resolve_jsonl_path")
@patch("power_atlas.web.get_semantic_status")
def test_session_status_cwd_based_no_resume_id(mock_semantic, mock_resolve):
    """CWD-based detection: a process in the cwd is enough to classify as live."""
    from power_atlas.status_classifier import SemanticStatus
    import tempfile, os
    # Create a temp file with recent mtime to pass the recency gate
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    mock_resolve.return_value = tmp.name
    try:
        # Snapshot: process running in /w but no session id matched (no --resume-id)
        snap = _snapshot(live_cwds={("claude-code", _normalize_path("/w"))})
        s = _make_session(session_id="sess1", cwd="/w", updated_at=_recent_iso())

        # Semantic classifier returns WORKING
        mock_semantic.return_value = SemanticStatus.WORKING
        assert _session_status(snap, s, "claude-code") == "working"

        # Semantic classifier returns WAITING
        mock_semantic.return_value = SemanticStatus.WAITING
        assert _session_status(snap, s, "claude-code") == "waiting"

        # Semantic classifier returns None + live process → fallback to "working"
        mock_semantic.return_value = None
        assert _session_status(snap, s, "claude-code") == "working"
    finally:
        os.unlink(tmp.name)

    # Different provider → no process in cwd → closed
    snap_kiro = _snapshot(live_cwds={("kiro-cli", _normalize_path("/w"))})
    assert _session_status(snap_kiro, s, "claude-code") == "closed"


def test_workspace_status_cwd_and_provider_scoped():
    snap = _snapshot(live_cwds={("claude-code", _normalize_path("/w"))})
    assert _workspace_status(snap, "/w", {"claude-code"}) == "working"
    # provider filter excludes the live claude process
    assert _workspace_status(snap, "/w", {"kiro-cli"}) == "closed"
    # different folder is closed
    assert _workspace_status(snap, "/other", None) == "closed"


def _tracked_snapshot(reported):
    """Snapshot with one tracked claude-code session in /w reporting `reported`."""
    norm = _normalize_path("/w")
    return _snapshot(
        live_sids={("claude-code", "s1")},
        live_cwds={("claude-code", norm)},
        sid_to_cwd={("claude-code", "s1"): norm},
        sid_status={("claude-code", "s1"): reported},
    )


@patch("power_atlas.web.get_semantic_status")
def test_session_status_report_outranks_classifier(mock_semantic):
    """A non-empty report is first-hand and current, so it beats the tail read.

    Asserted here rather than on the card, where "busy"/"shell" map onto the
    same "working" the aggregate already seeds itself with and so could not
    tell a folded-in report from an ignored one.
    """
    from power_atlas.status_classifier import SemanticStatus
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.ERRORED

    # busy/shell mean a turn is running, so even an errored tail loses.
    assert _session_status(_tracked_snapshot("busy"), s, "claude-code") == "working"
    assert _session_status(_tracked_snapshot("shell"), s, "claude-code") == "working"
    # "idle" carries no verdict, and no report at all carries none either:
    # both leave the classifier in charge.
    assert _session_status(_tracked_snapshot("idle"), s, "claude-code") == "errored"
    assert _session_status(_tracked_snapshot(""), s, "claude-code") == "errored"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_reads_provider_report(mock_semantic, mock_sessions):
    """The card dot honours the provider's own report, like per-session status."""
    mock_sessions.return_value = []
    mock_semantic.return_value = None

    # Sole signal is the report: an unclassifiable tail still shows waiting.
    assert _workspace_status(_tracked_snapshot("waiting"), "/w",
                             {"claude-code"}) == "waiting"
    # "idle" is ambiguous (finished/errored/never-started) — never waiting.
    assert _workspace_status(_tracked_snapshot("idle"), "/w",
                             {"claude-code"}) == "working"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_report_never_downgrades(mock_semantic, mock_sessions):
    """A report can settle an unknown state but never lower a richer verdict."""
    from power_atlas.status_classifier import SemanticStatus
    mock_sessions.return_value = []

    mock_semantic.return_value = SemanticStatus.ERRORED
    assert _workspace_status(_tracked_snapshot("busy"), "/w",
                             {"claude-code"}) == "errored"
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _workspace_status(_tracked_snapshot("busy"), "/w",
                             {"claude-code"}) == "waiting"
    # ...and it still raises when the classifier is the weaker signal.
    mock_semantic.return_value = SemanticStatus.WORKING
    assert _workspace_status(_tracked_snapshot("waiting"), "/w",
                             {"claude-code"}) == "waiting"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.status_classifier._resolve_jsonl_path")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_fallback_reads_provider_report(
        mock_semantic, mock_resolve, mock_sessions):
    """The no-resume-id fallback reads the report too (same live session ids)."""
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    mock_resolve.return_value = tmp.name
    norm = _normalize_path("/w")
    mock_sessions.return_value = [
        _make_session(session_id="s9", cwd="/w", updated_at=_recent_iso())]
    mock_semantic.return_value = None
    try:
        # No sid_to_cwd entry, so only the fallback scan can see this session.
        waiting = _snapshot(
            live_cwds={("claude-code", norm)},
            sid_status={("claude-code", "s9"): "waiting"})
        assert _workspace_status(waiting, "/w", {"claude-code"}) == "waiting"

        idle = _snapshot(
            live_cwds={("claude-code", norm)},
            sid_status={("claude-code", "s9"): "idle"})
        assert _workspace_status(idle, "/w", {"claude-code"}) == "working"
    finally:
        os.unlink(tmp.name)


def test_classify_kiro_v3_working():
    """v3 classifier returns WORKING for tool_call and user messages."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    # tool_call → working
    lines_tool = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_call","toolName":"read_file","args":{},"status":"completed","kind":"read"}}',
    ]
    assert classify_kiro_v3(lines_tool) == SemanticStatus.WORKING

    # user message → working
    lines_user = [
        '{"id":"2","timestamp":"2026-07-20T10:01:00Z","payload":{"type":"user","content":"fix the bug"}}',
    ]
    assert classify_kiro_v3(lines_user) == SemanticStatus.WORKING

    # tool_result (skipped) then tool_call → working
    lines_mixed = [
        '{"id":"3","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_result","content":"file contents","success":true}}',
        '{"id":"4","timestamp":"2026-07-20T10:01:00Z","payload":{"type":"tool_call","toolName":"fs_write","args":{},"status":"completed","kind":"edit"}}',
    ]
    assert classify_kiro_v3(lines_mixed) == SemanticStatus.WORKING


def test_classify_kiro_v3_waiting():
    """v3 classifier returns WAITING for assistant messages."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    # assistant → waiting
    lines = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_result","content":"ok","success":true}}',
        '{"id":"2","timestamp":"2026-07-20T10:01:00Z","payload":{"type":"assistant","content":"Done. The file has been updated."}}',
    ]
    assert classify_kiro_v3(lines) == SemanticStatus.WAITING


def test_classify_kiro_v3_errored():
    """v3 classifier returns ERRORED when multiple recent tool_results fail."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    # Multiple failed tool_results → errored
    lines = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_result","content":"error","success":false}}',
        '{"id":"2","timestamp":"2026-07-20T10:00:01Z","payload":{"type":"tool_result","content":"error","success":false}}',
        '{"id":"3","timestamp":"2026-07-20T10:00:02Z","payload":{"type":"assistant","content":"Failed."}}',
    ]
    assert classify_kiro_v3(lines) == SemanticStatus.ERRORED


def test_classify_kiro_v3_skips_noise():
    """v3 classifier skips non-meaningful message types."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    lines = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"user","content":"hello"}}',
        '{"id":"2","timestamp":"2026-07-20T10:00:01Z","payload":{"type":"usage_summary","elapsedTime":5,"status":"ok"}}',
        '{"id":"3","timestamp":"2026-07-20T10:00:02Z","payload":{"type":"session_metadata","key":"ctx","value":"100"}}',
    ]
    # Should skip usage_summary and session_metadata, find user → working
    assert classify_kiro_v3(lines) == SemanticStatus.WORKING


@patch("power_atlas.web.get_semantic_status")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_all_sessions_paginated")
@patch("power_atlas.web.load_config")
def test_all_sessions_dot_and_status_filter(mock_config, mock_paginated, mock_snap, mock_semantic, client, tmp_path):
    from power_atlas.config import Config
    from power_atlas.status_classifier import SemanticStatus
    mock_config.return_value = Config()
    ws = str(tmp_path)
    live_s = _make_session(session_id="live1", cwd=ws, updated_at=_recent_iso())
    dead_s = _make_session(session_id="dead1", cwd=ws, updated_at=_recent_iso())
    mock_paginated.return_value = ([(live_s, "claude-code"), (dead_s, "claude-code")], False)
    mock_snap.return_value = _snapshot(live_sids={("claude-code", "live1")})
    mock_semantic.return_value = SemanticStatus.WORKING

    # No filter: both rows render; dot rendering depends on template (Phase 4).
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert 'data-sid="live1"' in resp.text and 'data-sid="dead1"' in resp.text

    # status=live keeps only the live row.
    resp2 = client.get("/partials/all-sessions?page=1&status=live")
    assert 'data-sid="live1"' in resp2.text
    assert 'data-sid="dead1"' not in resp2.text

    # status=closed keeps only the non-live row.
    resp3 = client.get("/partials/all-sessions?page=1&status=closed")
    assert 'data-sid="dead1"' in resp3.text
    assert 'data-sid="live1"' not in resp3.text


@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspaces_status_filter_hides_dead_folders(mock_discover, mock_providers, mock_sessions, mock_snap, client, tmp_path):
    live_dir = tmp_path / "liveproj"; live_dir.mkdir()
    dead_dir = tmp_path / "deadproj"; dead_dir.mkdir()
    recent = _recent_iso()
    mock_discover.return_value = [
        (str(live_dir), 1, recent, "claude-code"),
        (str(dead_dir), 1, recent, "claude-code"),
    ]
    mock_providers.return_value = ["claude-code"]
    mock_sessions.return_value = [_make_session(cwd=str(live_dir))]
    mock_snap.return_value = _snapshot(live_cwds={("claude-code", _normalize_path(str(live_dir)))})

    resp = client.get("/partials/workspaces?status=live")
    assert resp.status_code == 200
    assert "liveproj" in resp.text
    assert "deadproj" not in resp.text


@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_all_sessions_paginated")
@patch("power_atlas.web.load_config")
def test_all_sessions_status_empty_state_escapes_input(mock_config, mock_paginated, mock_snap, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    mock_snap.return_value = _snapshot()
    resp = client.get("/partials/all-sessions", params={"page": 1, "status": "<img src=x onerror=alert(1)>"})
    assert resp.status_code == 200
    assert "<img src=x" not in resp.text          # raw tag must not be reflected
    assert "&lt;img" in resp.text                  # escaped instead


@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspaces_status_empty_state_escapes_input(mock_discover, mock_providers, mock_sessions, mock_snap, client, tmp_path):
    d = tmp_path / "proj"; d.mkdir()
    mock_discover.return_value = [(str(d), 1, _recent_iso(), "claude-code")]
    mock_providers.return_value = ["claude-code"]
    mock_sessions.return_value = [_make_session(cwd=str(d))]
    mock_snap.return_value = _snapshot()  # nothing live -> status=working filters all out
    resp = client.get("/partials/workspaces", params={"status": "<b>x</b>"})
    assert resp.status_code == 200
    assert "<b>x</b>" not in resp.text
    assert "&lt;b&gt;x" in resp.text


# --- Status classifier tests ---

import json as _json
import time as _time
from unittest.mock import patch as _patch, MagicMock

from power_atlas.status_classifier import (
    SemanticStatus,
    _read_tail_lines,
    _resolve_jsonl_path,
    _status_cache,
    classify_claude,
    classify_kiro_v2,
    classify_kiro_v3,
    get_semantic_status,
)


class TestSemanticStatusEnum:
    def test_enum_has_four_values(self):
        assert len(SemanticStatus) == 4
        assert SemanticStatus.WORKING == "working"
        assert SemanticStatus.WAITING == "waiting"
        assert SemanticStatus.ERRORED == "errored"
        assert SemanticStatus.CLOSED == "closed"


class TestResolveJsonlPath:
    def test_kiro_v2_returns_none_for_nonexistent(self, tmp_path):
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path / "v3_empty"):
            result = _resolve_jsonl_path("nonexistent-id", "kiro-cli", "C:\\proj")
            assert result is None

    def test_kiro_v2_returns_path_when_exists(self, tmp_path):
        session_file = tmp_path / "my-session.jsonl"
        session_file.write_text("")
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path):
            result = _resolve_jsonl_path("my-session", "kiro-cli", "C:\\proj")
            assert result == session_file

    def test_claude_code_returns_none_when_no_project_folder(self):
        with _patch("power_atlas.status_classifier._get_project_folder", return_value=None):
            result = _resolve_jsonl_path("sess-1", "claude-code", "C:\\proj")
            assert result is None

    def test_claude_code_returns_path_when_exists(self, tmp_path):
        session_file = tmp_path / "sess-1.jsonl"
        session_file.write_text("")
        with _patch("power_atlas.status_classifier._get_project_folder", return_value=tmp_path):
            result = _resolve_jsonl_path("sess-1", "claude-code", "C:\\proj")
            assert result == session_file

    def test_unknown_provider_returns_none(self):
        result = _resolve_jsonl_path("sess-1", "unknown-provider", "C:\\proj")
        assert result is None

    def test_kiro_v3_returns_path_when_exists(self, tmp_path):
        """v3 session found under sessions/<hash>/sess_<id>/messages.jsonl."""
        ws_hash_dir = tmp_path / "abc123"
        ws_hash_dir.mkdir()
        sess_dir = ws_hash_dir / "sess_my-session"
        sess_dir.mkdir()
        messages_file = sess_dir / "messages.jsonl"
        messages_file.write_text("")
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            result = _resolve_jsonl_path("my-session", "kiro-cli", "C:\\proj")
            assert result == messages_file

    def test_kiro_v3_with_sess_prefix(self, tmp_path):
        """v3 session_id already has sess_ prefix."""
        ws_hash_dir = tmp_path / "abc123"
        ws_hash_dir.mkdir()
        sess_dir = ws_hash_dir / "sess_uuid-here"
        sess_dir.mkdir()
        messages_file = sess_dir / "messages.jsonl"
        messages_file.write_text("")
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            result = _resolve_jsonl_path("sess_uuid-here", "kiro-cli", "C:\\proj")
            assert result == messages_file


class TestReadTailLines:
    def test_small_file_returns_all_lines(self, tmp_path):
        f = tmp_path / "small.jsonl"
        f.write_text("line1\nline2\nline3\n")
        lines = _read_tail_lines(f, max_bytes=4096)
        assert lines == ["line1", "line2", "line3"]

    def test_large_file_discards_first_partial_line(self, tmp_path):
        f = tmp_path / "large.jsonl"
        # Write content larger than max_bytes so seek happens
        padding = "A" * 100 + "\n"  # 101 bytes per line
        # Write 50 lines = 5050 bytes (> 4096)
        content = padding * 50 + "last_complete_line\n"
        f.write_text(content)
        lines = _read_tail_lines(f, max_bytes=200)
        # First line should NOT be a partial 'AAA...' line
        # It should start with a complete line
        assert lines[0].startswith("A" * 100) or lines[0] == "last_complete_line"
        # The key invariant: we don't get a partial line at the start
        # (the seek lands mid-line, that partial is discarded)
        for line in lines:
            # Every line is either full padding or the last line
            assert line == "A" * 100 or line == "last_complete_line"

    def test_exact_boundary_no_discard(self, tmp_path):
        """When file is exactly max_bytes, no seek past start → no discard."""
        f = tmp_path / "exact.jsonl"
        content = "line1\nline2\n"
        f.write_bytes(content.encode("utf-8"))
        lines = _read_tail_lines(f, max_bytes=len(content.encode("utf-8")) + 1)
        assert "line1" in lines
        assert "line2" in lines


class TestClassifyKiroV2:
    def _make_line(self, kind, data=None):
        return _json.dumps({"version": "v1", "kind": kind, "data": data or {}})

    def test_prompt_returns_active(self):
        lines = [self._make_line("Prompt", {"content": "hello"})]
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING

    def test_tool_results_returns_active(self):
        lines = [self._make_line("ToolResults", {"results": []})]
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING

    def test_assistant_with_tool_use_returns_active(self):
        data = {"content": [{"kind": "toolUse", "data": {"name": "fs_read"}}]}
        lines = [self._make_line("AssistantMessage", data)]
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING

    def test_assistant_without_tool_use_returns_idle(self):
        data = {"content": [{"kind": "text", "data": {"text": "Done."}}]}
        lines = [self._make_line("AssistantMessage", data)]
        assert classify_kiro_v2(lines) == SemanticStatus.WAITING

    def test_last_message_wins_reverse_order(self):
        """When multiple messages exist, the last (most recent) one determines status."""
        lines = [
            self._make_line("Prompt", {"content": "hello"}),
            self._make_line("AssistantMessage", {"content": [{"kind": "text", "data": {}}]}),
        ]
        # Last line is AssistantMessage without toolUse → IDLE
        assert classify_kiro_v2(lines) == SemanticStatus.WAITING

    def test_empty_lines_returns_none(self):
        assert classify_kiro_v2([]) is None

    def test_invalid_json_returns_none(self):
        assert classify_kiro_v2(["not json at all", "{broken"]) is None

    def test_skips_unparseable_finds_valid(self):
        """Skips garbage lines and classifies from the last valid one."""
        lines = [
            self._make_line("Prompt", {"content": "hello"}),
            "garbage line",
        ]
        # Reverse walk: skip garbage, find Prompt → ACTIVE
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING


class TestClassifyClaude:
    def test_tool_result_returns_active(self):
        lines = [_json.dumps({"type": "tool_result", "content": "output"})]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_tool_use_returns_active(self):
        lines = [_json.dumps({"type": "tool_use", "name": "read_file"})]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_user_message_returns_active(self):
        lines = [_json.dumps({"type": "user", "content": "do something"})]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_human_role_returns_active(self):
        """Legacy role-based format with 'human' role."""
        lines = [_json.dumps({"role": "human", "content": "hello"})]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_assistant_without_error_returns_idle(self):
        lines = [_json.dumps({"type": "assistant", "content": "Done."})]
        assert classify_claude(lines) == SemanticStatus.WAITING

    def test_assistant_with_is_error_block_returns_errored(self):
        lines = [_json.dumps({
            "type": "assistant",
            "content": [{"type": "text", "text": "failed", "is_error": True}],
        })]
        assert classify_claude(lines) == SemanticStatus.ERRORED

    def test_assistant_with_top_level_is_error_returns_errored(self):
        lines = [_json.dumps({
            "type": "assistant",
            "content": "something failed",
            "is_error": True,
        })]
        assert classify_claude(lines) == SemanticStatus.ERRORED

    def test_empty_lines_returns_none(self):
        assert classify_claude([]) is None

    def test_invalid_json_returns_none(self):
        assert classify_claude(["not json", "{{"]) is None

    def test_last_message_wins(self):
        lines = [
            _json.dumps({"type": "user", "content": "hi"}),
            _json.dumps({"type": "assistant", "content": "Done."}),
        ]
        # Last line is assistant without error → IDLE
        assert classify_claude(lines) == SemanticStatus.WAITING


class TestClassifyKiroV3:
    def test_user_returns_working(self):
        lines = [_json.dumps({"id": "x", "timestamp": "2026-01-01", "payload": {"type": "user", "content": "hello"}})]
        assert classify_kiro_v3(lines) == SemanticStatus.WORKING

    def test_tool_call_returns_working(self):
        lines = [_json.dumps({"id": "x", "timestamp": "2026-01-01", "payload": {"type": "tool_call", "toolName": "read_file", "args": {}, "status": "completed", "kind": "read"}})]
        assert classify_kiro_v3(lines) == SemanticStatus.WORKING

    def test_assistant_returns_waiting(self):
        lines = [_json.dumps({"id": "x", "timestamp": "2026-01-01", "payload": {"type": "assistant", "content": "Done."}})]
        assert classify_kiro_v3(lines) == SemanticStatus.WAITING

    def test_empty_returns_none(self):
        assert classify_kiro_v3([]) is None

    def test_skips_tool_result_finds_assistant(self):
        lines = [
            _json.dumps({"id": "1", "timestamp": "2026-01-01", "payload": {"type": "assistant", "content": "ok"}}),
            _json.dumps({"id": "2", "timestamp": "2026-01-01", "payload": {"type": "tool_result", "content": "output", "success": True}}),
        ]
        # tool_result is skipped; last meaningful is assistant → waiting
        assert classify_kiro_v3(lines) == SemanticStatus.WAITING


class TestGetSemanticStatus:
    def setup_method(self):
        """Clear cache between tests."""
        _status_cache.clear()

    def test_returns_none_for_missing_file(self):
        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=None):
            result = get_semantic_status("sess-1", "kiro-cli", "C:\\proj")
            assert result is None

    def test_caches_result_and_returns_on_same_mtime(self, tmp_path):
        session_file = tmp_path / "sess-1.jsonl"
        line = _json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "hi"}})
        session_file.write_text(line + "\n")

        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=session_file):
            # First call — classifies
            result1 = get_semantic_status("sess-1", "kiro-cli", "C:\\proj")
            assert result1 == SemanticStatus.WORKING

            # Second call — hits cache (same mtime)
            result2 = get_semantic_status("sess-1", "kiro-cli", "C:\\proj")
            assert result2 == SemanticStatus.WORKING

            # Verify cache was populated
            assert ("kiro-cli", "sess-1") in _status_cache

    def test_cache_invalidated_on_mtime_change(self, tmp_path):
        session_file = tmp_path / "sess-2.jsonl"
        # Write initial content -> ACTIVE
        line_active = _json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "hi"}})
        session_file.write_text(line_active + "\n")

        # Use a controlled monotonic clock so we can expire the TTL
        call_count = [0]
        base_time = 1000.0

        def fake_monotonic():
            # First few calls: time=1000 (within TTL)
            # After we bump: time=1010 (beyond 5s TTL)
            return base_time + (10.0 if call_count[0] > 2 else 0.0)

        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=session_file), \
             _patch("power_atlas.status_classifier.time.monotonic", side_effect=fake_monotonic):
            result1 = get_semantic_status("sess-2", "kiro-cli", "C:\\proj")
            assert result1 == SemanticStatus.WORKING

            # Write new content (IDLE) and force a different mtime
            line_idle = _json.dumps({
                "version": "v1", "kind": "AssistantMessage",
                "data": {"content": [{"kind": "text", "data": {"text": "Done."}}]},
            })
            session_file.write_text(line_idle + "\n")
            import os as _os
            stat = _os.stat(session_file)
            _os.utime(session_file, (stat.st_atime, stat.st_mtime + 10))

            # Advance the clock beyond TTL
            call_count[0] = 10

            # Next call: TTL expired + mtime changed -> reclassifies
            result2 = get_semantic_status("sess-2", "kiro-cli", "C:\\proj")
            assert result2 == SemanticStatus.WAITING

    def test_returns_none_on_stat_failure(self, tmp_path):
        fake_path = tmp_path / "gone.jsonl"
        # File doesn't exist, but _resolve_jsonl_path returns it
        # (simulates race condition)
        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=fake_path):
            result = get_semantic_status("sess-3", "kiro-cli", "C:\\proj")
            assert result is None

    def test_never_raises(self):
        """get_semantic_status swallows all exceptions."""
        with _patch("power_atlas.status_classifier._resolve_jsonl_path", side_effect=RuntimeError("boom")):
            result = get_semantic_status("sess-4", "kiro-cli", "C:\\proj")
            assert result is None


# --- Toast notification tests ---


class TestNotifications:
    """Tests for the notifications module (session status transition toasts)."""

    def setup_method(self):
        """Reset module state between tests."""
        from power_atlas import notifications
        notifications._session_states.clear()
        notifications._initialized = False

    def test_notification_transition_fires(self):
        """Working→Waiting triggers a toast notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        # Establish working state
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        # Transition to waiting should fire
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once_with("My Session", "waiting")

    def test_notification_working_to_waiting_fires(self):
        """Working→Waiting triggers a toast notification (explicit)."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once_with("My Session", "waiting")

    def test_notification_working_to_errored_fires(self):
        """Working→Errored triggers a toast notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "errored", True)
            mock_fire.assert_called_once_with("My Session", "errored")

    def test_notification_non_active_transition_does_not_fire(self):
        """Waiting→Closed does NOT trigger a notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "waiting", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "closed", True)
            mock_fire.assert_not_called()

    def test_notification_working_to_closed_does_not_fire(self):
        """Working→Closed does NOT trigger a notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-2", "Another Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-2", "Another Session", "closed", True)
            mock_fire.assert_not_called()

    def test_notification_cooldown(self):
        """Second transition within 60s is suppressed."""
        from power_atlas import notifications
        notifications.mark_initialized()
        # First: working → waiting (fires)
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once()
        # Go back to working then waiting again — within cooldown
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_not_called()  # suppressed by cooldown

    def test_notification_cooldown_expires(self):
        """After cooldown expires, notification fires again."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast"):
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
        # Manually expire the cooldown
        state = notifications._session_states["sess-1"]
        state.last_notified_at -= 61.0
        # Go back to working then waiting
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once()

    def test_notification_disabled(self):
        """enabled=False prevents all notifications."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", False)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", False)
            mock_fire.assert_not_called()

    def test_notification_startup_no_fire(self):
        """Before mark_initialized(), no notifications fire."""
        from power_atlas import notifications
        # Do NOT call mark_initialized
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "working", True)
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_not_called()

    def test_notification_startup_tracks_state(self):
        """Before mark_initialized(), state IS tracked (for baseline)."""
        from power_atlas import notifications
        # Establish state before init
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        # Now initialize
        notifications.mark_initialized()
        # Transition should work since state was already working
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once_with("My Session", "waiting")

    def test_notification_state_bounded(self):
        """>100 entries triggers LRU eviction of oldest."""
        from power_atlas import notifications
        notifications.mark_initialized()
        # Add 100 entries
        for i in range(100):
            notifications.check_and_notify(f"sess-{i}", f"Session {i}", "working", True)
        assert len(notifications._session_states) == 100
        assert "sess-0" in notifications._session_states
        # Add one more — should evict sess-0
        notifications.check_and_notify("sess-100", "Session 100", "working", True)
        assert len(notifications._session_states) == 100
        assert "sess-0" not in notifications._session_states
        assert "sess-100" in notifications._session_states


# --- POST /api/session-status (lightweight status polling) ---


class TestApiSessionStatus:
    """Tests for the lightweight POST /api/session-status endpoint."""

    @patch("power_atlas.web.presence.get_snapshot")
    def test_empty_cwds_returns_empty_maps(self, mock_snap, client):
        """Empty cwds list returns empty response immediately."""
        resp = client.post(
            "/api/session-status", json={"cwds": []},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"sessions": {}, "workspaces": {}, "active_cwds": []}
        # Snapshot never called for empty input
        mock_snap.assert_not_called()

    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_inactive_cwd_short_circuits(self, mock_snap, mock_cache, client):
        """CWDs without a live process return 'closed' without iterating sessions."""
        from power_atlas.presence import Snapshot
        # Snapshot with no live processes
        mock_snap.return_value = Snapshot(set(), set(), {})

        resp = client.post(
            "/api/session-status",
            json={"cwds": ["C:\\projects\\myapp", "C:\\other"]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspaces"]["C:\\projects\\myapp"] == "closed"
        assert body["workspaces"]["C:\\other"] == "closed"
        assert body["active_cwds"] == []
        assert body["sessions"] == {}
        # session_cache.get should NOT be called (short-circuit)
        mock_cache.get.assert_not_called()

    @patch("power_atlas.web.notifications.check_and_notify")
    @patch("power_atlas.web.get_semantic_status")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_active_cwd_returns_session_status_no_notifications(
        self, mock_snap, mock_cache, mock_semantic, mock_notify, client
    ):
        """Active CWDs return per-session status; notifications are NOT triggered."""
        from power_atlas.presence import Snapshot
        from power_atlas.data import _normalize_path

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)

        # Snapshot shows kiro-cli running in this cwd
        mock_snap.return_value = Snapshot(
            live_sids={("kiro-cli", "sess-1")},
            live_cwds={("kiro-cli", norm_cwd)},
            sid_to_cwd={("kiro-cli", "sess-1"): norm_cwd},
        )

        # Session cache returns one session
        sess = _make_session(session_id="sess-1", cwd=cwd)
        mock_cache.get.return_value = [sess]

        # Semantic status returns WAITING
        from power_atlas.status_classifier import SemanticStatus
        mock_semantic.return_value = SemanticStatus.WAITING

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Session status returned
        assert body["sessions"]["sess-1"] == "waiting"
        # Workspace-level status
        assert body["workspaces"][cwd] == "waiting"
        # Active cwds includes this one
        assert cwd in body["active_cwds"]
        # Notifications NOT triggered
        mock_notify.assert_not_called()

    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_skips_kiro_ide_provider(self, mock_snap, mock_cache, client):
        """kiro-ide sessions are never processed by this endpoint."""
        from power_atlas.presence import Snapshot
        from power_atlas.data import _normalize_path

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)

        # Only kiro-ide is running in this cwd — endpoint should see no active process
        mock_snap.return_value = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-ide", norm_cwd)},
            sid_to_cwd={},
        )

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()

        # CWD treated as inactive (kiro-ide excluded from poll_providers)
        assert body["workspaces"][cwd] == "closed"
        assert body["active_cwds"] == []
        # session_cache never accessed
        mock_cache.get.assert_not_called()

    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_mixed_active_inactive_cwds(self, mock_snap, mock_cache, client):
        """Mix of active and inactive cwds: only active are iterated."""
        from power_atlas.presence import Snapshot
        from power_atlas.data import _normalize_path

        active_cwd = "C:\\projects\\active"
        inactive_cwd = "C:\\projects\\idle"
        norm_active = _normalize_path(active_cwd)

        mock_snap.return_value = Snapshot(
            live_sids=set(),
            live_cwds={("claude-code", norm_active)},
            sid_to_cwd={},
        )
        # Cache returns None for both (no cached sessions)
        mock_cache.get.return_value = None

        resp = client.post(
            "/api/session-status",
            json={"cwds": [active_cwd, inactive_cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["workspaces"][inactive_cwd] == "closed"
        assert active_cwd in body["active_cwds"]
        assert inactive_cwd not in body["active_cwds"]

    @patch("power_atlas.web.get_semantic_status")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_workspace_dot_comes_from_workspace_status(
        self, mock_snap, mock_cache, mock_semantic, client
    ):
        """Card dot is _workspace_status, so a poll cannot contradict a render.

        Divergence case: nothing is cached for the cwd, so the old per-session
        aggregation could only ever answer "working"; _workspace_status reads
        the tracked session id and reports the classifier's verdict instead.
        """
        from power_atlas.status_classifier import SemanticStatus

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)
        snap = _snapshot(
            live_sids={("kiro-cli", "sess-9")},
            live_cwds={("kiro-cli", norm_cwd)},
            sid_to_cwd={("kiro-cli", "sess-9"): norm_cwd},
        )
        mock_snap.return_value = snap
        mock_cache.get.return_value = None
        mock_semantic.return_value = SemanticStatus.ERRORED

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspaces"][cwd] == "errored"
        assert body["sessions"] == {}   # nothing cached -> no per-row dots
        # Same answer the render path would have produced for this cwd. An
        # unfiltered page renders with providers=None, so that is what this
        # compares against — not the {kiro-cli, claude-code} set the poll
        # happens to build for "all".
        assert body["workspaces"][cwd] == _workspace_status(snap, cwd, None)

    @patch("power_atlas.status_classifier._resolve_jsonl_path")
    @patch("power_atlas.web.get_semantic_status")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_card_may_outrank_a_cached_untracked_row(
        self, mock_snap, mock_cache, mock_semantic, mock_resolve, client, tmp_path
    ):
        """The card answers for the session presence proved live, not for every row.

        A workspace holding a tracked session (working) plus a cached,
        untracked, recently-written one (waiting) shows a working card over a
        waiting row. That is deliberate: the untracked session's process is not
        the live one, and this is the answer a full render has always given —
        the poll now matches it rather than aggregating the rows itself.
        """
        from power_atlas.status_classifier import SemanticStatus

        jsonl = tmp_path / "s2.jsonl"
        jsonl.write_text("")
        mock_resolve.return_value = str(jsonl)

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)
        snap = _snapshot(
            live_sids={("claude-code", "s1")},
            live_cwds={("claude-code", norm_cwd)},
            sid_to_cwd={("claude-code", "s1"): norm_cwd},
        )
        mock_snap.return_value = snap
        s2 = _make_session(session_id="s2", cwd=cwd, updated_at=_recent_iso())
        mock_cache.get.side_effect = lambda c, p: [s2] if p == "claude-code" else None
        mock_semantic.side_effect = lambda sid, prov, c: (
            SemanticStatus.WORKING if sid == "s1" else SemanticStatus.WAITING)

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspaces"][cwd] == "working"
        assert body["sessions"]["s2"] == "waiting"
        # No render/poll divergence: the render path says the same thing, and
        # it asks with providers=None on an unfiltered page.
        assert body["workspaces"][cwd] == _workspace_status(snap, cwd, None)

    @patch("power_atlas.web.data.get_sessions")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_provider_filter_travels_with_the_poll(
        self, mock_snap, mock_cache, mock_sessions, client
    ):
        """A provider-filtered page polls about that provider only."""
        cwd = "C:\\projects\\myapp"
        mock_snap.return_value = _snapshot(
            live_cwds={("claude-code", _normalize_path(cwd))})
        mock_cache.get.return_value = None
        mock_sessions.return_value = []

        def poll(payload):
            resp = client.post("/api/session-status", json=payload,
                               headers={"Origin": "http://127.0.0.1"})
            assert resp.status_code == 200
            return resp.json()["workspaces"][cwd]

        # Only claude-code is running, so a kiro-cli-filtered page must not
        # light this card up — which is what the render path shows it.
        assert poll({"cwds": [cwd], "provider": "kiro-cli"}) == "closed"
        assert poll({"cwds": [cwd], "provider": "claude-code"}) == "working"
        # "all" and a client that sends no provider both see every CLI provider.
        assert poll({"cwds": [cwd], "provider": "all"}) == "working"
        assert poll({"cwds": [cwd]}) == "working"

    @patch("power_atlas.web.data.get_sessions")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_response_keeps_request_order(
        self, mock_snap, mock_cache, mock_sessions, client
    ):
        """Both maps echo the order the client asked in, live cwds included."""
        cwds = ["C:\\projects\\ccc", "C:\\projects\\aaa", "C:\\projects\\bbb"]
        mock_snap.return_value = _snapshot(live_cwds={
            ("claude-code", _normalize_path(c)) for c in cwds})
        mock_cache.get.return_value = None
        mock_sessions.return_value = []

        resp = client.post("/api/session-status", json={"cwds": cwds},
                           headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_cwds"] == cwds
        assert list(body["workspaces"]) == cwds

    @patch("power_atlas.web.data.get_sessions")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_non_string_provider_does_not_500(
        self, mock_snap, mock_cache, mock_sessions, client
    ):
        """A provider of the wrong JSON shape degrades to "all", not a 500.

        The value reaches a set literal, so an array or object would raise
        TypeError: unhashable type outside the per-cwd guard and take the
        whole endpoint down.
        """
        cwd = "C:\\projects\\myapp"
        mock_snap.return_value = _snapshot(
            live_cwds={("claude-code", _normalize_path(cwd))})
        mock_cache.get.return_value = None
        mock_sessions.return_value = []

        for bogus in ([], {}, ["kiro-cli"], {"name": "kiro-cli"}, 7, True, None):
            resp = client.post("/api/session-status",
                               json={"cwds": [cwd], "provider": bogus},
                               headers={"Origin": "http://127.0.0.1"})
            assert resp.status_code == 200, bogus
            assert resp.json()["workspaces"][cwd] == "working", bogus

    def test_missing_cwds_key_returns_empty(self, client):
        """Body without 'cwds' key returns empty response without error."""
        resp = client.post(
            "/api/session-status",
            json={},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"sessions": {}, "workspaces": {}, "active_cwds": []}


# --- Path/status caching (260725_PARSE_AND_POLL_PERFORMANCE) ----------------


class TestResolveJsonlPathCaching:
    """The kiro-cli v3 fallback scans every workspace dir, so it is memoized."""

    def setup_method(self):
        from power_atlas import status_classifier
        status_classifier._path_cache.clear()

    def test_repeat_lookup_avoids_rescan(self, tmp_path):
        """A second lookup revalidates the cached path instead of walking dirs."""
        ws = tmp_path / "abc123" / "sess_cached-id"
        ws.mkdir(parents=True)
        (ws / "messages.jsonl").write_text("")

        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            first = _resolve_jsonl_path("cached-id", "kiro-cli", "C:\\proj")
            assert first == ws / "messages.jsonl"

            with _patch("power_atlas.status_classifier._resolve_jsonl_path_uncached",
                        side_effect=AssertionError("should not rescan")):
                second = _resolve_jsonl_path("cached-id", "kiro-cli", "C:\\proj")
            assert second == first

    def test_deleted_file_reresolves(self, tmp_path):
        """A cached path whose file disappeared must not be returned."""
        ws = tmp_path / "abc123" / "sess_gone-id"
        ws.mkdir(parents=True)
        target = ws / "messages.jsonl"
        target.write_text("")

        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            assert _resolve_jsonl_path("gone-id", "kiro-cli", "C:\\proj") == target
            target.unlink()
            assert _resolve_jsonl_path("gone-id", "kiro-cli", "C:\\proj") is None

    def test_different_roots_do_not_collide(self, tmp_path):
        """The cache key carries the roots, so rebinding them re-resolves."""
        root_a = tmp_path / "a"
        (root_a / "h" / "sess_same-id").mkdir(parents=True)
        (root_a / "h" / "sess_same-id" / "messages.jsonl").write_text("")
        root_b = tmp_path / "b"
        (root_b / "h2" / "sess_same-id").mkdir(parents=True)
        (root_b / "h2" / "sess_same-id" / "messages.jsonl").write_text("")

        with _patch("power_atlas.status_classifier.SESSION_DIR", root_a / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", root_a):
            a = _resolve_jsonl_path("same-id", "kiro-cli", "C:\\proj")
        with _patch("power_atlas.status_classifier.SESSION_DIR", root_b / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", root_b):
            b = _resolve_jsonl_path("same-id", "kiro-cli", "C:\\proj")

        assert a == root_a / "h" / "sess_same-id" / "messages.jsonl"
        assert b == root_b / "h2" / "sess_same-id" / "messages.jsonl"

    def test_claude_code_is_not_cached(self, tmp_path):
        """claude-code resolution is two syscalls — caching it would only add staleness."""
        from power_atlas import status_classifier
        session_file = tmp_path / "cc-id.jsonl"
        session_file.write_text("")
        with _patch("power_atlas.status_classifier._get_project_folder", return_value=tmp_path):
            assert _resolve_jsonl_path("cc-id", "claude-code", "C:\\proj") == session_file
        assert len(status_classifier._path_cache) == 0


class TestStatusCacheLRU:
    """Status cache evicts least-recently-used in O(1) and stays bounded."""

    def test_stays_within_bound(self):
        from power_atlas import status_classifier as sc
        from power_atlas.status_classifier import SemanticStatus
        sc._status_cache.clear()
        for i in range(sc._MAX_CACHE_ENTRIES + 50):
            sc._status_cache[("kiro-cli", f"s{i}")] = (float(i), 0.0, SemanticStatus.WORKING)
            sc._evict_oldest()
        assert len(sc._status_cache) == sc._MAX_CACHE_ENTRIES

    def test_evicts_oldest_first(self):
        from power_atlas import status_classifier as sc
        from power_atlas.status_classifier import SemanticStatus
        sc._status_cache.clear()
        for i in range(sc._MAX_CACHE_ENTRIES):
            sc._status_cache[("kiro-cli", f"s{i}")] = (float(i), 0.0, SemanticStatus.WORKING)
        # Touch the oldest so it is no longer the eviction candidate
        sc._status_cache.move_to_end(("kiro-cli", "s0"))
        sc._status_cache[("kiro-cli", "new")] = (999.0, 0.0, SemanticStatus.WAITING)
        sc._evict_oldest()
        assert ("kiro-cli", "s0") in sc._status_cache
        assert ("kiro-cli", "s1") not in sc._status_cache

    def test_every_cache_operation_holds_the_lock(self, tmp_path, monkeypatch):
        """The status poll reaches this cache from a worker thread while a
        render reaches it from the event loop, so an unguarded
        get/move_to_end/popitem interleaving can raise KeyError — which
        get_semantic_status' blanket except turns into a silently wrong dot.
        Recorded rather than raised, for the same reason.
        """
        from collections import OrderedDict
        from power_atlas import status_classifier as sc

        jsonl = tmp_path / "sess.jsonl"
        jsonl.write_text('{"version":"v1","kind":"Prompt","data":{}}\n')

        class _LockChecked(OrderedDict):
            def __init__(self):
                super().__init__()
                self.unlocked_ops = []

            def _chk(self, op):
                if not sc._status_cache_lock.locked():
                    self.unlocked_ops.append(op)

            def get(self, key, default=None):
                self._chk("get")
                return super().get(key, default)

            def __setitem__(self, key, value):
                self._chk("setitem")
                super().__setitem__(key, value)

            def move_to_end(self, key, last=True):
                self._chk("move_to_end")
                super().move_to_end(key, last=last)

            def popitem(self, last=True):
                self._chk("popitem")
                return super().popitem(last=last)

        cache = _LockChecked()
        monkeypatch.setattr(sc, "_status_cache", cache)
        monkeypatch.setattr(sc, "_MAX_CACHE_ENTRIES", 1)
        monkeypatch.setattr(sc, "_resolve_jsonl_path", lambda sid, prov, cwd: jsonl)

        assert sc.get_semantic_status("s1", "kiro-cli", "/w") is not None  # miss: write
        assert sc.get_semantic_status("s1", "kiro-cli", "/w") is not None  # hit: read
        sc.get_semantic_status("s2", "kiro-cli", "/w")                     # forces eviction
        assert cache.unlocked_ops == []


@patch("power_atlas.web.get_semantic_status")
def test_session_status_prefers_provider_report_for_working(mock_semantic):
    """claude-code declaring a running turn beats an inferred verdict.

    The classifier reads the transcript tail, which lags an in-flight turn
    that has not flushed yet.
    """
    from power_atlas.status_classifier import SemanticStatus
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    for reported in ("busy", "shell"):
        snap = _snapshot(
            live_sids={("claude-code", "s1")},
            sid_status={("claude-code", "s1"): reported},
        )
        mock_semantic.return_value = SemanticStatus.WAITING
        assert _session_status(snap, s, "claude-code") == "working", reported
        # The classifier must not even be consulted for an unambiguous report.
        mock_semantic.return_value = None
        assert _session_status(snap, s, "claude-code") == "working", reported


@patch("power_atlas.web.get_semantic_status")
def test_session_status_reports_waiting_from_provider(mock_semantic):
    """"waiting" means a dialog is open and needs the human."""
    snap = _snapshot(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "waiting"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = None
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_idle_does_not_erase_errored(mock_semantic):
    """"idle" only rules out working — it must never overwrite a richer verdict.

    idle covers finished, errored and never-started alike, and the classifier
    is the only source of "errored".
    """
    from power_atlas.status_classifier import SemanticStatus
    snap = _snapshot(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "idle"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.ERRORED
    assert _session_status(snap, s, "claude-code") == "errored"
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_unknown_report_falls_through(mock_semantic):
    """An unrecognised value must degrade to today's behaviour, not break it.

    The field is undocumented internal state in a frequently-updated binary.
    """
    from power_atlas.status_classifier import SemanticStatus
    snap = _snapshot(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "some-future-value"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_report_never_revives_a_closed_session(mock_semantic):
    """A stale report must not make a dead session look alive."""
    snap = _snapshot(sid_status={("claude-code", "s9"): "busy"})
    s = _make_session(session_id="s9", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = None
    assert _session_status(snap, s, "claude-code") == "closed"


# --- Waiting reason: blocked-on-approval vs asked-a-question ---

def _snap_waiting(reason, status="waiting"):
    return presence.Snapshot(
        set(), set(), {},
        {("claude-code", "s1"): status},
        {("claude-code", "s1"): reason},
    )


def test_waiting_detail_separates_approval_from_question():
    from power_atlas.web import _waiting_detail
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    approval = ["permission prompt", "sandbox request", "worker request"]
    for r in approval:
        cat, phrase = _waiting_detail(_snap_waiting(r), s, "claude-code", "waiting")
        assert cat == "approval", r
        assert "approval" in phrase, r
    cat, phrase = _waiting_detail(_snap_waiting("input needed"), s, "claude-code", "waiting")
    assert (cat, phrase) == ("question", "asked you a question")


def test_waiting_detail_passes_through_an_unmapped_reason():
    """A new provider value must surface, not vanish."""
    from power_atlas.web import _waiting_detail
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    assert _waiting_detail(_snap_waiting("some new thing"), s, "claude-code", "waiting") \
        == ("other", "some new thing")


def test_waiting_detail_only_applies_to_waiting_sessions():
    from power_atlas.web import _waiting_detail
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    snap = _snap_waiting("permission prompt", status="busy")
    assert _waiting_detail(snap, s, "claude-code", "working") == ("", "")
    # kiro-cli reports no reason at all; must not raise or invent one.
    bare = presence.Snapshot(set(), set(), {}, {}, {})
    assert _waiting_detail(bare, s, "kiro-cli", "waiting") == ("", "")


def test_session_row_renders_the_waiting_reason():
    from power_atlas.web import templates
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    tpl = templates.get_template("partials/session_row.html")

    html = tpl.render(request=None, session=s, cwd="/w", stale=False,
                      pinned_sessions=[], provider_name="claude-code",
                      provider_color="", status="waiting",
                      waiting_detail=("approval", "needs your approval"))
    assert 'title="Waiting — needs your approval"' in html
    assert 'data-waiting="approval"' in html

    # Absent detail (kiro-cli, or an older snapshot) keeps the original text.
    plain = tpl.render(request=None, session=s, cwd="/w", stale=False,
                       pinned_sessions=[], provider_name="kiro-cli",
                       provider_color="", status="waiting")
    assert 'title="Waiting — needs your input"' in plain
    assert "data-waiting" not in plain
