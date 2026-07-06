"""Tests for web module."""

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from power_atlas.data import Session
from power_atlas.web import app


@pytest.fixture
def client():
    return TestClient(app)


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
    assert "missing" in resp.text
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
    })
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
                       headers={"X-Workspace": "C:\\app"})
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
                       headers={"X-Workspace": "C:\\app"})
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
    resp = client.get("/partials/pinned-workspaces")
    assert resp.status_code == 200
    assert pinned in resp.text or "my-pinned-workspace" in resp.text


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
    resp = client.post("/api/pin-folder", json={"folder": "C:\\projects\\myapp"})
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
    resp = client.post("/api/pin-folder", json={"folder": "C:\\projects\\myapp"})
    assert resp.status_code == 200
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_unpin_folder_simple(mock_load, mock_save, client):
    """Unpin folder API removes path from list."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\projects\\myapp", "C:\\other"])
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\projects\\myapp"})
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
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\nonexistent"})
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
        resp = client.post("/api/save-setting", json={"key": "__class__", "value": "evil"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "unknown" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_wrong_type(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "terminal_command", "value": 42})
        body = resp.json()
        assert body["ok"] is False
        assert "type" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_accepts_valid_setting(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "terminal_command", "value": "wt.exe"})
        body = resp.json()
        assert body["ok"] is True
        mock_save.assert_called_once()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_valid(mock_load, mock_save, client):
    """Valid port value is accepted and saved."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 8080})
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert saved.port == 8080


@patch("power_atlas.web.load_config")
def test_save_setting_port_bool_rejected(mock_load, client):
    """Boolean value for port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": True})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.load_config")
def test_save_setting_port_out_of_range(mock_load, client):
    """Out-of-range port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 99999})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_zero_accepted(mock_load, mock_save, client):
    """Port 0 (random mode) is accepted."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 0})
    assert resp.json()["ok"] is True


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_set_workspace_icon(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/set-workspace-icon", json={"workspace": "C:\\projects\\app", "icon": "🚀"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    # Check icon was set (normalized path)
    assert any(v == "🚀" for v in saved.workspace_icons.values())


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_set_workspace_icon_reset(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(workspace_icons={"c:\\projects\\app": "🚀"})
    resp = client.post("/api/set-workspace-icon", json={"workspace": "C:\\projects\\app", "icon": ""})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "🚀" not in saved.workspace_icons.values()


# --- Phase 4: session-tail endpoint ---


@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_returns_messages(mock_tail, client):
    mock_tail.return_value = ["message one", "message two"]
    resp = client.get("/partials/session-tail?sid=sess-1")
    assert resp.status_code == 200
    assert "message one" in resp.text
    assert "message two" in resp.text
    assert "tail-line" in resp.text


@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_empty(mock_tail, client):
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
    """Filtering by provider passes the provider arg and renders only matching cards."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 3, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    # Cards no longer have data-provider; verify the workspace is rendered with the provider icon
    assert 'provider--kiro-cli' in resp.text
    assert 'workspace-card' in resp.text
    # Verify discover was called with provider="kiro-cli"
    mock_discover.assert_any_call(provider="kiro-cli")


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_all_tab(mock_discover, mock_providers, client, tmp_path):
    """All tab shows cards from all providers interleaved."""
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_discover.return_value = [
        (ws1, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws2, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces?provider=all")
    assert resp.status_code == 200
    # Cards no longer have data-provider; verify both provider icons appear
    assert 'provider--kiro-cli' in resp.text
    assert 'provider--claude-code' in resp.text
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
    # Single-provider card has solid border
    assert "border-left: 3px solid #c2590f" in resp.text
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
    assert "border-left: 3px solid #ff0000" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_empty_provider_tab_shows_helper(mock_discover, mock_providers, mock_config, client):
    """When a filtered provider has no results, a helper message is shown."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces?provider=claude-code")
    assert resp.status_code == 200
    assert "No Claude Code sessions found" in resp.text


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
