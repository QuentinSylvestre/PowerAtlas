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
    get_sessions, session_cache,
)




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



def _reset_v3_caches():
    """Drop data_kiro_v3's process-global parse caches and cwd index.

    In production the session roots are fixed for the life of the process, so
    these caches are never stale. Tests repoint them at fresh tmp_paths per
    test; the cwd index is guarded only by the directory's mtime — which two
    tmp dirs created milliseconds apart share far more often than not. Without
    this reset a test reads the *previous* test's index and sees no sessions.
    """
    try:
        import power_atlas.data_kiro_v3 as data_kiro_v3
        data_kiro_v3._root_mtime = None
        data_kiro_v3._session_json_mtimes = {}
        data_kiro_v3._cwd_index = {}
        data_kiro_v3._norm_cwd_to_hash = {}
        data_kiro_v3._cwd_display = {}
        data_kiro_v3._session_path_cache = {}
        if hasattr(data_kiro_v3, '_prompts_cache') and hasattr(data_kiro_v3._prompts_cache, 'clear'):
            data_kiro_v3._prompts_cache.clear()
        if hasattr(data_kiro_v3, '_tail_cache') and hasattr(data_kiro_v3._tail_cache, 'clear'):
            data_kiro_v3._tail_cache.clear()
        if hasattr(data_kiro_v3, '_first_prompt_cache') and hasattr(data_kiro_v3._first_prompt_cache, 'clear'):
            data_kiro_v3._first_prompt_cache.clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear session cache and discovery cache between tests."""
    from power_atlas.data import session_cache
    from power_atlas import data
    session_cache.clear()
    data._cache.clear()
    _reset_v3_caches()
    yield
    session_cache.clear()
    data._cache.clear()
    _reset_v3_caches()




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
        cache.put("C:\\Work", [s_kiro], {}, provider="kiro-cli-v3")
        cache.put("C:\\Work", [s_claude], {}, provider="claude-code")

        kiro_result = cache.get("C:\\Work", provider="kiro-cli-v3")
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
        cache.put("C:\\A", [s1], {}, provider="kiro-cli-v3")
        cache.put("C:\\B", [s2], {}, provider="claude-code")

        from power_atlas.data import _normalize_path
        kiro_cwds = cache.get_loaded_cwds("kiro-cli-v3")
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


class TestKiroIdeGetFullTranscript:
    """`get_full_transcript` returns every history turn in order, unlike
    `get_session_tail` (assistant-only, last `max_lines`)."""

    def _make_session(self, tmp_path, monkeypatch, cwd, sid, history):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir(exist_ok=True)
        folder = sessions_dir / f"{sid}_folder"
        folder.mkdir()
        sessions_data = [{"sessionId": sid, "title": "T", "dateCreated": "1700000000000",
                           "workspaceDirectory": cwd}]
        (folder / "sessions.json").write_text(json.dumps(sessions_data), encoding="utf-8")
        (folder / f"{sid}.json").write_text(json.dumps({"history": history}), encoding="utf-8")
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        return folder

    def test_returns_empty_for_missing_session(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        assert data_kiro_ide.get_full_transcript("nonexistent", "C:\\Whatever") == []

    def test_full_ordered_transcript(self, tmp_path, monkeypatch):
        cwd = "C:\\FullTest"
        history = [
            {"message": {"role": "user", "content": [{"type": "text", "text": "q1"}]}},
            {"message": {"role": "assistant", "content": "answer 1"}},
            {"message": {"role": "user", "content": [{"type": "text", "text": "q2"}]}},
            {"message": {"role": "assistant", "content": "answer 2"}},
        ]
        self._make_session(tmp_path, monkeypatch, cwd, "full1", history)

        events = data_kiro_ide.get_full_transcript("full1", cwd)
        assert [e.kind for e in events] == ["user", "assistant", "user", "assistant"]
        assert [e.text for e in events] == ["q1", "answer 1", "q2", "answer 2"]

    def test_preserves_full_history_beyond_tail_window(self, tmp_path, monkeypatch):
        """Unlike get_session_tail, which is assistant-only and truncates to
        max_lines, a full transcript keeps every turn, including user ones."""
        cwd = "C:\\LongTest"
        history = [
            {"message": {"role": "assistant", "content": f"msg{i}"}}
            for i in range(10)
        ]
        self._make_session(tmp_path, monkeypatch, cwd, "long1", history)

        events = data_kiro_ide.get_full_transcript("long1", cwd)
        assert len(events) == 10
        assert events[0].text == "msg0"
        assert events[-1].text == "msg9"

    def test_unrecognized_role_skipped_not_raised(self, tmp_path, monkeypatch):
        cwd = "C:\\RoleTest"
        history = [
            {"message": {"role": "system", "content": "internal note"}},
            {"message": {"role": "user", "content": "real question"}},
        ]
        self._make_session(tmp_path, monkeypatch, cwd, "role1", history)

        events = data_kiro_ide.get_full_transcript("role1", cwd)
        assert [e.text for e in events] == ["real question"]

    def test_unknown_workspace_returns_empty_list(self, tmp_path, monkeypatch):
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        assert data_kiro_ide.get_full_transcript("full1", "C:\\NoSuchWorkspace") == []

    def test_not_cached_reflects_file_changes(self, tmp_path, monkeypatch):
        cwd = "C:\\LiveTest"
        folder = self._make_session(tmp_path, monkeypatch, cwd, "live1", [
            {"message": {"role": "user", "content": "first"}},
        ])
        assert [e.text for e in data_kiro_ide.get_full_transcript("live1", cwd)] == ["first"]

        (folder / "live1.json").write_text(json.dumps({"history": [
            {"message": {"role": "user", "content": "first"}},
            {"message": {"role": "assistant", "content": "second"}},
        ]}), encoding="utf-8")
        assert [e.text for e in data_kiro_ide.get_full_transcript("live1", cwd)] == ["first", "second"]


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



class TestFrozenSession:


    def test_session_extra_fields_default_is_not_shared(self):
        """Verify each Session gets a distinct extra_fields dict (not a shared mutable default)."""
        s1 = Session("id1", "title", "cwd", "", "", "", "", "")
        s2 = Session("id2", "title", "cwd", "", "", "", "", "")
        assert s1.extra_fields is not s2.extra_fields

    def test_session_is_hashable(self):
        """Session objects must remain hashable after extra_fields addition (hash=False excludes the dict)."""
        s = Session("id1", "title", "cwd", "", "", "", "", "")
        assert hash(s) == hash(s)
        assert {s, s} == {s}  # usable in sets

    def test_session_compare_ignores_extra_fields(self):
        """Sessions differing only in extra_fields compare equal (compare=False on extra_fields)."""
        s1 = Session("id1", "title", "cwd", "", "", "", "", "", {"key": "v1"})
        s2 = Session("id1", "title", "cwd", "", "", "", "", "", {"key": "v2"})
        assert s1 == s2


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


class TestClaudeGetFullTranscript:
    """`get_full_transcript` parses the whole session `.jsonl`, unlike
    `get_session_tail` (last-128KB tail, assistant-text-only) -- and, unlike
    both tail helpers, also surfaces tool_use/tool_result content blocks."""

    def _make_project(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        proj = projects_dir / "C--Work"
        proj.mkdir()
        monkeypatch.setattr("power_atlas.data_claude.CLAUDE_PROJECTS_DIR", projects_dir)
        return proj

    def test_missing_jsonl_returns_empty_list(self, tmp_path, monkeypatch):
        self._make_project(tmp_path, monkeypatch)
        assert data_claude.get_full_transcript("does-not-exist", "C:\\Work") == []

    def test_full_ordered_transcript_across_all_event_kinds(self, tmp_path, monkeypatch):
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s1"
        lines = [
            json.dumps({"type": "user", "message": {"role": "user", "content": "Please fix the bug"}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tc1", "name": "fs_write",
                 "input": {"path": "a.py", "text": "print(1)"}},
            ]}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tc1", "content": "ok", "is_error": False},
            ]}}),
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "Fixed it."}}),
        ]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        assert [e.kind for e in events] == ["user", "assistant", "tool_call", "tool_result", "assistant"]
        assert events[0].text == "Please fix the bug"
        assert events[1].text == "Let me check."
        assert events[2].tool_call_id == "tc1"
        assert events[2].tool_name == "fs_write"
        assert events[2].tool_args == {"path": "a.py", "text": "print(1)"}
        assert events[3].tool_call_id == "tc1"
        assert events[3].success is True
        assert events[4].text == "Fixed it."

    def test_tool_result_is_error_true_maps_to_success_false(self, tmp_path, monkeypatch):
        from power_atlas.data import TranscriptEvent

        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s2"
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tc2", "content": "boom", "is_error": True},
        ]}})]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        assert events == [TranscriptEvent(kind="tool_result", tool_call_id="tc2", success=False)]

    def test_tool_result_missing_is_error_normalizes_to_none(self, tmp_path, monkeypatch):
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s3"
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tc3", "content": "ok"},
        ]}})]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        assert events[0].success is None

    def test_meta_and_command_messages_excluded(self, tmp_path, monkeypatch):
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s4"
        lines = [
            json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta stuff"}}),
            json.dumps({"type": "mode", "message": {}}),
            json.dumps({"type": "user", "message": {"role": "user", "content": "real question"}}),
        ]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        assert [e.text for e in events] == ["real question"]

    def test_command_xml_stripped_from_user_text(self, tmp_path, monkeypatch):
        """A message starting with a command tag is filtered out entirely by
        `_is_meta_or_command_message` -- this test instead covers the
        *stripping* path: an embedded, non-leading command tag survives the
        filter but has its surrounding tag markup (not its inner text)
        cleaned out of the rendered text, per `_strip_command_xml`'s own
        tags-only regex."""
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s5"
        lines = [json.dumps({"type": "user", "message": {
            "role": "user",
            "content": "actual text <command-name>note</command-name> trailing",
        }})]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        assert events[0].text == "actual text note trailing"

    def test_malformed_line_skipped_not_raised(self, tmp_path, monkeypatch):
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s6"
        lines = [
            "not valid json",
            json.dumps({"type": "user", "message": {"role": "user", "content": "still readable"}}),
        ]
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        assert [e.text for e in events] == ["still readable"]

    def test_preserves_full_history_beyond_tail_window(self, tmp_path, monkeypatch):
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s7"
        lines = [json.dumps({"type": "user", "message": {"role": "user", "content": f"Q{i}"}})
                 for i in range(40)]
        lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "final"}}))
        (proj / f"{sid}.jsonl").write_text("\n".join(lines), encoding="utf-8")

        events = data_claude.get_full_transcript(sid, "C:\\Work")
        user_events = [e for e in events if e.kind == "user"]
        assert len(user_events) == 40
        assert events[-1].text == "final"

    def test_not_cached_reflects_file_changes_without_mtime_bump(self, tmp_path, monkeypatch):
        proj = self._make_project(tmp_path, monkeypatch)
        sid = "s8"
        jsonl_path = proj / f"{sid}.jsonl"
        jsonl_path.write_text(json.dumps(
            {"type": "user", "message": {"role": "user", "content": "first"}}), encoding="utf-8")
        assert [e.text for e in data_claude.get_full_transcript(sid, "C:\\Work")] == ["first"]

        jsonl_path.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "first"}}) + "\n"
            + json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "second"}}),
            encoding="utf-8")
        assert [e.text for e in data_claude.get_full_transcript(sid, "C:\\Work")] == ["first", "second"]

    def test_unknown_cwd_returns_empty_list(self, tmp_path, monkeypatch):
        self._make_project(tmp_path, monkeypatch)
        assert data_claude.get_full_transcript("s1", "C:\\NoSuchProject") == []








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


def _scan_with(procs, claude_dir=None):
    """Run presence._scan() with a faked process table.

    The sidecar directories are redirected away from the real home directory
    by default, so these tests do not depend on what happens to be running on
    the machine. Pass *claude_dir* to exercise the Claude sidecar.
    """
    from power_atlas import presence
    missing = Path(tempfile.gettempdir()) / "_pa_no_such_sidecar_dir"
    presence._sidecar_cache.clear()
    with patch.object(presence, "_AVAILABLE", True), \
         patch.object(presence.psutil, "process_iter", return_value=procs), \
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
    assert snap.is_live("kiro-cli-v3", "/w", "kx") is True
    # The claude flag prefix must not cross-match kiro's --resume-id.
    assert snap.is_live("claude-code", "/w", "kx") is False


def test_presence_matches_kiro_v3_resume_id_flag():
    """A resumed v3 session (sess_-prefixed id) attributes to kiro-cli-v3,
    not kiro-cli — SC-6. Both entries in _PROVIDER_SPECS share a binary, so
    the reroute has to happen after the argv match, using the resumed id's
    own shape."""
    snap = _scan_with([
        _FakeProc("kiro-cli", ["kiro-cli", "chat", "--resume-id",
                                "sess_12345678-1234-1234-1234-123456789abc"],
                   cwd="/w"),
    ])
    v3_id = "sess_12345678-1234-1234-1234-123456789abc"
    assert snap.is_live("kiro-cli-v3", "/w", v3_id) is True
    assert snap.is_live("kiro-cli", "/w", v3_id) is False


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



