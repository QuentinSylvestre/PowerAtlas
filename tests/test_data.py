"""Tests for data module."""

import json
import threading
from pathlib import Path

import pytest

from kiro_orchestrator.data import (
    Session, SessionCache, _FileInfo, _load_sessions,
    discover_workspaces, get_sessions, session_cache,
)


@pytest.fixture
def mock_sessions(tmp_path, monkeypatch):
    """Create mock session files."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr("kiro_orchestrator.data.SESSION_DIR", session_dir)
    monkeypatch.setattr("kiro_orchestrator.data.SQLITE_PATH", tmp_path / "nonexistent.db")
    session_cache.clear()
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



# --- Phase 2: refresh_stale_entries and warmup_pinned ---

from kiro_orchestrator.data import refresh_stale_entries, warmup_pinned


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear session cache between tests."""
    from kiro_orchestrator.data import session_cache
    session_cache.clear()
    yield
    session_cache.clear()


class TestRefreshStaleEntries:
    def test_detects_changed_jsonl_and_rereads(self, mock_sessions):
        from kiro_orchestrator.data import session_cache, _normalize_path, _FileInfo
        cwd = "C:\\Projects\\Refresh"
        _write_session(mock_sessions, "r1", cwd)
        # Populate cache via get_sessions
        sessions = get_sessions(cwd)
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "Hello world"

        # Modify the .jsonl file content and mtime
        jsonl_path = mock_sessions / "r1.jsonl"
        import time; time.sleep(0.05)
        jsonl_path.write_text(
            json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "Updated prompt"}}) + "\n"
            + json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "Updated reply"}}),
            encoding="utf-8",
        )

        refresh_stale_entries()
        cached = session_cache.get(_normalize_path(cwd))
        assert cached is not None
        assert cached[0].first_prompt == "Updated prompt"

    def test_skips_unchanged_files(self, mock_sessions):
        from kiro_orchestrator.data import session_cache, _normalize_path
        cwd = "C:\\Projects\\Unchanged"
        _write_session(mock_sessions, "u1", cwd)
        get_sessions(cwd)

        # Record stats before refresh
        stats_before = session_cache.get_file_stats(_normalize_path(cwd))

        refresh_stale_entries()

        # Stats should be identical (no reload happened)
        stats_after = session_cache.get_file_stats(_normalize_path(cwd))
        assert stats_before == stats_after

    def test_handles_missing_dir_gracefully(self, tmp_path, monkeypatch):
        from kiro_orchestrator.data import session_cache, _normalize_path, _FileInfo
        # Point SESSION_DIR to non-existent path
        monkeypatch.setattr("kiro_orchestrator.data.SESSION_DIR", tmp_path / "gone")
        monkeypatch.setattr("kiro_orchestrator.data.SQLITE_PATH", tmp_path / "no.db")
        # Manually inject a cache entry so refresh has something to check
        session_cache.put("c:\\fake", [], {})
        # Should not raise
        refresh_stale_entries()


class TestWarmupPinned:
    def test_populates_cache_for_existing_folders(self, mock_sessions):
        from kiro_orchestrator.data import session_cache, _normalize_path
        cwd = "C:\\Projects\\Warm"
        _write_session(mock_sessions, "w1", cwd)

        # Pretend the folder exists by patching Path.exists for that path
        warmup_pinned([str(mock_sessions)])
        # Since mock_sessions is the SESSION_DIR but cwd is C:\Projects\Warm,
        # let's use a path that exists and has sessions
        # Actually: warmup calls get_sessions(folder), so let's use the cwd directly
        # We need a folder that exists. mock_sessions exists, so use it as a proxy cwd.
        # Write a session with cwd matching mock_sessions path
        _write_session(mock_sessions, "w2", str(mock_sessions))
        session_cache.clear()

        warmup_pinned([str(mock_sessions)])
        cached = session_cache.get(str(mock_sessions))
        assert cached is not None
        assert any(s.session_id == "w2" for s in cached)

    def test_skips_nonexistent_folders(self, mock_sessions):
        from kiro_orchestrator.data import session_cache
        # Should not raise, should not populate cache
        warmup_pinned(["C:\\NonExistent\\Path\\12345"])
        assert session_cache.get("C:\\NonExistent\\Path\\12345") is None


# --- SessionCache tests ---


def test_cache_hit_no_reread(mock_sessions):
    """Second call returns cached data without reading files again."""
    _write_session(mock_sessions, "s1", "C:\\Work")
    result1 = get_sessions("C:\\Work")
    assert len(result1) == 1
    # Remove the files — cache should still serve
    (mock_sessions / "s1.json").unlink()
    (mock_sessions / "s1.jsonl").unlink()
    result2 = get_sessions("C:\\Work")
    assert len(result2) == 1
    assert result2[0].session_id == "s1"


def test_cache_miss_triggers_load(mock_sessions):
    """First call reads from disk and populates cache."""
    _write_session(mock_sessions, "s1", "C:\\Fresh")
    result = get_sessions("C:\\Fresh")
    assert len(result) == 1
    # Verify cache is populated
    cached = session_cache.get("C:\\Fresh")
    assert cached is not None
    assert len(cached) == 1


def test_cache_get_returns_copy(mock_sessions):
    """Mutating get() return value does not affect cache."""
    _write_session(mock_sessions, "s1", "C:\\Safe")
    get_sessions("C:\\Safe")
    result = session_cache.get("C:\\Safe")
    result.append(Session("fake", "fake", "fake", "", "", "", "", ""))
    # Cache should still have original length
    assert len(session_cache.get("C:\\Safe")) == 1


def test_cache_thread_safety():
    """Concurrent get/put operations do not corrupt state."""
    cache = SessionCache()
    barrier = threading.Barrier(4)
    errors = []

    def writer(idx):
        try:
            barrier.wait(timeout=5)
            s = Session(f"s{idx}", f"title{idx}", "C:\\T", "", "", "", "", "")
            cache.put("C:\\T", [s], {f"f{idx}": _FileInfo(1.0, 100)})
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            barrier.wait(timeout=5)
            result = cache.get("C:\\T")
            # Should be None or a valid list
            if result is not None:
                assert isinstance(result, list)
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=writer, args=(0,)),
        threading.Thread(target=writer, args=(1,)),
        threading.Thread(target=reader),
        threading.Thread(target=reader),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors


def test_cache_clear_resets_state():
    """clear() removes all cached data."""
    cache = SessionCache()
    s = Session("s1", "t", "C:\\X", "", "", "", "", "")
    cache.put("C:\\X", [s], {"f": _FileInfo(1.0, 50)})
    assert cache.get("C:\\X") is not None
    cache.clear()
    assert cache.get("C:\\X") is None
    assert cache.get_loaded_cwds() == set()
    assert cache.get_file_stats("C:\\X") == {}
