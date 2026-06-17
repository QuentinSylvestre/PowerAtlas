"""Tests for data module."""

import json
from pathlib import Path

import pytest

from kiro_orchestrator.data import discover_workspaces, get_sessions, Session


@pytest.fixture
def mock_sessions(tmp_path, monkeypatch):
    """Create mock session files."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr("kiro_orchestrator.data.SESSION_DIR", session_dir)
    monkeypatch.setattr("kiro_orchestrator.data.SQLITE_PATH", tmp_path / "nonexistent.db")
    return session_dir


def _write_session(session_dir: Path, session_id: str, cwd: str, **kwargs):
    """Helper to write a session .json + .jsonl."""
    meta = {
        "session_id": session_id,
        "cwd": cwd,
        "created_at": kwargs.get("created_at", "2026-01-01T00:00:00Z"),
        "updated_at": kwargs.get("updated_at", "2026-01-02T00:00:00Z"),
        "title": kwargs.get("title", f"Session {session_id}"),
        "parent_session_id": kwargs.get("parent_session_id", None),
    }
    (session_dir / f"{session_id}.json").write_text(json.dumps(meta), encoding="utf-8")

    lines = kwargs.get("jsonl_lines", [
        json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "Hello world"}}),
        json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "Hi there, how can I help?"}}),
    ])
    (session_dir / f"{session_id}.jsonl").write_text("\n".join(lines), encoding="utf-8")


def test_discover_workspaces_with_data(mock_sessions):
    _write_session(mock_sessions, "s1", "C:\\Projects\\A", updated_at="2026-06-01T00:00:00Z")
    _write_session(mock_sessions, "s2", "C:\\Projects\\B", updated_at="2026-06-02T00:00:00Z")
    result = discover_workspaces()
    assert len(result) == 2
    # B is more recent
    assert "projects\\b" in result[0] or "projects/b" in result[0]


def test_discover_workspaces_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("kiro_orchestrator.data.SESSION_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr("kiro_orchestrator.data.SQLITE_PATH", tmp_path / "nonexistent.db")
    assert discover_workspaces() == []


def test_discover_workspaces_filters_subagents(mock_sessions):
    _write_session(mock_sessions, "s1", "C:\\Projects\\A")
    _write_session(mock_sessions, "s2", "C:\\Projects\\SubAgent", parent_session_id="s1")
    result = discover_workspaces()
    assert len(result) == 1


def test_get_sessions_returns_populated(mock_sessions):
    _write_session(mock_sessions, "s1", "C:\\Work", title="My session")
    sessions = get_sessions("C:\\Work")
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == "s1"
    assert s.title == "My session"
    assert s.first_prompt == "Hello world"
    assert s.last_reply_tail == "Hi there, how can I help?"


def test_get_sessions_filters_subagents(mock_sessions):
    _write_session(mock_sessions, "s1", "C:\\Work")
    _write_session(mock_sessions, "s2", "C:\\Work", parent_session_id="s1")
    sessions = get_sessions("C:\\Work")
    assert len(sessions) == 1
    assert sessions[0].session_id == "s1"


def test_malformed_json_skipped(mock_sessions):
    """Bad .json file should be skipped without crash."""
    (mock_sessions / "bad.json").write_text("not json{{{", encoding="utf-8")
    _write_session(mock_sessions, "good", "C:\\Work")
    sessions = get_sessions("C:\\Work")
    assert len(sessions) == 1


def test_malformed_jsonl_skipped(mock_sessions):
    """Bad .jsonl lines should be skipped, session still returned."""
    _write_session(mock_sessions, "s1", "C:\\Work", jsonl_lines=[
        "not valid json",
        json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "valid prompt"}}),
    ])
    sessions = get_sessions("C:\\Work")
    assert len(sessions) == 1
    assert sessions[0].first_prompt == "valid prompt"


def test_missing_jsonl_still_returns_session(mock_sessions):
    """Session with metadata but no .jsonl should still be returned with empty prompts."""
    meta = {"session_id": "s1", "cwd": "C:\\Work", "created_at": "", "updated_at": "", "title": "T"}
    (mock_sessions / "s1.json").write_text(json.dumps(meta), encoding="utf-8")
    sessions = get_sessions("C:\\Work")
    assert len(sessions) == 1
    assert sessions[0].first_prompt == ""
    assert sessions[0].last_prompt == ""
