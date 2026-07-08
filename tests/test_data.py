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
    """Clear session cache and discovery cache between tests."""
    from power_atlas.data import session_cache
    from power_atlas import data
    session_cache.clear()
    data._cache.clear()
    yield
    session_cache.clear()
    data._cache.clear()


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
