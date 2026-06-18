"""Tests for web module."""

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
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_partials_workspaces(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [_make_session(cwd=workspace)]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert workspace in resp.text
    assert "test session" in resp.text
    assert "1 session" in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_partials_workspaces_empty(mock_discover, mock_sessions, client):
    mock_discover.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "No sessions found" in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_partials_workspaces_stale(mock_discover, mock_sessions, client):
    mock_discover.return_value = ["C:\\nonexistent\\path\\xyz"]
    mock_sessions.return_value = [_make_session(cwd="C:\\nonexistent\\path\\xyz")]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "folder missing" in resp.text
    assert "stale" in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_partials_workspaces_error(mock_discover, mock_sessions, client):
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

    resp = client.get("/partials/workspaces")
    assert "my title" in resp.text
    assert "first question" in resp.text
    assert "last question" in resp.text
    assert "final answer" in resp.text


@patch("kiro_orchestrator.web.data.get_sessions")
@patch("kiro_orchestrator.web.data.discover_workspaces")
def test_pinned_folder_empty_sessions(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = []

    resp = client.get("/partials/workspaces")
    assert "No sessions yet" in resp.text
