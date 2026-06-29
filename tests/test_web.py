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
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z")]
    mock_sessions.return_value = [_make_session(cwd=workspace)]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert workspace in resp.text or Path(workspace).name in resp.text
    assert "1</span>" in resp.text or "card-count" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_empty(mock_discover, mock_config, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "No sessions found" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_stale(mock_discover, mock_sessions, client):
    mock_discover.return_value = [("C:\\nonexistent\\path\\xyz", 1, "2026-01-01T00:00:00Z")]
    mock_sessions.return_value = [_make_session(cwd="C:\\nonexistent\\path\\xyz")]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "missing" in resp.text
    assert "stale" in resp.text


@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_error(mock_discover, client):
    mock_discover.side_effect = RuntimeError("db unavailable")
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "Error" in resp.text


@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_filters(mock_discover, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [
        (workspace, 2, "2026-01-01T00:00:00Z"),
        ("C:\\other\\project", 1, "2026-01-01T00:00:00Z"),
    ]

    resp = client.get(f"/search?q={Path(workspace).name}")
    assert resp.status_code == 200
    assert Path(workspace).name in resp.text


@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_no_results(mock_discover, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "")]

    resp = client.get("/search?q=zzzznotfound")
    assert resp.status_code == 200
    assert "No results" in resp.text


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_toggle_trust(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(trust_all_tools=False)

    resp = client.post("/api/toggle-trust")
    assert resp.status_code == 200
    assert resp.json()["trust_all_tools"] is True
    mock_save.assert_called_once()


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
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_folder_empty_sessions(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 0, "")]
    mock_sessions.return_value = []

    resp = client.get("/partials/workspaces")
    assert "Loading" in resp.text or "workspace-card" in resp.text



@patch("power_atlas.web.save_config")
@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_save_settings(mock_config, mock_autostart, mock_save, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_autostart.return_value = False
    resp = client.post("/api/settings", data={
        "terminal_command": "pwsh",
        "trust_all_tools": "on",
        "pinned_folders": "C:\\a|C:\\b",
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert saved.terminal_command == "pwsh"
    assert saved.trust_all_tools is True
    assert saved.pinned_folders == ["C:\\a", "C:\\b"]


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


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_folders_merged(mock_discover, mock_sessions, mock_config, client, tmp_path):
    from power_atlas.config import Config
    workspace = str(tmp_path)
    pinned = "C:\\my-pinned-workspace"
    mock_config.return_value = Config(pinned_folders=[pinned])
    mock_discover.return_value = [(workspace, 0, "")]
    mock_sessions.return_value = []
    resp = client.get("/partials/workspaces")
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
        resp = client.post("/api/save-setting", json={"key": "trust_all_tools", "value": "yes"})
        body = resp.json()
        assert body["ok"] is False
        assert "type" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_accepts_valid_setting(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "trust_all_tools", "value": True})
        body = resp.json()
        assert body["ok"] is True
        mock_save.assert_called_once()


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


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_launcher_create(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/launcher/create", json={
        "name": "Dev Server", "command": "npm", "custom_args": "start", "cwd": "C:\\proj", "icon": "🔥"
    })
    assert resp.status_code == 200
    assert "created" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert len(saved.custom_launchers) == 1
    assert saved.custom_launchers[0]["name"] == "Dev Server"
    assert saved.custom_launchers[0]["id"]  # UUID generated


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_launcher_delete(mock_load, mock_save, client):
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
