"""Tests for web module."""

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from power_atlas.data import Session
from power_atlas.web import app
from power_atlas import launcher


@pytest.fixture
def client():
    """TestClient with default Origin header for same-origin guard."""
    c = TestClient(app)
    # Patch the post method to add Origin by default
    _original_post = c.post
    def _post_with_origin(*args, **kwargs):
        headers = kwargs.get("headers", {})
        if "Origin" not in headers and "origin" not in headers:
            headers["Origin"] = "http://testserver"
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


@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_filters(mock_discover, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [
        (workspace, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
        ("C:\\other\\project", 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]

    resp = client.get(f"/search?q={Path(workspace).name}")
    assert resp.status_code == 200
    assert Path(workspace).name in resp.text


@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_no_results(mock_discover, client, tmp_path):
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
    }, headers={"Origin": "http://testserver"})
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
                       headers={"X-Workspace": "C:\\app", "Origin": "http://testserver"})
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
                       headers={"X-Workspace": "C:\\app", "Origin": "http://testserver"})
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
                       headers={"Origin": "http://testserver"})
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
                       headers={"Origin": "http://testserver"})
    assert resp.status_code == 200
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_unpin_folder_simple(mock_load, mock_save, client):
    """Unpin folder API removes path from list."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\projects\\myapp", "C:\\other"])
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://testserver"})
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
                       headers={"Origin": "http://testserver"})
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
                           headers={"Origin": "http://testserver"})
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
                           headers={"Origin": "http://testserver"})
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
                           headers={"Origin": "http://testserver"})
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
                           headers={"Origin": "http://testserver"})
        body = resp.json()
        assert body["ok"] is True
        mock_save.assert_called_once()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_valid(mock_load, mock_save, client):
    """Valid port value is accepted and saved."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 8080}, headers={"Origin": "http://testserver"})
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert saved.port == 8080


@patch("power_atlas.web.load_config")
def test_save_setting_port_bool_rejected(mock_load, client):
    """Boolean value for port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": True}, headers={"Origin": "http://testserver"})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.load_config")
def test_save_setting_port_out_of_range(mock_load, client):
    """Out-of-range port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 99999}, headers={"Origin": "http://testserver"})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_zero_accepted(mock_load, mock_save, client):
    """Port 0 (random mode) is accepted."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 0}, headers={"Origin": "http://testserver"})
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
    assert 'title="Kiro CLI"' in resp.text


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
    return TestClient(app)


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
                               headers={"Origin": "http://testserver"})
        assert resp.status_code == 200

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_valid_referer_only_accepted(self, mock_load, mock_save, raw_client):
        """Matching Referer (without Origin) passes the guard."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Referer": "http://testserver/some/page"})
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
    }, headers={"Origin": "http://testserver"})
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
    }, headers={"Origin": "http://testserver"})
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
    }, headers={"Origin": "http://testserver"})
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
    }, headers={"Origin": "http://testserver"})
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
    }, headers={"Origin": "http://testserver"})
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
    }, headers={"Origin": "http://testserver"})
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
