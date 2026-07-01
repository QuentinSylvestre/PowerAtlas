"""Tests for data module."""

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from power_atlas.data import (
    Session, SessionCache, _FileInfo,
    discover_workspaces, get_sessions, session_cache,
)
from power_atlas import data_kiro


@pytest.fixture
def mock_sessions(tmp_path, monkeypatch):
    """Create mock session files."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr("power_atlas.data_kiro.SESSION_DIR", session_dir)
    monkeypatch.setattr("power_atlas.data.SESSION_DIR", session_dir)
    monkeypatch.setattr("power_atlas.data_kiro.SQLITE_PATH", tmp_path / "nonexistent.db")
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
    # B is more recent, returns display paths (original casing)
    assert "Projects\\B" in result[0] or "Projects/B" in result[0]


def test_discover_workspaces_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("power_atlas.data_kiro.SESSION_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr("power_atlas.data_kiro.SQLITE_PATH", tmp_path / "nonexistent.db")
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

from power_atlas.data import refresh_stale_entries, warmup_pinned


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear session cache between tests."""
    from power_atlas.data import session_cache
    session_cache.clear()
    yield
    session_cache.clear()


class TestRefreshStaleEntries:
    def test_detects_changed_jsonl_and_rereads(self, mock_sessions):
        from power_atlas.data import session_cache, _normalize_path
        cwd = "C:\\Projects\\Refresh"
        _write_session(mock_sessions, "r1", cwd)
        # Populate cache via get_sessions
        sessions = get_sessions(cwd)
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "Hello world"

        # Modify the .jsonl file content and mtime
        import time; time.sleep(0.05)
        jsonl_path = mock_sessions / "r1.jsonl"
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
        from power_atlas.data import session_cache, _normalize_path
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
        from power_atlas.data import session_cache
        # Point SESSION_DIR to non-existent path
        monkeypatch.setattr("power_atlas.data_kiro.SESSION_DIR", tmp_path / "gone")
        monkeypatch.setattr("power_atlas.data_kiro.SQLITE_PATH", tmp_path / "no.db")
        # Manually inject a cache entry so refresh has something to check
        session_cache.put("c:\\fake", [], {})
        # Should not raise
        refresh_stale_entries()


class TestWarmupPinned:
    def test_populates_cache_for_existing_folders(self, mock_sessions):
        from power_atlas.data import session_cache, _normalize_path
        cwd = "C:\\Projects\\Warm"
        _write_session(mock_sessions, "w1", cwd)

        # Write a session with cwd matching mock_sessions path
        _write_session(mock_sessions, "w2", str(mock_sessions))
        session_cache.clear()

        warmup_pinned([str(mock_sessions)])
        cached = session_cache.get(str(mock_sessions))
        assert cached is not None
        assert any(s.session_id == "w2" for s in cached)

    def test_skips_nonexistent_folders(self, mock_sessions):
        from power_atlas.data import session_cache
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




class TestNormalizePath:
    def test_forward_slashes_normalized(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("C:/Users/test/project") == _normalize_path("C:\\Users\\test\\project")

    def test_mixed_slashes_normalized(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("C:/Users\\test/project") == _normalize_path("C:\\Users\\test\\project")

    def test_trailing_separator_stripped(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("C:\\Users\\test\\") == _normalize_path("C:\\Users\\test")

    def test_case_insensitive_on_windows(self):
        import sys
        from power_atlas.data import _normalize_path
        if sys.platform == "win32":
            assert _normalize_path("C:\\Users\\Test") == _normalize_path("C:\\users\\test")



# --- Phase 4: get_session_tail ---

from power_atlas.data import get_session_tail
from power_atlas.data_kiro import _tail_cache


class TestGetSessionTail:
    def test_extracts_assistant_messages(self, mock_sessions):
        lines = [
            json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "question 1"}}),
            json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "answer 1"}}),
            json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "question 2"}}),
            json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "answer 2"}}),
        ]
        _write_session(mock_sessions, "tail1", "C:\\Work", jsonl_lines=lines)
        _tail_cache.clear()
        result = get_session_tail("tail1")
        assert result == ["answer 1", "answer 2"]

    def test_skips_tool_use_lines(self, mock_sessions):
        lines = [
            json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": [{"kind": "toolUse", "data": {"name": "read"}}]}}),
            json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "real answer"}}),
        ]
        _write_session(mock_sessions, "tail2", "C:\\Work", jsonl_lines=lines)
        _tail_cache.clear()
        result = get_session_tail("tail2")
        assert result == ["real answer"]

    def test_truncates_long_messages(self, mock_sessions):
        long_msg = "x" * 2500
        lines = [json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": long_msg}})]
        _write_session(mock_sessions, "tail3", "C:\\Work", jsonl_lines=lines)
        _tail_cache.clear()
        result = get_session_tail("tail3")
        assert len(result) == 1
        assert len(result[0]) == 2000  # capped at 2000 chars

    def test_returns_empty_for_missing_file(self, mock_sessions):
        _tail_cache.clear()
        result = get_session_tail("nonexistent")
        assert result == []

    def test_cache_returns_same_result(self, mock_sessions):
        lines = [json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "cached"}})]
        _write_session(mock_sessions, "tail4", "C:\\Work", jsonl_lines=lines)
        _tail_cache.clear()
        r1 = get_session_tail("tail4")
        r2 = get_session_tail("tail4")
        assert r1 == r2 == ["cached"]

    def test_max_lines_respected(self, mock_sessions):
        lines = [json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": f"msg{i}"}}) for i in range(10)]
        _write_session(mock_sessions, "tail5", "C:\\Work", jsonl_lines=lines)
        _tail_cache.clear()
        result = get_session_tail("tail5", max_lines=3)
        assert len(result) == 3


class TestNormalizePathLinux:
    @patch("power_atlas.data.sys.platform", "linux")
    def test_preserves_forward_slashes(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/user/project") == "/home/user/project"

    @patch("power_atlas.data.sys.platform", "linux")
    def test_strips_trailing_slash(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/user/project/") == "/home/user/project"

    @patch("power_atlas.data.sys.platform", "linux")
    def test_preserves_case(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/User/MyProject") == "/home/User/MyProject"

    @patch("power_atlas.data.sys.platform", "linux")
    def test_no_backslash_conversion(self):
        from power_atlas.data import _normalize_path
        assert _normalize_path("/home/user/a/b/c") == "/home/user/a/b/c"


# --- Compound cache key tests ---


class TestCompoundCacheKey:
    def test_provider_isolation(self):
        """Same cwd with different providers should be isolated in cache."""
        cache = SessionCache()
        s_kiro = Session("k1", "kiro session", "C:\\Work", "", "", "", "", "")
        s_claude = Session("c1", "claude session", "C:\\Work", "", "", "", "", "")
        cache.put("C:\\Work", [s_kiro], {}, provider="kiro-cli")
        cache.put("C:\\Work", [s_claude], {}, provider="claude-code")

        kiro_result = cache.get("C:\\Work", provider="kiro-cli")
        claude_result = cache.get("C:\\Work", provider="claude-code")

        assert len(kiro_result) == 1
        assert kiro_result[0].session_id == "k1"
        assert len(claude_result) == 1
        assert claude_result[0].session_id == "c1"

    def test_get_loaded_cwds_with_provider_filter(self):
        """get_loaded_cwds with provider returns only that provider's cwds."""
        cache = SessionCache()
        s1 = Session("s1", "t", "C:\\A", "", "", "", "", "")
        s2 = Session("s2", "t", "C:\\B", "", "", "", "", "")
        cache.put("C:\\A", [s1], {}, provider="kiro-cli")
        cache.put("C:\\B", [s2], {}, provider="claude-code")

        from power_atlas.data import _normalize_path
        kiro_cwds = cache.get_loaded_cwds("kiro-cli")
        claude_cwds = cache.get_loaded_cwds("claude-code")
        all_cwds = cache.get_loaded_cwds()

        assert _normalize_path("C:\\A") in kiro_cwds
        assert _normalize_path("C:\\B") not in kiro_cwds
        assert _normalize_path("C:\\B") in claude_cwds
        assert _normalize_path("C:\\A") not in claude_cwds
        assert _normalize_path("C:\\A") in all_cwds
        assert _normalize_path("C:\\B") in all_cwds




# --- Claude Code adapter tests ---

from power_atlas import data_claude


class TestClaudeIsAvailable:
    def test_available_when_projects_dir_has_content(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "some-project").mkdir()
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        assert data_claude.is_available() is True

    def test_not_available_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", tmp_path / "nonexistent")
        assert data_claude.is_available() is False

    def test_not_available_when_dir_empty(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        assert data_claude.is_available() is False


class TestClaudePathToFolderName:
    def test_windows_path(self):
        result = data_claude._path_to_folder_name("C:\\Users\\QSylvestre.POLESTAR")
        assert result == "C--Users-QSylvestre-POLESTAR"

    def test_unix_path(self):
        result = data_claude._path_to_folder_name("/home/user/my-project")
        assert result == "-home-user-my-project"

    def test_spaces_replaced(self):
        result = data_claude._path_to_folder_name("C:\\Users\\My User\\project")
        assert result == "C--Users-My-User-project"

    def test_dots_replaced(self):
        result = data_claude._path_to_folder_name("C:\\Users\\user.name\\proj")
        assert result == "C--Users-user-name-proj"


class TestClaudeBuildPathIndex:
    def test_builds_index_from_history(self, tmp_path, monkeypatch):
        history = tmp_path / "history.jsonl"
        lines = [
            json.dumps({"display": "hello", "timestamp": 1000, "project": "C:\\Users\\Dev\\ProjectA"}),
            json.dumps({"display": "world", "timestamp": 2000, "project": "C:\\Users\\Dev\\ProjectB"}),
            json.dumps({"display": "no project"}),  # no project field
        ]
        history.write_text("\n".join(lines), encoding="utf-8")
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_HISTORY_PATH", history)
        # Reset cache
        monkeypatch.setattr("power_atlas.data_claude._path_index_cache", None)

        index = data_claude._build_path_index()
        assert "C--Users-Dev-ProjectA" in index
        assert index["C--Users-Dev-ProjectA"] == "C:\\Users\\Dev\\ProjectA"
        assert "C--Users-Dev-ProjectB" in index

    def test_returns_empty_when_no_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_HISTORY_PATH", tmp_path / "nope.jsonl")
        monkeypatch.setattr("power_atlas.data_claude._path_index_cache", None)
        assert data_claude._build_path_index() == {}


class TestClaudeDiscoverWorkspaces:
    def test_discovers_projects(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()

        # Create project folder with session files
        proj_folder = projects_dir / "C--Users-Dev-MyProject"
        proj_folder.mkdir()
        (proj_folder / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl").write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
            encoding="utf-8",
        )

        # History for path resolution
        history = tmp_path / "history.jsonl"
        history.write_text(
            json.dumps({"display": "hi", "timestamp": 1000, "project": "C:\\Users\\Dev\\MyProject"}),
            encoding="utf-8",
        )

        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_HISTORY_PATH", history)
        monkeypatch.setattr("power_atlas.data_claude._path_index_cache", None)

        results = data_claude.discover_workspaces()
        assert len(results) == 1
        cwd, count, updated_at = results[0]
        assert cwd == "C:\\Users\\Dev\\MyProject"
        assert count == 1
        assert updated_at  # non-empty ISO timestamp

    def test_skips_empty_folders(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "empty-folder").mkdir()

        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_HISTORY_PATH", tmp_path / "nope.jsonl")
        monkeypatch.setattr("power_atlas.data_claude._path_index_cache", None)

        results = data_claude.discover_workspaces()
        assert len(results) == 0


class TestClaudeLoadSessions:
    def _make_project(self, tmp_path, monkeypatch, folder_name, sessions_data):
        """Helper to set up a mock Claude Code project."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(exist_ok=True)
        proj_folder = projects_dir / folder_name
        proj_folder.mkdir(exist_ok=True)

        for sid, lines in sessions_data.items():
            (proj_folder / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        return proj_folder

    def test_parses_ai_title(self, tmp_path, monkeypatch):
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        lines = [
            json.dumps({"type": "mode", "mode": "normal", "sessionId": sid}),
            json.dumps({"type": "ai-title", "aiTitle": "Fix the login bug", "sessionId": sid}),
            json.dumps({"parentUuid": "x", "type": "user", "message": {"role": "user", "content": "Please fix login"}, "uuid": "u1"}),
            json.dumps({"parentUuid": "u1", "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done!"}]}, "uuid": "u2"}),
        ]
        self._make_project(tmp_path, monkeypatch, "C--Work", {sid: lines})

        sessions, stats = data_claude.load_sessions("C:\\Work")
        assert len(sessions) == 1
        assert sessions[0].title == "Fix the login bug"
        assert sessions[0].first_prompt == "Please fix login"
        assert sessions[0].session_id == sid

    def test_fallback_title_from_first_user_message(self, tmp_path, monkeypatch):
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        lines = [
            json.dumps({"type": "mode", "mode": "normal", "sessionId": sid}),
            json.dumps({"parentUuid": "x", "type": "user", "message": {"role": "user", "content": "Refactor the auth module completely"}, "uuid": "u1"}),
            json.dumps({"parentUuid": "u1", "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "OK"}]}, "uuid": "u2"}),
        ]
        self._make_project(tmp_path, monkeypatch, "C--Work", {sid: lines})

        sessions, stats = data_claude.load_sessions("C:\\Work")
        assert len(sessions) == 1
        # No ai-title, so title is first 80 chars of first user message
        assert sessions[0].title == "Refactor the auth module completely"
        assert sessions[0].first_prompt == "Refactor the auth module completely"

    def test_skips_non_uuid_files(self, tmp_path, monkeypatch):
        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}, "uuid": "u1"}),
        ]
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "C--Work"
        proj.mkdir()
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")
        (proj / "_meta.jsonl").write_text("not a session", encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)

        sessions, stats = data_claude.load_sessions("C:\\Work")
        assert len(sessions) == 1
        assert sessions[0].session_id == sid
