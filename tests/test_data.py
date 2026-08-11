"""Tests for data module."""

import contextlib
import itertools
import json
import os
import tempfile
import threading
import time
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


# Windows stamps file and directory timestamps from the ~15 ms system clock
# tick, so two writes issued back to back routinely land on the identical
# mtime. The production parse caches key on (mtime, size) and the kiro-cli
# cwd index keys on the session directory's own mtime, so a test that mutates
# a fixture file and re-reads it is otherwise asking the cache to notice a
# change the filesystem never recorded. Stamping every fixture write with a
# strictly increasing mtime makes the mutation observable by construction,
# leaving the assertions to test the cache rather than the clock.
_MTIME_BASE = time.time() + 60
_mtime_ticks = itertools.count()


def _bump_mtime(*paths: Path) -> None:
    """Stamp paths with an mtime distinct from every earlier _bump_mtime call."""
    stamp = _MTIME_BASE + next(_mtime_ticks) * 0.01
    for p in paths:
        os.utime(p, (stamp, stamp))


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
    meta_path = session_dir / f"{session_id}.json"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    lines = kwargs.get("jsonl_lines", [
        json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "Hello world"}}),
        json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "Hi there, how can I help?"}}),
    ])
    jsonl_path = session_dir / f"{session_id}.jsonl"
    jsonl_path.write_text("\n".join(lines), encoding="utf-8")
    _bump_mtime(meta_path, jsonl_path, session_dir)


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


def _reset_kiro_caches():
    """Drop data_kiro's process-global parse caches and cwd index.

    In production SESSION_DIR is fixed for the life of the process, so these
    are never stale. Tests repoint it at a fresh tmp_path per test, and the
    cwd index is guarded only by that directory's mtime — which two tmp dirs
    created milliseconds apart share far more often than not. Without this
    reset a test reads the *previous* test's index and sees no sessions at all.
    """
    data_kiro._meta_cache.clear()
    data_kiro._cwd_index = {}
    data_kiro._cwd_index_mtime = None
    data_kiro._prompts_cache.clear()
    data_kiro._tail_cache.clear()
    data_kiro._first_prompt_cache.clear()


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear session cache and discovery cache between tests."""
    from power_atlas.data import session_cache
    from power_atlas import data
    session_cache.clear()
    data._cache.clear()
    _reset_kiro_caches()
    yield
    session_cache.clear()
    data._cache.clear()
    _reset_kiro_caches()


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
        jsonl_path = mock_sessions / "r1.jsonl"
        jsonl_path.write_text(
            json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "Updated prompt"}}) + "\n"
            + json.dumps({"version": "v1", "kind": "AssistantMessage", "data": {"content": "Updated reply"}}),
            encoding="utf-8",
        )
        _bump_mtime(jsonl_path)

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


# --- Kiro IDE adapter tests ---

from power_atlas import data_kiro_ide


class TestKiroIdeEncodeDecode:
    def test_encode_path_basic(self):
        encoded = data_kiro_ide._encode_path("c:\\Users\\Test")
        # Should be URL-safe base64 with - / _ / ? replacements
        assert "+" not in encoded
        assert "/" not in encoded
        assert "=" not in encoded

    def test_decode_roundtrip(self):
        original = "c:\\Users\\QSylvestre.POLESTAR\\Documents\\test"
        encoded = data_kiro_ide._encode_path(original)
        decoded = data_kiro_ide._decode_folder_name(encoded)
        # Decode should recover the original (no trailing garbage for exact-length inputs)
        assert decoded.startswith(original) or original in decoded

    def test_encode_replaces_correctly(self):
        # Test specific character replacements
        path = "c:\\test"
        import base64
        raw = base64.b64encode(path.encode("utf-8")).decode("ascii")
        encoded = data_kiro_ide._encode_path(path)
        expected = raw.replace("+", "-").replace("/", "_").replace("=", "?")
        assert encoded == expected


class TestKiroIdeIsAvailable:
    def test_available_when_dir_has_content(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        (sessions_dir / "some_folder").mkdir()
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        assert data_kiro_ide.is_available() is True

    def test_not_available_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", tmp_path / "nonexistent")
        assert data_kiro_ide.is_available() is False

    def test_not_available_when_dir_empty(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        assert data_kiro_ide.is_available() is False


class TestKiroIdeDiscoverWorkspaces:
    def _make_workspace(self, sessions_dir, folder_name, sessions_data):
        """Helper to create a mock workspace folder with sessions.json."""
        folder = sessions_dir / folder_name
        folder.mkdir(exist_ok=True)
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")
        return folder

    def test_discovers_workspaces(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        self._make_workspace(sessions_dir, "folder_a", [
            {"sessionId": "s1", "title": "Session 1", "dateCreated": "1700000000000", "workspaceDirectory": "C:\\ProjectA"},
        ])
        self._make_workspace(sessions_dir, "folder_b", [
            {"sessionId": "s2", "title": "Session 2", "dateCreated": "1700100000000", "workspaceDirectory": "C:\\ProjectB"},
            {"sessionId": "s3", "title": "Session 3", "dateCreated": "1700200000000", "workspaceDirectory": "C:\\ProjectB"},
        ])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        results = data_kiro_ide.discover_workspaces()
        assert len(results) == 2
        # ProjectB has more recent dateCreated, should be first
        assert results[0][0] == "C:\\ProjectB"
        assert results[0][1] == 2  # 2 sessions
        assert results[1][0] == "C:\\ProjectA"
        assert results[1][1] == 1  # 1 session

    def test_empty_sessions_json_skipped(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        self._make_workspace(sessions_dir, "empty_folder", [])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        results = data_kiro_ide.discover_workspaces()
        assert results == []

    def test_malformed_json_skipped(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        folder = sessions_dir / "bad_folder"
        folder.mkdir()
        (folder / "sessions.json").write_text("not valid json{{{", encoding="utf-8")

        # Also add a valid workspace
        self._make_workspace(sessions_dir, "good_folder", [
            {"sessionId": "s1", "title": "OK", "dateCreated": "1700000000000", "workspaceDirectory": "C:\\Good"},
        ])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        results = data_kiro_ide.discover_workspaces()
        assert len(results) == 1
        assert results[0][0] == "C:\\Good"

    def test_no_workspace_directory_falls_back_to_decode(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        # Create folder with sessions that lack workspaceDirectory
        self._make_workspace(sessions_dir, "some_folder", [
            {"sessionId": "s1", "title": "S", "dateCreated": "1700000000000"},
        ])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        results = data_kiro_ide.discover_workspaces()
        # Should try decode; may or may not produce valid path, but shouldn't crash
        # The decode of "some_folder" won't be a valid path, so it'll be skipped or included
        assert isinstance(results, list)

    def test_returns_empty_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", tmp_path / "nonexistent")
        assert data_kiro_ide.discover_workspaces() == []


class TestKiroIdeLoadSessions:
    def _make_workspace_with_sessions(self, sessions_dir, cwd, sessions, folder_name=None):
        """Helper to create a workspace folder with sessions.json and session files."""
        # Use explicit folder_name to avoid Windows-incompatible chars from base64 encoding
        if folder_name is None:
            folder_name = cwd.replace("\\", "_").replace(":", "_").replace("/", "_")
        folder = sessions_dir / folder_name
        folder.mkdir(exist_ok=True)

        sessions_index = []
        for sid, title, date_created, history in sessions:
            sessions_index.append({
                "sessionId": sid,
                "title": title,
                "dateCreated": date_created,
                "workspaceDirectory": cwd,
            })
            if history is not None:
                session_data = {
                    "history": history,
                    "title": title,
                    "sessionId": sid,
                    "workspaceDirectory": cwd,
                }
                (folder / f"{sid}.json").write_text(json.dumps(session_data), encoding="utf-8")

        (folder / "sessions.json").write_text(json.dumps(sessions_index), encoding="utf-8")
        return folder

    def test_loads_sessions_with_content(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        history = [
            {"message": {"role": "user", "content": [{"type": "text", "text": "Hello world"}]}, "contextItems": []},
            {"message": {"role": "assistant", "content": "Hi there!"}, "contextItems": []},
        ]
        self._make_workspace_with_sessions(sessions_dir, "C:\\MyProject", [
            ("sess-1", "My Session", "1700000000000", history),
        ])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        sessions, stats = data_kiro_ide.load_sessions("C:\\MyProject")
        assert len(sessions) == 1
        s = sessions[0]
        assert s.session_id == "sess-1"
        assert s.title == "My Session"
        assert s.first_prompt == "Hello world"
        assert s.last_reply_tail == "Hi there!"
        assert stats  # should have file stats

    def test_missing_session_file_still_returns_entry(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        # Pass None for history to skip creating session file
        self._make_workspace_with_sessions(sessions_dir, "C:\\Project", [
            ("sess-missing", "No File", "1700000000000", None),
        ])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        sessions, stats = data_kiro_ide.load_sessions("C:\\Project")
        assert len(sessions) == 1
        assert sessions[0].session_id == "sess-missing"
        assert sessions[0].first_prompt == ""
        assert sessions[0].last_reply_tail == ""

    def test_returns_empty_for_unknown_workspace(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        sessions, stats = data_kiro_ide.load_sessions("C:\\NonExistent")
        assert sessions == []
        assert stats == {}

    def test_multiple_sessions_sorted_by_date(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        history = [
            {"message": {"role": "user", "content": [{"type": "text", "text": "test"}]}, "contextItems": []},
        ]
        self._make_workspace_with_sessions(sessions_dir, "C:\\Multi", [
            ("older", "Older", "1700000000000", history),
            ("newer", "Newer", "1700200000000", history),
        ])

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        sessions, _ = data_kiro_ide.load_sessions("C:\\Multi")
        assert len(sessions) == 2
        # Newer should be first (sorted by created_at desc)
        assert sessions[0].session_id == "newer"
        assert sessions[1].session_id == "older"

    def test_finds_workspace_by_scan_fallback(self, tmp_path, monkeypatch):
        """When encode doesn't match folder name, scan finds it via workspaceDirectory."""
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        # Create with a non-standard folder name (not the expected encoding)
        folder = sessions_dir / "arbitrary_name"
        folder.mkdir()
        sessions_data = [
            {"sessionId": "s1", "title": "T", "dateCreated": "1700000000000", "workspaceDirectory": "C:\\FindMe"},
        ]
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")
        history = [{"message": {"role": "user", "content": [{"type": "text", "text": "found"}]}, "contextItems": []}]
        session_data = {"history": history, "title": "T", "sessionId": "s1", "workspaceDirectory": "C:\\FindMe"}
        (folder / "s1.json").write_text(json.dumps(session_data), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)

        sessions, _ = data_kiro_ide.load_sessions("C:\\FindMe")
        assert len(sessions) == 1
        assert sessions[0].first_prompt == "found"


class TestKiroIdeFindSessionWorkspace:
    def test_finds_workspace_for_known_session(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        folder = sessions_dir / "ws_folder"
        folder.mkdir()
        sessions_data = [
            {"sessionId": "target-sess", "title": "T", "dateCreated": "1700000000000", "workspaceDirectory": "C:\\Target"},
        ]
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        # Clear cached reverse index
        monkeypatch.setattr("power_atlas.data_kiro_ide._reverse_index", None)

        result = data_kiro_ide.find_session_workspace("target-sess")
        assert result == "C:\\Target"

    def test_returns_none_for_unknown_session(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr("power_atlas.data_kiro_ide._reverse_index", None)

        result = data_kiro_ide.find_session_workspace("nonexistent-session")
        assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", tmp_path / "nonexistent")
        monkeypatch.setattr("power_atlas.data_kiro_ide._reverse_index", None)

        result = data_kiro_ide.find_session_workspace("any-session")
        assert result is None


class TestKiroIdeGetSessionTail:
    def test_extracts_assistant_messages(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        cwd = "C:\\TailTest"
        folder = sessions_dir / "tail_test_folder"
        folder.mkdir()

        sessions_data = [{"sessionId": "t1", "title": "T", "dateCreated": "1700000000000", "workspaceDirectory": cwd}]
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")

        history = [
            {"message": {"role": "user", "content": [{"type": "text", "text": "q1"}]}, "contextItems": []},
            {"message": {"role": "assistant", "content": "answer 1"}, "contextItems": []},
            {"message": {"role": "user", "content": [{"type": "text", "text": "q2"}]}, "contextItems": []},
            {"message": {"role": "assistant", "content": "answer 2"}, "contextItems": []},
        ]
        (folder / "t1.json").write_text(json.dumps({"history": history}), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        data_kiro_ide._tail_cache.clear()

        result = data_kiro_ide.get_session_tail("t1", cwd)
        assert result == ["answer 1", "answer 2"]

    def test_max_lines_respected(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        cwd = "C:\\MaxLines"
        folder = sessions_dir / "max_lines_folder"
        folder.mkdir()

        sessions_data = [{"sessionId": "ml1", "title": "T", "dateCreated": "1700000000000", "workspaceDirectory": cwd}]
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")

        history = [
            {"message": {"role": "assistant", "content": f"msg{i}"}, "contextItems": []}
            for i in range(10)
        ]
        (folder / "ml1.json").write_text(json.dumps({"history": history}), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        data_kiro_ide._tail_cache.clear()

        result = data_kiro_ide.get_session_tail("ml1", cwd, max_lines=3)
        assert len(result) == 3

    def test_returns_empty_for_missing_session(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        data_kiro_ide._tail_cache.clear()

        result = data_kiro_ide.get_session_tail("nonexistent", "C:\\Whatever")
        assert result == []


class TestKiroIdeGetFirstPrompt:
    def test_extracts_first_user_message(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()

        cwd = "C:\\PromptTest"
        folder = sessions_dir / "prompt_test_folder"
        folder.mkdir()

        sessions_data = [{"sessionId": "p1", "title": "T", "dateCreated": "1700000000000", "workspaceDirectory": cwd}]
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")

        history = [
            {"message": {"role": "assistant", "content": "I can help!"}, "contextItems": []},
            {"message": {"role": "user", "content": [{"type": "text", "text": "First question"}]}, "contextItems": []},
            {"message": {"role": "assistant", "content": "Here's the answer"}, "contextItems": []},
        ]
        (folder / "p1.json").write_text(json.dumps({"history": history}), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        data_kiro_ide._first_prompt_cache.clear()

        result = data_kiro_ide.get_first_prompt("p1", cwd)
        assert result == "First question"

    def test_returns_empty_for_missing_session(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        data_kiro_ide._first_prompt_cache.clear()

        result = data_kiro_ide.get_first_prompt("nonexistent", "C:\\Whatever")
        assert result == ""


class TestKiroIdeRefreshStale:
    def test_detects_changed_file(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text("[]", encoding="utf-8")
        st = f.stat()
        old_stats = {str(f): _FileInfo(mtime=st.st_mtime - 1, size=2)}

        assert data_kiro_ide.refresh_stale_entries_for_cwd("C:\\X", old_stats) is True

    def test_no_change_when_same(self, tmp_path):
        f = tmp_path / "test.json"
        f.write_text("[]", encoding="utf-8")
        st = f.stat()
        old_stats = {str(f): _FileInfo(mtime=st.st_mtime, size=st.st_size)}

        assert data_kiro_ide.refresh_stale_entries_for_cwd("C:\\X", old_stats) is False

    def test_detects_deleted_file(self, tmp_path):
        old_stats = {str(tmp_path / "gone.json"): _FileInfo(mtime=1.0, size=10)}
        assert data_kiro_ide.refresh_stale_entries_for_cwd("C:\\X", old_stats) is True

    def test_empty_stats_returns_false(self):
        assert data_kiro_ide.refresh_stale_entries_for_cwd("C:\\X", {}) is False


# --- Phase 3: Discovery lock, fail-closed, frozen Session, caching ---

from power_atlas import data
from power_atlas.data import discover_workspaces_with_counts, PROVIDERS


class TestDiscoverFailClosed:
    def test_discover_unknown_provider_returns_empty(self, mock_sessions):
        """Calling with an unknown provider returns [] and does not poison _cache."""
        data._cache.clear()
        result = discover_workspaces_with_counts(provider="bogus")
        assert result == []
        # Verify no new cache key was created for the bogus provider
        assert not any("bogus" in k for k in data._cache)

    def test_discover_known_provider_still_works(self, mock_sessions):
        """Known provider still triggers discovery."""
        _write_session(mock_sessions, "s1", "C:\\Projects\\X", updated_at="2026-06-01T00:00:00Z")
        data._cache.clear()
        result = discover_workspaces_with_counts(provider="kiro-cli")
        assert len(result) >= 1


class TestFrozenSession:
    def test_session_cache_get_isolated(self, mock_sessions):
        """Frozen Session objects cannot be mutated post-construction."""
        _write_session(mock_sessions, "s1", "C:\\Immutable")
        sessions = get_sessions("C:\\Immutable")
        assert len(sessions) == 1
        s = sessions[0]
        with pytest.raises(AttributeError):
            s.title = "hacked"


class TestClaudeTailCached:
    def test_claude_tail_cached(self, tmp_path, monkeypatch):
        """get_session_tail from Claude adapter returns cached result on second call (no re-read)."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "C--Work"
        proj.mkdir()

        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        lines = [
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "answer 1"}, "uuid": "u1"}),
        ]
        jsonl_file = proj / f"{sid}.jsonl"
        jsonl_file.write_text("\n".join(lines), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        data_claude._tail_cache.clear()

        # First call — reads from disk
        result1 = data_claude.get_session_tail(sid, "C:\\Work")
        assert result1 == ["answer 1"]

        # Overwrite the file content (but keep same mtime to simulate cache hit)
        # We just verify that a second call within TTL returns same result without re-reading
        import unittest.mock
        with unittest.mock.patch("builtins.open", side_effect=AssertionError("should not re-read")):
            # stat() is still needed for mtime check, but open() should not be called
            result2 = data_claude.get_session_tail(sid, "C:\\Work")

        assert result2 == ["answer 1"]


class TestClaudeFirstPromptCached:
    def test_claude_first_prompt_cached(self, tmp_path, monkeypatch):
        """get_first_prompt from Claude adapter returns cached result on second call."""
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "C--Work"
        proj.mkdir()

        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "my question"}, "uuid": "u1"}),
        ]
        jsonl_file = proj / f"{sid}.jsonl"
        jsonl_file.write_text("\n".join(lines), encoding="utf-8")

        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        data_claude._first_prompt_cache.clear()

        # First call — reads from disk
        result1 = data_claude.get_first_prompt(sid, "C:\\Work")
        assert result1 == "my question"

        # Second call within TTL — should use cache (no file re-read)
        import unittest.mock
        with unittest.mock.patch("builtins.open", side_effect=AssertionError("should not re-read")):
            result2 = data_claude.get_first_prompt(sid, "C:\\Work")

        assert result2 == "my question"


class TestKiroFirstPromptMtimeRefresh:
    def test_kiro_first_prompt_refreshes_after_mtime_change(self, mock_sessions):
        """Kiro first_prompt cache refreshes when source file mtime changes."""
        # Write initial session
        _write_session(mock_sessions, "mtime1", "C:\\Work", jsonl_lines=[
            json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "original prompt"}}),
        ])
        # Also write a .history file (preferred source)
        history_path = mock_sessions / "mtime1.history"
        history_path.write_text("original prompt\n", encoding="utf-8")

        data_kiro._first_prompt_cache.clear()

        # First call — reads from disk
        result1 = data_kiro.get_first_prompt("mtime1")
        assert result1 == "original prompt"

        # Simulate mtime change by rewriting with new content
        import time as time_mod
        time_mod.sleep(0.05)  # ensure mtime differs
        history_path.write_text("updated prompt\n", encoding="utf-8")

        # Expire TTL by manipulating cache entry time
        cached = data_kiro._first_prompt_cache.get("mtime1")
        if cached:
            # Set cache_time to long ago so TTL check forces re-read
            data_kiro._first_prompt_cache["mtime1"] = (0.0, cached[1], cached[2])

        result2 = data_kiro.get_first_prompt("mtime1")
        assert result2 == "updated prompt"


class TestDiscoverSingleFlight:
    def test_concurrent_cold_callers_trigger_single_scan(self, mock_sessions, monkeypatch):
        """SC7: N concurrent cold callers trigger one scan, not N."""
        import concurrent.futures
        call_count = 0

        _write_session(mock_sessions, "s1", "C:\\Project", updated_at="2026-07-01T00:00:00Z")

        original_discover = data_kiro.discover_workspaces

        def counting_discover():
            nonlocal call_count
            call_count += 1
            import time as t
            t.sleep(0.05)  # simulate slow discovery
            return original_discover()

        monkeypatch.setattr("power_atlas.data_kiro.discover_workspaces", counting_discover)
        data._cache.clear()

        n_threads = 8
        barrier = threading.Barrier(n_threads)

        def call_discover():
            barrier.wait()
            return discover_workspaces_with_counts(provider="kiro-cli")

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_threads) as pool:
            futures = [pool.submit(call_discover) for _ in range(n_threads)]
            results = [f.result() for f in futures]

        # Single-flight: only 1 scan should have occurred
        assert call_count == 1, f"Expected 1 scan, got {call_count}"
        # All callers get the same result
        assert all(r == results[0] for r in results)


# --- get_all_sessions_paginated tests ---

from power_atlas.data import get_all_sessions_paginated


class TestGetAllSessionsPaginated:
    def test_basic_pagination_page1(self, mock_sessions, monkeypatch):
        """25 sessions: page 1 returns 20, has_more=True."""
        for i in range(25):
            _write_session(
                mock_sessions,
                f"pag-{i:02d}",
                "C:\\Projects\\Paginate",
                updated_at=f"2026-06-{i+1:02d}T00:00:00Z",
            )
        session_cache.clear()
        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\Paginate", 25, "2026-06-25T00:00:00Z", "kiro-cli"),
            ],
        )

        results, has_more = get_all_sessions_paginated(
            page=1, page_size=20, enabled_providers={"kiro-cli"},
        )
        assert len(results) == 20
        assert has_more is True

    def test_basic_pagination_page2(self, mock_sessions, monkeypatch):
        """25 sessions: page 2 returns 5, has_more=False."""
        for i in range(25):
            _write_session(
                mock_sessions,
                f"pag-{i:02d}",
                "C:\\Projects\\Paginate",
                updated_at=f"2026-06-{i+1:02d}T00:00:00Z",
            )
        session_cache.clear()
        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\Paginate", 25, "2026-06-25T00:00:00Z", "kiro-cli"),
            ],
        )

        results, has_more = get_all_sessions_paginated(
            page=2, page_size=20, enabled_providers={"kiro-cli"},
        )
        assert len(results) == 5
        assert has_more is False

    def test_pinned_at_top(self, mock_sessions, monkeypatch):
        """Pinned sessions appear first regardless of page."""
        for i in range(10):
            _write_session(
                mock_sessions,
                f"pin-{i:02d}",
                "C:\\Projects\\Pin",
                updated_at=f"2026-06-{i+1:02d}T00:00:00Z",
            )
        session_cache.clear()
        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\Pin", 10, "2026-06-10T00:00:00Z", "kiro-cli"),
            ],
        )

        # Pin the 2 oldest sessions
        pinned_ids = ["pin-00", "pin-01"]
        results, has_more = get_all_sessions_paginated(
            page=1, page_size=20,
            pinned_sessions=pinned_ids,
            enabled_providers={"kiro-cli"},
        )

        # Pinned sessions should be at the top
        pinned_results = [(s, p) for s, p in results if s.session_id in pinned_ids]
        non_pinned_results = [(s, p) for s, p in results if s.session_id not in pinned_ids]

        # Pinned come first in the list
        for i, (s, _p) in enumerate(pinned_results):
            assert results[i] == (s, _p)

        assert len(pinned_results) == 2
        assert len(non_pinned_results) == 8
        assert has_more is False

    def test_provider_filtering(self, mock_sessions, monkeypatch):
        """Filter to a specific provider returns only that provider's sessions."""
        # Create kiro-cli sessions via mock_sessions (the standard fixture)
        _write_session(mock_sessions, "kiro-1", "C:\\Projects\\Multi", updated_at="2026-06-01T00:00:00Z")
        _write_session(mock_sessions, "kiro-2", "C:\\Projects\\Multi", updated_at="2026-06-02T00:00:00Z")
        session_cache.clear()

        # Mock claude-code to return its own sessions
        claude_session = Session(
            "claude-1", "Claude Session", "C:\\Projects\\Multi",
            "2026-06-03T00:00:00Z", "2026-06-03T00:00:00Z",
            "hello", "last", "reply",
        )

        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\Multi", 2, "2026-06-02T00:00:00Z", "kiro-cli"),
                ("C:\\Projects\\Multi", 1, "2026-06-03T00:00:00Z", "claude-code"),
            ],
        )

        # Pre-populate cache for both providers
        session_cache.put(
            "C:\\Projects\\Multi",
            [claude_session],
            {},
            provider="claude-code",
        )

        # Filter to claude-code only
        results, has_more = get_all_sessions_paginated(
            page=1, page_size=20, provider="claude-code",
        )
        assert len(results) == 1
        assert results[0][0].session_id == "claude-1"
        assert results[0][1] == "claude-code"
        assert has_more is False

    def test_empty_state(self, mock_sessions, monkeypatch):
        """No sessions available → returns ([], False)."""
        session_cache.clear()
        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [],
        )

        results, has_more = get_all_sessions_paginated(
            page=1, page_size=20, enabled_providers={"kiro-cli"},
        )
        assert results == []
        assert has_more is False

    def test_deduplication(self, mock_sessions, monkeypatch):
        """Same session visible from multiple cache paths → only one entry."""
        # Write a session
        _write_session(mock_sessions, "dup-1", "C:\\Projects\\Dup", updated_at="2026-06-01T00:00:00Z")
        session_cache.clear()

        # Load it into cache under kiro-cli
        sessions = get_sessions("C:\\Projects\\Dup", "kiro-cli")
        assert len(sessions) == 1

        # Manually put the same session_id into cache under a different normalized path
        # (simulating it being visible from two cache paths)
        dup_session = Session(
            "dup-1", "Duplicate", "C:\\Projects\\Dup",
            "2026-06-01T00:00:00Z", "2026-06-01T00:00:00Z",
            "hello", "last", "reply",
        )
        session_cache.put("C:\\Projects\\Dup\\Sub", [dup_session], {}, provider="kiro-cli")

        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\Dup", 1, "2026-06-01T00:00:00Z", "kiro-cli"),
                ("C:\\Projects\\Dup\\Sub", 1, "2026-06-01T00:00:00Z", "kiro-cli"),
            ],
        )

        results, has_more = get_all_sessions_paginated(
            page=1, page_size=20, enabled_providers={"kiro-cli"},
        )
        # Should have only 1 entry despite being in two cache paths
        session_ids = [s.session_id for s, _ in results]
        assert session_ids.count("dup-1") == 1
        assert has_more is False

    def test_enabled_providers_filter(self, mock_sessions, monkeypatch):
        """Only sessions from enabled providers are returned."""
        _write_session(mock_sessions, "en-1", "C:\\Projects\\En", updated_at="2026-06-01T00:00:00Z")
        session_cache.clear()
        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\En", 1, "2026-06-01T00:00:00Z", "kiro-cli"),
            ],
        )

        # kiro-cli sessions exist but we disable kiro-cli
        results, has_more = get_all_sessions_paginated(
            page=1, page_size=20,
            enabled_providers={"claude-code"},
        )
        # kiro-cli sessions excluded since it's not in enabled_providers
        kiro_sessions = [s for s, p in results if p == "kiro-cli"]
        assert len(kiro_sessions) == 0

    def test_sort_order_by_updated_at(self, mock_sessions, monkeypatch):
        """Sessions are sorted by updated_at descending."""
        _write_session(mock_sessions, "sort-old", "C:\\Projects\\Sort", updated_at="2026-01-01T00:00:00Z")
        _write_session(mock_sessions, "sort-new", "C:\\Projects\\Sort", updated_at="2026-06-15T00:00:00Z")
        _write_session(mock_sessions, "sort-mid", "C:\\Projects\\Sort", updated_at="2026-03-01T00:00:00Z")
        session_cache.clear()
        monkeypatch.setattr(
            "power_atlas.data.discover_workspaces_with_counts",
            lambda provider=None: [
                ("C:\\Projects\\Sort", 3, "2026-06-15T00:00:00Z", "kiro-cli"),
            ],
        )

        results, _ = get_all_sessions_paginated(
            page=1, page_size=20, enabled_providers={"kiro-cli"},
        )
        ids = [s.session_id for s, _ in results]
        assert ids == ["sort-new", "sort-mid", "sort-old"]


# --- Live-session presence detection (presence.py) ---

class _FakeProc:
    """Minimal psutil.Process stand-in for presence scan tests."""

    def __init__(self, name, cmdline, cwd=None, cwd_error=False,
                 pid=None, create_time=None):
        self.info = {"name": name, "cmdline": cmdline}
        if pid is not None:
            self.info["pid"] = pid
        self._cwd = cwd
        self._cwd_error = cwd_error
        self._create_time = create_time

    def cwd(self):
        if self._cwd_error:
            raise RuntimeError("access denied")
        return self._cwd

    def create_time(self):
        if self._create_time is None:
            raise RuntimeError("no create_time")
        return self._create_time


def _scan_with(procs, kiro_dir=None, claude_dir=None):
    """Run presence._scan() with a faked process table.

    The sidecar directories are redirected away from the real home directory
    by default, so these tests do not depend on what happens to be running on
    the machine. Pass *kiro_dir* / *claude_dir* to exercise them.
    """
    from power_atlas import presence
    missing = Path(tempfile.gettempdir()) / "_pa_no_such_sidecar_dir"
    presence._sidecar_cache.clear()
    with patch.object(presence, "_AVAILABLE", True), \
         patch.object(presence.psutil, "process_iter", return_value=procs), \
         patch.object(presence, "_KIRO_LOCK_DIR", Path(kiro_dir) if kiro_dir else missing), \
         patch.object(presence, "_CLAUDE_SESSION_DIR", Path(claude_dir) if claude_dir else missing):
        return presence._scan()


def test_presence_matches_claude_resume_id():
    snap = _scan_with([
        _FakeProc("claude", ["claude", "--resume", "abc123"], cwd="/home/u/proj"),
    ])
    assert snap.is_live("claude-code", "/home/u/proj", "abc123") is True
    assert snap.is_live("claude-code", "/home/u/proj", "other") is False
    # live_cwds holds normalized paths; on Windows that rewrites separators
    # and casefolds, so compare against the same normalization.
    from power_atlas.data import _normalize_path
    assert _normalize_path("/home/u/proj") in snap.live_cwds({"claude-code"})


def test_presence_matches_kiro_resume_id_flag():
    snap = _scan_with([
        _FakeProc("kiro-cli", ["kiro-cli", "chat", "--resume-id", "kx"], cwd="/w"),
    ])
    assert snap.is_live("kiro-cli", "/w", "kx") is True
    # The claude flag prefix must not cross-match kiro's --resume-id.
    assert snap.is_live("claude-code", "/w", "kx") is False


def test_presence_resume_equals_form():
    snap = _scan_with([
        _FakeProc("claude", ["claude", "--resume=eqid"], cwd="/w"),
    ])
    assert snap.is_live("claude-code", "/w", "eqid") is True


def test_presence_kiro_ide_never_live():
    # A kiro (IDE) process must never be treated as a resumable session.
    snap = _scan_with([_FakeProc("kiro", ["kiro", "/some/folder"], cwd="/some/folder")])
    assert snap.is_live("kiro-ide", "/some/folder", "anything") is False
    assert snap.live_cwds() == set()


def test_presence_cwd_access_denied_is_tolerated():
    snap = _scan_with([
        _FakeProc("claude", ["claude", "--resume", "id1"], cwd_error=True),
    ])
    # sid still matched even though cwd() raised
    assert snap.is_live("claude-code", "/whatever", "id1") is True
    assert snap.live_cwds() == set()


def test_presence_ignores_unrelated_processes():
    snap = _scan_with([
        _FakeProc("bash", ["bash", "-c", "sleep 1"], cwd="/w"),
        _FakeProc("python", ["python", "app.py"], cwd="/w"),
    ])
    assert snap.live_cwds() == set()
    assert snap.is_live("claude-code", "/w", "id") is False


def test_presence_unavailable_returns_empty():
    from power_atlas import presence
    with patch.object(presence, "_AVAILABLE", False):
        snap = presence._scan()
    assert snap.live_cwds() == set()
    assert snap.is_live("claude-code", "/w", "id") is False


# --- Phase 2: Fresh session detection (probable_fresh_session) ---

from types import SimpleNamespace
from datetime import datetime, timezone, timedelta
from power_atlas.presence import Snapshot


class TestProbableFreshSession:
    def _make_session(self, session_id: str, created_at: str, cwd: str = "C:\\Work"):
        """Create a mock Session-like object."""
        return SimpleNamespace(session_id=session_id, created_at=created_at, cwd=cwd)

    def _now_iso(self, offset_seconds: int = 0) -> str:
        """Return ISO-8601 timestamp offset from now by given seconds."""
        dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
        return dt.isoformat()

    def test_fresh_session_matched(self):
        """Process in cwd, newest session <90s old → returns session_id."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\Fresh"
        norm = _normalize_path(cwd)

        # Simulate a live process in this cwd but no explicit session id match
        snap = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-cli", norm)},
        )

        recent_ts = self._now_iso(-30)  # 30 seconds ago
        sessions = [self._make_session("sess-new", recent_ts, cwd)]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result == "sess-new"

    def test_old_session_not_matched(self):
        """Process in cwd, newest session >90s old → returns None."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\Old"
        norm = _normalize_path(cwd)

        snap = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-cli", norm)},
        )

        old_ts = self._now_iso(-200)  # 200 seconds ago
        sessions = [self._make_session("sess-old", old_ts, cwd)]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result is None

    def test_explicit_match_takes_precedence(self):
        """Session id already in _live_sids → returns None (no double-detect)."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\Explicit"
        norm = _normalize_path(cwd)

        recent_ts = self._now_iso(-10)  # 10 seconds ago
        snap = Snapshot(
            live_sids={("kiro-cli", "sess-explicit")},
            live_cwds={("kiro-cli", norm)},
        )

        sessions = [self._make_session("sess-explicit", recent_ts, cwd)]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result is None

    def test_no_process_in_cwd(self):
        """No provider process running in this cwd → returns None."""
        cwd = "C:\\Projects\\NoProcess"

        # live_cwds is empty — no process detected
        snap = Snapshot(
            live_sids=set(),
            live_cwds=set(),
        )

        recent_ts = self._now_iso(-5)
        sessions = [self._make_session("sess-noproc", recent_ts, cwd)]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result is None

    def test_multiple_sessions_only_newest_considered(self):
        """Multiple sessions — only the newest one is evaluated for freshness."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\Multi"
        norm = _normalize_path(cwd)

        snap = Snapshot(
            live_sids=set(),
            live_cwds={("claude-code", norm)},
        )

        # Newest is fresh (20s ago), older ones are stale
        sessions = [
            self._make_session("sess-old1", "2026-01-01T00:00:00Z", cwd),
            self._make_session("sess-old2", "2026-06-01T00:00:00Z", cwd),
            self._make_session("sess-newest", self._now_iso(-20), cwd),
        ]

        result = snap.probable_fresh_session("claude-code", cwd, sessions)
        assert result == "sess-newest"

    def test_multiple_sessions_newest_is_old(self):
        """Multiple sessions, all old — even the newest is >90s → returns None."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\AllOld"
        norm = _normalize_path(cwd)

        snap = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-cli", norm)},
        )

        sessions = [
            self._make_session("sess-a", "2026-01-01T00:00:00Z", cwd),
            self._make_session("sess-b", "2026-06-01T00:00:00Z", cwd),
            self._make_session("sess-c", self._now_iso(-120), cwd),  # 120s ago
        ]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result is None

    def test_empty_sessions_list(self):
        """Empty sessions list → returns None."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\Empty"
        norm = _normalize_path(cwd)

        snap = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-cli", norm)},
        )

        result = snap.probable_fresh_session("kiro-cli", cwd, [])
        assert result is None

    def test_z_suffix_timestamp_parsed(self):
        """ISO-8601 timestamps with 'Z' suffix are correctly parsed."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\Zulu"
        norm = _normalize_path(cwd)

        snap = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-cli", norm)},
        )

        # Create a timestamp with Z suffix that's fresh
        now = datetime.now(timezone.utc) - timedelta(seconds=10)
        z_ts = now.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        sessions = [self._make_session("sess-z", z_ts, cwd)]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result == "sess-z"

    def test_two_fresh_sessions_same_cwd_only_newest(self):
        """Two sessions both <90s old — only the newest one is returned."""
        from power_atlas.data import _normalize_path
        cwd = "C:\\Projects\\TwoFresh"
        norm = _normalize_path(cwd)

        snap = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-cli", norm)},
        )

        # Both are fresh but one is newer
        sessions = [
            self._make_session("sess-older-fresh", self._now_iso(-60), cwd),
            self._make_session("sess-newer-fresh", self._now_iso(-10), cwd),
        ]

        result = snap.probable_fresh_session("kiro-cli", cwd, sessions)
        assert result == "sess-newer-fresh"


# --- Parse/head caching (260725_PARSE_AND_POLL_PERFORMANCE) ------------------


class TestBoundedCache:
    """LRU bound and eviction order for the shared parse-cache primitive."""

    def test_evicts_least_recently_used(self):
        from power_atlas.data import BoundedCache
        c = BoundedCache(2)
        c.put("a", (1,))
        c.put("b", (2,))
        c.get("a")           # 'a' becomes most-recently-used
        c.put("c", (3,))     # evicts 'b', not 'a'
        assert c.get("a") == (1,)
        assert c.get("b") is None
        assert c.get("c") == (3,)

    def test_respects_maxsize(self):
        from power_atlas.data import BoundedCache
        c = BoundedCache(8)
        for i in range(100):
            c.put(f"k{i}", (i,))
        assert len(c) == 8

    def test_clear_empties(self):
        from power_atlas.data import BoundedCache
        c = BoundedCache(4)
        c.put("a", (1,))
        c.clear()
        assert len(c) == 0
        assert c.get("a") is None


@pytest.fixture
def claude_project(tmp_path, monkeypatch):
    """A Claude Code project folder with clean parse/head caches."""
    from power_atlas import data_claude
    projects = tmp_path / "projects"
    folder = projects / "-home-user-proj"
    folder.mkdir(parents=True)
    monkeypatch.setattr(data_claude, "CLAUDE_PROJECTS_DIR", projects)
    monkeypatch.setattr(data_claude, "CLAUDE_HISTORY_PATH", tmp_path / "history.jsonl")
    data_claude._parse_cache.clear()
    data_claude._head_cache.clear()
    return folder


def _claude_session(folder: Path, name: str, lines: list[bytes]) -> Path:
    p = folder / f"{name}.jsonl"
    p.write_bytes(b"".join(l + b"\n" for l in lines))
    return p


class TestParseSessionFileHeadScan:
    """The head scan skips json.loads on non-title lines once first_prompt is known."""

    def test_ai_title_after_first_prompt_still_found(self, claude_project):
        p = _claude_session(claude_project, "a", [
            b'{"type":"user","message":{"content":"the first prompt"}}',
            *[b'{"type":"assistant","message":{"content":"filler"}}'] * 50,
            b'{"type":"ai-title","aiTitle":"Found Late"}',
        ])
        title, first_prompt, _lp, _lr, _ts = data_claude._parse_session_file(p)
        assert title == "Found Late"
        assert first_prompt == "the first prompt"

    def test_custom_title_overrides_ai_title(self, claude_project):
        p = _claude_session(claude_project, "b", [
            b'{"type":"ai-title","aiTitle":"Auto"}',
            b'{"type":"user","message":{"content":"prompt"}}',
            b'{"type":"custom-title","customTitle":"Renamed By User"}',
        ])
        title, _fp, _lp, _lr, _ts = data_claude._parse_session_file(p)
        assert title == "Renamed By User"

    def test_word_title_in_content_does_not_corrupt(self, claude_project):
        """A message containing the word 'title' is parsed, not mistaken for one."""
        p = _claude_session(claude_project, "c", [
            b'{"type":"ai-title","aiTitle":"Real Title"}',
            b'{"type":"user","message":{"content":"what is the title of this book"}}',
            b'{"type":"assistant","message":{"content":"the title is X"}}',
        ])
        title, first_prompt, _lp, _lr, _ts = data_claude._parse_session_file(p)
        assert title == "Real Title"
        assert first_prompt == "what is the title of this book"

    def test_invalid_utf8_line_still_parses(self, claude_project):
        """Binary reads must reproduce text-mode errors='replace' behaviour."""
        p = _claude_session(claude_project, "d", [
            b'{"type":"user","message":{"content":"caf\xff\xfe bad"}}',
            b'{"type":"assistant","message":{"content":"ok"}}',
        ])
        _t, first_prompt, _lp, last_reply, _ts = data_claude._parse_session_file(p)
        assert first_prompt.startswith("caf")
        assert last_reply == "ok"

    def test_malformed_line_skipped(self, claude_project):
        p = _claude_session(claude_project, "e", [
            b'{not json at all',
            b'{"type":"user","message":{"content":"survived"}}',
        ])
        _t, first_prompt, _lp, _lr, _ts = data_claude._parse_session_file(p)
        assert first_prompt == "survived"


class TestParseSessionFileTailScan:
    """A rename must be found even when it precedes the session's last real turn."""

    def test_rename_past_head_scan_before_last_turn_still_wins(self, claude_project):
        # The rename sits past the head scan's 500-line cap, and a real user/
        # assistant turn follows it — the exact shape that let the reverse
        # tail scan break on that turn before ever reaching the rename.
        p = _claude_session(claude_project, "g", [
            b'{"type":"custom-title","customTitle":"original-name"}',
            b'{"type":"ai-title","aiTitle":"Auto"}',
            b'{"type":"user","message":{"content":"first prompt"}}',
            *[b'{"type":"assistant","message":{"content":"filler"}}'] * 510,
            b'{"type":"custom-title","customTitle":"renamed-name"}',
            b'{"type":"user","message":{"content":"say hi"}}',
            b'{"type":"assistant","message":{"content":"hi there"}}',
        ])
        title, _fp, last_prompt, last_reply, _ts = data_claude._parse_session_file(p)
        assert title == "renamed-name"
        assert last_prompt == "say hi"
        assert last_reply == "hi there"


class TestParseCache:
    """load_sessions must re-parse only files whose (mtime, size) changed."""

    def test_unchanged_file_not_reparsed(self, claude_project):
        # load_sessions only picks up UUID-named files
        _claude_session(claude_project, "11111111-1111-4111-8111-111111111111", [
            b'{"type":"user","message":{"content":"hello"}}',
        ])
        sessions, _ = data_claude.load_sessions("/home/user/proj")
        assert len(sessions) == 1

        with patch.object(data_claude, "_parse_session_file",
                          side_effect=AssertionError("should not re-parse")):
            again, _ = data_claude.load_sessions("/home/user/proj")
        assert [s.title for s in again] == [s.title for s in sessions]

    def test_changed_file_is_reparsed(self, claude_project):
        p = _claude_session(claude_project, "22222222-2222-4222-8222-222222222222", [
            b'{"type":"user","message":{"content":"before"}}',
        ])
        first, _ = data_claude.load_sessions("/home/user/proj")
        assert first[0].first_prompt == "before"

        p.write_bytes(b'{"type":"user","message":{"content":"after"}}\n')
        os.utime(p, (time.time() + 5, time.time() + 5))
        second, _ = data_claude.load_sessions("/home/user/proj")
        assert second[0].first_prompt == "after"


class TestHeadCacheInvalidation:
    """first_prompt survives appends but must not survive a truncate-rewrite."""

    def test_append_picks_up_rename(self, claude_project):
        p = _claude_session(claude_project, "h1", [
            b'{"type":"user","message":{"content":"original"}}',
        ])
        data_claude._parse_session_file(p, p.stat())

        with open(p, "ab") as fh:
            fh.write(b'{"type":"custom-title","customTitle":"Renamed"}\n')
        os.utime(p, (time.time() + 5, time.time() + 5))

        title, first_prompt, _lp, _lr, _ts = data_claude._parse_session_file(p, p.stat())
        assert title == "Renamed"
        assert first_prompt == "original"

    def test_truncate_rewrite_invalidates_head(self, claude_project):
        p = _claude_session(claude_project, "h2", [
            b'{"type":"user","message":{"content":"original first prompt padded out"}}',
            b'{"type":"assistant","message":{"content":"padding to grow the file"}}',
        ])
        data_claude._parse_session_file(p, p.stat())

        # Shrink and rewrite — the cached head must not be reused.
        p.write_bytes(b'{"type":"user","message":{"content":"brand new"}}\n')
        os.utime(p, (time.time() + 5, time.time() + 5))

        _t, first_prompt, _lp, _lr, _ts = data_claude._parse_session_file(p, p.stat())
        assert first_prompt == "brand new"


class TestKiroPromptsCache:
    """The per-session .jsonl parse is memoized on (mtime, size).

    The metadata index (c346982) stops load_sessions re-reading the whole store,
    but each matched session's .jsonl was still re-parsed on every call.
    """

    def test_unchanged_jsonl_not_reparsed(self, mock_sessions):
        _write_session(mock_sessions, "p1", "C:\\Projects\\P")
        data_kiro._prompts_cache.clear()
        first, _ = data_kiro.load_sessions("C:\\Projects\\P")
        assert first[0].first_prompt == "Hello world"

        with patch.object(data_kiro, "_extract_prompts",
                          side_effect=AssertionError("should not re-parse")):
            again, _ = data_kiro.load_sessions("C:\\Projects\\P")
        assert again[0].first_prompt == "Hello world"

    def test_changed_jsonl_is_reparsed(self, mock_sessions):
        _write_session(mock_sessions, "p2", "C:\\Projects\\Q")
        data_kiro._prompts_cache.clear()
        first, _ = data_kiro.load_sessions("C:\\Projects\\Q")
        assert first[0].first_prompt == "Hello world"

        jsonl = mock_sessions / "p2.jsonl"
        jsonl.write_text(json.dumps(
            {"version": "v1", "kind": "Prompt", "data": {"content": "Changed prompt"}}
        ), encoding="utf-8")
        _bump_mtime(jsonl)

        second, _ = data_kiro.load_sessions("C:\\Projects\\Q")
        assert second[0].first_prompt == "Changed prompt"

    def test_missing_jsonl_bypasses_cache(self, mock_sessions):
        """No stat means no cache key — fall through rather than guess."""
        _write_session(mock_sessions, "p3", "C:\\Projects\\R")
        (mock_sessions / "p3.jsonl").unlink()
        data_kiro._prompts_cache.clear()
        sessions, _ = data_kiro.load_sessions("C:\\Projects\\R")
        assert len(sessions) == 1
        assert sessions[0].first_prompt == ""


# --- Sidecar-derived session identity (presence.py) ---
#
# Neither CLI puts its session id on argv, so these files are the only way a
# terminal-started session becomes identifiable. They are also not reliably
# deleted, so the guard against a recycled pid is the part that matters: a
# false positive marks an unrelated process as a live session.

def _write_kiro_lock(dirpath, sid, pid, started_iso, cwd=None):
    (dirpath / f"{sid}.lock").write_text(
        json.dumps({"pid": pid, "started_at": started_iso}), encoding="utf-8")
    if cwd is not None:
        (dirpath / f"{sid}.json").write_text(
            json.dumps({"session_id": sid, "cwd": cwd}), encoding="utf-8")


def _write_claude_session(dirpath, pid, sid, started_ms, cwd, status="idle",
                          proc_start=None):
    body = {
        "pid": pid, "sessionId": sid, "cwd": cwd,
        "startedAt": started_ms, "status": status, "kind": "interactive",
    }
    if proc_start is not None:
        body["procStart"] = proc_start
    (dirpath / f"{pid}.json").write_text(json.dumps(body), encoding="utf-8")


_FILETIME_UNIX_DELTA = 116_444_736_000_000_000


def _filetime(epoch_seconds):
    """Windows FILETIME string for a POSIX time — the test-side inverse of
    ``presence._filetime_to_epoch``."""
    return str(int(round(epoch_seconds * 10_000_000)) + _FILETIME_UNIX_DELTA)


def test_presence_rejects_electron_helper_processes(tmp_path):
    """Claude Desktop ships a binary also named claude.exe and forks helpers."""
    snap = _scan_with([
        _FakeProc("claude.exe", ["C:/App/claude.exe", "--type=renderer"],
                  cwd="C:/proj", pid=101, create_time=1000.0),
        _FakeProc("claude.exe", ["C:/App/claude.exe", "--type=gpu-process"],
                  cwd="C:/proj", pid=102, create_time=1000.0),
    ])
    assert snap.live_cwds({"claude-code"}) == set()


def test_presence_sidecar_identifies_kiro_session(tmp_path):
    _write_kiro_lock(tmp_path, "sess-a", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    snap = _scan_with(
        [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "chat"],
                   pid=500, create_time=started - 1.2)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-a") is True


def test_presence_sidecar_rejects_recycled_pid_on_other_binary(tmp_path):
    """785 stale locks on one machine named 21 live pids; 20 were unrelated."""
    _write_kiro_lock(tmp_path, "sess-old", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    snap = _scan_with(
        [_FakeProc("svchost.exe", ["C:/windows/svchost.exe"],
                   pid=500, create_time=started - 1.2)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-old") is False


def test_presence_sidecar_rejects_pid_recycled_onto_same_binary(tmp_path):
    """Same provider, wrong process: only the start time separates them."""
    _write_kiro_lock(tmp_path, "sess-old", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    snap = _scan_with(
        [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "chat"],
                   pid=500, create_time=started + 9000)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-old") is False


@contextlib.contextmanager
def _acp_published(session_ids, agent_pid):
    """Publish an ACP live-set for the duration of one test, then restore.

    Restored rather than left set: `_acp_live` is a module global and a test
    that leaked one would silently change what every later presence test sees.
    """
    from power_atlas import presence
    previous = presence._acp_live
    presence.publish_acp_sessions(session_ids, agent_pid)
    try:
        yield
    finally:
        presence._acp_live = previous


def test_presence_hides_a_lock_our_own_agent_orphaned(tmp_path):
    """D32, closed.

    `session/load` makes PowerAtlas's own ACP agent write a lock naming
    *itself*. A lock left behind by a failed load, or by a close whose
    terminate raised, therefore has `pid == the live agent` and a *forward*
    delta — so every other check in the sidecar pass passes it, and since D10
    dropped the forward ceiling for kiro-cli there is nothing left to expire
    it. It read live for the agent's whole lifetime.
    """
    _write_kiro_lock(tmp_path, "sess-orphan", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    agent = _FakeProc("kiro-cli.exe", ["kiro-cli.exe", "acp", "-a"],
                      pid=500, create_time=started - 600.0)
    # The agent holds a different session; this lock's is not among them.
    with _acp_published({"sess-other"}, 500):
        snap = _scan_with([agent], kiro_dir=tmp_path)
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-orphan") is False

    # Positive control, same fixture: once the agent does hold it, it is live.
    with _acp_published({"sess-orphan"}, 500):
        snap = _scan_with([agent], kiro_dir=tmp_path)
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-orphan") is True


def test_presence_leaves_a_foreign_kiro_lock_alone(tmp_path):
    """The suppression is scoped to *our* agent's pid and nothing else.

    A kiro-cli the user started in a terminal holds its own sessions, and this
    process has no idea which. Suppressing on session id alone would hide every
    terminal session from the dashboard — the opposite of what presence is for.
    """
    _write_kiro_lock(tmp_path, "sess-terminal", 700, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    foreign = _FakeProc("kiro-cli.exe", ["kiro-cli.exe", "chat"],
                        pid=700, create_time=started - 1.2)
    # Our agent is pid 500 and holds nothing. 700 is somebody else's.
    with _acp_published(set(), 500):
        snap = _scan_with([foreign], kiro_dir=tmp_path)
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-terminal") is True


def test_presence_suppresses_nothing_until_something_publishes(tmp_path):
    """No answer must not read as "no sessions".

    A plain `presence` import, a test that does not opt in, and a build where
    `acp` failed to import all leave the default in place. Failing that way
    round restores today's accepted D32 residual — a wrong live dot — rather
    than hiding a session the dashboard should show.
    """
    from power_atlas import presence
    assert presence._acp_live == (frozenset(), None)
    _write_kiro_lock(tmp_path, "sess-a", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    snap = _scan_with(
        [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "acp", "-a"],
                   pid=500, create_time=started - 600.0)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-a") is True


def test_publish_acp_sessions_copies_and_survives_bad_input():
    """The stored set must not alias the caller's, and must not raise.

    The argument is derived from loop-owned state; storing a live view would
    hand worker threads exactly what D9 keeps away from them.
    """
    from power_atlas import presence
    previous = presence._acp_live
    try:
        live = {"a", "b"}
        presence.publish_acp_sessions(live, 42)
        live.add("c")
        assert presence._acp_live[0] == frozenset({"a", "b"})
        assert presence._acp_live[1] == 42
        # A non-iterable, and a pid that is not an int, are both survivable:
        # this runs on the session create/close paths and must never raise
        # there.
        presence.publish_acp_sessions(object(), "not-a-pid")
        assert presence._acp_live == (frozenset(), None)
    finally:
        presence._acp_live = previous


def test_presence_sidecar_rejects_sidecar_predating_its_process(tmp_path):
    """A sidecar is written after its process spawns, never long before."""
    _write_kiro_lock(tmp_path, "sess-x", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    snap = _scan_with(
        [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "chat"],
                   pid=500, create_time=started + 60)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-x") is False


def test_presence_sidecar_reports_claude_status(tmp_path):
    started_ms = 1784920809496
    _write_claude_session(tmp_path, 700, "sess-c", started_ms,
                          "C:/work/pa", status="busy")
    snap = _scan_with(
        [_FakeProc("claude.exe", ["C:/u/.local/bin/claude.exe"],
                   pid=700, create_time=started_ms / 1000.0 - 1.5)],
        claude_dir=tmp_path,
    )
    assert snap.is_live("claude-code", "C:/work/pa", "sess-c") is True
    assert snap.reported_status("claude-code", "sess-c") == "busy"
    assert snap.reported_status("claude-code", "nope") == ""


def test_presence_sidecar_malformed_record_does_not_drop_others(tmp_path):
    """One bad file must not silently regress the scan to matching nothing."""
    (tmp_path / "broken.lock").write_text("{not json", encoding="utf-8")
    (tmp_path / "typed.lock").write_text(
        json.dumps({"pid": "not-an-int", "started_at": "2026-07-24T10:00:01Z"}),
        encoding="utf-8")
    _write_kiro_lock(tmp_path, "sess-good", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    started = _epoch("2026-07-24T10:00:01Z")
    snap = _scan_with(
        [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "chat"],
                   pid=500, create_time=started - 1.2)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-good") is True


def test_presence_kiro_lock_far_newer_than_its_process_is_live(tmp_path):
    """PowerAtlas's ACP agent is spawned once and opens sessions for hours.

    The lock's ``started_at`` tracks session-open time, not process start, so
    an ACP-owned session is legitimately minutes or hours newer than the agent
    that wrote it. Measured 2026-07-31: +1.88s and +23.77s for two sessions
    opened 21.5s apart, both against the same agent pid.
    """
    _write_kiro_lock(tmp_path, "sess-acp", 500, "2026-07-24T13:00:00Z",
                     cwd="C:/work/proj")
    agent_start = _epoch("2026-07-24T10:00:01Z")  # ~3h before the lock
    snap = _scan_with(
        [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "acp", "-a"],
                   pid=500, create_time=agent_start)],
        kiro_dir=tmp_path,
    )
    assert snap.is_live("kiro-cli", "C:/work/proj", "sess-acp") is True


def test_presence_claude_sidecar_outside_window_is_not_live(tmp_path):
    """The dropped forward ceiling is kiro-only; claude-code keeps its 120s.

    One `claude` process owns one session and writes its sidecar just after
    spawn, so nothing justifies widening that window. This test pins one
    direction only: it fails if the ceiling is deleted for both providers, but
    it still passes if the ``provider != "kiro-cli"`` conjunct is removed while
    the ceiling stays. That mutation is caught by
    ``test_presence_kiro_lock_far_newer_than_its_process_is_live`` instead. The
    pair, failing in opposite directions, is what makes the scoping real rather
    than incidental — neither test proves it alone.
    """
    started_ms = 1784920809496
    _write_claude_session(tmp_path, 700, "sess-c", started_ms, "C:/work/pa")
    snap = _scan_with(
        [_FakeProc("claude.exe", ["C:/u/.local/bin/claude.exe"],
                   pid=700, create_time=started_ms / 1000.0 - 300.0)],
        claude_dir=tmp_path,
    )
    assert snap.is_live("claude-code", "C:/work/pa", "sess-c") is False


def test_presence_proc_start_accepts_outside_the_started_at_window(tmp_path):
    """`procStart` is an identity check, not a wider window on the same test.

    `startedAt` puts this 300s outside the 120s forward ceiling — the exact
    fixture `test_presence_claude_sidecar_outside_window_is_not_live` uses to
    prove the ceiling rejects it. Here the sidecar also carries `procStart`
    naming the *same* creation time psutil reports, and that settles liveness
    on its own: the two clocks are readings of one FILETIME, so agreement
    between them is not something a wall-clock skew window gets a vote on.
    """
    started_ms = 1784920809496
    create_time = started_ms / 1000.0 - 300.0
    _write_claude_session(tmp_path, 700, "sess-c", started_ms, "C:/work/pa",
                          proc_start=_filetime(create_time))
    snap = _scan_with(
        [_FakeProc("claude.exe", ["C:/u/.local/bin/claude.exe"],
                   pid=700, create_time=create_time)],
        claude_dir=tmp_path,
    )
    assert snap.is_live("claude-code", "C:/work/pa", "sess-c") is True


def test_presence_proc_start_rejects_mismatch_inside_the_started_at_window(tmp_path):
    """The exact check can reject what the softer window would have admitted.

    `startedAt` is 1.2s ahead of `create_time` — comfortably inside both skew
    constants, the shape every other sidecar test in this file uses as its
    positive control. But `procStart` names a creation time 50s away from what
    psutil reports for this pid, so the two clocks disagree about whose
    process this is. A mismatched `procStart` must reject outright rather than
    falling back to the `started_at` check that would have passed it —
    otherwise "exact" is only ever a looser accept, never a stricter refusal.
    """
    started = _epoch("2026-07-24T10:00:01Z")
    _write_claude_session(tmp_path, 700, "sess-c", int(started * 1000),
                          "C:/work/pa", proc_start=_filetime(started - 50.0))
    snap = _scan_with(
        [_FakeProc("claude.exe", ["C:/u/.local/bin/claude.exe"],
                   pid=700, create_time=started - 1.2)],
        claude_dir=tmp_path,
    )
    assert snap.is_live("claude-code", "C:/work/pa", "sess-c") is False


def test_presence_proc_start_absent_falls_back_to_the_skew_window(tmp_path):
    """No `procStart` on the sidecar — every kiro-cli record, and a
    claude-code one from before the field existed — must fall back to the
    `started_at` window exactly as before this field was read at all.
    """
    started_ms = 1784920809496
    _write_claude_session(tmp_path, 700, "sess-c", started_ms, "C:/work/pa")
    snap = _scan_with(
        [_FakeProc("claude.exe", ["C:/u/.local/bin/claude.exe"],
                   pid=700, create_time=started_ms / 1000.0 - 1.5)],
        claude_dir=tmp_path,
    )
    assert snap.is_live("claude-code", "C:/work/pa", "sess-c") is True


def test_presence_proc_start_unparseable_falls_back_to_the_skew_window(tmp_path):
    """A `procStart` that fails to parse must fail open to the window, not
    reject the session outright — it is best-effort, read off JSON the agent
    wrote and not validated input."""
    started_ms = 1784920809496
    _write_claude_session(tmp_path, 700, "sess-c", started_ms, "C:/work/pa",
                          proc_start="not-a-filetime")
    snap = _scan_with(
        [_FakeProc("claude.exe", ["C:/u/.local/bin/claude.exe"],
                   pid=700, create_time=started_ms / 1000.0 - 1.5)],
        claude_dir=tmp_path,
    )
    assert snap.is_live("claude-code", "C:/work/pa", "sess-c") is True


def test_filetime_to_epoch_conversion():
    from power_atlas.presence import _filetime_to_epoch
    # 134303452332239908 was observed on a real claude-code sidecar
    # (plans/ROADMAP.md, 2026-08-04) and converts to within 1.757s of that
    # record's `startedAt`, not to it exactly — the two are independent
    # readings and are not expected to agree to sub-second precision. This
    # pins the conversion's magnitude and epoch alignment, not that figure.
    got = _filetime_to_epoch("134303452332239908")
    assert got is not None
    from datetime import datetime, timezone
    year = datetime.fromtimestamp(got, tz=timezone.utc).year
    assert 2020 <= year <= 2030, f"conversion landed outside a plausible range: {got}"
    assert _filetime_to_epoch(None) is None
    assert _filetime_to_epoch("") is None
    assert _filetime_to_epoch("not-a-number") is None
    assert _filetime_to_epoch(0) is None
    assert _filetime_to_epoch(-5) is None


def test_presence_kiro_lock_rewritten_in_place_is_reparsed(tmp_path):
    """`session/load` rewrites a lock in place, leaving the directory untouched.

    A directory-listing cache keyed on the directory's own mtime would hand
    ``_load_json_cached`` the pre-rewrite stat, which matches its cache key and
    pins the previous parse. Both scans here run without clearing the module
    caches, which is what makes the regression observable at all.
    """
    from power_atlas import presence
    started = _epoch("2026-07-24T10:00:01Z")
    _write_kiro_lock(tmp_path, "sess-rw", 500, "2026-07-24T10:00:01Z",
                     cwd="C:/work/proj")
    procs = [_FakeProc("kiro-cli.exe", ["kiro-cli.exe", "chat"],
                       pid=500, create_time=started - 1.2)]
    missing = Path(tempfile.gettempdir()) / "_pa_no_such_sidecar_dir"
    presence._sidecar_cache.clear()
    with patch.object(presence, "_AVAILABLE", True), \
         patch.object(presence.psutil, "process_iter", return_value=procs), \
         patch.object(presence, "_KIRO_LOCK_DIR", tmp_path), \
         patch.object(presence, "_CLAUDE_SESSION_DIR", missing):
        first = presence._scan()
        assert first.is_live("kiro-cli", "C:/work/proj", "sess-rw") is True
        dir_mtime_before = tmp_path.stat().st_mtime
        # No create, no delete — just new bytes in an existing file, which is
        # what leaves the directory's own mtime where it was.
        (tmp_path / "sess-rw.lock").write_text(json.dumps({
            "pid": 999, "started_at": "2026-07-24T10:00:01Z",
            "note": "rewritten in place by session/load",
        }), encoding="utf-8")
        assert tmp_path.stat().st_mtime == dir_mtime_before
        second = presence._scan()
        assert second.is_live("kiro-cli", "C:/work/proj", "sess-rw") is False


def _epoch(iso):
    from power_atlas.presence import _epoch_from_iso
    return _epoch_from_iso(iso)


# --- Transcript tail reader (status_classifier.py) ---

def test_read_tail_lines_widens_when_final_line_exceeds_window(tmp_path):
    """A window narrower than the last line yields nothing without the retry.

    That returned None, which web.py renders as "working" — the opposite of
    the truth for a session awaiting input.
    """
    from power_atlas.status_classifier import _read_tail_lines
    p = tmp_path / "t.jsonl"
    p.write_text('{"a":1}\n{"big":"' + "x" * 200_000 + '"}\n', encoding="utf-8")
    lines = _read_tail_lines(p, max_bytes=4096)
    assert lines, "widening retry did not recover the oversized final line"
    assert lines[-1].startswith('{"big":')


def test_read_tail_lines_small_file_returns_all_lines(tmp_path):
    from power_atlas.status_classifier import _read_tail_lines
    p = tmp_path / "s.jsonl"
    p.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    assert _read_tail_lines(p) == ['{"a":1}', '{"b":2}']


def test_read_tail_lines_discards_partial_first_line(tmp_path):
    from power_atlas.status_classifier import _read_tail_lines
    p = tmp_path / "m.jsonl"
    p.write_text('{"first":"' + "y" * 500 + '"}\n{"second":2}\n', encoding="utf-8")
    lines = _read_tail_lines(p, max_bytes=64)
    assert lines == ['{"second":2}']


# --- kiro-cli metadata index (data_kiro.py) ---
#
# Sessions live flat in one directory, so filtering per workspace used to
# parse the whole store for every workspace opened.

def _kiro_meta(dirpath, sid, cwd, updated, parent=None):
    body = {"session_id": sid, "cwd": cwd, "title": f"t-{sid}",
            "created_at": updated, "updated_at": updated}
    if parent:
        body["parent_session_id"] = parent
    meta_path = dirpath / f"{sid}.json"
    jsonl_path = dirpath / f"{sid}.jsonl"
    meta_path.write_text(json.dumps(body), encoding="utf-8")
    jsonl_path.write_text("", encoding="utf-8")
    # The directory too: adding a session is only visible to the cwd index if
    # the directory's own mtime moves, and every timestamp here is a rewrite
    # of the same byte count, so size cannot discriminate either.
    _bump_mtime(meta_path, jsonl_path, dirpath)


def _fresh_kiro(tmp_path):
    _reset_kiro_caches()
    return patch.object(data_kiro, "SESSION_DIR", tmp_path)


def test_kiro_load_sessions_filters_by_cwd_and_skips_subagents(tmp_path):
    from power_atlas import data_kiro
    _kiro_meta(tmp_path, "a", "C:/one", "2026-01-01T00:00:00Z")
    _kiro_meta(tmp_path, "b", "C:/two", "2026-01-02T00:00:00Z")
    _kiro_meta(tmp_path, "c", "C:/one", "2026-01-03T00:00:00Z", parent="a")
    with _fresh_kiro(tmp_path):
        sessions, stats = data_kiro.load_sessions("C:/one")
    assert [s.session_id for s in sessions] == ["a"], "wrong cwd or subagent leaked"
    assert len(stats) >= 1


def test_kiro_index_picks_up_a_newly_created_session(tmp_path):
    """A new session adds a file, which bumps the directory mtime."""
    from power_atlas import data_kiro
    _kiro_meta(tmp_path, "a", "C:/one", "2026-01-01T00:00:00Z")
    with _fresh_kiro(tmp_path):
        first, _ = data_kiro.load_sessions("C:/one")
        assert [s.session_id for s in first] == ["a"]
        _kiro_meta(tmp_path, "b", "C:/one", "2026-01-04T00:00:00Z")
        second, _ = data_kiro.load_sessions("C:/one")
    assert sorted(s.session_id for s in second) == ["a", "b"]


def test_kiro_load_sessions_sees_rewritten_metadata(tmp_path):
    """An active session rewrites updated_at without the directory changing."""
    from power_atlas import data_kiro
    _kiro_meta(tmp_path, "a", "C:/one", "2026-01-01T00:00:00Z")
    with _fresh_kiro(tmp_path):
        first, _ = data_kiro.load_sessions("C:/one")
        assert first[0].updated_at == "2026-01-01T00:00:00Z"
        _kiro_meta(tmp_path, "a", "C:/one", "2026-06-06T00:00:00Z")
        second, _ = data_kiro.load_sessions("C:/one")
    assert second[0].updated_at == "2026-06-06T00:00:00Z", "stale metadata served"
