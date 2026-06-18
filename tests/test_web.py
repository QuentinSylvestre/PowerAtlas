"""Tests for web module."""

from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from kiro_orchestrator.data import Session
from kiro_orchestrator.web import app


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


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces_with_counts")
def test_partials_workspaces(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1)]
    mock_sessions.return_value = [_make_session(cwd=workspace)]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert workspace in resp.text or Path(workspace).name in resp.text
    assert "1</span>" in resp.text or "card-count" in resp.text


@patch("kiro_orchestrator.web.load_config")
@patch("kiro_orchestrator.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_empty(mock_discover, mock_config, client):
    from kiro_orchestrator.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "No sessions found" in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_stale(mock_discover, mock_sessions, client):
    mock_discover.return_value = [("C:\\nonexistent\\path\\xyz", 1)]
    mock_sessions.return_value = [_make_session(cwd="C:\\nonexistent\\path\\xyz")]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "missing" in resp.text
    assert "stale" in resp.text


@patch("kiro_orchestrator.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_error(mock_discover, client):
    mock_discover.side_effect = RuntimeError("db unavailable")
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "Error" in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_search_filters(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [
        _make_session(title="fix login bug", cwd=workspace),
        _make_session(title="add feature", cwd=workspace, session_id="sess-2"),
    ]

    resp = client.get("/search?q=login")
    assert resp.status_code == 200
    assert "fix login bug" in resp.text
    assert "add feature" not in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_search_no_results(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [_make_session(cwd=workspace)]

    resp = client.get("/search?q=zzzznotfound")
    assert resp.status_code == 200
    assert "No results" in resp.text


@patch("kiro_orchestrator.web.save_config")
@patch("kiro_orchestrator.web.load_config")
def test_toggle_trust(mock_load, mock_save, client):
    from kiro_orchestrator.config import Config
    mock_load.return_value = Config(trust_all_tools=False)

    resp = client.post("/api/toggle-trust")
    assert resp.status_code == 200
    assert resp.json()["trust_all_tools"] is True
    mock_save.assert_called_once()


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
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


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces_with_counts")
def test_pinned_folder_empty_sessions(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 0)]
    mock_sessions.return_value = []

    resp = client.get("/partials/workspaces")
    assert "Loading" in resp.text or "workspace-card" in resp.text



@patch("kiro_orchestrator.web.autostart.is_enabled")
@patch("kiro_orchestrator.web.load_config")
def test_settings_page_renders(mock_config, mock_autostart, client):
    from kiro_orchestrator.config import Config
    mock_config.return_value = Config(terminal_command="wt", pinned_folders=["C:\\myapp"])
    mock_autostart.return_value = False
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Terminal" in resp.text
    assert "wt" in resp.text
    assert "C:\\myapp" in resp.text


@patch("kiro_orchestrator.web.save_config")
@patch("kiro_orchestrator.web.autostart.is_enabled")
@patch("kiro_orchestrator.web.load_config")
def test_save_settings(mock_config, mock_autostart, mock_save, client):
    from kiro_orchestrator.config import Config
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


@patch("kiro_orchestrator.web.save_config")
@patch("kiro_orchestrator.web.load_config")
@patch("kiro_orchestrator.web.data.get_sessions")
def test_pin_session(mock_sessions, mock_config, mock_save, client):
    from kiro_orchestrator.config import Config
    mock_config.return_value = Config()
    mock_sessions.return_value = []
    resp = client.post("/api/pin-session", json={"session_id": "sess-1"},
                       headers={"X-Workspace": "C:\\app"})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "sess-1" in saved.pinned_sessions


@patch("kiro_orchestrator.web.save_config")
@patch("kiro_orchestrator.web.load_config")
@patch("kiro_orchestrator.web.data.get_sessions")
def test_unpin_session(mock_sessions, mock_config, mock_save, client):
    from kiro_orchestrator.config import Config
    mock_config.return_value = Config(pinned_sessions=["sess-1", "sess-2"])
    mock_sessions.return_value = []
    resp = client.post("/api/unpin-session", json={"session_id": "sess-1"},
                       headers={"X-Workspace": "C:\\app"})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "sess-1" not in saved.pinned_sessions


@patch("kiro_orchestrator.web.load_config")
@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces_with_counts")
def test_pinned_folders_merged(mock_discover, mock_sessions, mock_config, client, tmp_path):
    from kiro_orchestrator.config import Config
    workspace = str(tmp_path)
    pinned = "C:\\my-pinned-workspace"
    mock_config.return_value = Config(pinned_folders=[pinned])
    mock_discover.return_value = [(workspace, 0)]
    mock_sessions.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert pinned in resp.text or "my-pinned-workspace" in resp.text


@patch("kiro_orchestrator.web.load_config")
@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_pinned_sessions_sorted_first(mock_discover, mock_sessions, mock_config, client, tmp_path):
    from kiro_orchestrator.config import Config
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
