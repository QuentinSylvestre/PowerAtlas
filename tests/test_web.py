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
from power_atlas.web import _age_seconds, _session_status, _workspace_status, _status_matches
from power_atlas.data import _normalize_path


def _recent_iso(secs=5):
    return (datetime.now(timezone.utc) - timedelta(seconds=secs)).isoformat()


def _old_iso(mins=10):
    return (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()


def _snapshot(live_sids=(), live_cwds=(), sid_to_cwd=None):
    return presence.Snapshot(set(live_sids), set(live_cwds), sid_to_cwd or {})


def test_age_seconds_parses_and_degrades():
    assert _age_seconds(_recent_iso()) < 30
    assert _age_seconds("") is None
    assert _age_seconds("not-a-date") is None
    # Naive timestamp (Kiro-style) is interpreted, not rejected.
    naive = (datetime.now() - timedelta(seconds=3)).replace(microsecond=0).isoformat()
    assert _age_seconds(naive) is not None


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
            headers={"Origin": "http://testserver"},
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
            headers={"Origin": "http://testserver"},
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
            headers={"Origin": "http://testserver"},
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
            headers={"Origin": "http://testserver"},
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
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["workspaces"][inactive_cwd] == "closed"
        assert active_cwd in body["active_cwds"]
        assert inactive_cwd not in body["active_cwds"]

    def test_missing_cwds_key_returns_empty(self, client):
        """Body without 'cwds' key returns empty response without error."""
        resp = client.post(
            "/api/session-status",
            json={},
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"sessions": {}, "workspaces": {}, "active_cwds": []}


def _snapshot_with_status(live_sids=(), sid_status=None):
    return presence.Snapshot(set(live_sids), set(), {}, sid_status or {})


@patch("power_atlas.web.get_semantic_status")
def test_session_status_prefers_provider_report_for_working(mock_semantic):
    """claude-code declaring a running turn beats an inferred verdict.

    The classifier reads the transcript tail, which lags an in-flight turn
    that has not flushed yet.
    """
    from power_atlas.status_classifier import SemanticStatus
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    for reported in ("busy", "shell"):
        snap = _snapshot_with_status(
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
    snap = _snapshot_with_status(
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
    snap = _snapshot_with_status(
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
    snap = _snapshot_with_status(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "some-future-value"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_report_never_revives_a_closed_session(mock_semantic):
    """A stale report must not make a dead session look alive."""
    snap = _snapshot_with_status(sid_status={("claude-code", "s9"): "busy"})
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
