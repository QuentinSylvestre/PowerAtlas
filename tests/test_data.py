"""Tests for data module."""

import ast
import builtins
import contextlib
import ctypes
import errno
import importlib.util
import io
import itertools
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from power_atlas import data as data_mod
from power_atlas import data_codex, data_codex_state, lock_owner, quiet_log
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

    @pytest.mark.skipif(sys.platform != "win32", reason="8.3 short names are Windows-only")
    def test_short_name_matches_long_name(self, tmp_path):
        import ctypes
        from power_atlas.data import _normalize_path
        long_dir = tmp_path / "Long Directory Name"
        long_dir.mkdir()
        buf = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, len(buf))
        if "~" not in buf.value:
            pytest.skip("8.3 name generation disabled on this volume")
        assert _normalize_path(buf.value) == _normalize_path(str(long_dir))
        # A missing tail under a short-named ancestor still matches.
        assert (_normalize_path(buf.value + "\\gone\\sub")
                == _normalize_path(str(long_dir / "gone" / "sub")))



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

    def test_sessions_json_with_utf8_bom(self, tmp_path, monkeypatch):
        """A sessions.json or per-session <id>.json written with a UTF-8 BOM
        (seen on a real Kiro IDE store) is still read, not silently skipped."""
        sessions_dir = tmp_path / "workspace-sessions"
        sessions_dir.mkdir()
        history = [
            {"message": {"role": "user", "content": [{"type": "text", "text": "BOM prompt"}]}, "contextItems": []},
            {"message": {"role": "assistant", "content": "BOM reply"}, "contextItems": []},
        ]
        folder = self._make_workspace_with_sessions(sessions_dir, "C:\\BomProject", [
            ("sess-bom", "BOM Session", "1700000000000", history),
        ], folder_name="bom_folder")
        for name in ("sessions.json", "sess-bom.json"):
            f = folder / name
            f.write_bytes(b"\xef\xbb\xbf" + f.read_bytes())

        monkeypatch.setattr("power_atlas.data_kiro_ide.SESSIONS_DIR", sessions_dir)
        monkeypatch.setattr(data_kiro_ide, "_tail_cache", {})
        monkeypatch.setattr(data_kiro_ide, "_first_prompt_cache", {})

        assert [r[0] for r in data_kiro_ide.discover_workspaces()] == ["C:\\BomProject"]
        sessions, _ = data_kiro_ide.load_sessions("C:\\BomProject")
        assert [s.session_id for s in sessions] == ["sess-bom"]
        assert sessions[0].first_prompt == "BOM prompt"
        assert data_kiro_ide.get_session_tail("sess-bom", "C:\\BomProject") == ["BOM reply"]
        assert data_kiro_ide.get_first_prompt("sess-bom", "C:\\BomProject") == "BOM prompt"

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
                 pid=None, create_time=None, ppid=None):
        self.info = {"name": name, "cmdline": cmdline}
        if pid is not None:
            self.info["pid"] = pid
        self._cwd = cwd
        self._cwd_error = cwd_error
        self._create_time = create_time
        self._ppid = ppid

    def cwd(self):
        if self._cwd_error:
            raise RuntimeError("access denied")
        return self._cwd

    def create_time(self):
        if self._create_time is None:
            raise RuntimeError("no create_time")
        return self._create_time

    def ppid(self):
        if self._ppid is None:
            raise RuntimeError("no ppid")
        return self._ppid


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




# --- Codex adapter (data_codex.py) ---
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1
#
# Every fixture here is SYNTHETIC, built by the helpers below from the record
# key paths measured in 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 0
# (plan section 9, item 4). No real session text, id, path or folder name
# from any developer's store belongs in this file: the
# repository is public.

_CX_TS = "2026-09-01T10:00:00.000Z"
_CX_OLD = 1_577_836_800  # 2020-01-01T00:00:00Z: older than every record timestamp below


def _cx_id(n: int) -> str:
    """A synthetic thread id, distinct per n and never a real one."""
    return f"{n:08x}-1111-4222-8333-{n:012x}"


@pytest.fixture(autouse=True)
def codex_home(tmp_path, monkeypatch):
    """Point the three Codex roots at tmp_path and drop every Codex cache.

    Autouse and unconditional: the adapter is registered in `data.PROVIDERS`, so
    any test that lists providers would otherwise read the developer's real
    ~/.codex. The 5 s memos are disabled so a test sees each store mutation.
    """
    home = tmp_path / "codex-home"
    monkeypatch.setattr(data_codex, "CODEX_SESSIONS_DIR", home / "sessions")
    monkeypatch.setattr(data_codex, "CODEX_SESSION_INDEX", home / "session_index.jsonl")
    monkeypatch.setattr(data_codex, "CODEX_LOCKS_DIR", home / "thread-writer-locks")
    monkeypatch.setattr(data_codex, "_STORE_TTL", 0.0)
    monkeypatch.setattr(data_codex, "_AVAILABLE_TTL", 0.0)
    monkeypatch.setattr(data_codex_state, "CODEX_STATE_DIR", home)
    monkeypatch.setattr(lock_owner, "_resolver", lock_owner._Resolver(threads=False, self_test=False))
    data_codex._clear_caches()
    data_codex_state.clear_memo()
    quiet_log.reset()
    yield home
    data_codex._clear_caches()
    data_codex_state.clear_memo()
    quiet_log.reset()


def _cx_rec(rtype, payload, ts=_CX_TS):
    return {"ordinal": 0, "timestamp": ts, "type": rtype, "payload": payload}


def _cx_meta(sid, cwd, *, source="cli", ts=_CX_TS, base_chars=17000, **extra):
    payload = {
        "id": sid, "session_id": sid, "timestamp": ts, "cwd": cwd,
        "originator": "codex_cli_rs", "cli_version": "0.159.2", "source": source,
        "model_provider": "openai", "history_mode": "full",
        "base_instructions": {"text": "b" * base_chars},
    }
    payload.update(extra)
    return _cx_rec("session_meta", payload, ts)


def _cx_user(text, ts=_CX_TS):
    return _cx_rec("event_msg", {
        "type": "item_completed", "thread_id": "t", "turn_id": "u", "completed_at_ms": 1,
        "item": {"type": "UserMessage", "id": "i1",
                 "content": [{"type": "text", "text": text, "text_elements": []}]},
    }, ts)


def _cx_agent(text, ts=_CX_TS):
    return _cx_rec("event_msg", {
        "type": "item_completed", "thread_id": "t", "turn_id": "u", "completed_at_ms": 2,
        "item": {"type": "AgentMessage", "id": "i2", "phase": "final_answer",
                 "content": [{"type": "Text", "text": text}]},
    }, ts)


def _cx_injected(text):
    """A user-role response_item message: injected context, never a prompt."""
    return _cx_rec("response_item", {
        "type": "message", "role": "user", "id": "m1",
        "content": [{"type": "input_text", "text": text}],
    })


def _cx_call(name, arguments, call_id, ts=_CX_TS):
    return _cx_rec("response_item", {
        "type": "function_call", "name": name, "arguments": arguments, "call_id": call_id}, ts)


def _cx_custom_call(name, input_text, call_id, ts=_CX_TS):
    return _cx_rec("response_item", {
        "type": "custom_tool_call", "name": name, "input": input_text,
        "call_id": call_id, "status": "completed"}, ts)


def _cx_output(call_id, output, *, custom=False, ts=_CX_TS):
    kind = "custom_tool_call_output" if custom else "function_call_output"
    return _cx_rec("response_item", {"type": kind, "call_id": call_id, "output": output}, ts)


def _cx_line(rec) -> bytes:
    return json.dumps(rec, separators=(",", ":")).encode("utf-8") + b"\n"


def _cx_write(home, sid, cwd, records=(), *, day="2026/09/01", source="cli", ts=_CX_TS,
              first=None, name=None, tail=b"", mtime=None) -> Path:
    """Write one rollout. `first` overrides the first line, `tail` is appended raw,
    `mtime` (epoch seconds) overrides the strictly increasing fixture mtime."""
    folder = home / "sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"rollout-2026-09-01T10-00-00-{sid}.jsonl")
    head = first if first is not None else _cx_line(_cx_meta(sid, cwd, source=source, ts=ts))
    path.write_bytes(head + b"".join(_cx_line(r) for r in records) + tail)
    if mtime is None:
        _bump_mtime(path)
    else:
        os.utime(path, (mtime, mtime))
    return path


def _cx_index(home, entries):
    path = home / "session_index.jsonl"
    path.write_bytes(b"".join(
        _cx_line({"id": i, "thread_name": n, "updated_at": _CX_TS}) for i, n in entries))
    _bump_mtime(path)
    return path


class _CountingFile:
    """Wraps a handle and counts the bytes the adapter actually reads from it."""

    def __init__(self, fh, counter):
        self._fh, self._counter = fh, counter

    def read(self, *args):
        chunk = self._fh.read(*args)
        self._counter[0] += len(chunk)
        return chunk

    def readline(self, *args):
        line = self._fh.readline(*args)
        self._counter[0] += len(line)
        return line

    def seek(self, *args):
        return self._fh.seek(*args)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()


@pytest.fixture
def counted_reads(monkeypatch):
    """A one-element list holding the bytes read through `open_shared` since patching."""
    counter = [0]
    real = data_codex.open_shared
    monkeypatch.setattr(data_codex, "open_shared",
                        lambda path, mode="rb": _CountingFile(real(path, mode), counter))
    return counter


def _cx_filler(n_lines, width=1000):
    """Lines that carry no message and no tool record: they only take space."""
    return [_cx_rec("turn_context", {"model": "m", "cwd": "C:\\x", "pad": "p" * width})
            for _ in range(n_lines)]


class TestCodexGuards:
    def test_roots_are_redirected_from_the_real_home(self):
        """Fails if any Codex root still resolves under the real ~/.codex (or
        $CODEX_HOME): an unpatched root would let a test read a developer's store."""
        real = [Path.home() / ".codex"]
        if os.environ.get("CODEX_HOME", "").strip():
            real.append(Path(os.environ["CODEX_HOME"]))
        for root in (data_codex.CODEX_SESSIONS_DIR, data_codex.CODEX_SESSION_INDEX,
                     data_codex.CODEX_LOCKS_DIR):
            for forbidden in real:
                assert not Path(root).resolve().is_relative_to(forbidden.resolve()), root

    def test_codex_is_a_registered_provider(self):
        assert data_mod.PROVIDERS["codex"] is data_codex

    def test_platform_modules_are_not_imported_at_top_level(self):
        """D23: a top-level `import msvcrt` / `fcntl` breaks import on the other OS."""
        tree = ast.parse(Path(data_codex.__file__).read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top.add(node.module.split(".")[0])
        assert not top & {"msvcrt", "fcntl", "ctypes", "winreg"}

    def test_open_shared_posix_branch(self, tmp_path, monkeypatch):
        p = tmp_path / "f.bin"
        p.write_bytes(b"abc")
        monkeypatch.setattr(sys, "platform", "linux")
        with data_codex.open_shared(p) as fh:
            assert fh.read() == b"abc"

    def test_open_shared_never_creates_and_rejects_every_mode_but_rb(self, tmp_path):
        """Read-only is the only mode (final review finding A9): nothing writes a Codex file."""
        with pytest.raises(FileNotFoundError):
            data_codex.open_shared(tmp_path / "absent.lock", "rb")
        assert not (tmp_path / "absent.lock").exists()
        target = tmp_path / "x"
        target.write_bytes(b"data")
        for mode in ("r", "r+b", "wb", "ab", "rb+"):
            with pytest.raises(ValueError):
                data_codex.open_shared(target, mode)
        assert target.read_bytes() == b"data"


class TestCodexSharedDelete:
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows file-sharing semantics")
    def test_another_process_can_rename_and_remove_while_a_reader_is_open(self, tmp_path):
        target = tmp_path / "rollout.jsonl"
        script = ("import os, sys\n"
                  "src, dst = sys.argv[1:3]\n"
                  "os.rename(src, dst)\n"
                  "os.remove(dst)\n"
                  "print('renamed-and-removed')\n")

        def attempt(opener):
            target.write_bytes(b"line\n")
            with opener(target) as fh:
                assert fh.read(1) == b"l"
                return subprocess.run(
                    [sys.executable, "-c", script, str(target), str(tmp_path / "moved.jsonl")],
                    capture_output=True, text=True, timeout=60)

        # Control: a plain open() blocks both (winerror 32), which is the defect
        # open_shared exists to avoid.
        blocked = attempt(lambda p: open(p, "rb"))
        assert blocked.returncode != 0
        assert "renamed-and-removed" not in blocked.stdout
        assert target.exists()

        shared = attempt(data_codex.open_shared)
        assert shared.returncode == 0, shared.stderr
        assert "renamed-and-removed" in shared.stdout
        assert not target.exists()

    def test_no_public_function_reads_a_rollout_through_builtins_open(
            self, codex_home, monkeypatch):
        """Every rollout read goes through open_shared (D10). `activity_epoch` belongs to
        261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3,
        so it is not part of this check yet."""
        sid, cwd = _cx_id(1), "C:\\Work\\One"
        _cx_write(codex_home, sid, cwd, [_cx_user("hello there"), _cx_agent("hi back"),
                                         _cx_call("shell_command", '{"command":"ls"}', "c1"),
                                         _cx_output("c1", "Exit code: 0\nOutput:\nok")])
        real_open = builtins.open
        root = str(data_codex.CODEX_SESSIONS_DIR).lower()

        def guarded(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and os.fspath(file).lower().startswith(root):
                raise AssertionError(f"builtins.open used for a rollout: {file}")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", guarded)
        # Each result is non-neutral: a contained AssertionError would otherwise
        # read as a pass.
        assert data_codex.is_available() is True
        found = data_codex.discover_workspaces()
        assert [(c, n) for c, n, _ in found] == [(cwd, 1)]
        sessions, stats = data_codex.load_sessions(cwd)
        assert [s.first_prompt for s in sessions] == ["hello there"]
        assert data_codex.find_session_workspace(sid) == cwd
        path = data_codex.rollout_path(sid)
        assert path is not None
        assert data_codex.read_meta(path)["cwd"] == cwd
        assert data_codex.is_subagent_rollout(path) is False
        assert data_codex.get_session_tail(sid, cwd) == ["hi back"]
        assert data_codex.get_first_prompt(sid, cwd) == "hello there"
        assert [e.kind for e in data_codex.get_full_transcript(sid, cwd)] == [
            "user", "assistant", "tool_call", "tool_result"]
        assert data_codex.refresh_stale_entries_for_cwd(data_mod._normalize_path(cwd), stats) is False


class TestCodexAvailability:
    def test_unavailable_without_rollouts(self, codex_home):
        assert data_codex.is_available() is False
        (codex_home / "sessions" / "2026" / "09" / "01").mkdir(parents=True)
        assert data_codex.is_available() is False

    def test_available_with_a_rollout_in_any_date_folder(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", day="2031/12/31")
        assert data_codex.is_available() is True

    def test_archived_sessions_sibling_is_ignored(self, codex_home):
        archived = codex_home / "archived_sessions"
        archived.mkdir(parents=True)
        (archived / f"rollout-2026-09-01T10-00-00-{_cx_id(1)}.jsonl").write_bytes(
            _cx_line(_cx_meta(_cx_id(1), "C:\\W")))
        assert data_codex.is_available() is False
        assert data_codex.discover_workspaces() == []

    def test_unreadable_root_is_false_and_does_not_raise(self, codex_home, monkeypatch):
        _cx_write(codex_home, _cx_id(1), "C:\\W")
        assert data_codex.is_available() is True

        def denied(*args, **kwargs):
            raise PermissionError("denied")
        monkeypatch.setattr(os, "scandir", denied)
        data_codex._clear_caches()
        assert data_codex.is_available() is False
        assert data_codex.discover_workspaces() == []

    def test_root_that_is_a_file_is_unavailable(self, codex_home, monkeypatch):
        not_a_dir = codex_home / "plain-file"
        codex_home.mkdir(parents=True, exist_ok=True)
        not_a_dir.write_bytes(b"x")
        monkeypatch.setattr(data_codex, "CODEX_SESSIONS_DIR", not_a_dir)
        assert data_codex.is_available() is False


class TestCodexDiscovery:
    def test_subagent_rollouts_are_excluded_and_every_top_level_source_kept(self, codex_home):
        cwd = "C:\\Work\\Proj"
        spawn = {"subagent": {"thread_spawn": {"agent_nickname": None, "agent_path": None,
                                               "agent_role": None, "depth": 1,
                                               "parent_thread_id": _cx_id(1)}}}
        guardian = {"subagent": {"other": "guardian"}}
        _cx_write(codex_home, _cx_id(1), cwd, source="cli")
        _cx_write(codex_home, _cx_id(2), cwd, source="vscode")
        # A string that merely says "subagent", and an object without the key,
        # are top-level: only an object containing the key `subagent` is a child.
        _cx_write(codex_home, _cx_id(3), cwd, source="subagent")
        _cx_write(codex_home, _cx_id(4), cwd, source={"other_key": 1})
        _cx_write(codex_home, _cx_id(5), cwd, source=spawn)
        _cx_write(codex_home, _cx_id(6), cwd, source=guardian)
        sessions, _ = data_codex.load_sessions(cwd)
        assert {s.session_id for s in sessions} == {_cx_id(1), _cx_id(2), _cx_id(3), _cx_id(4)}
        assert [(c, n) for c, n, _ in data_codex.discover_workspaces()] == [(cwd, 4)]
        assert data_codex.find_session_workspace(_cx_id(5)) is None
        # ... while the file itself is still found, and classified as a child.
        child = data_codex.rollout_path(_cx_id(5))
        assert data_codex.is_subagent_rollout(child) is True
        assert data_codex.is_subagent_rollout(data_codex.rollout_path(_cx_id(3))) is False

    @pytest.mark.skipif(sys.platform != "win32", reason="drive-letter case folding is Windows-only")
    def test_cwds_differing_only_in_drive_letter_case_are_one_workspace(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\Work\\Proj", mtime=_CX_OLD)
        _cx_write(codex_home, _cx_id(2), "c:\\Work\\Proj")  # newer, lower-case spelling
        _cx_write(codex_home, _cx_id(3), "C:\\Work\\Other", mtime=_CX_OLD + 5)
        found = {c: n for c, n, _ in data_codex.discover_workspaces()}
        # The display spelling is the one the most recently modified session used.
        assert found == {"c:\\Work\\Proj": 2, "C:\\Work\\Other": 1}
        sessions, _ = data_codex.load_sessions("C:\\Work\\Proj")
        assert {s.session_id for s in sessions} == {_cx_id(1), _cx_id(2)}

    def test_workspaces_come_back_newest_first_with_session_counts(self, codex_home):
        # The meta line is itself a record, so it is stamped before every mtime here.
        old = "2019-12-31T00:00:00Z"
        _cx_write(codex_home, _cx_id(1), "C:\\A", mtime=_CX_OLD, ts=old)
        _cx_write(codex_home, _cx_id(2), "C:\\B", mtime=_CX_OLD + 100, ts=old)
        _cx_write(codex_home, _cx_id(3), "C:\\B", mtime=_CX_OLD + 50, ts=old)
        _cx_write(codex_home, _cx_id(4), "C:\\B", mtime=_CX_OLD + 10, ts=old)
        found = data_codex.discover_workspaces()
        assert [(c, n) for c, n, _ in found] == [("C:\\B", 3), ("C:\\A", 1)]
        assert found[0][2] == "2020-01-01T00:01:40.000000+00:00"

    @pytest.mark.parametrize("case", [
        "legacy_without_payload", "torn_first_line", "first_line_over_cap", "non_dict_first_line",
        "wrong_typed_cwd", "list_cwd", "empty_cwd", "blank_cwd", "wrong_typed_id", "non_uuid_id",
        "id_differs_from_filename", "recursion_bomb", "empty_file", "binary_garbage", "payload_not_a_dict",
        "not_session_meta",
    ])
    def test_a_bad_file_is_skipped_and_never_affects_other_sessions(self, codex_home, case):
        good_cwd = "C:\\Work\\Good"
        bad_sid = _cx_id(2)
        _cx_write(codex_home, _cx_id(1), good_cwd)
        bad_cwd = "C:\\Work\\Bad"
        meta = _cx_meta(bad_sid, bad_cwd)
        first = {
            "legacy_without_payload": _cx_line({"id": bad_sid, "timestamp": _CX_TS, "cwd": bad_cwd,
                                                "instructions": "x"}),
            "torn_first_line": b'{"ordinal":0,"timestamp":"2026-09-01T10:0',
            "first_line_over_cap": _cx_line(_cx_meta(bad_sid, bad_cwd, base_chars=300_000)),
            "non_dict_first_line": b"[1,2,3]\n",
            "wrong_typed_cwd": _cx_line(_cx_meta(bad_sid, 5)),
            "list_cwd": _cx_line(_cx_meta(bad_sid, [bad_cwd])),
            "empty_cwd": _cx_line(_cx_meta(bad_sid, "")),
            "blank_cwd": _cx_line(_cx_meta(bad_sid, "   ")),
            "wrong_typed_id": _cx_line({**meta, "payload": {**meta["payload"], "id": 7}}),
            "non_uuid_id": _cx_line({**meta, "payload": {**meta["payload"], "id": "not-a-uuid"}}),
            "id_differs_from_filename": _cx_line(_cx_meta(_cx_id(99), bad_cwd)),
            # Under the 256 KiB cap so it reaches json.loads: the nesting, not
            # the size, is what is being tolerated.
            "recursion_bomb": b"[" * 100_000 + b"]" * 100_000 + b"\n",
            "empty_file": b"",
            "binary_garbage": bytes(range(256)) * 8 + b"\n",
            "payload_not_a_dict": _cx_line({**meta, "payload": [1, 2]}),
            "not_session_meta": _cx_line({**meta, "type": "event_msg"}),
        }[case]
        _cx_write(codex_home, bad_sid, bad_cwd, first=first)
        sessions, stats = data_codex.load_sessions(good_cwd)
        assert [s.session_id for s in sessions] == [_cx_id(1)]
        assert [(c, n) for c, n, _ in data_codex.discover_workspaces()] == [(good_cwd, 1)]
        assert data_codex.load_sessions(bad_cwd)[0] == []
        assert data_codex.find_session_workspace(bad_sid) is None

    def test_wrong_typed_timestamp_falls_back_to_the_file_mtime(self, codex_home):
        """D10 (the plan's test list says such a file is skipped; the decision
        text says its timestamp falls back to the mtime: the decision is
        followed and the divergence recorded)."""
        meta = _cx_meta(_cx_id(1), "C:\\W")
        meta["payload"]["timestamp"] = ["not", "a", "time"]
        _cx_write(codex_home, _cx_id(1), "C:\\W", first=_cx_line(meta), mtime=_CX_OLD)
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.created_at == "2020-01-01T00:00:00.000000+00:00"

    def test_created_at_is_normalised_to_utc_iso(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", ts="2026-09-01T12:30:00+02:00")
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.created_at == "2026-09-01T10:30:00.000000+00:00"

    def test_unknown_record_types_and_cli_version_are_tolerated(self, codex_home):
        meta = _cx_meta(_cx_id(1), "C:\\W", cli_version="99.0.0-future", novel_key={"a": [1]})
        _cx_write(codex_home, _cx_id(1), "C:\\W", first=_cx_line(meta), records=[
            _cx_rec("novel_future_type", {"x": 1}), _cx_user("still found"),
            _cx_rec("event_msg", {"type": "brand_new_event"})])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "still found"

    def test_two_rollouts_for_one_thread_id_the_newer_wins(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        older = _cx_write(codex_home, sid, cwd, [_cx_user("from the older file")], day="2026/09/01")
        newer = _cx_write(codex_home, sid, cwd, [_cx_user("from the newer file")], day="2026/09/02",
                          name=f"rollout-2026-09-02T10-00-00-{sid}.jsonl")
        assert os.stat(newer).st_mtime_ns > os.stat(older).st_mtime_ns
        sessions, stats = data_codex.load_sessions(cwd)
        assert [s.first_prompt for s in sessions] == ["from the newer file"]
        assert [(c, n) for c, n, _ in data_codex.discover_workspaces()] == [(cwd, 1)]
        assert data_codex.rollout_path(sid) == newer
        assert str(newer) in stats and str(older) not in stats
        # The other way round: the file with the later mtime wins, not the later name.
        _bump_mtime(older)
        data_codex._clear_caches()
        assert data_codex.rollout_path(sid) == older
        assert data_codex.load_sessions(cwd)[0][0].first_prompt == "from the older file"


class TestCodexPrompts:
    def test_first_real_prompt_ignores_a_40kb_injected_user_message(self, codex_home):
        injected = "# AGENTS.md instructions for C:\\Work\\Proj\n" + "rule " * 8000  # about 40 KB
        assert len(injected) >= 40_000
        _cx_write(codex_home, _cx_id(1), "C:\\W", [
            _cx_injected(injected), _cx_user("fix the flaky parser"), _cx_agent("on it")])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "fix the flaky parser"
        assert session.title == "fix the flaky parser"
        assert not session.first_prompt.startswith("# AGENTS.md")

    def test_a_session_with_only_injected_context_has_no_prompt_and_is_titled_by_its_id(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_injected("# AGENTS.md instructions\nrules")])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "" and session.last_prompt == ""
        assert session.title == _cx_id(1)

    def test_first_prompt_last_prompt_and_reply_tail_come_from_the_right_ends(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [
            _cx_user("first question"), _cx_agent("first answer"),
            _cx_user("second question"), _cx_agent("second answer"),
            _cx_rec("token_count", {"type": "token_count", "info": None})])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "first question"
        assert session.last_prompt == "second question"
        assert session.last_reply_tail == "second answer"

    def test_text_caps(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [
            _cx_user("p" * 500), _cx_agent("r" * 500), _cx_user("q" * 500)])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert len(session.first_prompt) == 200 and len(session.last_prompt) == 200
        assert len(session.last_reply_tail) == 100
        assert len(session.title) == 80

    def test_multi_element_content_and_non_text_elements(self, codex_home):
        rec = _cx_user("unused")
        rec["payload"]["item"]["content"] = [
            {"type": "text", "text": "look at"}, {"type": "local_image", "path": "x.png"},
            {"type": "image"}, {"type": "text", "text": "this"}]
        _cx_write(codex_home, _cx_id(1), "C:\\W", [rec])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "look at this"

    def test_wrong_typed_record_fields_are_ignored(self, codex_home):
        junk = [
            {"ordinal": 1, "timestamp": _CX_TS, "type": "event_msg", "payload": "text"},
            {"ordinal": 2, "timestamp": _CX_TS, "type": "event_msg",
             "payload": {"type": "item_completed", "item": [1, 2]}},
            {"ordinal": 3, "timestamp": 5, "type": "event_msg",
             "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": "no list"}}},
            {"ordinal": 4, "timestamp": _CX_TS, "type": "event_msg",
             "payload": {"type": "item_completed", "item": {"type": "UserMessage",
                                                            "content": [{"type": "text", "text": 42}]}}},
            {"ordinal": 5, "timestamp": _CX_TS, "type": 7, "payload": {}},
            [1, 2], "a string", 3,
        ]
        _cx_write(codex_home, _cx_id(1), "C:\\W", junk + [_cx_user("the real one")])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "the real one"
        events = data_codex.get_full_transcript(_cx_id(1), "C:\\W")
        assert [e.text for e in events] == ["the real one"]

    def test_a_prompt_beyond_the_first_tail_window_is_found_by_widening(self, codex_home):
        """The last UserMessage sits more than 256 KiB from the end, behind
        filler whose line length makes the window start mid-line."""
        records = ([_cx_user("opening prompt")] + _cx_filler(3)
                   + [_cx_user("closing prompt"), _cx_agent("closing reply")]
                   + _cx_filler(700, width=1013))
        _cx_write(codex_home, _cx_id(1), "C:\\W", records)
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "opening prompt"
        assert session.last_prompt == "closing prompt"
        assert session.last_reply_tail == "closing reply"

    def test_a_user_message_over_the_line_cap_is_skipped_and_the_next_one_is_the_first_prompt(
            self, codex_home):
        huge = _cx_user("H" * 300_000)  # a valid UserMessage, over 256 KiB
        _cx_write(codex_home, _cx_id(1), "C:\\W", [huge, _cx_user("small real prompt")])
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "small real prompt"
        assert session.title == "small real prompt"

    def test_get_first_prompt_and_get_session_tail_smoke(self, codex_home):
        sid = _cx_id(1)
        _cx_write(codex_home, sid, "C:\\W", [
            _cx_injected("ctx"), _cx_user("ask one"), _cx_agent("answer one"),
            _cx_user("ask two"), _cx_agent("answer two"), _cx_agent("answer three")])
        assert data_codex.get_first_prompt(sid, "C:\\W") == "ask one"
        assert data_codex.get_session_tail(sid, "C:\\W") == ["answer one", "answer two", "answer three"]
        assert data_codex.get_session_tail(sid, "C:\\W", max_lines=2) == ["answer two", "answer three"]
        assert data_codex.get_session_tail(sid, "C:\\W", max_lines=0) == []
        assert data_codex.get_first_prompt(_cx_id(2), "C:\\W") == ""
        assert data_codex.get_session_tail("../../etc/passwd", "C:\\W") == []


class TestCodexTitles:
    def test_index_name_beats_the_first_prompt_and_the_last_duplicate_wins(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a prompt")])
        _cx_write(codex_home, _cx_id(2), "C:\\W", [_cx_user("another prompt")])
        _cx_index(codex_home, [(_cx_id(1), "Old name"), (_cx_id(1), "New name"),
                               (_cx_id(3), "Other thread")])
        titles = {s.session_id: s.title for s in data_codex.load_sessions("C:\\W")[0]}
        assert titles == {_cx_id(1): "New name", _cx_id(2): "another prompt"}

    def test_blank_or_malformed_index_entries_do_not_name_a_session(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a prompt")])
        index = codex_home / "session_index.jsonl"
        index.write_bytes(_cx_line({"id": _cx_id(1), "thread_name": "   "}) + b"not json\n"
                          + _cx_line({"id": 5, "thread_name": "x"}) + _cx_line([1]))
        assert data_codex.load_sessions("C:\\W")[0][0].title == "a prompt"

    def test_a_rename_with_an_unchanged_rollout_shows_and_marks_the_workspace_stale(self, codex_home):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a prompt")])
        _cx_index(codex_home, [(_cx_id(1), "AAAA")])
        sessions, stats = data_codex.load_sessions(cwd)
        assert sessions[0].title == "AAAA"
        norm = data_mod._normalize_path(cwd)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        _cx_index(codex_home, [(_cx_id(1), "BBBB")])  # same size, only the mtime moves
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True
        assert data_codex.load_sessions(cwd)[0][0].title == "BBBB"

    def test_the_index_appearing_or_vanishing_marks_the_workspace_stale(self, codex_home):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a prompt")])
        norm = data_mod._normalize_path(cwd)
        _sessions, absent = data_codex.load_sessions(cwd)
        assert data_codex.refresh_stale_entries_for_cwd(norm, absent) is False
        index = _cx_index(codex_home, [(_cx_id(1), "Named")])
        assert data_codex.refresh_stale_entries_for_cwd(norm, absent) is True
        _sessions, present = data_codex.load_sessions(cwd)
        assert data_codex.refresh_stale_entries_for_cwd(norm, present) is False
        os.remove(index)
        assert data_codex.refresh_stale_entries_for_cwd(norm, present) is True


class TestCodexUpdatedAt:
    def test_the_last_complete_record_beats_a_frozen_older_mtime(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [
            _cx_user("a", ts="2026-09-01T10:01:00Z"), _cx_agent("b", ts="2026-09-01T10:05:00Z")],
            mtime=_CX_OLD)
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.updated_at == "2026-09-01T10:05:00.000000+00:00"

    def test_a_newer_mtime_beats_an_older_last_record(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a", ts="2026-09-01T10:01:00Z")],
                  mtime=1_900_000_000)  # 2030-03-17
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.updated_at == "2030-03-17T17:46:40.000000+00:00"

    def test_a_torn_last_line_is_not_a_record(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a", ts="2026-09-01T10:01:00Z")],
                  tail=b'{"ordinal":9,"timestamp":"2026-09-01T23:59:59Z","type":"event_ms', mtime=_CX_OLD)
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.updated_at == "2026-09-01T10:01:00.000000+00:00"

    def test_a_future_record_timestamp_cannot_pin_a_session(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a", ts="2999-01-01T00:00:00Z")],
                  mtime=_CX_OLD)
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.updated_at < "2100"
        assert datetime.fromisoformat(session.updated_at) <= datetime.now(timezone.utc)


class TestCodexParseCache:
    def test_a_frozen_mtime_with_a_growing_size_is_reparsed(self, codex_home):
        """Windows freezes the mtime of a rollout Codex holds open while it grows."""
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("first"), _cx_user("second")])
        assert data_codex.load_sessions(cwd)[0][0].last_prompt == "second"
        frozen = os.stat(path).st_mtime_ns
        with open(path, "ab") as fh:
            fh.write(_cx_line(_cx_user("third")))
        os.utime(path, ns=(frozen, frozen))
        assert os.stat(path).st_mtime_ns == frozen
        sessions, stats = data_codex.load_sessions(cwd)
        assert sessions[0].last_prompt == "third"
        assert sessions[0].first_prompt == "first"
        assert stats[str(path)].size == os.stat(path).st_size

    def test_a_rewritten_smaller_file_is_reparsed(self, codex_home):
        """Compaction rewrites a rollout in place; a file only ever shrinking
        or changing size at the same mtime must not serve the old parse."""
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("before compaction " + "x" * 3000),
                                                _cx_agent("long reply " + "y" * 3000)])
        assert data_codex.load_sessions(cwd)[0][0].first_prompt.startswith("before compaction")
        mtime = os.stat(path).st_mtime_ns
        big = os.stat(path).st_size
        path.write_bytes(_cx_line(_cx_meta(sid, cwd)) + _cx_line(_cx_user("after")))
        os.utime(path, ns=(mtime, mtime))
        assert os.stat(path).st_size < big
        (session,), _ = data_codex.load_sessions(cwd)
        assert session.first_prompt == "after"
        assert session.last_reply_tail == ""

    def test_an_unchanged_file_is_not_reparsed(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a")])
        calls = []
        real = data_codex._parse_rollout
        monkeypatch.setattr(data_codex, "_parse_rollout", lambda p: calls.append(p) or real(p))
        data_codex.load_sessions(cwd)
        data_codex.load_sessions(cwd)
        data_codex.discover_workspaces()
        assert len(calls) == 1

    def test_the_store_index_is_reused_within_its_ttl(self, codex_home, monkeypatch):
        _cx_write(codex_home, _cx_id(1), "C:\\W")
        monkeypatch.setattr(data_codex, "_STORE_TTL", 5.0)
        walks = []
        real = data_codex._walk_rollouts
        monkeypatch.setattr(data_codex, "_walk_rollouts", lambda root: walks.append(root) or real(root))
        for _ in range(3):
            data_codex.discover_workspaces()
            data_codex.find_session_workspace(_cx_id(1))
            data_codex.rollout_path(_cx_id(1))
        assert len(walks) == 1
        # A rollout that appears is seen once the memo is rebuilt (never a glob per call).
        _cx_write(codex_home, _cx_id(2), "C:\\W")
        monkeypatch.setattr(data_codex, "_STORE_TTL", 0.0)
        assert [n for _, n, _ in data_codex.discover_workspaces()] == [2]

    def test_the_caches_are_larger_than_the_store(self):
        """A cache smaller than a sequential scan thrashes on every pass (D10)."""
        assert data_codex._parse_cache._maxsize >= 4096
        assert data_codex._verdict_cache._maxsize >= 4096


class TestCodexBoundedReads:
    def test_head_and_tail_scans_read_no_more_than_their_caps(self, codex_home, counted_reads):
        """No message anywhere in 7 MB: the head scan runs to its 1 MiB cap and the
        tail scan widens to its 2 MiB cap, and neither reads one byte more."""
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, _cx_filler(7000))
        first_line = len(_cx_line(_cx_meta(sid, cwd)))
        assert path.stat().st_size > 7_000_000
        (session,), _ = data_codex.load_sessions(cwd)
        assert session.first_prompt == ""
        reads = counted_reads[0]
        # One first-line read for the verdict, then head (first line included) <= 1 MiB,
        # tail <= 2 MiB.
        assert reads <= first_line + 1 * 1024 * 1024 + 2 * 1024 * 1024
        assert reads >= 3 * 1024 * 1024 - 64 * 1024, "the scans did not run to their caps"

    def test_a_small_file_is_read_once_per_scan_not_per_widening(self, codex_home, counted_reads):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a"), _cx_agent("b")])
        size = next((codex_home / "sessions").rglob("*.jsonl")).stat().st_size
        data_codex.load_sessions("C:\\W")
        first_line = len(_cx_line(_cx_meta(_cx_id(1), "C:\\W")))
        assert counted_reads[0] <= first_line + size + size  # verdict + head + tail, nothing more

    def test_transcript_reads_at_most_8_mib_and_keeps_the_last_3000_events(
            self, codex_home, counted_reads):
        sid, cwd = _cx_id(1), "C:\\W"
        pad = "z" * 1200
        records = [_cx_agent(f"msg-{i:06d} {pad}") for i in range(24_000)]
        path = _cx_write(codex_home, sid, cwd, records)
        assert path.stat().st_size > 28_000_000
        events = data_codex.get_full_transcript(sid, cwd)
        first_line = len(_cx_line(_cx_meta(sid, cwd)))  # read once to classify the file
        assert counted_reads[0] <= 8 * 1024 * 1024 + 1 + first_line  # the window, one byte early
        assert len(events) == 3000 + 1
        notice, *kept = events
        assert notice.kind == "assistant" and notice.text.startswith("(Earlier events omitted")
        # The last 3000 of the 24,000 (independent of the code): ids 21000..23999.
        assert kept[0].text.startswith("msg-021000 ") and kept[-1].text.startswith("msg-023999 ")
        assert [e.text[:10] for e in kept] == [f"msg-{i:06d}" for i in range(21_000, 24_000)]

    def test_event_cap_alone_says_how_many_were_left_out(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [_cx_agent(f"m{i}") for i in range(3005)])
        events = data_codex.get_full_transcript(sid, cwd)
        assert events[0].text == "(Earlier events omitted: showing the last 3000 of 3005.)"
        assert events[1].text == "m5" and events[-1].text == "m3004"

    def test_a_transcript_within_every_cap_has_no_notice(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [_cx_agent("only")])
        assert [e.text for e in data_codex.get_full_transcript(sid, cwd)] == ["only"]

    @pytest.mark.parametrize("window, expected", [(4095, 3), (4096, 4), (4097, 4), (4098, 4)])
    def test_the_window_starts_at_the_first_complete_line(self, codex_home, monkeypatch, window, expected):
        """Six 1024-byte lines: a window of 4096 starts exactly on a line boundary
        and keeps that line; one byte less starts inside it and drops it."""
        sid, cwd = _cx_id(1), "C:\\W"

        def line_of_1024(i):
            base = len(_cx_line(_cx_agent(f"line-{i}-")))
            return _cx_agent(f"line-{i}-" + "k" * (1024 - base))

        records = [line_of_1024(i) for i in range(6)]
        assert all(len(_cx_line(r)) == 1024 for r in records)
        _cx_write(codex_home, sid, cwd, records)
        monkeypatch.setattr(data_codex, "_TRANSCRIPT_WINDOW", window)
        events = data_codex.get_full_transcript(sid, cwd)
        assert events[0].text.startswith("(Earlier events omitted")
        kept = [e.text.split("-")[1] for e in events[1:]]
        assert kept == [str(i) for i in range(6 - expected, 6)]

    def test_a_line_over_the_cap_is_skipped_without_losing_the_lines_after_it(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [
            _cx_user("before"), _cx_agent("H" * 300_000), _cx_agent("after the giant"),
            _cx_user("U" * 300_000), _cx_agent("last")])
        texts = [e.text for e in data_codex.get_full_transcript(sid, cwd)]
        assert texts == ["before", _CX_NOTICE, "after the giant", _CX_NOTICE, "last"]

    def test_a_line_that_is_not_json_is_skipped_and_the_rest_still_read(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("one")])
        with open(path, "ab") as fh:
            fh.write(b'{"torn line\n' + b"\xff\xfe not utf-8 \n" + b"[" * 50_000 + b"\n")
            fh.write(_cx_line(_cx_agent("two")))
        assert [e.text for e in data_codex.get_full_transcript(sid, cwd)] == ["one", "two"]


class TestCodexLookups:
    def test_find_session_workspace_and_rollout_path(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\Work\\Proj"
        path = _cx_write(codex_home, sid, cwd)
        assert data_codex.find_session_workspace(sid) == cwd
        assert data_codex.find_session_workspace(sid.upper()) == cwd
        assert data_codex.rollout_path(sid) == path
        assert data_codex.find_session_workspace(_cx_id(2)) is None
        assert data_codex.rollout_path(_cx_id(2)) is None

    @pytest.mark.parametrize("bad", ["", "..", "../x", "not-a-uuid", _cx_id(1) + "/..",
                                     _cx_id(1)[:-1], None, 5])
    def test_an_invalid_id_is_refused_before_any_lookup(self, codex_home, monkeypatch, bad):
        monkeypatch.setattr(data_codex, "_store_index",
                            lambda: pytest.fail("the store was consulted for an invalid id"))
        assert data_codex.rollout_path(bad) is None
        assert data_codex.find_session_workspace(bad) is None

    def test_a_kiro_style_id_is_simply_not_found(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W")
        assert data_codex.rollout_path("sess_" + _cx_id(1)) is None

    def test_the_negative_memo_is_bounded_and_a_new_rollout_is_still_found(self, codex_home, monkeypatch):
        monkeypatch.setattr(data_codex, "_STORE_TTL", 5.0)  # the memo is consulted only within a store generation
        for n in range(1000, 1000 + 700):
            assert data_codex.rollout_path(_cx_id(n)) is None
        assert len(data_codex._missing) <= 512
        # The last id looked up is still remembered as missing; a rollout that
        # then appears is found as soon as the store is rebuilt.
        _cx_write(codex_home, _cx_id(1699), "C:\\W")
        assert data_codex.rollout_path(_cx_id(1699)) is None
        data_codex._store_memo.built -= 10.0
        assert data_codex.rollout_path(_cx_id(1699)) is not None

    def test_refresh_stale_entries_for_cwd(self, codex_home):
        cwd = "C:\\W"
        norm = data_mod._normalize_path(cwd)
        keep = _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a")], day="2026/09/01")
        gone = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("b")], day="2026/09/01")
        _sessions, stats = data_codex.load_sessions(cwd)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        assert data_codex.refresh_stale_entries_for_cwd(norm, {}) is False
        # A new rollout in a date folder far from today's.
        _cx_write(codex_home, _cx_id(3), cwd, day="2019/02/03")
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True
        # ... a sub-agent rollout for the same cwd is not a new session ...
        _sessions, stats = data_codex.load_sessions(cwd)
        _cx_write(codex_home, _cx_id(4), cwd, source={"subagent": {"other": "guardian"}})
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        # ... a changed file ...
        with open(keep, "ab") as fh:
            fh.write(_cx_line(_cx_agent("more")))
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True
        _sessions, stats = data_codex.load_sessions(cwd)
        # ... and a deleted one.
        os.remove(gone)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True

    def test_read_meta_and_is_subagent_rollout(self, codex_home):
        path = _cx_write(codex_home, _cx_id(1), "C:\\W", source="vscode")
        meta = data_codex.read_meta(path)
        assert meta["cwd"] == "C:\\W" and meta["source"] == "vscode" and meta["id"] == _cx_id(1)
        assert "base_instructions" not in meta  # 17 KB to 42 KB per file, never needed
        bad = _cx_write(codex_home, _cx_id(2), "C:\\W", first=b"[1]\n")
        assert data_codex.read_meta(bad) is None
        assert data_codex.read_meta(codex_home / "missing.jsonl") is None
        assert data_codex.is_subagent_rollout(codex_home / "missing.jsonl") is True  # unexaminable: final review A8
        legacy = _cx_write(codex_home, _cx_id(3), "C:\\W", first=_cx_line({"id": _cx_id(3)}))
        assert data_codex.is_subagent_rollout(legacy) is False  # legacy is skipped, not a child


class TestCodexTranscript:
    def _transcript(self, codex_home, records):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, records)
        return data_codex.get_full_transcript(sid, cwd)

    def test_message_mapping_ignores_injected_context_and_other_records(self, codex_home):
        events = self._transcript(codex_home, [
            _cx_injected("# AGENTS.md instructions " + "x" * 100),
            _cx_user("question", ts="2026-09-01T10:00:01Z"),
            _cx_rec("response_item", {"type": "message", "role": "assistant",
                                      "content": [{"type": "output_text", "text": "duplicate of the item"}]}),
            _cx_rec("response_item", {"type": "reasoning", "encrypted_content": "zz"}),
            _cx_rec("event_msg", {"type": "token_count", "info": None}),
            _cx_agent("answer", ts="2026-09-01T10:00:02Z")])
        assert [(e.kind, e.text, e.timestamp) for e in events] == [
            ("user", "question", "2026-09-01T10:00:01Z"),
            ("assistant", "answer", "2026-09-01T10:00:02Z")]

    def test_tool_success_comes_from_the_tools_own_exit_code(self, codex_home):
        events = self._transcript(codex_home, [
            _cx_call("shell_command", '{"command":"ls","workdir":"C:\\\\w"}', "c0"),
            _cx_output("c0", "Exit code: 0\nWall time: 0.1 seconds\nOutput:\nfine"),
            _cx_call("shell_command", '{"command":"false"}', "c1"),
            _cx_output("c1", "Exit code: 1\nWall time: 0.1 seconds\nOutput:\n"),
            _cx_call("shell_command", '{"command":"x"}', "c2"),
            _cx_output("c2", "Exit code: 10\nWall time: 0.1 seconds"),
            _cx_call("shell", '{"command":["git","status"]}', "c3"),
            _cx_output("c3", json.dumps({"output": "ok", "metadata": {"exit_code": 0, "duration_seconds": 0.1}})),
            _cx_call("shell", '{"command":["false"]}', "c4"),
            _cx_output("c4", json.dumps({"output": "no", "metadata": {"exit_code": 2, "duration_seconds": 0.1}})),
            _cx_custom_call("apply_patch", "*** Begin Patch\n*** End Patch", "c5"),
            _cx_output("c5", json.dumps({"output": "Done", "metadata": {"exit_code": 0}}), custom=True),
            _cx_custom_call("apply_patch", "*** Begin Patch", "c6"),
            _cx_output("c6", "Exit code: 3\nWall time: 0s", custom=True),
        ])
        calls = {e.tool_call_id: e for e in events if e.kind == "tool_call"}
        results = {e.tool_call_id: e.success for e in events if e.kind == "tool_result"}
        assert results == {"c0": True, "c1": False, "c2": False, "c3": True, "c4": False,
                           "c5": True, "c6": False}
        assert calls["c0"].tool_name == "shell_command"
        assert calls["c0"].tool_args == {"command": "ls", "workdir": "C:\\w"}
        assert calls["c3"].tool_args == {"command": ["git", "status"]}
        assert calls["c5"].tool_args == {"content": "*** Begin Patch\n*** End Patch"}
        assert not any(e.outcome_unknown for e in events)

    def test_exec_with_a_success_marker_is_shown_as_completed_not_started_in_the_translated_frames(
            self, codex_home):
        from power_atlas.transcript_translator import translate_transcript
        sid = _cx_id(1)
        events = self._transcript(codex_home, [
            _cx_custom_call("exec", "text('hi')", "e1"),
            _cx_output("e1", [{"type": "input_text", "text": "Script completed"}], custom=True),
            _cx_call("update_plan", '{"plan":[]}', "u1"),
            _cx_output("u1", "Plan updated")])
        frames = translate_transcript(events, sid)
        assert [(f["type"], f["payload"]["toolCallId"], f["payload"]["status"]) for f in frames] == [
            ("tool_call", "e1", "started"), ("tool_update", "e1", "completed"),
            ("tool_call", "u1", "started"), ("tool_update", "u1", "finished")]

    def test_tool_arguments_are_cut_and_odd_shapes_survive(self, codex_home):
        events = self._transcript(codex_home, [
            _cx_call("shell_command", json.dumps({"command": "x" * 5000, "nested": ["y" * 5000] * 3}), "a1"),
            _cx_custom_call("apply_patch", "p" * 5000, "a2"),
            _cx_call("weird", "not json {", "a3"),
            _cx_call("weird", "[1, 2]", "a4"),
            _cx_call("weird", "r" * 5000, "a5"),
            _cx_call("many", json.dumps({"items": list(range(500))}), "a6"),
            _cx_rec("response_item", {"type": "function_call", "name": "no_id", "arguments": "{}"}),
        ])
        args = {e.tool_call_id: e.tool_args for e in events if e.kind == "tool_call"}
        assert len(args["a1"]["command"]) == 2000 and args["a1"]["command"] == "x" * 2000
        assert [len(s) for s in args["a1"]["nested"]] == [2000] * 3
        assert len(args["a2"]["content"]) == 2000
        assert args["a3"] == {"raw": "not json {"}
        assert args["a4"] == {"raw": "[1, 2]"}
        assert len(args["a5"]["raw"]) == 2000
        assert args["a6"]["items"] == list(range(50)) + ["(+450 more)"]
        assert set(args) == {"a1", "a2", "a3", "a4", "a5", "a6"}  # a call without an id is dropped


class TestCodexFailureIsolation:
    def test_every_public_function_contains_a_raising_helper(self, codex_home, monkeypatch):
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("a")])

        def boom(*args, **kwargs):
            raise RuntimeError("boom")
        monkeypatch.setattr(data_codex, "_store_index", boom)
        monkeypatch.setattr(data_codex, "_walk_rollouts", boom)
        monkeypatch.setattr(data_codex, "_read_meta_payload", boom)
        monkeypatch.setattr(data_codex, "open_shared", boom)
        assert data_codex.is_available() is False
        assert data_codex.discover_workspaces() == []
        sessions, tombstone = data_codex.load_sessions(cwd)
        assert sessions == [] and list(tombstone) == [str(data_codex.CODEX_SESSION_INDEX)]
        st = path.stat()
        assert data_codex.refresh_stale_entries_for_cwd(
            "c:\\w", {str(path): _FileInfo(st.st_mtime, st.st_size)}) is False
        assert data_codex.find_session_workspace(sid) is None
        assert data_codex.rollout_path(sid) is None
        assert data_codex.get_session_tail(sid, cwd) == []
        assert data_codex.get_first_prompt(sid, cwd) == ""
        assert data_codex.get_full_transcript(sid, cwd) == []
        assert data_codex.read_meta(path) is None
        # A verdict that cannot be computed is excluded, never counted (F3, D7).
        assert data_codex.is_subagent_rollout(path) is True

    def test_a_raising_adapter_does_not_stop_warmup_all(self, codex_home, monkeypatch):
        sid = _cx_id(1)
        _cx_write(codex_home, sid, "C:\\W")
        monkeypatch.setattr(data_mod, "PROVIDERS", {"codex": data_codex})

        def boom(*args, **kwargs):
            raise RuntimeError("boom")
        monkeypatch.setattr(data_codex, "_store_index", boom)
        data_mod.warmup_done.clear()
        data_mod.warmup_all([str(codex_home)], [sid])
        assert data_mod.warmup_done.is_set()

    def test_a_file_that_cannot_be_opened_is_skipped_by_discovery_and_listing(self, codex_home, monkeypatch):
        """A rollout another process holds exclusively, or one deleted between the
        walk and the read, must cost one session and nothing else."""
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("fine")])
        locked = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("locked")])
        real = data_codex._read_meta_payload

        def read_meta_payload(path):
            if path == str(locked):
                raise PermissionError("held exclusively")
            return real(path)
        monkeypatch.setattr(data_codex, "_read_meta_payload", read_meta_payload)
        assert [(c, n) for c, n, _ in data_codex.discover_workspaces()] == [(cwd, 1)]
        assert [s.session_id for s in data_codex.load_sessions(cwd)[0]] == [_cx_id(1)]
        assert data_codex.find_session_workspace(_cx_id(2)) is None

    def test_one_file_that_fails_while_listing_does_not_blank_the_workspace(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("fine")])
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("poisoned")])
        real = data_codex._parse_rollout

        def parse(path):
            if path == str(bad):
                raise RuntimeError("boom")
            return real(path)
        monkeypatch.setattr(data_codex, "_parse_rollout", parse)
        assert [s.session_id for s in data_codex.load_sessions(cwd)[0]] == [_cx_id(1)]

    def test_the_failure_log_is_throttled_and_carries_no_content(self, codex_home, monkeypatch, caplog):
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("SECRET-PROMPT-TEXT")])
        monkeypatch.setattr(data_codex, "_read_meta_payload",
                            lambda p: (_ for _ in ()).throw(RuntimeError("SECRET-PROMPT-TEXT")))
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            for _ in range(5):
                data_codex.read_meta(path)
        messages = [r.getMessage() for r in caplog.records]
        assert len(messages) == 1
        assert "SECRET-PROMPT-TEXT" not in messages[0] and "RuntimeError" in messages[0]


# --- Codex adapter: review fixes ------------------------------------------------------
# Review findings of 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1.
# Each class names the finding it pins (F1..F17 of the merged review list).

_CX_NOTICE = "(An oversized record was omitted.)"


class TestCodexExecOutcome:
    """F1: an `exec` output's leading marker is its outcome (plan D13 as amended
    2026-10-01); any other Codex result without an exit code is flagged unknown."""

    def _events(self, codex_home, records):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, records)
        return data_codex.get_full_transcript(sid, cwd)

    def test_an_exec_leading_marker_is_its_outcome_and_only_the_leading_one(self, codex_home):
        events = self._events(codex_home, [
            _cx_custom_call("exec", "a", "e1"),
            _cx_output("e1", [{"type": "input_text",
                               "text": "Script completed\nWall time 0.0 seconds\nOutput:\nScript failed earlier"}],
                       custom=True),
            _cx_custom_call("exec", "b", "e2"),
            _cx_output("e2", "Script failed\nWall time 1.2 seconds\nOutput:\nScript completed later",
                       custom=True),
            _cx_custom_call("exec", "c", "e3"),
            _cx_output("e3", "aborted by user after 3.2s.", custom=True),
            _cx_custom_call("exec", "d", "e4"),
            _cx_output("e4", "Script running with cell ID 7", custom=True),
            _cx_custom_call("exec", "e", "e5"),
            _cx_output("e5", "Output: Script failed (not leading)", custom=True),
        ])
        results = {e.tool_call_id: (e.success, e.outcome_unknown)
                   for e in events if e.kind == "tool_result"}
        assert results == {"e1": (True, False), "e2": (False, False), "e3": (False, False),
                           "e5": (None, True)}
        # e4 is not a final outcome: it has no result event at all, so the call stays "started".
        assert "e4" not in results
        assert [e.tool_call_id for e in events if e.kind == "tool_call"] == ["e1", "e2", "e3", "e4", "e5"]

    def test_any_other_result_without_an_exit_code_is_flagged_unknown(self, codex_home):
        events = self._events(codex_home, [
            _cx_call("wait_agent", "{}", "w1"), _cx_output("w1", "agent finished"),
            _cx_call("spawn_agent", "{}", "w2"), _cx_output("w2", json.dumps({"agent_id": "x"})),
            _cx_call("shell_command", '{"command":"x"}', "w3"),
            _cx_output("w3", "execution error: could not start"),
            _cx_call("update_plan", "{}", "w4"), _cx_output("w4", "Plan updated"),
            _cx_call("view_image", "{}", "w5"), _cx_output("w5", [{"type": "input_image"}]),
            _cx_call("shell_command", '{"command":"ok"}', "k1"), _cx_output("k1", "Exit code: 0\nOutput:"),
        ])
        results = {e.tool_call_id: (e.success, e.outcome_unknown)
                   for e in events if e.kind == "tool_result"}
        for call_id in ("w1", "w2", "w3", "w4", "w5"):
            assert results[call_id] == (None, True), call_id
        assert results["k1"] == (True, False)  # a recorded exit code is never "unknown"

    def test_the_translator_shows_each_outcome_with_its_own_status(self, codex_home):
        from power_atlas.transcript_translator import translate_transcript
        events = self._events(codex_home, [
            _cx_custom_call("exec", "a", "e1"), _cx_output("e1", "Script failed\nWall time", custom=True),
            _cx_custom_call("exec", "b", "e2"), _cx_output("e2", "Script running with cell ID 3", custom=True),
            _cx_call("wait_agent", "{}", "w1"), _cx_output("w1", "done"),
        ])
        frames = translate_transcript(events, _cx_id(1))
        assert [(f["type"], f["payload"]["toolCallId"], f["payload"]["status"]) for f in frames] == [
            ("tool_call", "e1", "started"), ("tool_update", "e1", "failed"),
            ("tool_call", "e2", "started"),
            ("tool_call", "w1", "started"), ("tool_update", "w1", "finished")]


class TestCodexExecToolName:
    """261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, finding 22.

    An exec marker maps an outcome only for a result whose call is the `exec`
    tool (D13): another tool's output that merely starts with the same words, and
    a result whose call the window left out, follow the generic rule.
    """

    _TEXTS = ["Script completed\nWall time 0.0 seconds", "Script failed\nWall time 1.0 seconds",
              "aborted by user after 3.2s.", "Script running with cell ID 3"]

    def _results(self, codex_home, records):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, records)
        return {e.tool_call_id: (e.success, e.outcome_unknown)
                for e in data_codex.get_full_transcript(sid, cwd) if e.kind == "tool_result"}

    def test_the_same_text_maps_for_exec_and_not_for_another_tool(self, codex_home):
        records = []
        for n, text in enumerate(self._TEXTS):
            records += [_cx_custom_call("exec", "x", f"e{n}"), _cx_output(f"e{n}", text, custom=True),
                        _cx_call("wait_agent", "{}", f"w{n}"), _cx_output(f"w{n}", text),
                        _cx_call("shell_command", '{"command":"x"}', f"s{n}"), _cx_output(f"s{n}", text)]
        results = self._results(codex_home, records)
        assert [results[f"e{n}"] for n in range(3)] == [(True, False), (False, False), (False, False)]
        assert "e3" not in results      # exec "running": no result, the call stays started
        for n in range(4):
            # Not exec: no marker mapping, and a "running" text is an ordinary result.
            assert results[f"w{n}"] == (None, True), n
            assert results[f"s{n}"] == (None, True), n

    def test_a_function_call_named_exec_and_a_custom_tool_call_named_exec_are_both_exec(
            self, codex_home):
        results = self._results(codex_home, [
            _cx_call("exec", '{"code":"x"}', "f1"), _cx_output("f1", "Script completed"),
            _cx_custom_call("exec", "x", "c1"), _cx_output("c1", "Script completed", custom=True),
            _cx_call("exec", '{"code":"x"}', "f2"), _cx_output("f2", "Script failed"),
        ])
        assert results == {"f1": (True, False), "c1": (True, False), "f2": (False, False)}

    def test_only_the_exact_tool_name_exec_counts(self, codex_home):
        results = self._results(codex_home, [
            _cx_custom_call("exec_command", "x", "a1"), _cx_output("a1", "Script completed", custom=True),
            _cx_custom_call("Exec", "x", "a2"), _cx_output("a2", "Script completed", custom=True),
            _cx_custom_call("exec ", "x", "a3"), _cx_output("a3", "Script completed", custom=True),
            _cx_call("", "{}", "a4"), _cx_output("a4", "Script completed"),
            _cx_call("exec_command", "{}", "a5"), _cx_output("a5", "Script completed"),
            _cx_call("Exec", "{}", "a6"), _cx_output("a6", "Script completed"),
        ])
        assert results == {k: (None, True) for k in ("a1", "a2", "a3", "a4", "a5", "a6")}

    def test_the_exec_name_belongs_to_its_call_id_not_to_the_neighbouring_call(self, codex_home):
        results = self._results(codex_home, [
            _cx_custom_call("exec", "x", "e1"), _cx_call("wait_agent", "{}", "w1"),
            _cx_output("w1", "Script completed"), _cx_output("e1", "Script completed", custom=True),
            _cx_output("orphan", "Script completed"),
        ])
        assert results["w1"] == (None, True)
        assert results["e1"] == (True, False)
        assert results["orphan"] == (None, True)

    def test_a_result_whose_exec_call_the_window_left_out_is_not_mapped_from_its_text(
            self, codex_home, monkeypatch):
        """The call (and so its tool name) is outside the window: no guess from the text."""
        sid, cwd = _cx_id(1), "C:\\W"
        records = [_cx_custom_call("exec", "x", "e1")] + _cx_filler(40) + [
            _cx_output("e1", "Script completed", custom=True)]
        _cx_write(codex_home, sid, cwd, records)
        monkeypatch.setattr(data_codex, "_TRANSCRIPT_WINDOW", 8 * 1024)
        events = data_codex.get_full_transcript(sid, cwd)
        assert [(e.tool_call_id, e.success, e.outcome_unknown)
                for e in events if e.kind == "tool_result"] == [("e1", None, True)]
        assert not [e for e in events if e.kind == "tool_call"]

    def test_the_translator_shows_a_foreign_marker_text_as_finished_not_completed(self, codex_home):
        from power_atlas.transcript_translator import translate_transcript
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [
            _cx_call("wait_agent", "{}", "w1"), _cx_output("w1", "Script completed"),
            _cx_call("wait_agent", "{}", "w2"), _cx_output("w2", "Script failed"),
            _cx_custom_call("exec", "x", "e1"), _cx_output("e1", "Script completed", custom=True)])
        frames = translate_transcript(data_codex.get_full_transcript(sid, cwd), sid)
        assert [(f["payload"]["toolCallId"], f["payload"]["status"])
                for f in frames if f["type"] == "tool_update"] == [
            ("w1", "finished"), ("w2", "finished"), ("e1", "completed")]


class TestCodexExitCodeRobustness:
    """F2: a hostile `Exit code:` number must not cost the whole transcript."""

    @pytest.mark.parametrize("text, expected", [
        ("Exit code: 0\nOutput:", True),
        ("Exit code: -1\nOutput:", False),
        ("Exit code: 000000001", False),          # nine digits: accepted
        ("Exit code: 000000000", True),
        ("Exit code: 1000000000", None),          # ten digits: not a plausible exit code
        ("Exit code: " + "9" * 5000, None),
        ("Output:\nExit code: 0", None),           # `match`, not `search`: mid-text is not a result
    ])
    def test_exit_code_digit_bounds(self, text, expected):
        assert data_codex.exit_success(text) is expected

    def test_a_bool_exit_code_in_json_is_not_a_number(self):
        assert data_codex.exit_success('{"metadata":{"exit_code":true}}') is None
        assert data_codex.exit_success('{"metadata":{"exit_code":false}}') is None
        assert data_codex.exit_success('{"metadata":{"exit_code":0}}') is True
        assert data_codex.exit_success('{"metadata":{"exit_code":3}}') is False

    def test_a_5000_digit_exit_code_keeps_the_other_events(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [
            _cx_user("question"), _cx_call("shell_command", '{"command":"x"}', "c1"),
            _cx_output("c1", "Exit code: " + "7" * 5000 + "\nOutput:"), _cx_agent("answer")])
        events = data_codex.get_full_transcript(sid, cwd)
        assert [e.kind for e in events] == ["user", "tool_call", "tool_result", "assistant"]
        assert [e.text for e in events if e.kind in ("user", "assistant")] == ["question", "answer"]
        assert events[2].success is None and events[2].outcome_unknown is True

    def test_one_record_that_raises_is_skipped_not_the_whole_transcript(self, codex_home, monkeypatch):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [
            _cx_user("before"), _cx_call("shell_command", "{}", "c1"), _cx_output("c1", "BOOM"),
            _cx_agent("after")])
        real = data_codex.exit_success

        def exit_success(text):
            if text == "BOOM":
                raise RuntimeError("boom")
            return real(text)
        monkeypatch.setattr(data_codex, "exit_success", exit_success)
        assert [e.kind for e in data_codex.get_full_transcript(sid, cwd)] == ["user", "tool_call", "assistant"]

    def test_one_record_that_raises_does_not_lose_the_tail_or_the_first_prompt(self, codex_home, monkeypatch):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [
            _cx_user("BOOM prompt"), _cx_user("real prompt"), _cx_agent("a1"),
            _cx_agent("BOOM reply"), _cx_agent("a3")])
        real = data_codex._message_text

        def message_text(obj, item_type):
            if "BOOM" in json.dumps(obj):
                raise RuntimeError("boom")
            return real(obj, item_type)
        monkeypatch.setattr(data_codex, "_message_text", message_text)
        assert data_codex.get_first_prompt(sid, cwd) == "real prompt"
        assert data_codex.get_session_tail(sid, cwd) == ["a1", "a3"]


class TestCodexVerdictCache:
    """F3 and F7: only a definite verdict is cached, and by (mtime_ns, size)."""

    def test_a_transient_open_failure_is_retried_not_cached(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("fine")])
        real = data_codex._read_meta_payload
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError("held for a moment")
            return real(path)
        monkeypatch.setattr(data_codex, "_read_meta_payload", flaky)
        assert data_codex.load_sessions(cwd)[0] == []            # the glitch hides it once ...
        assert [s.session_id for s in data_codex.load_sessions(cwd)[0]] == [_cx_id(1)]  # ... not forever

    def test_a_verdict_that_could_not_be_computed_counts_as_a_subagent(self, codex_home, monkeypatch):
        """D7 fail-safe: an unknown rollout is excluded from usage rather than counted."""
        path = _cx_write(codex_home, _cx_id(1), "C:\\W")
        real = data_codex._read_meta_payload
        monkeypatch.setattr(data_codex, "_read_meta_payload",
                            lambda p: (_ for _ in ()).throw(PermissionError("glitch")))
        assert data_codex.is_subagent_rollout(path) is True
        monkeypatch.setattr(data_codex, "_read_meta_payload", real)
        assert data_codex.is_subagent_rollout(path) is False      # and the glitch was not remembered

    def test_a_known_non_subagent_skip_is_not_a_subagent(self, codex_home):
        torn = _cx_write(codex_home, _cx_id(1), "C:\\W", first=b'{"ordinal":0,"timest')
        legacy = _cx_write(codex_home, _cx_id(2), "C:\\W", first=_cx_line({"id": _cx_id(2)}))
        assert data_codex.is_subagent_rollout(torn) is False
        assert data_codex.is_subagent_rollout(legacy) is False

    def test_the_verdict_cache_key_includes_the_size(self, codex_home):
        """A torn first line is cached as a skip; the file is then completed with
        the same mtime restored. Only the size tells the two states apart."""
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, first=b'{"ordinal":0,"timest')
        assert data_codex.discover_workspaces() == []
        frozen = os.stat(path).st_mtime_ns
        before = os.stat(path).st_size
        path.write_bytes(_cx_line(_cx_meta(sid, cwd)))
        os.utime(path, ns=(frozen, frozen))
        assert os.stat(path).st_mtime_ns == frozen and os.stat(path).st_size != before
        assert [(c, n) for c, n, _ in data_codex.discover_workspaces()] == [(cwd, 1)]

    def test_an_uppercase_id_in_the_file_still_matches_its_filename(self, codex_home):
        sid, cwd = _cx_id(0xabc), "C:\\W"
        _cx_write(codex_home, sid, cwd, first=_cx_line(_cx_meta(sid.upper(), cwd)))
        assert [s.session_id for s in data_codex.load_sessions(cwd)[0]] == [sid]


class TestCodexUnterminatedLastLine:
    """F4: a complete last record without a trailing newline is still a record."""

    def test_transcript_tail_and_listing_agree(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        last = _cx_line(_cx_agent("final words")).rstrip(b"\n")
        _cx_write(codex_home, sid, cwd, [_cx_user("ask"), _cx_agent("earlier words")], tail=last)
        assert [e.text for e in data_codex.get_full_transcript(sid, cwd)] == [
            "ask", "earlier words", "final words"]
        assert data_codex.get_session_tail(sid, cwd) == ["earlier words", "final words"]
        assert data_codex.load_sessions(cwd)[0][0].last_reply_tail == "final words"

    def test_a_torn_last_line_is_still_dropped(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [_cx_user("ask")],
                  tail=_cx_line(_cx_agent("cut off")).rstrip(b"\n")[:-9])
        assert [e.text for e in data_codex.get_full_transcript(sid, cwd)] == ["ask"]


class TestCodexRefreshAfterFailure:
    """F5 and F16: an empty or failed listing must not stick, and a file that is
    listed but skipped must not make every poll report a change."""

    def test_a_listing_that_failed_once_is_retried_by_the_next_refresh(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a prompt")])
        monkeypatch.setattr(data_mod, "PROVIDERS", {"codex": data_codex})
        data_mod.session_cache.clear()
        real = data_codex._store_index
        state = {"fail": True}

        def store_index():
            if state["fail"]:
                raise RuntimeError("transient")
            return real()
        monkeypatch.setattr(data_codex, "_store_index", store_index)
        try:
            assert data_mod.get_sessions(cwd, "codex") == []
            state["fail"] = False
            data_mod.refresh_stale_entries()
            assert [s.session_id for s in data_mod.get_sessions(cwd, "codex")] == [_cx_id(1)]
            # Once loaded for real, the entry is stable again.
            norm = data_mod._normalize_path(cwd)
            assert data_codex.refresh_stale_entries_for_cwd(
                norm, data_mod.session_cache.get_file_stats(cwd, "codex")) is False
        finally:
            data_mod.session_cache.clear()

    def test_a_listed_file_that_fails_to_parse_does_not_stale_every_poll(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        norm = data_mod._normalize_path(cwd)
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("fine")])
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("poisoned")])
        real = data_codex._parse_rollout

        def parse(path):
            if path == str(bad):
                raise RuntimeError("boom")
            return real(path)
        monkeypatch.setattr(data_codex, "_parse_rollout", parse)
        sessions, stats = data_codex.load_sessions(cwd)
        assert [s.session_id for s in sessions] == [_cx_id(1)]
        for _ in range(3):
            assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        with open(bad, "ab") as fh:           # a change to the failing file is still noticed
            fh.write(_cx_line(_cx_agent("more")))
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True

    def test_a_file_that_turned_non_top_level_since_the_store_was_built(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        norm = data_mod._normalize_path(cwd)
        sid = _cx_id(1)
        path = _cx_write(codex_home, sid, cwd, [_cx_user("a")])
        monkeypatch.setattr(data_codex, "_STORE_TTL", 60.0)
        assert [n for _, n, _ in data_codex.discover_workspaces()] == [1]   # the store lists it
        path.write_bytes(b'{"torn first line, rewritten shorter"')           # now torn
        sessions, stats = data_codex.load_sessions(cwd)
        assert sessions == []
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False


class TestCodexParseRetry:
    """261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, finding 21.

    A rollout whose parse fails with an OSError (a sharing violation on an idle
    file) is retried by later refresh polls without any change to the file: at
    most 3 retries, at least _RETRY_SPACING apart. Any other failure is
    deterministic and is not retried (finding 16). The recorded `file_stats` stay
    the file's real stat in every case.
    """

    @pytest.fixture(autouse=True)
    def _fast_retry(self, monkeypatch):
        self.default_spacing = data_codex._RETRY_SPACING
        monkeypatch.setattr(data_codex, "_RETRY_SPACING", 0.0)

    @staticmethod
    def _flaky(monkeypatch, bad, failures, exc=PermissionError):
        """`_parse_rollout` raises `exc` for `bad` on its first `failures` calls
        (None: on every call). Returns the call counter."""
        real = data_codex._parse_rollout
        calls = {"n": 0}

        def parse(path):
            if path == str(bad):
                calls["n"] += 1
                if failures is None or calls["n"] <= failures:
                    raise exc("sharing violation")
            return real(path)
        monkeypatch.setattr(data_codex, "_parse_rollout", parse)
        return calls

    @staticmethod
    def _poll(cwd, stats):
        """One refresh poll as `data.refresh_stale_entries` runs it: reload when stale.
        Returns (reloaded, sessions, stats)."""
        norm = data_mod._normalize_path(cwd)
        if not data_codex.refresh_stale_entries_for_cwd(norm, stats):
            return False, None, stats
        sessions, stats = data_codex.load_sessions(cwd)
        return True, sessions, stats

    def test_the_default_spacing_is_at_least_five_seconds(self):
        assert self.default_spacing >= 5.0

    def test_a_transient_open_failure_recovers_without_any_change_to_the_file(
            self, codex_home, monkeypatch):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("fine")])
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("flaky")])
        calls = self._flaky(monkeypatch, bad, failures=1)
        monkeypatch.setattr(data_mod, "PROVIDERS", {"codex": data_codex})
        data_mod.session_cache.clear()
        try:
            assert [s.session_id for s in data_mod.get_sessions(cwd, "codex")] == [_cx_id(1)]
            before = os.stat(bad)
            data_mod.refresh_stale_entries()          # the next poll, the file untouched
            assert sorted(s.session_id for s in data_mod.get_sessions(cwd, "codex")) == [
                _cx_id(1), _cx_id(2)]
            after = os.stat(bad)
            assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size)
            assert calls["n"] == 2
            norm = data_mod._normalize_path(cwd)
            stats = data_mod.session_cache.get_file_stats(cwd, "codex")
            assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False   # stable again
        finally:
            data_mod.session_cache.clear()

    def test_the_recorded_stat_is_the_files_real_stat_while_a_retry_is_pending(
            self, codex_home, monkeypatch):
        cwd = "C:\\W"
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("flaky")])
        self._flaky(monkeypatch, bad, failures=None)
        sessions, stats = data_codex.load_sessions(cwd)
        st = os.stat(bad)
        assert sessions == []
        assert stats[str(bad)] == data_mod._FileInfo(mtime=st.st_mtime, size=st.st_size)

    def test_a_permanent_oserror_stops_after_exactly_three_retries(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("fine")])
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("broken")])
        calls = self._flaky(monkeypatch, bad, failures=None)
        sessions, stats = data_codex.load_sessions(cwd)
        assert calls["n"] == 1 and [s.session_id for s in sessions] == [_cx_id(1)]
        outcomes = []
        for _ in range(8):
            reloaded, _s, stats = self._poll(cwd, stats)
            outcomes.append(reloaded)
        assert outcomes == [True, True, True] + [False] * 5
        assert calls["n"] == 4            # the first load plus exactly 3 retries, never a 5th

    def test_a_success_on_the_second_attempt_is_final_and_resets_the_budget(
            self, codex_home, monkeypatch):
        cwd = "C:\\W"
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("slow to open")])
        self._flaky(monkeypatch, bad, failures=2)
        sessions, stats = data_codex.load_sessions(cwd)                # attempt 1: fails
        assert sessions == []
        reloaded, sessions, stats = self._poll(cwd, stats)             # attempt 2: fails again
        assert reloaded is True and sessions == []
        reloaded, sessions, stats = self._poll(cwd, stats)             # attempt 3: succeeds
        assert reloaded is True and [s.session_id for s in sessions] == [_cx_id(2)]
        assert [self._poll(cwd, stats)[0] for _ in range(4)] == [False] * 4
        # The budget starts over: a later failure of the same, unchanged file gets 3 retries again.
        data_codex._parse_cache.clear()
        calls = self._flaky(monkeypatch, bad, failures=None)
        sessions, stats = data_codex.load_sessions(cwd)
        assert sessions == [] and calls["n"] == 1
        assert [self._poll(cwd, stats)[0] for _ in range(5)] == [True, True, True, False, False]

    def test_a_non_oserror_is_never_retried(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("poisoned")])
        calls = self._flaky(monkeypatch, bad, failures=None, exc=ValueError)
        _sessions, stats = data_codex.load_sessions(cwd)
        assert [self._poll(cwd, stats)[0] for _ in range(4)] == [False] * 4
        assert calls["n"] == 1

    def test_retries_are_spaced(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("broken")])
        self._flaky(monkeypatch, bad, failures=None)
        _sessions, stats = data_codex.load_sessions(cwd)
        norm = data_mod._normalize_path(cwd)
        monkeypatch.setattr(data_codex, "_RETRY_SPACING", 3600.0)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        monkeypatch.setattr(data_codex, "_RETRY_SPACING", 0.0)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True

    def test_a_changed_file_is_noticed_and_gets_a_fresh_budget(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        norm = data_mod._normalize_path(cwd)
        bad = _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("broken")])
        calls = self._flaky(monkeypatch, bad, failures=None)
        _sessions, stats = data_codex.load_sessions(cwd)
        for _ in range(3):                                             # drain the budget
            _reloaded, _s, stats = self._poll(cwd, stats)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is False
        # The file changes, and not by growing: shorter, with an older mtime.
        _cx_write(codex_home, _cx_id(2), cwd, [], mtime=_CX_OLD)
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True
        sessions, stats = data_codex.load_sessions(cwd)       # fails again: a new stat, a new budget
        assert sessions == []
        calls["n"] = 0
        assert [self._poll(cwd, stats)[0] for _ in range(5)] == [True, True, True, False, False]
        assert calls["n"] == 3

    def test_the_failure_memo_is_bounded_and_dropped_with_the_caches(self, codex_home, monkeypatch):
        cwd = "C:\\W"
        count = 520                    # more rollouts than the memo can hold
        bad = set()
        for n in range(1, count + 1):
            sid = _cx_id(n)
            bad.add(str(_cx_write(codex_home, sid, cwd, [], first=_cx_line(_cx_meta(sid, cwd, base_chars=10)))))
        real = data_codex._parse_rollout

        def parse(path):
            if path in bad:
                raise PermissionError("sharing violation")
            return real(path)
        monkeypatch.setattr(data_codex, "_parse_rollout", parse)
        sessions, _stats = data_codex.load_sessions(cwd)
        assert sessions == []
        assert 0 < len(data_codex._parse_failed) < count
        data_codex._clear_caches()
        assert len(data_codex._parse_failed) == 0


class TestCodexWorkspaceRecency:
    """F6: a workspace's updated_at is the same later-of-mtime-and-last-record a
    session's is; Windows freezes the mtime of a rollout Codex holds open."""

    def test_a_frozen_mtime_does_not_age_the_workspace(self, codex_home):
        path = _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a", ts="2026-09-30T08:00:00Z")],
                         ts="2026-09-30T07:00:00Z", mtime=_CX_OLD)
        assert os.stat(path).st_mtime < 1_600_000_000
        ((cwd, count, updated),) = data_codex.discover_workspaces()
        assert (cwd, count, updated) == ("C:\\W", 1, "2026-09-30T08:00:00.000000+00:00")
        assert updated == data_codex.load_sessions("C:\\W")[0][0].updated_at

    def test_workspaces_are_ordered_by_that_recency(self, codex_home):
        # `active` has the OLDER mtime but the newer last record; `idle` is the reverse.
        _cx_write(codex_home, _cx_id(1), "C:\\Idle", [_cx_user("a", ts="2026-09-01T08:00:00Z")],
                  ts="2026-09-01T07:00:00Z", mtime=_CX_OLD + 1000)
        _cx_write(codex_home, _cx_id(2), "C:\\Active", [_cx_user("a", ts="2026-09-30T08:00:00Z")],
                  ts="2026-09-30T07:00:00Z", mtime=_CX_OLD)
        assert [c for c, _, _ in data_codex.discover_workspaces()] == ["C:\\Active", "C:\\Idle"]

    def test_a_future_record_stamp_cannot_pin_a_workspace(self, codex_home):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a", ts="2999-01-01T00:00:00Z")], mtime=_CX_OLD)
        ((_, _, updated),) = data_codex.discover_workspaces()
        assert updated < "2100"


class TestCodexCacheSizing:
    """F9 and F10."""

    def test_the_caches_grow_with_the_store_so_a_second_pass_does_not_thrash(self, codex_home, monkeypatch):
        # The floor is patched down to 64 (the real one is 4096): 70 files cross it as 4,100 did, in
        # a fraction of the time. The property is "above the floor", not the floor's value.
        # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, final review B9
        monkeypatch.setattr(data_codex, "_CACHE_MIN", 64)
        data_codex._clear_caches()
        cwd = "C:\\Big"
        count = 70  # above the 64 floor
        for n in range(count):
            _cx_write(codex_home, _cx_id(n + 1), cwd,
                      first=_cx_line(_cx_meta(_cx_id(n + 1), cwd, base_chars=0)))
        verdicts, parses = [], []
        real_verdict, real_parse = data_codex._compute_verdict, data_codex._parse_rollout
        monkeypatch.setattr(data_codex, "_compute_verdict",
                            lambda p, m, u: verdicts.append(p) or real_verdict(p, m, u))
        monkeypatch.setattr(data_codex, "_parse_rollout", lambda p: parses.append(p) or real_parse(p))
        sessions, _ = data_codex.load_sessions(cwd)
        assert len(sessions) == count and len(verdicts) == count and len(parses) == count
        assert data_codex._verdict_cache._maxsize >= 2 * count
        assert data_codex._parse_cache._maxsize >= 2 * count
        data_codex.load_sessions(cwd)
        assert len(verdicts) == count and len(parses) == count, "a second pass re-read files (thrash)"

    def test_the_negative_memo_is_valid_only_for_one_store_generation(self, codex_home, monkeypatch):
        """With a store TTL above zero the memo is really consulted; a rollout that
        appears is found as soon as the store is rebuilt, never 60 s later."""
        monkeypatch.setattr(data_codex, "_STORE_TTL", 5.0)
        missing = _cx_id(7)
        assert data_codex.rollout_path(missing) is None
        store = data_codex._store_index()
        hit = data_codex._missing.get(missing)
        assert hit is not None and hit[0] == store.built          # remembered for this generation
        _cx_write(codex_home, missing, "C:\\W")
        assert data_codex.rollout_path(missing) is None          # same generation: still memoised
        data_codex._store_memo.built -= 10.0                      # the store expires ...
        assert data_codex.rollout_path(missing) is not None      # ... and the memo with it
        assert len(data_codex._missing) <= 512


class TestCodexPublicNames:
    """The record readers overview.py uses are the adapter's own public functions, not
    wrappers around private ones (final review finding A10)."""

    def test_the_record_readers_answer_as_documented(self):
        item = {"type": "event_msg", "payload": {"type": "item_completed",
                                                 "item": {"type": "UserMessage", "content": [{"type": "text", "text": "hi"}]}}}
        found = data_codex.item_of(item)
        assert found is not None and found[0] == "UserMessage" and data_codex.item_text(found[1]) == "hi"
        assert data_codex.output_text([{"text": "a"}, {"text": "b"}]) == "a\nb"
        assert data_codex.exit_success("Exit code: 0\n") is True and data_codex.exit_success("Exit code: 2") is False
        assert data_codex.exec_outcome("Script failed") is False and data_codex.exec_outcome("Script completed") is True
        assert data_codex.exec_outcome("something else") is None
        assert data_codex.loads(b'{"a": 1}') == {"a": 1} and data_codex.loads(b"{") is None
        assert data_codex.read_first_line(io.BytesIO(b"first\nsecond\n")) == b"first"
        assert data_codex.EXEC_RUNNING == "Script running with cell" and data_codex.FUTURE_SKEW == 5.0


class TestCodexSessionIndexReads:
    """F13: one stat of the index per listing; a capped index says so."""

    def test_load_sessions_stats_the_index_once(self, codex_home, monkeypatch):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("a")])
        _cx_index(codex_home, [(_cx_id(1), "Named")])
        index = os.fspath(data_codex.CODEX_SESSION_INDEX)
        seen = []
        real_stat = os.stat

        def counting(path, *args, **kwargs):
            if os.fspath(path) == index:
                seen.append(path)
            return real_stat(path, *args, **kwargs)
        monkeypatch.setattr(os, "stat", counting)
        data_codex.load_sessions("C:\\W")
        assert len(seen) == 1

    def test_an_index_over_its_cap_is_logged_and_later_entries_are_ignored(
            self, codex_home, monkeypatch, caplog):
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("prompt one")])
        _cx_write(codex_home, _cx_id(2), "C:\\W", [_cx_user("prompt two")])
        _cx_index(codex_home, [(_cx_id(1), "Early name"), (_cx_id(2), "Late name")])
        one_entry = len(_cx_line({"id": _cx_id(1), "thread_name": "Early name", "updated_at": _CX_TS}))
        monkeypatch.setattr(data_codex, "_INDEX_CAP", one_entry + 5)
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            titles = {s.session_id: s.title for s in data_codex.load_sessions("C:\\W")[0]}
            data_codex.load_sessions("C:\\W")
        assert titles == {_cx_id(1): "Early name", _cx_id(2): "prompt two"}
        assert len([r for r in caplog.records if "session index" in r.getMessage()]) == 1

    def test_the_index_signature_includes_the_size(self, codex_home):
        cwd = "C:\\W"
        norm = data_mod._normalize_path(cwd)
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a")])
        index = _cx_index(codex_home, [(_cx_id(1), "AAAA")])
        _sessions, stats = data_codex.load_sessions(cwd)
        frozen = os.stat(index).st_mtime_ns
        _cx_index(codex_home, [(_cx_id(1), "AAAA-and-a-longer-name")])
        os.utime(index, ns=(frozen, frozen))
        assert data_codex.refresh_stale_entries_for_cwd(norm, stats) is True

    def test_the_names_memo_key_includes_the_size(self, codex_home):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("a")])
        index = _cx_index(codex_home, [(_cx_id(1), "AAAA")])
        assert data_codex.load_sessions(cwd)[0][0].title == "AAAA"
        frozen = os.stat(index).st_mtime_ns
        _cx_index(codex_home, [(_cx_id(1), "BBBB-longer")])
        os.utime(index, ns=(frozen, frozen))
        assert data_codex.load_sessions(cwd)[0][0].title == "BBBB-longer"


class TestCodexSharedThreadId:
    """F14: of several rollouts for one thread id, the newest VALID one is used."""

    def test_a_newer_torn_rollout_does_not_hide_the_valid_older_one(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        older = _cx_write(codex_home, sid, cwd, [_cx_user("the valid one")], day="2026/09/01")
        _cx_write(codex_home, sid, cwd, day="2026/09/02", first=b'{"torn',
                  name=f"rollout-2026-09-02T10-00-00-{sid}.jsonl")
        assert [s.first_prompt for s in data_codex.load_sessions(cwd)[0]] == ["the valid one"]
        assert [(c, n) for c, n, _ in data_codex.discover_workspaces()] == [(cwd, 1)]
        assert data_codex.rollout_path(sid) == older
        assert data_codex.get_first_prompt(sid, cwd) == "the valid one"

    def test_with_no_valid_rollout_the_newest_is_the_lookup_target(self, codex_home):
        sid = _cx_id(1)
        spawn = {"subagent": {"other": "guardian"}}
        _cx_write(codex_home, sid, "C:\\W", source=spawn, day="2026/09/01")
        newer = _cx_write(codex_home, sid, "C:\\W", source=spawn, day="2026/09/02",
                          name=f"rollout-2026-09-02T10-00-00-{sid}.jsonl")
        assert data_codex.rollout_path(sid) == newer
        assert data_codex.is_subagent_rollout(newer) is True


class TestCodexOversizeRecords:
    """F17: a record over 256 KiB is skipped, but the transcript says so."""

    def test_each_skipped_record_leaves_a_notice_in_place(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [
            _cx_user("before"), _cx_agent("H" * 300_000), _cx_agent("middle"),
            _cx_user("U" * 300_000), _cx_agent("last")])
        events = data_codex.get_full_transcript(sid, cwd)
        assert [(e.kind, e.text) for e in events] == [
            ("user", "before"), ("assistant", _CX_NOTICE), ("assistant", "middle"),
            ("assistant", _CX_NOTICE), ("assistant", "last")]

    def test_at_most_five_notices_per_transcript(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [_cx_agent("H" * 300_000) for _ in range(8)] + [_cx_agent("end")])
        texts = [e.text for e in data_codex.get_full_transcript(sid, cwd)]
        assert texts == [_CX_NOTICE] * 5 + ["end"]

    def test_an_oversized_record_of_no_interest_leaves_no_notice(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        _cx_write(codex_home, sid, cwd, [_cx_rec("turn_context", {"model": "m", "pad": "p" * 300_000}),
                                         _cx_agent("kept")])
        assert [e.text for e in data_codex.get_full_transcript(sid, cwd)] == ["kept"]


class TestCodexToolArgumentCaps:
    """F12."""

    def test_scalars_at_the_depth_limit_keep_their_type(self):
        args = data_codex._call_args(json.dumps({"a": {"b": {"c": [True, 1, None, 2.5, "s"]}}}))
        assert args == {"a": {"b": {"c": [True, 1, None, 2.5, "s"]}}}

    def test_a_container_at_the_depth_limit_is_flattened_to_text(self):
        args = data_codex._call_args(json.dumps({"a": {"b": {"c": {"d": {"e": 1}}}}}))
        assert args["a"]["b"]["c"]["d"] == "{'e': 1}"

    @pytest.mark.parametrize("n, extra", [(49, None), (50, None), (51, "(+1 more)"), (60, "(+10 more)")])
    def test_dropped_items_are_counted(self, n, extra):
        as_list = data_codex._call_args(json.dumps({"items": list(range(n))}))["items"]
        as_dict = data_codex._call_args(json.dumps({"obj": {f"k{i}": i for i in range(n)}}))["obj"]
        if extra is None:
            assert as_list == list(range(n)) and len(as_dict) == n
        else:
            assert as_list == list(range(50)) + [extra]
            assert len(as_dict) == 51 and as_dict[extra] == "" and as_dict["k49"] == 49 and "k50" not in as_dict


class TestCodexFailureLogPaths:
    """F11: a failure names the rollout path, never content."""

    def test_redact_path_hides_only_the_thread_id_of_the_file_name(self):
        sid = _cx_id(1)
        rollout = f"C:\\codex\\sessions\\2026\\09\\01\\rollout-2026-09-01T10-00-00-{sid}.jsonl"
        assert data_codex.redact_path(rollout) == (
            "C:\\codex\\sessions\\2026\\09\\01\\rollout-2026-09-01T10-00-00-<id>.jsonl")
        assert data_codex.redact_path(f"/locks/{sid.upper()}.lock") == "/locks/<id>.lock"
        assert data_codex.redact_path("C:\\codex\\sessions") == "C:\\codex\\sessions"
        assert data_codex.redact_path("") == ""

    def test_log_path_redacts_rollouts_only(self):
        sid = _cx_id(1)
        claude = f"C:\\p\\{sid}.jsonl"
        assert data_codex.log_path(claude) == claude, "another provider's file name is left alone"
        assert "<id>" in data_codex.log_path(f"C:\\p\\rollout-2026-09-01T10-00-00-{sid}.jsonl")

    def test_the_warning_names_the_path_given_to_a_path_function(self, codex_home, monkeypatch, caplog):
        sid_text = _cx_id(1)
        path = _cx_write(codex_home, sid_text, "C:\\W", [_cx_user("SECRET-PROMPT-TEXT")])
        monkeypatch.setattr(data_codex, "_read_meta_payload",
                            lambda p: (_ for _ in ()).throw(RuntimeError("SECRET-PROMPT-TEXT")))
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            data_codex.read_meta(path)
        (message,) = [r.getMessage() for r in caplog.records]
        assert f"path={data_codex.redact_path(path)}" in message and "SECRET-PROMPT-TEXT" not in message
        assert sid_text not in message, "the thread id is hidden in the log line"

    def test_the_warning_names_the_path_an_os_error_carries(self, codex_home, monkeypatch, caplog):
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("a")])
        assert data_codex.rollout_path(sid) == path  # verdict cached: only the transcript read fails below

        def broken(p, mode="rb"):
            raise PermissionError(13, "denied", os.fspath(p))
        monkeypatch.setattr(data_codex, "open_shared", broken)
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.get_full_transcript(sid, cwd) == []
        assert any(f"path={data_codex.redact_path(path)}" in r.getMessage() and sid not in r.getMessage()
                   for r in caplog.records)


class TestCodexFinalReviewAdapter:
    """Final review, adapter side (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW).
    Findings A1 to A8, A11, A12 and A14 of the final review; the plan's Review Log names them."""

    @staticmethod
    def _link_or_skip(link, target):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"cannot create a symlink here: {exc}")

    @staticmethod
    def _junction_or_skip(link, target):
        if sys.platform != "win32":
            pytest.skip("NTFS junctions are Windows-only")
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            pytest.skip(f"cannot create a junction here: {done.stdout} {done.stderr}")

    # --- A1: one definition of "a Codex session file" -------------------------------

    def test_canonical_rollouts_is_exactly_the_set_of_files_discovery_lists(self, codex_home, monkeypatch):
        cwd = "C:\\Work\\Proj"
        # Thread 1: a resumed copy in an older date folder next to the newer one.
        older = _cx_write(codex_home, _cx_id(1), cwd, day="2026/08/30", mtime=_CX_OLD)
        newer = _cx_write(codex_home, _cx_id(1), cwd, day="2026/09/01", mtime=_CX_OLD + 1000)
        plain = _cx_write(codex_home, _cx_id(3), cwd)
        child = _cx_write(codex_home, _cx_id(2), cwd, source={"subagent": {"other": "guardian"}})
        # A well-formed file whose NAME does not carry the thread id: not a rollout.
        stray = _cx_write(codex_home, _cx_id(4), cwd, name="notes-2026-09-01.jsonl")
        lookalike = _cx_write(codex_home, _cx_id(5), cwd, name=f"copy-of-rollout-x-{_cx_id(5)}.jsonl")
        # A file that cannot be opened is unexaminable.
        broken = _cx_write(codex_home, _cx_id(6), cwd)
        real_open = data_codex.open_shared

        def deny_broken(path, mode="rb"):
            if os.fspath(path) == str(broken):
                raise PermissionError("denied")
            return real_open(path, mode)
        monkeypatch.setattr(data_codex, "open_shared", deny_broken)

        found = data_codex.canonical_rollouts()
        assert [c.path for c in found] == sorted([str(newer), str(plain)])
        for c in found:
            st = os.stat(c.path)
            assert (c.mtime_ns, c.size) == (st.st_mtime_ns, st.st_size)
        # The property the finding is about: what discovery lists is what usage may count.
        sessions, _ = data_codex.load_sessions(cwd)
        assert {str(data_codex.rollout_path(s.session_id)) for s in sessions} == {c.path for c in found}
        assert [n for _c, n, _u in data_codex.discover_workspaces()] == [len(found)]
        for excluded in (older, child, stray, lookalike, broken):
            assert str(excluded) not in {c.path for c in found}

    def test_canonical_rollouts_is_empty_without_a_store_and_never_raises(self, codex_home, monkeypatch):
        assert data_codex.canonical_rollouts() == []
        monkeypatch.setattr(data_codex, "_store_index", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert data_codex.canonical_rollouts() == []

    def test_a_symlinked_rollout_is_neither_discovered_nor_canonical(self, codex_home, tmp_path):
        cwd = "C:\\W"
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / f"rollout-2026-09-01T10-00-00-{_cx_id(7)}.jsonl"
        target.write_bytes(_cx_line(_cx_meta(_cx_id(7), cwd)))
        folder = codex_home / "sessions" / "2026" / "09" / "01"
        folder.mkdir(parents=True)
        self._link_or_skip(folder / target.name, target)
        _cx_write(codex_home, _cx_id(8), cwd)           # a real one, so the walk is not vacuous
        assert data_codex.is_available() is True
        assert [n for _c, n, _u in data_codex.discover_workspaces()] == [1]
        assert [os.path.basename(c.path) for c in data_codex.canonical_rollouts()] == [
            f"rollout-2026-09-01T10-00-00-{_cx_id(8)}.jsonl"]
        assert data_codex.rollout_path(_cx_id(7)) is None

    # --- A2: junctions are never followed ---------------------------------------------

    def test_a_directory_junction_is_not_followed(self, codex_home, tmp_path):
        cwd = "C:\\W"
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / f"rollout-2026-09-01T10-00-00-{_cx_id(9)}.jsonl").write_bytes(
            _cx_line(_cx_meta(_cx_id(9), cwd)))
        month = codex_home / "sessions" / "2026" / "09"
        month.mkdir(parents=True)
        link = month / "02"
        self._junction_or_skip(link, elsewhere)
        try:
            assert (link / f"rollout-2026-09-01T10-00-00-{_cx_id(9)}.jsonl").exists()   # reachable through it
            assert data_codex.is_available() is False
            assert data_codex.discover_workspaces() == []
            assert data_codex.canonical_rollouts() == []
            assert data_codex.rollout_path(_cx_id(9)) is None
            _cx_write(codex_home, _cx_id(10), cwd, day="2026/09/03")   # a real folder beside it
            data_codex._clear_caches()
            assert [n for _c, n, _u in data_codex.discover_workspaces()] == [1]
        finally:
            os.rmdir(link)       # a junction is removed with rmdir, never by deleting its target's files

    # --- A3: a UNC cwd never reaches GetLongPathNameW ----------------------------------

    @pytest.mark.skipif(sys.platform != "win32", reason="8.3 short names are Windows-only")
    def test_a_network_or_device_cwd_never_expands_short_names_but_a_local_one_does(self, codex_home, monkeypatch):
        import ctypes
        calls = []

        def get_long_path(path, buf, size):
            calls.append(path)
            return 0                       # "no such path": the expansion falls back to the text
        monkeypatch.setattr(ctypes.windll.kernel32, "GetLongPathNameW", get_long_path)
        data_mod._expand_short_path.cache_clear()
        unc = ["\\\\host\\share\\proj~1", "//host/share/proj~2", "\\\\?\\UNC\\host\\share\\p~3",
               "\\\\?\\C:\\Users\\X~1\\p4", "\\\\.\\pipe\\p~5"]
        for n, cwd in enumerate(unc, start=1):
            _cx_write(codex_home, _cx_id(n), cwd)
        sessions = {cwd: data_codex.load_sessions(cwd)[0] for cwd in unc}
        assert all(len(found) == 1 for found in sessions.values())
        assert len(data_codex.discover_workspaces()) == len(unc)
        assert len(data_codex.canonical_rollouts()) == len(unc)
        assert calls == [], calls
        # The same folder in another case, and with a trailing separator, is one workspace.
        assert len(data_codex.load_sessions("\\\\HOST\\Share\\Proj~1\\")[0]) == 1
        assert data_codex.load_sessions("//host/share/proj~2/")[0][0].session_id == _cx_id(2)
        assert calls == []
        # Control: a local path with a "~" still expands, so the guard is not a blanket skip.
        local = "C:\\Users\\X~1\\proj"
        _cx_write(codex_home, _cx_id(20), local)
        data_codex._clear_caches()
        assert len(data_codex.load_sessions(local)[0]) == 1
        assert calls, "a local cwd with a ~ is expanded as before"

    def test_a_posix_style_cwd_keeps_its_own_normalisation(self, codex_home):
        cwd = "/home/dev/proj/"
        _cx_write(codex_home, _cx_id(1), cwd)
        assert data_codex.load_sessions("/home/dev/proj")[0][0].session_id == _cx_id(1)

    # --- A4: a byte-order mark ---------------------------------------------------------

    def test_a_bom_on_the_first_line_does_not_hide_the_session(self, codex_home):
        cwd = "C:\\W"
        sid = _cx_id(1)
        _cx_write(codex_home, sid, cwd, [_cx_user("hello")], first=b"\xef\xbb\xbf" + _cx_line(_cx_meta(sid, cwd)))
        (session,), _ = data_codex.load_sessions(cwd)
        assert session.session_id == sid and session.first_prompt == "hello"
        assert data_codex.read_meta(data_codex.rollout_path(sid))["id"] == sid
        assert data_codex.loads(b"\xef\xbb\xbf{\"a\": 1}") == {"a": 1}
        assert data_codex.loads(b"\xef\xbb\xbf{") is None

    def test_a_bom_on_the_first_line_of_the_session_index_keeps_the_first_name(self, codex_home):
        cwd = "C:\\W"
        _cx_write(codex_home, _cx_id(1), cwd, [_cx_user("prompt one")])
        _cx_write(codex_home, _cx_id(2), cwd, [_cx_user("prompt two")])
        path = codex_home / "session_index.jsonl"
        path.write_bytes(b"\xef\xbb\xbf" + _cx_line({"id": _cx_id(1), "thread_name": "First name"})
                         + _cx_line({"id": _cx_id(2), "thread_name": "Second name"}))
        _bump_mtime(path)
        assert {s.session_id: s.title for s in data_codex.load_sessions(cwd)[0]} == {
            _cx_id(1): "First name", _cx_id(2): "Second name"}

    # --- A5: an unreadable sessions folder is logged ------------------------------------

    def test_an_unreadable_sessions_root_is_logged_once_with_its_path_only(self, codex_home, monkeypatch, caplog):
        _cx_write(codex_home, _cx_id(1), "C:\\W")
        root = str(data_codex.CODEX_SESSIONS_DIR)
        real_scandir = os.scandir

        def scandir(path):
            if os.fspath(path) == root:
                raise PermissionError(errno.EACCES, "denied", root)
            return real_scandir(path)
        monkeypatch.setattr(os, "scandir", scandir)
        data_codex._clear_caches()
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.is_available() is False
            assert data_codex.discover_workspaces() == []
            assert data_codex.canonical_rollouts() == []
        warnings = [r.getMessage() for r in caplog.records if "sessions_root" in r.getMessage()]
        assert len(warnings) == 1, warnings            # throttled: one per minute, not per call
        assert f"path={root}" in warnings[0] and "PermissionError" in warnings[0]

    def test_a_missing_sessions_root_is_silent_and_a_subfolder_failure_is_not_a_root_warning(
            self, codex_home, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.is_available() is False      # no ~/.codex/sessions at all: not an error
        assert caplog.records == []
        _cx_write(codex_home, _cx_id(1), "C:\\W")
        deep = str(codex_home / "sessions" / "2026" / "09" / "01")
        real_scandir = os.scandir

        def scandir(path):
            if os.fspath(path) == deep:
                raise PermissionError(errno.EACCES, "denied", deep)
            return real_scandir(path)
        monkeypatch.setattr(os, "scandir", scandir)
        data_codex._clear_caches()
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.discover_workspaces() == []
        assert not [r for r in caplog.records if "sessions_root" in r.getMessage()]

    # --- A6: one parse per change, whoever asks first ----------------------------------

    def test_a_growing_file_is_parsed_once_per_change_across_alternating_calls(self, codex_home, monkeypatch):
        cwd, sid = "C:\\W", _cx_id(1)
        path = _cx_write(codex_home, sid, cwd, [_cx_user("first")])
        monkeypatch.setattr(data_codex, "_STORE_TTL", 5.0)        # the store index goes stale
        parses = []
        real = data_codex._parse_rollout
        monkeypatch.setattr(data_codex, "_parse_rollout", lambda p: parses.append(p) or real(p))
        data_codex.load_sessions(cwd)
        assert len(parses) == 1
        with open(path, "ab") as fh:                              # the file grows; the index does not see it
            fh.write(_cx_line(_cx_user("second")))
        _bump_mtime(path)
        for _ in range(3):
            assert data_codex.load_sessions(cwd)[0][0].last_prompt == "second"
            data_codex.discover_workspaces()
        assert len(parses) == 2, "one parse for the initial state and one for the change"
        ((_, _, updated),) = data_codex.discover_workspaces()
        assert updated >= data_codex._iso(data_codex._ns_dt(os.stat(path).st_mtime_ns))

    # --- A7: growing the caches is atomic ----------------------------------------------

    def test_concurrent_cache_growth_publishes_one_set_of_caches(self, monkeypatch):
        built = []
        real_cache = data_codex.BoundedCache

        class SlowCache(real_cache):
            def __init__(self, maxsize):
                time.sleep(0.02)                    # a window wide enough for every thread to enter
                built.append(maxsize)
                super().__init__(maxsize)
        monkeypatch.setattr(data_codex, "BoundedCache", SlowCache)

        def slow_max(*args):
            time.sleep(0.02)                        # between the size check and the cap assignment
            return max(*args)
        monkeypatch.setattr(data_codex, "max", slow_max, raising=False)
        barrier = threading.Barrier(8)

        def grow():
            barrier.wait()
            data_codex.fit_caches(5000)
        threads = [threading.Thread(target=grow) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert len(built) == 3, built                # verdict, parse and last-record caches, once
        assert data_codex._parse_cache._maxsize == data_codex._cache_cap >= 2 * 5000
        data_codex.fit_caches(5000)
        assert len(built) == 3                       # already large enough: no rebuild

    # --- A8: what cannot be examined is a sub-agent -------------------------------------

    def test_a_rollout_that_cannot_be_examined_reads_as_a_subagent(self, codex_home):
        assert data_codex.is_subagent_rollout(codex_home / "missing.jsonl") is True     # the stat fails
        assert data_codex.is_subagent_rollout(12345) is True                            # not a path at all
        ok = _cx_write(codex_home, _cx_id(1), "C:\\W")
        assert data_codex.is_subagent_rollout(ok) is False                              # a known non-sub-agent
        legacy = _cx_write(codex_home, _cx_id(2), "C:\\W", first=_cx_line({"id": _cx_id(2)}))
        assert data_codex.is_subagent_rollout(legacy) is False

    # --- A11: titles from the session index are capped ----------------------------------

    def test_a_thread_name_is_capped_after_it_is_stripped(self, codex_home):
        cwd = "C:\\W"
        for n in (1, 2, 3):
            _cx_write(codex_home, _cx_id(n), cwd, [_cx_user("p")])
        _cx_index(codex_home, [(_cx_id(1), " " * 5 + "n" * 5000), (_cx_id(2), "short name"),
                               (_cx_id(3), "e" * 200)])
        titles = {s.session_id: s.title for s in data_codex.load_sessions(cwd)[0]}
        assert titles == {_cx_id(1): "n" * 200, _cx_id(2): "short name", _cx_id(3): "e" * 200}

    # --- A12: one UUID pattern ------------------------------------------------------------

    def test_the_uuid_pattern_is_lower_case_ascii_and_the_others_are_built_from_it(self):
        good = "0123abcd-1111-4222-8333-0123456789ab"
        assert data_mod.UUID_RE.fullmatch(good)
        for bad in (good.upper(), good + "\n", good[:-1], good + "0", "g" + good[1:],
                    good.replace("0", "\uff10", 1)):
            assert data_mod.UUID_RE.fullmatch(bad) is None, bad
        assert data_mod.SESSION_ID_RE.fullmatch(good) and data_mod.SESSION_ID_RE.fullmatch("sess_" + good)
        assert data_mod.SESSION_ID_RE.fullmatch("sess_" + good.upper()) is None
        assert data_mod.UUID_RE.pattern in data_mod.SESSION_ID_RE.pattern
        assert data_mod.UUID_RE.pattern in data_codex._ROLLOUT_RE.pattern
        # Upper case stays accepted in a rollout's name and id, as before (lower-cased first).
        assert data_codex._ROLLOUT_RE.fullmatch(f"rollout-2026-09-01T10-00-00-{good.upper()}.jsonl")
        assert data_codex._file_uuid(f"x/rollout-t-{good.upper()}.jsonl") == good

    # --- A14: a slow provider does not hold up another -----------------------------------

    @staticmethod
    def _fake_provider(name, delay=0.0, started=None, active=None):
        class Fake:
            calls = 0
            peak = 0
            now = 0

            @staticmethod
            def is_available():
                return True

            @classmethod
            def discover_workspaces(cls):
                cls.calls += 1
                cls.now += 1
                cls.peak = max(cls.peak, cls.now)
                if started is not None:
                    started.set()
                time.sleep(delay)
                cls.now -= 1
                return [(f"C:\\{name}", 1, "2026-09-01T10:00:00.000000+00:00")]
        return Fake

    def test_a_slow_provider_does_not_delay_another_providers_discovery(self, monkeypatch):
        started = threading.Event()
        slow = self._fake_provider("codex", delay=1.5, started=started)
        fast = self._fake_provider("claude")
        monkeypatch.setattr(data_mod, "PROVIDERS", {"claude-code": fast, "codex": slow})
        monkeypatch.setattr(data_mod, "_cache", {})
        everything = []
        worker = threading.Thread(target=lambda: everything.append(data_mod.discover_workspaces_with_counts()))
        worker.start()
        try:
            assert started.wait(10)
            t0 = time.monotonic()
            only_claude = data_mod.discover_workspaces_with_counts("claude-code")
            assert time.monotonic() - t0 < 0.75, "waited behind the other provider's discovery"
            assert only_claude == [("C:\\claude", 1, "2026-09-01T10:00:00.000000+00:00", "claude-code")]
        finally:
            worker.join(timeout=30)
        assert sorted(r[3] for r in everything[0]) == ["claude-code", "codex"]

    def test_identical_concurrent_requests_still_compute_once(self, monkeypatch):
        started = threading.Event()
        slow = self._fake_provider("codex", delay=0.4, started=started)
        monkeypatch.setattr(data_mod, "PROVIDERS", {"codex": slow})
        monkeypatch.setattr(data_mod, "_cache", {})
        results = []
        threads = [threading.Thread(target=lambda: results.append(data_mod.discover_workspaces_with_counts("codex")))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert slow.calls == 1 and len(results) == 4 and all(r == results[0] for r in results)

    def test_one_provider_never_runs_two_discoveries_at_once(self, monkeypatch):
        slow = self._fake_provider("codex", delay=0.3)
        monkeypatch.setattr(data_mod, "PROVIDERS", {"codex": slow})
        monkeypatch.setattr(data_mod, "_cache", {})
        threads = [threading.Thread(target=lambda: data_mod.discover_workspaces_with_counts("codex")),
                   threading.Thread(target=lambda: data_mod.discover_workspaces_with_counts())]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert slow.calls == 2 and slow.peak == 1


class TestCodexSurvivingMutations:
    """F8: behaviours the first review round found unpinned."""

    def test_last_prompt_falls_back_to_the_first_when_the_tail_has_none(self, codex_home):
        # The only UserMessage is more than the 2 MiB tail window from the end.
        _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("only prompt")] + _cx_filler(2300, width=1000))
        (session,), _ = data_codex.load_sessions("C:\\W")
        assert session.first_prompt == "only prompt"
        assert session.last_prompt == "only prompt"

    def test_the_line_cap_applies_to_the_reverse_reader_too(self, codex_home):
        sid = _cx_id(1)
        _cx_write(codex_home, sid, "C:\\W", [_cx_agent("small reply"), _cx_agent("H" * 300_000)])
        assert data_codex.get_session_tail(sid, "C:\\W") == ["small reply"]

    def test_sessions_are_listed_newest_first(self, codex_home):
        for n in (2, 3, 1):  # written out of id order; mtimes strictly increase with the write
            _cx_write(codex_home, _cx_id(n), "C:\\W", [_cx_user(f"p{n}")])
        assert [s.session_id for s in data_codex.load_sessions("C:\\W")[0]] == [
            _cx_id(1), _cx_id(3), _cx_id(2)]

    def test_availability_is_memoised_until_its_ttl_expires(self, codex_home, monkeypatch):
        monkeypatch.setattr(data_codex, "_AVAILABLE_TTL", 1000.0)
        assert data_codex.is_available() is False
        _cx_write(codex_home, _cx_id(1), "C:\\W")
        assert data_codex.is_available() is False       # still inside the TTL
        monkeypatch.setattr(data_codex, "_AVAILABLE_TTL", 0.0)
        assert data_codex.is_available() is True        # expired

    def test_the_reverse_reader_stops_when_the_file_shrinks_under_it(self):
        fh = io.BytesIO(b"first\nsecond\n")
        assert list(data_codex._iter_lines_reverse(fh, 100)) == []   # claimed size 100, real 13

    def test_the_walk_is_depth_bounded_and_does_not_follow_symlinks(self, codex_home, tmp_path):
        root = codex_home / "sessions"
        deep5, deep6 = root.joinpath(*"abcde"), root.joinpath(*"abcdef")
        for folder, n in ((deep5, 1), (deep6, 2)):
            folder.mkdir(parents=True)
            (folder / f"rollout-x-{_cx_id(n)}.jsonl").write_bytes(b"")
        found = {u for _, u in data_codex._walk_rollouts(str(root))}
        assert found == {_cx_id(1)}                      # five folders deep is reached, six is not
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / f"rollout-x-{_cx_id(3)}.jsonl").write_bytes(b"")
        try:
            os.symlink(outside, root / "link", target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("cannot create a directory symlink here")
        assert _cx_id(3) not in {u for _, u in data_codex._walk_rollouts(str(root))}

    def test_a_tail_window_with_no_newline_stops_at_its_cap(self, codex_home, counted_reads):
        sid, cwd = _cx_id(1), "C:\\W"
        first = _cx_line(_cx_meta(sid, cwd))
        _cx_write(codex_home, sid, cwd, first=first, tail=b"x" * 3_500_000)
        (session,), _ = data_codex.load_sessions(cwd)
        assert session.session_id == sid
        assert counted_reads[0] <= len(first) + 1024 * 1024 + 2 * 1024 * 1024

    def test_a_deeply_nested_line_that_reaches_the_parser_is_skipped(self, codex_home):
        sid, cwd = _cx_id(1), "C:\\W"
        bomb = b'{"payload":{"type":"item_completed"},"x":' + b"[" * 5000 + b"]" * 5000 + b"}"
        assert data_codex.loads(bomb) is None          # a RecursionError is not a ValueError
        path = _cx_write(codex_home, sid, cwd, [_cx_agent("one")])
        with open(path, "ab") as fh:
            fh.write(bomb + b"\n")
            fh.write(_cx_line(_cx_agent("two")))
        assert [e.text for e in data_codex.get_full_transcript(sid, cwd)] == ["one", "two"]

    def test_a_deeply_nested_user_line_does_not_cost_the_session(self, codex_home):
        """The listing's head scan has no per-record guard of its own: `_loads` must
        absorb the RecursionError, or the whole file is skipped."""
        bomb = b'{"payload":{"item":{"type":"UserMessage"}},"x":' + b"[" * 5000 + b"]" * 5000 + b"}\n"
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [])
        with open(path, "ab") as fh:
            fh.write(bomb)
            fh.write(_cx_line(_cx_user("the real prompt")))
        (session,), _ = data_codex.load_sessions(cwd)
        assert session.first_prompt == "the real prompt"


# --- Codex live dot and Resume lock state (presence.py, data_codex.py) ---
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3
#
# Argv shapes are copied from the process table measured before this code was written (plan
# section 9, item 5), with placeholders for every id, URL and path. Lock files are real files in
# tmp_path; the holder and acquirer are real subprocesses that lock them the way
# Codex's writer_lock.rs does (whole file), so the probe is tested against the actual
# OS locking, not a mock of it.

# The D15 deny-list, written out independently of the code under test. The last five are
# the review's additions: the hidden helpers `responses-api-proxy`, `stdio-to-uds`,
# `execpolicy` and `tcp-tunnel`, and the `cloud-tasks` alias of `cloud`, each confirmed with
# `codex <name> --help` on codex-cli 0.159.2 and absent from `codex --help`.
_CX_D15 = ["app-server", "exec-server", "exec", "e", "mcp", "mcp-server", "plugin", "login",
           "logout", "review", "remote-control", "app", "completion", "update", "doctor",
           "sandbox", "debug", "apply", "a", "queue", "archive", "delete", "unarchive",
           "migrate-rollouts", "cloud", "features", "agents", "help", "daemon",
           "responses-api-proxy", "stdio-to-uds", "execpolicy", "tcp-tunnel", "cloud-tasks"]

_CX_CWD = "C:\\Work\\Proj"


def _cx_basename(argv0: str) -> str:
    return argv0.replace("\\", "/").rsplit("/", 1)[-1]


# 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 3 (D8): the snapshot
# records every Codex process with its kind and parent; `_match_provider` is untouched.
class TestCodexProcessKinds:
    @staticmethod
    def _p(pid, argv, ppid=None, ct=100.0, name=None):
        return _FakeProc(name or _cx_basename(argv[0]), argv, cwd=_CX_CWD, pid=pid, create_time=ct, ppid=ppid)

    def _procs(self, *procs):
        return _scan_with(list(procs)).codex_procs()

    def test_a_no_daemon_terminal_is_a_terminal(self):
        got = self._procs(self._p(10, ["codex.exe", "--no-daemon"], ppid=2), self._p(2, ["pwsh.exe"], name="pwsh.exe"))
        assert got == {10: (100.0, "terminal", 2)}

    def test_a_plain_terminal_and_a_resume_are_terminals(self):
        got = self._procs(self._p(10, ["codex.exe"]), self._p(11, ["codex.exe", "resume", _cx_id(7)]))
        assert {pid: kind for pid, (_c, kind, _pp) in got.items()} == {10: "terminal", 11: "terminal"}

    def test_a_managed_daemon_is_a_daemon_even_though_it_carries_a_helper_token(self):
        argv = ["codex.exe", "app-server", "--listen", "unix://x", "--managed-daemon"]
        assert {pid: kind for pid, (_c, kind, _pp) in self._procs(self._p(20, argv)).items()} == {20: "daemon"}

    @pytest.mark.parametrize("token", ["app-server", "exec", "mcp-server", "login"])
    def test_any_other_helper_subcommand_is_a_helper(self, token):
        got = self._procs(self._p(30, ["codex.exe", token]))
        assert {pid: kind for pid, (_c, kind, _pp) in got.items()} == {30: "helper"}

    def test_the_parent_is_kept_when_it_is_alive_and_none_when_it_is_gone_or_unreadable(self):
        host = _FakeProc("ChatGPT.exe", ["ChatGPT.exe"], pid=500, create_time=1.0)
        got = self._procs(
            host,
            self._p(31, ["codex.exe", "app-server"], ppid=500),          # a live non-Codex parent
            self._p(32, ["codex.exe", "app-server"], ppid=999),          # a parent that is gone
            self._p(33, ["codex.exe", "app-server"], ppid=None))         # a parent that cannot be read
        assert got[31][2] == 500 and got[32][2] is None and got[33][2] is None
        assert all(kind == "helper" for _c, kind, _pp in got.values())

    def test_a_codex_parent_is_kept_so_a_helper_can_be_traced_to_its_terminal(self):
        got = self._procs(self._p(40, ["codex.exe"]), self._p(41, ["codex.exe", "app-server"], ppid=40))
        assert got[41][2] == 40 and got[40][1] == "terminal"

    @pytest.mark.parametrize("name,argv", [
        ("ChatGPT.exe", ["ChatGPT.exe"]),
        ("ChatGPT.exe", ["ChatGPT.exe", "--type=renderer"]),
        ("codex.exe", ["codex.exe", "--type=utility"]),
        ("notepad.exe", ["codex.exe", "--managed-daemon"]),
        ("notepad.exe", ["C:\\bin\\codex.exe", "app-server"]),
        ("codex-code-mode-host.exe", ["codex-code-mode-host.exe"]),
        ("codex-windows-sandbox-service.exe", ["codex-windows-sandbox-service.exe"]),
        ("node.exe", ["node", "C:\\npm\\codex.js", "resume", _cx_id(7)]),
        ("pwsh.exe", ["pwsh.exe", "-c", "codex.exe app-server"]),
        ("ChatGPT.exe", ["ChatGPT.exe", "app-server"]),
        ("codex-code-mode-host.exe", ["codex-code-mode-host.exe", "exec"]),
        ("codex.exe", ["codex.exe", "app-server", "--type=utility"]),
    ])
    def test_other_processes_are_not_recorded(self, name, argv):
        assert self._procs(_FakeProc(name, argv, pid=50, create_time=1.0, ppid=1)) == {}

    def test_argv0_stands_in_only_when_the_scan_got_no_image_name(self):
        got = self._procs(_FakeProc("", ["C:\\bin\\codex.exe", "app-server", "--managed-daemon"], pid=80, create_time=1.0, ppid=1))
        assert {pid: kind for pid, (_c, kind, _pp) in got.items()} == {80: "daemon"}

    def test_a_parent_pid_that_belongs_to_a_newer_process_is_not_the_parent(self):
        host = _FakeProc("ChatGPT.exe", ["ChatGPT.exe"], pid=500, create_time=500.0)
        newer = self._procs(host, self._p(41, ["codex.exe", "app-server"], ppid=500, ct=100.0))
        assert newer[41][2] is None, "pid 500 started after its supposed child: a reused pid"
        older = self._procs(_FakeProc("ChatGPT.exe", ["ChatGPT.exe"], pid=500, create_time=50.0),
                            self._p(41, ["codex.exe", "app-server"], ppid=500, ct=100.0))
        assert older[41][2] == 500
        unreadable = self._procs(_FakeProc("ChatGPT.exe", ["ChatGPT.exe"], pid=500, create_time=None),
                                 self._p(41, ["codex.exe", "app-server"], ppid=500, ct=100.0))
        assert unreadable[41][2] == 500, "a parent whose start time cannot be read is kept"

    def test_a_process_whose_start_time_cannot_be_read_is_not_recorded(self):
        assert self._procs(_FakeProc("codex.exe", ["codex.exe"], pid=60, create_time=None, ppid=1)) == {}

    def test_has_codex_terminal_needs_a_terminal_or_a_daemon(self):
        from power_atlas import presence
        snap = _scan_with([self._p(70, ["codex.exe", "app-server"])])
        assert snap.has_codex_terminal() is False
        assert _scan_with([self._p(71, ["codex.exe"])]).has_codex_terminal() is True
        assert _scan_with([self._p(72, ["codex.exe", "app-server", "--managed-daemon"])]).has_codex_terminal() is True
        assert presence._EMPTY.has_codex_terminal() is False and presence._EMPTY.codex_procs() == {}

    def test_the_new_field_is_trailing_and_keyword_defaulted_and_the_map_is_a_copy(self):
        from power_atlas import presence
        snap = presence.Snapshot(set(), set(), {}, {}, {}, {}, {})   # the positional call sites keep working
        assert snap.codex_procs() == {}
        snap = presence.Snapshot(set(), set(), codex_procs={1: (1.0, "terminal", None)})
        snap.codex_procs().clear()
        assert snap.codex_procs() == {1: (1.0, "terminal", None)}

    def test_match_provider_still_rejects_helpers_and_accepts_a_daemon_free_terminal(self):
        from power_atlas import presence
        assert presence._match_provider("codex.exe", ["codex.exe", "app-server", "--managed-daemon"]) is None
        assert presence._match_provider("codex.exe", ["codex.exe"]) == "codex"

    def test_presence_gains_no_state_and_no_import(self):
        src = Path(data_codex.__file__).with_name("presence.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        froms = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "lock_owner" not in imports | froms and "data_codex" not in imports | froms


class TestCodexPresence:
    def _scan(self, *procs):
        return _scan_with(list(procs))

    @staticmethod
    def _proc(argv, cwd=_CX_CWD, name=None):
        return _FakeProc(name or _cx_basename(argv[0]), argv, cwd=cwd)

    def test_the_deny_list_is_exactly_d15_plus_the_hidden_helpers_and_leaves_fork_alone(self):
        from power_atlas import presence
        assert presence._CODEX_HELPER_SUBCOMMANDS == frozenset(_CX_D15)
        assert len(_CX_D15) == 34 == len(set(_CX_D15)) and "fork" not in presence._CODEX_HELPER_SUBCOMMANDS
        assert "resume" not in presence._CODEX_HELPER_SUBCOMMANDS

    @pytest.mark.parametrize("token", _CX_D15)
    def test_every_helper_subcommand_is_not_a_match_in_any_position(self, token):
        from power_atlas import presence
        for argv in (["codex.exe", token],
                     ["codex.exe", "-c", "features.x=true", token, "--flag"],
                     ["C:\\bin\\codex.exe", "--remote", "ws://h:1", token]):
            assert presence._match_provider("codex.exe", argv) is None, argv

    @pytest.mark.parametrize("argv", [
        ["codex.exe"],
        ["codex.exe", "fix the failing test"],
        ["codex.exe", "resume", _cx_id(7)],
        ["codex.exe", "-c", "k=v", "--enable", "feature"],
        ["codex.exe", "--remote", "ws://127.0.0.1:9"],
        ["codex.exe", "fork", _cx_id(7)],
        ["codex.cmd", "resume", _cx_id(7)],
        ["C:\\Users\\u\\bin\\codex.exe", "resume", _cx_id(7)],
    ])
    def test_a_terminal_session_is_a_match(self, argv):
        from power_atlas import presence
        assert presence._match_provider(_cx_basename(argv[0]), argv) == "codex"

    @pytest.mark.parametrize("name,argv", [
        ("ChatGPT.exe", ["ChatGPT.exe"]),
        ("ChatGPT.exe", ["ChatGPT.exe", "--type=renderer", "--annotation=prod=Codex"]),
        ("codex-code-mode-host.exe", ["codex-code-mode-host.exe"]),
        ("codex-computer-use-swift.exe", ["codex-computer-use-swift.exe", "--parent-pid", "1"]),
        ("codex-windows-sandbox-service.exe", ["codex-windows-sandbox-service.exe"]),
        ("node.exe", ["node", "C:\\npm\\@openai\\codex\\bin\\codex.js", "resume", _cx_id(7)]),
        ("codex.exe", ["codex.exe", "--type=utility"]),
    ])
    def test_other_processes_that_merely_mention_codex_are_not_a_match(self, name, argv):
        from power_atlas import presence
        assert presence._match_provider(name, argv) is None

    def test_a_resume_uuid_marks_that_thread_live_in_its_workspace(self):
        from power_atlas.data import _normalize_path
        sid = _cx_id(7)
        snap = self._scan(self._proc(["codex.exe", "resume", sid]))
        assert snap.is_live("codex", _CX_CWD, sid) is True
        assert snap.is_live("codex", _CX_CWD, _cx_id(8)) is False
        assert _normalize_path(_CX_CWD) in snap.live_cwds({"codex"})
        assert [(p, s) for p, s, _c in snap.live_sids()] == [("codex", sid)]

    def test_an_upper_case_resume_uuid_marks_the_lower_case_session_live(self):
        sid = _cx_id(0xABC)
        assert sid != sid.upper()
        snap = self._scan(self._proc(["codex.exe", "resume", sid.upper()]))
        assert snap.is_live("codex", _CX_CWD, sid) is True

    @pytest.mark.parametrize("argv", [
        ["codex.exe", "resume", "--last"],
        ["codex.exe", "resume", "fix the failing build"],
        ["codex.exe", "resume", "my-session-name"],
        ["codex.exe", "resume", _cx_id(7) + "\n"],
        ["codex.exe", "resume", _cx_id(7)[:-1]],
        ["codex.exe", "resume", "sess_" + _cx_id(7)],
        ["codex.exe", "resume", _cx_id(7) + "x"],
        ["codex.exe", "resume"],
        ["codex.exe", "resume=not-a-uuid"],
    ])
    def test_anything_after_resume_that_is_not_a_uuid_leaves_the_process_live_by_workspace_only(self, argv):
        from power_atlas.data import _normalize_path
        snap = self._scan(self._proc(argv))
        assert snap.live_sids() == []
        assert _normalize_path(_CX_CWD) in snap.live_cwds({"codex"})

    def test_fork_marks_the_workspace_live_but_never_the_source_thread(self):
        from power_atlas.data import _normalize_path
        sid = _cx_id(7)
        snap = self._scan(self._proc(["codex.exe", "fork", sid]))
        assert snap.is_live("codex", _CX_CWD, sid) is False
        assert snap.live_sids() == []
        assert _normalize_path(_CX_CWD) in snap.live_cwds({"codex"})

    def test_a_bare_tui_and_a_tui_with_a_prompt_are_live_by_workspace(self):
        from power_atlas.data import _normalize_path
        for argv in (["codex.exe"], ["codex.exe", "write a haiku"]):
            snap = self._scan(self._proc(argv))
            assert snap.live_sids() == []
            assert snap.live_cwds({"codex"}) == {_normalize_path(_CX_CWD)}

    def test_the_machines_daemons_in_a_workspace_light_nothing_up(self):
        """The measured table: a desktop app-server, an exec-server, a managed daemon and
        its pid-update loop, plus the desktop app itself. None is a session."""
        snap = self._scan(
            self._proc(["codex.exe", "-c", "features.code_mode_host=true", "app-server",
                        "--analytics-default-enabled", "-c", "k=v"]),
            self._proc(["codex.exe", "exec-server", "--remote", "wss://h.invalid/x",
                        "--environment-id", "env-1"]),
            self._proc(["codex.exe", "app-server", "--listen", "unix://", "--managed-daemon"]),
            self._proc(["codex.exe", "app-server", "daemon", "pid-update-loop"]),
            self._proc(["ChatGPT.exe"]),
            self._proc(["codex.exe", "exec", "summarise the repo"]),
        )
        assert snap.live_cwds() == set()
        assert snap.live_sids() == []

    def test_a_tui_beside_daemons_is_the_only_live_workspace(self):
        from power_atlas.data import _normalize_path
        other = "C:\\Work\\Other"
        snap = self._scan(
            self._proc(["codex.exe", "app-server", "daemon", "pid-update-loop"], cwd=_CX_CWD),
            self._proc(["codex.exe"], cwd=other),
        )
        assert snap.live_cwds({"codex"}) == {_normalize_path(other)}

    def test_codex_never_marks_another_providers_session_live(self):
        sid = _cx_id(7)
        snap = self._scan(self._proc(["codex.exe", "resume", sid]))
        assert snap.is_live("claude-code", _CX_CWD, sid) is False
        assert snap.is_live("kiro-cli-v3", _CX_CWD, sid) is False

    def test_a_claude_resume_id_is_not_validated_as_a_uuid(self):
        """The UUID rule is Codex's: Claude Code's and kiro-cli's id handling is unchanged."""
        snap = self._scan(_FakeProc("claude", ["claude", "--resume", "abc123"], cwd="/w"))
        assert snap.is_live("claude-code", "/w", "abc123") is True

    @pytest.mark.parametrize("token", _CX_D15)
    def test_the_deny_list_applies_to_codex_only(self, token):
        """A word of Codex's helper list in another provider's argv (a prompt, a project
        name, a subcommand of its own) must not stop that provider's process matching:
        claude and kiro-cli have their own subcommands and free-text arguments."""
        from power_atlas import presence
        cases = [
            ("claude-code", ["claude", token, "--resume", "sid-1"]),        # right after argv0
            ("claude-code", ["claude", "--resume", "sid-1", token]),        # last
            ("claude-code", ["claude", "--resume", token]),                 # as the id itself
            ("kiro-cli-v3", ["kiro-cli", token, "chat", "--resume-id", "sid-1"]),
            ("kiro-cli-v3", ["kiro-cli", "chat", token, "--resume-id", "sid-1"]),
            ("kiro-cli-v3", ["kiro-cli", "chat", "--resume-id", "sid-1", token]),
        ]
        for provider, argv in cases:
            assert presence._match_provider(argv[0], argv) == provider, argv

    def test_a_claude_or_kiro_process_with_a_codex_helper_word_is_still_live(self):
        snap = self._scan(
            _FakeProc("claude", ["claude", "--resume", "sid-1", "review"], cwd="/w"),
            _FakeProc("kiro-cli", ["kiro-cli", "app", "chat", "--resume-id", "sid-2"], cwd="/k"),
        )
        assert snap.is_live("claude-code", "/w", "sid-1") is True
        assert snap.is_live("kiro-cli-v3", "/k", "sid-2") is True

    def test_an_unknown_helper_subcommand_is_documented_as_matching(self):
        """CANARY, not a requirement. The deny-list ages: a Codex release that adds a
        helper subcommand not in the list makes that helper read as a terminal session (a false
        dot in the folder it runs in, bounded by the 300 s rollout recency). This test
        pins today's behaviour so the day it is fixed is a decision. Reopen trigger: a
        reported false dot, or a new subcommand in `codex --help` after a Codex upgrade;
        then add it to presence._CODEX_HELPER_SUBCOMMANDS and to _CX_D15 above."""
        from power_atlas import presence
        assert presence._match_provider("codex.exe", ["codex.exe", "frobnicate-server"]) == "codex"


def _cx_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class TestCodexTurnState:
    """Phase 4 (D11): `turn_state` is the last of task_started (working), task_complete and
    turn_aborted (idle), matched on `type` and `payload.type`, None past the tail window and
    None for a working turn whose newest record is older than 30 minutes."""

    SID, CWD = _cx_id(31), "C:\\W"

    @staticmethod
    def _ev(kind, ago):
        return _cx_rec("event_msg", {"type": kind}, _cx_iso(time.time() - ago))

    def _state(self, home, records, **kw):
        path = _cx_write(home, self.SID, self.CWD, records, **kw)
        return data_codex.turn_state(path), path

    def test_each_last_record_gives_its_verdict(self, codex_home):
        for kind, want in (("task_started", "working"), ("task_complete", "idle"),
                           ("turn_aborted", "idle")):
            got, _ = self._state(codex_home, [self._ev("task_started", 50), self._ev(kind, 10)])
            assert got == want, kind

    def test_the_last_turn_record_wins_over_earlier_ones(self, codex_home):
        got, _ = self._state(codex_home, [self._ev("task_complete", 90), self._ev("task_started", 20),
                                          _cx_user("go", ts=_cx_iso(time.time() - 15))])
        assert got == "working"

    def test_no_turn_record_gives_none(self, codex_home):
        got, _ = self._state(codex_home, [_cx_user("hi", ts=_cx_iso(time.time() - 5))])
        assert got is None

    def test_a_torn_last_line_is_skipped(self, codex_home):
        got, _ = self._state(codex_home, [self._ev("task_started", 20)],
                             tail=b'{"timestamp":"2099-01-01T00:00:00Z","type":"event_msg","payload":{"type":"task_comp')
        assert got == "working"

    def test_a_line_over_the_cap_is_skipped_not_parsed(self, codex_home):
        huge = _cx_line(_cx_rec("event_msg", {"type": "task_complete", "pad": "p" * (data_codex._LINE_CAP + 10)},
                                _cx_iso(time.time() - 5)))
        got, _ = self._state(codex_home, [self._ev("task_started", 20)], tail=huge)
        assert got == "working", "an over-long record is dropped unparsed, so it cannot end the turn"

    def test_a_tool_output_that_quotes_the_words_is_not_a_record(self, codex_home):
        quoted = _cx_output("c1", 'saw {"type":"event_msg","payload":{"type":"task_complete"}} in the log',
                            ts=_cx_iso(time.time() - 5))
        got, _ = self._state(codex_home, [self._ev("task_started", 20), quoted])
        assert got == "working"

    def test_a_message_inside_an_event_that_quotes_the_words_is_not_a_turn_record(self, codex_home):
        said = _cx_rec("event_msg", {"type": "item_completed", "item": {"type": "AgentMessage", "content": [
            {"type": "Text", "text": 'the log says {"type":"event_msg","payload":{"type":"task_complete"}}'}]}},
            _cx_iso(time.time() - 5))
        got, _ = self._state(codex_home, [self._ev("task_started", 20), said])
        assert got == "working", "matched on the payload's own type, not on a word anywhere in the line"

    def test_a_working_turn_whose_records_carry_no_time_is_not_trusted(self, codex_home):
        got, _ = self._state(codex_home, [_cx_rec("event_msg", {"type": "task_started"}, ts=None)])
        assert got is None, "its age cannot be checked, so it cannot be called working"

    def test_a_record_of_another_type_with_the_same_payload_type_does_not_count(self, codex_home):
        other = _cx_rec("response_item", {"type": "task_complete"}, _cx_iso(time.time() - 5))
        got, _ = self._state(codex_home, [self._ev("task_started", 20), other])
        assert got == "working"

    def test_a_turn_start_beyond_the_tail_window_gives_none(self, codex_home):
        pad = _cx_filler(int(data_codex._TAIL_MAX / 1000) + 400)
        got, _ = self._state(codex_home, [self._ev("task_started", 40)] + pad)
        assert got is None

    def test_a_working_turn_with_no_record_for_30_minutes_is_stuck_and_gives_none(self, codex_home):
        got, _ = self._state(codex_home, [self._ev("task_started", data_codex.TURN_STUCK_SECONDS + 60)])
        assert got is None
        got, _ = self._state(codex_home, [self._ev("task_started", data_codex.TURN_STUCK_SECONDS - 60)])
        assert got == "working"

    def test_the_stuck_cap_looks_at_the_newest_record_not_the_turn_start(self, codex_home):
        # a long command: the turn started 40 minutes ago but a record landed 10 seconds ago
        got, _ = self._state(codex_home, [self._ev("task_started", 2400),
                                          _cx_user("tick", ts=_cx_iso(time.time() - 10))])
        assert got == "working"

    def test_a_working_turn_stamped_in_the_future_is_not_trusted(self, codex_home):
        got, _ = self._state(codex_home, [self._ev("task_started", -3600)])
        assert got is None, "a clock error must not keep a turn alive for ever"
        got, _ = self._state(codex_home, [self._ev("task_started", -1.5)])
        assert got == "working", "a stamp a second ahead is jitter"

    def test_an_old_idle_turn_stays_idle(self, codex_home):
        got, _ = self._state(codex_home, [self._ev("task_complete", 90000)])
        assert got == "idle"

    def test_the_verdict_follows_a_file_that_grows(self, codex_home):
        got, path = self._state(codex_home, [self._ev("task_started", 20)])
        assert got == "working"
        with open(path, "ab") as fh:
            fh.write(_cx_line(self._ev("task_complete", 1)))
        assert data_codex.turn_state(path) == "idle"

    def test_a_frozen_mtime_with_a_changing_size_refreshes(self, codex_home):
        got, path = self._state(codex_home, [self._ev("task_started", 20)])
        frozen = os.stat(path).st_mtime_ns
        with open(path, "ab") as fh:
            fh.write(_cx_line(self._ev("task_complete", 1)))
        os.utime(path, ns=(frozen, frozen))
        assert data_codex.turn_state(path) == "idle", "keyed on mtime alone it would still say working"

    def test_an_unchanged_file_is_read_once(self, codex_home, monkeypatch):
        got, path = self._state(codex_home, [self._ev("task_started", 20)])
        calls = []
        real = data_codex._read_turn_state
        monkeypatch.setattr(data_codex, "_read_turn_state", lambda p: calls.append(p) or real(p))
        data_codex.turn_state(path)
        data_codex.turn_state(path)
        assert calls == [], "the first call in this test already filled the cache"

    def test_the_stuck_cap_applies_to_a_cached_answer_too(self, codex_home, monkeypatch):
        got, path = self._state(codex_home, [self._ev("task_started", data_codex.TURN_STUCK_SECONDS - 30)])
        assert got == "working"
        real = time.time
        monkeypatch.setattr(data_codex.time, "time", lambda: real() + 120)
        assert data_codex.turn_state(path) is None, "the clock moved on; the file did not"

    def test_clearing_the_caches_drops_a_cached_verdict(self, codex_home):
        got, path = self._state(codex_home, [self._ev("task_started", 20)])
        assert got == "working" and data_codex._turn_cache.get(os.fspath(path)) is not None
        data_codex._clear_caches()
        assert data_codex._turn_cache.get(os.fspath(path)) is None

    def test_a_missing_file_gives_none_and_never_raises(self, codex_home):
        assert data_codex.turn_state(codex_home / "sessions" / "nope.jsonl") is None


class TestCodexActivityEpoch:
    """D16: the later of the rollout mtime and, only when the mtime is older than the
    300 s window, the last complete record's timestamp; a stamp from the future is
    dropped, never clamped to now (a clamp would read as age 0, which is live)."""

    SID, CWD = _cx_id(21), "C:\\W"

    def _rollout(self, home, last_ts, mtime, extra_tail=b""):
        return _cx_write(home, self.SID, self.CWD,
                         [_cx_user("go", ts=_cx_iso(last_ts - 5)), _cx_agent("done", ts=_cx_iso(last_ts))],
                         tail=extra_tail, mtime=mtime)

    @staticmethod
    def _activity(path):
        return data_codex.activity_epoch(path, os.stat(path))

    def test_a_frozen_mtime_with_a_recent_last_record_is_recent_activity(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now - 40, now - 5000)
        got = self._activity(path)
        assert got == pytest.approx(now - 40, abs=1.0)
        assert now - got <= data_codex.ACTIVITY_WINDOW

    def test_both_old_is_old_and_the_later_of_the_two_wins(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now - 2000, now - 5000)
        assert self._activity(path) == pytest.approx(now - 2000, abs=1.0)
        # an mtime newer than the last record, itself outside the window: the mtime wins
        path = self._rollout(codex_home, now - 7000, now - 3000)
        assert self._activity(path) == pytest.approx(now - 3000, abs=1.0)
        assert now - self._activity(path) > data_codex.ACTIVITY_WINDOW

    def test_the_last_record_is_read_only_when_the_mtime_is_outside_the_window(self, codex_home, counted_reads):
        now = time.time()
        inside = self._rollout(codex_home, now - 5000, now - 290)   # the record would say old
        assert self._activity(inside) == pytest.approx(now - 290, abs=1.0)
        assert counted_reads[0] == 0, "a warm active file must cost one stat, not a read"
        outside = self._rollout(codex_home, now - 100, now - 310)
        assert self._activity(outside) == pytest.approx(now - 100, abs=1.0)
        assert counted_reads[0] > 0

    def test_a_future_record_timestamp_is_no_activity_at_all(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now + 3600, now - 5000)
        got = self._activity(path)
        assert got == pytest.approx(now - 5000, abs=1.0)
        assert got <= time.time()
        assert time.time() - got > data_codex.ACTIVITY_WINDOW   # not live: nothing was clamped to now

    def test_a_stamp_a_second_ahead_is_clock_jitter_and_counts_no_later_than_now(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now + 1.5, now - 5000)
        assert self._activity(path) <= time.time()
        assert time.time() - self._activity(path) < 5

    def test_a_future_mtime_is_not_trusted_either(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now - 30, now + 3600)
        assert self._activity(path) == pytest.approx(now - 30, abs=1.0)
        old = self._rollout(codex_home, now - 9000, now + 3601)   # another mtime: another cache key
        assert self._activity(old) == pytest.approx(now - 9000, abs=1.0)

    def test_a_torn_last_line_is_skipped_for_the_last_complete_record(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now - 60, now - 5000,
                             extra_tail=b'{"timestamp":"2099-01-01T00:00:00Z","type":"event_ms')
        assert self._activity(path) == pytest.approx(now - 60, abs=1.0)
        assert data_codex.last_event_epoch(path) == pytest.approx(now - 60, abs=1.0)

    def test_last_event_epoch_is_the_exact_instant_of_the_last_record(self, codex_home):
        stamp = datetime(2026, 9, 1, 10, 0, 7, 250000, tzinfo=timezone.utc)
        path = _cx_write(codex_home, self.SID, self.CWD,
                         [_cx_agent("a", ts="2026-09-01T09:00:00Z"), _cx_agent("b", ts=stamp.isoformat())])
        assert data_codex.last_event_epoch(path) == stamp.timestamp()

    def test_last_event_epoch_is_none_for_no_timestamp_or_no_file(self, codex_home, tmp_path):
        path = _cx_write(codex_home, self.SID, self.CWD, [], first=b'{"type":"session_meta"}\n{"x":1}\n')
        assert data_codex.last_event_epoch(path) is None
        assert data_codex.last_event_epoch(tmp_path / "absent.jsonl") is None

    def test_the_answer_is_cached_by_mtime_ns_and_size(self, codex_home, counted_reads):
        now = time.time()
        old = now - 5000
        path = self._rollout(codex_home, now - 100, old)
        first = self._activity(path)
        reads = counted_reads[0]
        assert reads > 0 and self._activity(path) == first
        assert counted_reads[0] == reads, "same (mtime_ns, size): no second read"
        # Same mtime, the file grew: a frozen-mtime file that Codex is still writing.
        with open(path, "ab") as fh:
            fh.write(_cx_line(_cx_agent("more", ts=_cx_iso(now - 50))))
        os.utime(path, (old, old))
        assert self._activity(path) == pytest.approx(now - 50, abs=1.0)
        assert counted_reads[0] > reads
        # Same size, another mtime_ns: also a new key.
        reads = counted_reads[0]
        os.utime(path, (old - 1, old - 1))
        self._activity(path)
        assert counted_reads[0] > reads

    def test_it_never_raises(self, codex_home, tmp_path):
        missing = tmp_path / "gone.jsonl"
        st = os.stat_result((0o100644, 0, 0, 1, 0, 0, 0, 0, 1.0, 0))
        assert data_codex.activity_epoch(missing, st) == 1.0   # unreadable tail: the mtime stands
        assert data_codex.activity_epoch(missing, object()) == 0.0

    @pytest.mark.parametrize("make", [
        lambda tmp: None, lambda tmp: 7, lambda tmp: 3.5, lambda tmp: object(), lambda tmp: [],
        lambda tmp: b"no-such-file.jsonl", lambda tmp: "", lambda tmp: tmp / "absent.jsonl",
        lambda tmp: tmp, lambda tmp: str(tmp / "absent.jsonl")])
    @pytest.mark.parametrize("age", [5000, 10])
    def test_a_hostile_first_argument_is_total_and_the_mtime_stands(self, tmp_path, make, age):
        """Review fix: the handler used to call os.fspath(path) again, so a first argument
        that is not a path raised TypeError out of a function documented as total, when the
        mtime was stale enough to need the tail. None, numbers, bytes, a directory and a
        missing file all return the mtime."""
        from types import SimpleNamespace
        mtime = time.time() - age
        st = SimpleNamespace(st_mtime=mtime, st_mtime_ns=int(mtime * 1e9), st_size=1)
        got = data_codex.activity_epoch(make(tmp_path), st)
        assert got == pytest.approx(mtime, abs=0.01)

    def test_a_path_object_with_a_stale_mtime_still_reads_the_tail(self, codex_home):
        now = time.time()
        path = self._rollout(codex_home, now - 60, now - 5000)
        assert data_codex.activity_epoch(path, os.stat(path)) == pytest.approx(now - 60, abs=1.0)
        assert data_codex.activity_epoch(str(path), os.stat(path)) == pytest.approx(now - 60, abs=1.0)

    def test_a_stamp_without_a_zone_is_read_as_utc(self, codex_home):
        """Documents today's behaviour, not a requirement (review fix P13): _parse_iso reads a
        stamp with no `Z` and no offset as UTC. Real Codex stamps end in `Z`, so this never
        shows in practice; a naive stamp written in local time east of UTC would read as
        the future and be dropped by activity_epoch."""
        assert data_codex._parse_iso("2026-09-01T10:00:00") == datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert data_codex._parse_iso("2026-09-01T10:00:00.250") == datetime(
            2026, 9, 1, 10, 0, 0, 250000, tzinfo=timezone.utc)
        now = time.time()
        naive = datetime.fromtimestamp(now - 40, tz=timezone.utc).replace(tzinfo=None).isoformat()
        path = _cx_write(codex_home, self.SID, self.CWD, [_cx_agent("x", ts=naive)], mtime=now - 5000)
        assert data_codex.last_event_epoch(path) == pytest.approx(now - 40, abs=1.0)

    def test_the_last_record_cache_grows_with_the_store_so_a_second_pass_does_not_thrash(self, codex_home, monkeypatch):
        """Review fix P9: like the parse and verdict caches (D10), the last-record cache is
        sized from the store. A fixed 4096 below a 4100-file store re-read every tail on every
        pass (a sequential pass over an LRU smaller than the set misses every time)."""
        # The floor is patched down to 64 (the real one is 4096): 70 files cross it as 4,100 did.
        # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, final review B9
        monkeypatch.setattr(data_codex, "_CACHE_MIN", 64)
        data_codex._clear_caches()
        cwd, count = "C:\\Big", 70  # above the 64 floor
        paths = [_cx_write(codex_home, _cx_id(n + 1), cwd,
                           [_cx_agent("x", ts=_cx_iso(_CX_OLD + 5))],
                           first=_cx_line(_cx_meta(_cx_id(n + 1), cwd, base_chars=0)), mtime=_CX_OLD)
                 for n in range(count)]
        data_codex.load_sessions(cwd)       # builds the store and sizes the caches
        assert data_codex._last_event_cache._maxsize >= 2 * count
        reads = []
        real = data_codex._read_last_event
        monkeypatch.setattr(data_codex, "_read_last_event", lambda p: reads.append(p) or real(p))
        for _ in range(2):
            for path in paths:
                data_codex.activity_epoch(path, os.stat(path))
        assert len(reads) == count, "a second pass re-read tails (thrash)"


# A portable stand-in for Codex's writer lock. `hold` takes a whole-file lock on a file
# and keeps it; `acquirer` replays WriterLockCoordinator::acquire and the guard's drop in
# a loop (coordination lock, blocking; then a fail-fast lock on <id>.lock; removal of the
# lock file under the coordination lock) and reports how often acquisition failed. Whole
# file on both platforms, as Rust std's File::lock does (LockFileEx / flock).
_CX_LOCK_HELPER = r'''
import json, os, sys, time
if sys.platform == "win32":
    import ctypes, ctypes.wintypes as wt, msvcrt
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    class _OV(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]
    _k32.LockFileEx.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.POINTER(_OV)]
    _k32.LockFileEx.restype = wt.BOOL
    _k32.UnlockFileEx.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.POINTER(_OV)]
    _k32.UnlockFileEx.restype = wt.BOOL
    def lock(f, nonblock):
        return bool(_k32.LockFileEx(msvcrt.get_osfhandle(f.fileno()), 2 | (1 if nonblock else 0),
                                    0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(_OV())))
    def unlock(f):
        _k32.UnlockFileEx(msvcrt.get_osfhandle(f.fileno()), 0, 0xFFFFFFFF, 0xFFFFFFFF, ctypes.byref(_OV()))
    def lock1(f):   # byte 0 only, fail at once: tells where the probe's lock really sits
        return bool(_k32.LockFileEx(msvcrt.get_osfhandle(f.fileno()), 3, 0, 1, 0, ctypes.byref(_OV())))
else:
    import fcntl
    def lock(f, nonblock):
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblock else 0))
            return True
        except OSError:
            return False
    def unlock(f):
        fcntl.flock(f, fcntl.LOCK_UN)
    def lock1(f):   # flock has no byte ranges: the whole file
        return lock(f, True)

role = sys.argv[1]
if role == "hold":
    f = open(sys.argv[2], "a+b")
    if not lock(f, True):
        print("busy", flush=True)
        sys.exit(3)
    print("ready", flush=True)
    sys.stdin.read()
elif role == "try0":
    f = open(sys.argv[2], "rb")
    print("free" if lock1(f) else "busy", flush=True)
elif role == "acquirer":
    d, tid, n, pause = sys.argv[2], sys.argv[3], int(sys.argv[4]), float(sys.argv[5])
    coord_path = os.path.join(d, ".coordination.lock")
    thread_path = os.path.join(d, tid + ".lock")
    fails = remove_fails = 0
    for _ in range(n):
        coord = open(coord_path, "a+b")
        lock(coord, False)
        try:
            f = open(thread_path, "a+b")
            time.sleep(pause)          # real I/O is not instant: the window a probe can land in
            ok = lock(f, True)
        finally:
            unlock(coord)
            coord.close()
        if not ok:
            fails += 1
            f.close()
            continue
        coord = open(coord_path, "a+b")
        lock(coord, False)
        f.close()
        try:
            os.remove(thread_path)
        except FileNotFoundError:
            pass
        except OSError:
            remove_fails += 1
        unlock(coord)
        coord.close()
    print(json.dumps({"failures": fails, "remove_failures": remove_fails}))
'''

_CX_ACQUIRE_CYCLES = 500
_CX_CAN_LOCK = sys.platform == "win32" or importlib.util.find_spec("fcntl") is not None
_needs_os_locks = pytest.mark.skipif(not _CX_CAN_LOCK, reason="no whole-file lock primitive here")


@contextlib.contextmanager
def _cx_hold(path):
    """A separate process holding a whole-file lock on `path` until the block ends."""
    proc = subprocess.Popen([sys.executable, "-c", _CX_LOCK_HELPER, "hold", str(path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        yield proc
    finally:
        proc.kill()                      # a killed holder: its lock goes, its file stays
        proc.wait(timeout=30)
        proc.stdout.close()
        proc.stdin.close()


def _cx_try_byte0(path) -> str:
    """"busy" when another process holds byte 0 of `path`, else "free"."""
    out = subprocess.run([sys.executable, "-c", _CX_LOCK_HELPER, "try0", str(path)],
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


class _Tracked:
    """A lock-file handle that records its own close and carries its file name."""

    def __init__(self, fh, name, events):
        self._fh, self._name, self._events = fh, name, events

    def close(self):
        self._events.append(("close", self._name))
        self._fh.close()

    def __getattr__(self, attr):
        return getattr(self._fh, attr)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _LocksDir:
    """The redirected thread-writer-locks folder plus the recorded lock events."""

    def __init__(self, path, events):
        self.path, self.events = path, events

    def __truediv__(self, name):
        return self.path / name

    def __str__(self):
        return str(self.path)


@pytest.fixture
def locks(codex_home, monkeypatch):
    """The redirected thread-writer-locks folder with its coordination file, and a
    recorder of every open, lock, unlock and close the probe makes on a lock file.
    The 5 s answer cache, the 1 s busy-fallback cache and the 2 s stuck-probe-lock marker are
    off (the marker's TTL is zero, as the two cache TTLs are, so no earlier test sees it);
    tests that exercise them turn them back on.
    261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3"""
    folder = codex_home / "thread-writer-locks"
    folder.mkdir(parents=True)
    (folder / ".coordination.lock").write_bytes(b"")
    monkeypatch.setattr(data_codex, "_LOCK_TTL", 0.0)
    monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", 0.0)
    monkeypatch.setattr(data_codex, "_LOCK_STUCK_TTL", 0.0)
    monkeypatch.setattr(data_codex, "_probe_stuck_until", 0.0)
    monkeypatch.setattr(data_codex, "_STATE_TTL", 0.0)
    monkeypatch.setattr(data_codex, "_STATE_BUSY_TTL", 0.0)
    events = []
    real_open, real_lock, real_unlock = data_codex.open_shared, data_codex._lock_byte, data_codex._unlock_byte

    def tracked_open(path, mode="rb"):
        name = os.path.basename(os.fspath(path))
        fh = real_open(path, mode)
        if not name.endswith(".lock"):
            return fh
        events.append(("open", name))
        return _Tracked(fh, name, events)

    def tracked_lock(fh):
        events.append(("lock", fh._name))
        return real_lock(fh)

    def tracked_unlock(fh):
        events.append(("unlock", fh._name))
        return real_unlock(fh)

    monkeypatch.setattr(data_codex, "open_shared", tracked_open)
    monkeypatch.setattr(data_codex, "_lock_byte", tracked_lock)
    monkeypatch.setattr(data_codex, "_unlock_byte", tracked_unlock)
    return _LocksDir(folder, events)


def _cx_lockfile(folder, sid):
    path = folder / f"{sid}.lock"
    path.write_bytes(b"")
    return path


# The shipped values, read when this module is imported (before any fixture patches them).
_CX_LOCK_TTL = data_codex._LOCK_TTL
_CX_LOCK_BUSY_TTL = data_codex._LOCK_BUSY_TTL
_CX_LOCK_PROBE_WAIT = data_codex._LOCK_PROBE_WAIT
_CX_LOCK_STUCK_TTL = data_codex._LOCK_STUCK_TTL
_CX_STATE_TTL = data_codex._STATE_TTL
_CX_STATE_BUSY_TTL = data_codex._STATE_BUSY_TTL
_CX_STATE_STALE_MAX = data_codex._STATE_STALE_MAX


def _cx_fake_posix(monkeypatch, script, calls):
    """Drive the fcntl branch through a fake module: one scripted outcome (None or an
    exception) per flock call, in order; `calls` records the operation of each."""
    class FakeFcntl:
        LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

        @staticmethod
        def flock(fd, op):
            calls.append(op)
            outcome = script.pop(0)
            if outcome is not None:
                raise outcome

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "fcntl", FakeFcntl)


class TestCodexWriterLock:
    SID = _cx_id(31)

    def test_an_absent_lock_file_is_false_and_nothing_is_opened(self, locks):
        assert data_codex.session_writer_locked(self.SID) is False
        assert locks.events == []

    @pytest.mark.parametrize("bad", [
        "", "abc", "../" + _cx_id(31), _cx_id(31) + "\n", _cx_id(31) + ".lock",
        _cx_id(31)[:-1], "g" + _cx_id(31)[1:], None, 7, b"x"])
    def test_an_invalid_id_is_false_without_touching_a_file(self, locks, bad):
        for name in ("abc", _cx_id(31)[:-1]):
            (locks / f"{name}.lock").write_bytes(b"")
        assert data_codex.session_writer_locked(bad) is False
        assert locks.events == []

    def test_a_present_unlocked_file_is_false_and_the_locks_nest_correctly(self, locks):
        _cx_lockfile(locks, self.SID)
        thread = f"{self.SID}.lock"
        assert data_codex.session_writer_locked(self.SID) is False
        assert locks.events == [
            ("open", ".coordination.lock"), ("lock", ".coordination.lock"),
            ("open", thread), ("lock", thread), ("unlock", thread), ("close", thread),
            ("unlock", ".coordination.lock"), ("close", ".coordination.lock")]
        assert (locks / thread).exists() and (locks / ".coordination.lock").exists()

    @_needs_os_locks
    def test_the_probe_leaves_both_files_lockable_by_another_process(self, locks):
        path = _cx_lockfile(locks, self.SID)
        assert data_codex.session_writer_locked(self.SID) is False
        with _cx_hold(locks / ".coordination.lock"), _cx_hold(path):
            pass

    @_needs_os_locks
    def test_held_by_another_process_is_true_and_false_once_it_is_gone(self, locks):
        path = _cx_lockfile(locks, self.SID)
        with _cx_hold(path):
            assert data_codex.session_writer_locked(self.SID) is True
        assert path.exists(), "a killed holder leaves its file behind"
        # The OS releases a dead process's locks a moment after it exits (an earlier
        # measurement waited 0.3 s), so allow for that, then the stale file must read as not held.
        deadline = time.monotonic() + 10
        while data_codex.session_writer_locked(self.SID) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert data_codex.session_writer_locked(self.SID) is False, "a stale file is not a held lock"

    @_needs_os_locks
    def test_only_the_held_thread_reads_locked(self, locks):
        mine, other = _cx_id(41), _cx_id(42)
        a, b = _cx_lockfile(locks, mine), _cx_lockfile(locks, other)
        with _cx_hold(a):
            assert data_codex.session_writer_locked(mine) is True
            assert data_codex.session_writer_locked(other) is False
        with _cx_hold(b):
            assert data_codex.session_writer_locked(mine) is False
            assert data_codex.session_writer_locked(other) is True

    def test_an_upper_case_id_is_not_a_session_id_here(self, locks):
        """Session rows carry the lower-case id (data.SESSION_ID_RE is lower-case only)."""
        _cx_lockfile(locks, self.SID)
        assert data_codex.session_writer_locked(self.SID.upper()) is False
        assert locks.events == []

    def test_a_missing_coordination_file_is_false_and_is_not_created(self, locks):
        (locks / ".coordination.lock").unlink()
        _cx_lockfile(locks, self.SID)
        assert data_codex.session_writer_locked(self.SID) is False
        assert not (locks / ".coordination.lock").exists()
        assert ("open", f"{self.SID}.lock") not in locks.events

    def test_a_lock_file_that_vanishes_between_the_stat_and_the_open_is_false(self, locks, monkeypatch):
        path = _cx_lockfile(locks, self.SID)
        real = data_codex.open_shared

        def vanishing(p, mode="rb"):
            if os.path.basename(os.fspath(p)) == path.name:
                os.remove(path)
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", vanishing)
        assert data_codex.session_writer_locked(self.SID) is False
        assert locks.events[-2:] == [("unlock", ".coordination.lock"), ("close", ".coordination.lock")]

    @pytest.mark.parametrize("which,kind", [("coordination", "session_writer_locked"), ("thread", "writer_lock.open")])
    def test_an_oserror_on_open_is_false_and_the_coordination_lock_is_released(
            self, locks, monkeypatch, which, kind, caplog):
        """Review fix R3 (re-review of 56b44f0): a failed open of <id>.lock is queued and logged
        as writer_lock.open, no longer as an escaped exception, so its False is cached."""
        _cx_lockfile(locks, self.SID)
        real = data_codex.open_shared
        target = ".coordination.lock" if which == "coordination" else f"{self.SID}.lock"

        def failing(p, mode="rb"):
            if os.path.basename(os.fspath(p)) == target:
                raise PermissionError(errno.EACCES, "denied", os.fspath(p))
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", failing)
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.session_writer_locked(self.SID) is False
        assert any(kind in r.getMessage() for r in caplog.records)
        if which == "thread":
            assert locks.events[-2:] == [("unlock", ".coordination.lock"), ("close", ".coordination.lock")]

    def test_repeated_calls_within_five_seconds_probe_once(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        results = [data_codex.session_writer_locked(self.SID) for _ in range(4)]
        assert results == [False] * 4
        assert locks.events.count(("open", f"{self.SID}.lock")) == 1

    def test_the_answer_is_probed_again_once_it_is_older_than_five_seconds(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 5.5, True))
        assert data_codex.session_writer_locked(self.SID) is False, "an expired answer is not served"
        assert locks.events.count(("open", f"{self.SID}.lock")) == 1
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 4.0, True))
        assert data_codex.session_writer_locked(self.SID) is True, "a fresh answer is served as it was"
        assert locks.events.count(("open", f"{self.SID}.lock")) == 1

    def test_a_fresh_answer_is_served_without_taking_the_process_wide_lock(self, locks, monkeypatch):
        """Rows of a listing ask for their thread's lock one after another: a cache hit must
        not queue behind a probe that is in flight for another thread."""
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic(), True))
        got = []
        with data_codex._probe_lock:        # another thread is mid-probe
            worker = threading.Thread(target=lambda: got.append(data_codex.session_writer_locked(self.SID)))
            worker.start()
            worker.join(timeout=10)
            assert not worker.is_alive(), "a fresh cached answer waited for the probe lock"
        assert got == [True] and locks.events == []

    def test_a_deleted_lock_file_is_false_at_once_whatever_the_cache_says(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        data_codex._lock_cache.put(self.SID, (time.monotonic(), True))
        assert data_codex.session_writer_locked(self.SID) is False

    @_needs_os_locks
    def test_a_busy_coordination_lock_retries_three_times_and_never_opens_the_thread_file(self, locks):
        _cx_lockfile(locks, self.SID)
        sleeps = []
        with _cx_hold(locks / ".coordination.lock"):
            started = time.monotonic()
            with patch.object(data_codex.time, "sleep", lambda s: sleeps.append(s)):
                assert data_codex.session_writer_locked(self.SID) is False
            assert time.monotonic() - started < 5, "a busy coordination lock must not block the caller"
        assert sleeps == [0.005] * 3
        assert locks.events.count(("open", ".coordination.lock")) == 4, "one try and three retries"
        assert not [e for e in locks.events if e[1] == f"{self.SID}.lock"], (
            "the thread file must never be opened without the coordination lock: "
            "that is the plain try-lock that made Codex's own acquire fail")
        assert ("close", ".coordination.lock") in locks.events

    @_needs_os_locks
    def test_a_busy_coordination_lock_returns_the_last_cached_answer_even_expired(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 600, True))
        with _cx_hold(locks / ".coordination.lock"), patch.object(data_codex.time, "sleep", lambda s: None):
            assert data_codex.session_writer_locked(self.SID) is True
        assert not [e for e in locks.events if e[1] == f"{self.SID}.lock"]

    @_needs_os_locks
    def test_two_threads_with_a_busy_coordination_file_each_stay_inside_one_retry_budget(self, locks):
        ids = (_cx_id(51), _cx_id(52))
        for sid in ids:
            _cx_lockfile(locks, sid)
        sleeps, violations, results = [], [], {}

        def fake_sleep(seconds):
            sleeps.append(seconds)
            if data_codex._probe_lock.locked():
                violations.append("slept while holding the process-wide probe lock")

        def run(sid):
            results[sid] = data_codex.session_writer_locked(sid)

        with _cx_hold(locks / ".coordination.lock"), patch.object(data_codex.time, "sleep", fake_sleep):
            threads = [threading.Thread(target=run, args=(s,)) for s in ids]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            assert not any(t.is_alive() for t in threads)
        assert results == {ids[0]: False, ids[1]: False}
        assert sleeps == [0.005] * 6 and violations == []

    def test_a_second_probe_waits_for_the_first_in_this_process(self, locks, monkeypatch):
        """Two handles of one process contend for the same file, so the probe is
        serialised process-wide: a thread starting while another probes must not open
        the coordination file until that probe is done."""
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 30.0)  # this test waits on purpose
        ids = (_cx_id(61), _cx_id(62))
        for sid in ids:
            _cx_lockfile(locks, sid)
        real = data_codex.open_shared
        opened = []
        gate = threading.Event()

        def gated(p, mode="rb"):
            if os.path.basename(os.fspath(p)) == ".coordination.lock":
                opened.append(threading.current_thread().name)
                if len(opened) == 1:
                    assert gate.wait(30)
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", gated)
        first = threading.Thread(target=data_codex.session_writer_locked, args=(ids[0],), name="first")
        second = threading.Thread(target=data_codex.session_writer_locked, args=(ids[1],), name="second")
        first.start()
        deadline = time.monotonic() + 10
        while not opened and time.monotonic() < deadline:
            time.sleep(0.01)
        second.start()
        time.sleep(0.3)
        assert opened == ["first"], "the second probe started while the first was still running"
        gate.set()
        first.join(timeout=30)
        second.join(timeout=30)
        assert opened == ["first", "second"]

    def test_two_threads_on_one_id_never_report_a_phantom_lock(self, locks):
        _cx_lockfile(locks, self.SID)
        seen = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait(10)
            seen.extend(data_codex.session_writer_locked(self.SID) for _ in range(150))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert len(seen) == 300 and set(seen) == {False}

    @_needs_os_locks
    def test_two_threads_on_one_held_id_both_read_locked(self, locks):
        path = _cx_lockfile(locks, self.SID)
        seen = []
        barrier = threading.Barrier(2)

        def run():
            barrier.wait(10)
            seen.extend(data_codex.session_writer_locked(self.SID) for _ in range(60))

        with _cx_hold(path):
            threads = [threading.Thread(target=run) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        assert len(seen) == 120 and set(seen) == {True}

    def test_an_exception_while_probing_releases_both_locks_and_closes_both_handles(self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        real = data_codex._lock_byte

        def boom_on_the_thread_file(fh):
            if fh._name == f"{self.SID}.lock":
                raise RuntimeError("not an OSError")
            return real(fh)

        monkeypatch.setattr(data_codex, "_lock_byte", boom_on_the_thread_file)
        assert data_codex.session_writer_locked(self.SID) is False
        assert locks.events[-3:] == [("close", f"{self.SID}.lock"),
                                     ("unlock", ".coordination.lock"), ("close", ".coordination.lock")]
        assert not data_codex._probe_lock.locked()
        monkeypatch.setattr(data_codex, "_lock_byte", real)
        assert data_codex.session_writer_locked(self.SID) is False, "the next probe works"

    @_needs_os_locks
    def test_after_an_exception_the_next_probe_still_sees_a_held_lock(self, locks, monkeypatch):
        path = _cx_lockfile(locks, self.SID)
        real = data_codex._lock_byte

        def boom_on_the_thread_file(fh):
            if fh._name != ".coordination.lock":
                raise RuntimeError("not an OSError")
            return real(fh)

        monkeypatch.setattr(data_codex, "_lock_byte", boom_on_the_thread_file)
        assert data_codex.session_writer_locked(self.SID) is False
        monkeypatch.setattr(data_codex, "_lock_byte", real)
        with _cx_hold(path):
            assert data_codex.session_writer_locked(self.SID) is True
        with _cx_hold(locks / ".coordination.lock"):
            pass

    def test_no_handle_is_left_on_either_lock_file(self, locks):
        thread_lock = _cx_lockfile(locks, self.SID)
        coord = locks / ".coordination.lock"
        assert data_codex.session_writer_locked(self.SID) is False
        for target in (thread_lock, coord):
            other = locks / "replacement.tmp"
            other.write_bytes(b"x")
            os.replace(other, target)           # fails on Windows while any handle on `target` is open
        opened = {e[1] for e in locks.events if e[0] == "open"}
        assert opened == {".coordination.lock", f"{self.SID}.lock"}
        assert {e for e in locks.events if e[0] == "close"} == {("close", n) for n in opened}

    @pytest.mark.skipif(sys.platform != "win32", reason="msvcrt.locking")
    def test_windows_lock_errors_are_classified(self, locks, monkeypatch, caplog):
        import msvcrt
        _cx_lockfile(locks, self.SID)
        real = msvcrt.locking

        def scripted(code):
            state = {"n": 0}

            def locking(fd, mode, nbytes):
                if mode == msvcrt.LK_NBLCK:
                    state["n"] += 1
                    if state["n"] == 2:                 # the second try-lock is the thread file's
                        raise OSError(code, "scripted")
                return real(fd, mode, nbytes)
            return locking

        for code, expected in ((errno.EACCES, True), (errno.EDEADLOCK, True), (errno.EBADF, False),
                               (errno.EINVAL, False)):
            monkeypatch.setattr(msvcrt, "locking", scripted(code))
            with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
                assert data_codex.session_writer_locked(self.SID) is expected, errno.errorcode[code]
        assert len([r for r in caplog.records if "writer_lock.lock" in r.getMessage()]) == 1, "logged once"

    @_needs_os_locks
    def test_an_acquirer_replaying_codexs_acquire_never_fails_against_a_probing_thread(self, locks):
        """The property D17 exists for: the probe never makes Codex's own acquisition (or
        its removal of the lock file) fail. A plain try-lock on the thread file, with no
        coordination lock, made this exact loop fail in 2.4-4.1 % of cycles when measured."""
        probes = [0]
        real = data_codex._probe_writer_lock

        def counting(lock_path, coord_path):
            probes[0] += 1
            return real(lock_path, coord_path)

        proc = subprocess.Popen([sys.executable, "-c", _CX_LOCK_HELPER, "acquirer", str(locks), self.SID, str(_CX_ACQUIRE_CYCLES), "0.001"],
                                stdout=subprocess.PIPE, text=True)
        try:
            with patch.object(data_codex, "_probe_writer_lock", counting):
                deadline = time.monotonic() + 240
                while proc.poll() is None and time.monotonic() < deadline:
                    data_codex.session_writer_locked(self.SID)
            out = proc.communicate(timeout=60)[0]
        finally:
            if proc.poll() is None:
                proc.kill()
        report = json.loads(out.strip().splitlines()[-1])
        assert report == {"failures": 0, "remove_failures": 0}
        assert probes[0] >= 50, f"too few probes found a lock file to probe, to mean anything: {probes[0]}"

    def test_the_posix_branch_orders_its_locks_and_maps_contention(self, locks, monkeypatch):
        """The fcntl branch, driven through a fake module on any platform."""
        script = []            # one outcome (None or an exception) per flock call, in order
        calls = []

        class FakeFcntl:
            LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

            @staticmethod
            def flock(fd, op):
                calls.append(op)
                outcome = script.pop(0)
                if outcome is not None:
                    raise outcome

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setitem(sys.modules, "fcntl", FakeFcntl)
        _cx_lockfile(locks, self.SID)
        ex_nb, un = FakeFcntl.LOCK_EX | FakeFcntl.LOCK_NB, FakeFcntl.LOCK_UN

        # free: coordination, thread (acquired), unlock thread, unlock coordination
        script[:] = [None, None, None, None]
        assert data_codex.session_writer_locked(self.SID) is False
        assert calls == [ex_nb, ex_nb, un, un]
        # contention on the thread file: BlockingIOError, or EAGAIN / EACCES
        for contention in (BlockingIOError(errno.EAGAIN, "x"), OSError(errno.EAGAIN, "x"), OSError(errno.EACCES, "x")):
            calls.clear()
            script[:] = [None, contention, None]
            assert data_codex.session_writer_locked(self.SID) is True
            assert calls == [ex_nb, ex_nb, un], "no unlock for a lock never taken; then the coordination lock"
        # any other error is not contention
        for other in (OSError(errno.EBADF, "x"), OSError(errno.ENOTSUP, "x"), OSError(errno.EINVAL, "x")):
            script[:] = [None, other, None]
            assert data_codex.session_writer_locked(self.SID) is False
        # a busy coordination file is retried, and the thread file is never locked
        calls.clear()
        locks.events.clear()
        script[:] = [BlockingIOError(errno.EAGAIN, "x")] * 4
        with patch.object(data_codex.time, "sleep", lambda s: None):
            assert data_codex.session_writer_locked(self.SID) is False
        assert calls == [ex_nb] * 4
        assert not [e for e in locks.events if e[1] == f"{self.SID}.lock"]

    def test_no_platform_lock_module_is_imported_until_a_probe_needs_one(self, tmp_path):
        """D23: a top-level `import msvcrt` or `import fcntl` would break import on the
        other OS. Importing must work with both blocked, and so must a call that never
        reaches a lock."""
        src = Path(data_codex.__file__).resolve().parents[1]
        code = ("import sys, subprocess\n"   # the stdlib's own platform imports come first
                "sys.modules['msvcrt'] = None\n"
                "sys.modules['fcntl'] = None\n"
                "import power_atlas.data_codex as d\n"
                "assert d.session_writer_locked('00000000-1111-4222-8333-000000000000') is False\n"
                "print('ok')\n")
        env = dict(os.environ, PYTHONPATH=str(src), CODEX_HOME=str(tmp_path / "codex-home-subprocess"))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
        assert out.stdout.strip() == "ok", out.stderr[-2000:]

    def test_a_platform_lock_module_that_cannot_be_imported_is_false_not_an_error(self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        monkeypatch.setitem(sys.modules, "msvcrt", None)
        monkeypatch.setitem(sys.modules, "fcntl", None)
        assert data_codex.session_writer_locked(self.SID) is False
        assert locks.events[-1] == ("close", ".coordination.lock")

    # --- Review fixes (P6, P7, P8, P10, P11) -------------------------------------------

    def test_the_answer_cache_lasts_five_seconds(self, locks, monkeypatch):
        assert _CX_LOCK_TTL == 5.0
        monkeypatch.setattr(data_codex, "_LOCK_TTL", _CX_LOCK_TTL)
        _cx_lockfile(locks, self.SID)
        thread = ("open", f"{self.SID}.lock")
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 4.9, True))
        assert data_codex.session_writer_locked(self.SID) is True, "a 4.9 s answer is still served"
        assert thread not in locks.events
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 5.1, True))
        assert data_codex.session_writer_locked(self.SID) is False, "a 5.1 s answer is probed again"
        assert thread in locks.events

    def test_the_answer_cache_holds_512_ids_and_evicts_beyond_that(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", _CX_LOCK_TTL)
        ids = [_cx_id(0x1000 + n) for n in range(513)]
        for sid in ids:
            _cx_lockfile(locks, sid)
        for sid in ids:
            assert data_codex.session_writer_locked(sid) is False
        assert len(data_codex._lock_cache) == 512
        assert data_codex._lock_cache.get(ids[0]) is None, "the oldest id was evicted"
        assert data_codex._lock_cache.get(ids[-1]) is not None

    @_needs_os_locks
    def test_the_busy_fallback_cache_holds_512_ids_and_evicts_beyond_that(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", _CX_LOCK_BUSY_TTL)
        ids = [_cx_id(0x2000 + n) for n in range(513)]
        for sid in ids:
            _cx_lockfile(locks, sid)
        with _cx_hold(locks / ".coordination.lock"), patch.object(data_codex.time, "sleep", lambda s: None):
            for sid in ids:
                assert data_codex.session_writer_locked(sid) is False
        assert len(data_codex._busy_cache) == 512
        assert data_codex._busy_cache.get(ids[0]) is None and data_codex._busy_cache.get(ids[-1]) is not None

    @_needs_os_locks
    def test_a_busy_coordination_lock_is_probed_once_a_second_not_once_a_call(self, locks, monkeypatch):
        """Review fix P6: with the coordination lock busy, every call used to spend the whole
        retry budget (four attempts, three sleeps), so a listing of 20 rows paid it 20 times."""
        assert _CX_LOCK_BUSY_TTL == 1.0
        monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", _CX_LOCK_BUSY_TTL)
        other = _cx_id(32)
        _cx_lockfile(locks, self.SID)
        _cx_lockfile(locks, other)

        def coord_opens():
            return locks.events.count(("open", ".coordination.lock"))

        sleeps = []
        with _cx_hold(locks / ".coordination.lock"), patch.object(data_codex.time, "sleep", sleeps.append):
            assert [data_codex.session_writer_locked(self.SID) for _ in range(5)] == [False] * 5
            assert coord_opens() == 4 and sleeps == [0.005] * 3, "one budget for five calls"
            # the answer is per id: another id pays its own budget
            assert data_codex.session_writer_locked(other) is False
            assert coord_opens() == 8
            # both sides of the one-second edge
            data_codex._busy_cache.put(self.SID, (time.monotonic() - 0.9, False))
            data_codex.session_writer_locked(self.SID)
            assert coord_opens() == 8, "a 0.9 s fallback is still served"
            data_codex._busy_cache.put(self.SID, (time.monotonic() - 1.1, False))
            data_codex.session_writer_locked(self.SID)
            assert coord_opens() == 12, "a 1.1 s fallback is probed again"

    @_needs_os_locks
    def test_the_busy_fallback_is_the_last_known_answer_and_a_later_probe_replaces_it(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", _CX_LOCK_BUSY_TTL)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 600, True))   # long expired
        with _cx_hold(locks / ".coordination.lock"), patch.object(data_codex.time, "sleep", lambda s: None):
            assert data_codex.session_writer_locked(self.SID) is True
            assert data_codex.session_writer_locked(self.SID) is True
        kept = data_codex._lock_cache.get(self.SID)
        assert kept[1] is True and kept[0] < time.monotonic() - 500, (
            "the fallback must not refresh the real answer: it would then be served as fresh for 5 s")
        # the coordination lock is free again and the fallback has aged out: a real probe wins
        data_codex._busy_cache.put(self.SID, (time.monotonic() - 1.5, True))
        assert data_codex.session_writer_locked(self.SID) is False
        assert data_codex._lock_cache.get(self.SID)[1] is False

    def test_a_probe_lock_not_free_in_time_takes_the_fallback_and_logs_once(self, locks, monkeypatch, caplog):
        """Review fix P11: _probe_lock is held across an open and the lock calls, which can
        block on antivirus or a network or OneDrive home. A caller now waits at most
        _LOCK_PROBE_WAIT for it, then returns the last known answer (else False)."""
        assert _CX_LOCK_PROBE_WAIT == 0.5
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 0.3)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 600, True))   # expired: the stale answer

        def hold(seconds):
            held = threading.Event()

            def run():
                with data_codex._probe_lock:
                    held.set()
                    time.sleep(seconds)

            worker = threading.Thread(target=run)
            worker.start()
            assert held.wait(10)
            return worker

        worker = hold(1.2)
        started = time.monotonic()
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            got = data_codex.session_writer_locked(self.SID)
        elapsed = time.monotonic() - started
        assert got is True, "the stale answer, not a wait for the probe lock"
        assert 0.25 <= elapsed < 0.9, elapsed
        assert not [e for e in locks.events if e[1] == f"{self.SID}.lock"], "nothing was probed"
        # a second call is not made to wait again: the fallback was cached for a second
        monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", _CX_LOCK_BUSY_TTL)
        started = time.monotonic()
        assert data_codex.session_writer_locked(self.SID) is True
        assert time.monotonic() - started < 0.15
        monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", 0.0)
        worker.join(timeout=10)
        # logged once however many callers time out
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 0.05)
        worker = hold(0.6)
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            for _ in range(3):
                assert data_codex.session_writer_locked(self.SID) is True
        worker.join(timeout=10)
        waits = [r for r in caplog.records if "writer_lock.wait" in r.getMessage()]
        assert len(waits) == 1
        assert self.SID not in waits[0].getMessage(), "no id in the log line"

    def test_a_probe_lock_freed_inside_the_wait_is_taken_and_the_probe_runs(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 1.5)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 600, True))
        held = threading.Event()

        def run():
            with data_codex._probe_lock:
                held.set()
                time.sleep(0.2)

        worker = threading.Thread(target=run)
        worker.start()
        assert held.wait(10)
        started = time.monotonic()
        assert data_codex.session_writer_locked(self.SID) is False, "the real probe, not the stale True"
        assert 0.1 <= time.monotonic() - started < 1.4
        worker.join(timeout=10)
        assert ("open", f"{self.SID}.lock") in locks.events

    @pytest.mark.parametrize("script,expected,kind", [
        ([None, None, OSError(errno.EIO, "x"), None], False, "writer_lock.unlock"),            # thread unlock fails
        ([None, BlockingIOError(errno.EAGAIN, "x"), OSError(errno.EIO, "x")], True, "writer_lock.unlock"),  # coordination unlock fails
        ([None, OSError(errno.ENOTSUP, "x"), None], False, "writer_lock.lock"),                # thread lock fails
    ])
    def test_a_failure_under_the_coordination_lock_is_logged_after_it_is_released(
            self, locks, monkeypatch, script, expected, kind):
        """Review fix P7: D17 allows only open, try-lock, unlock and close while the
        machine-wide coordination lock is held, and logging is I/O."""
        _cx_lockfile(locks, self.SID)
        _cx_fake_posix(monkeypatch, list(script), [])
        seen = []
        monkeypatch.setattr(data_codex, "_warn",
                            lambda k, exc=None, path="", note="": seen.append((k, list(locks.events))))
        assert data_codex.session_writer_locked(self.SID) is expected
        assert [k for k, _ in seen] == [kind]
        events = seen[0][1]
        assert ("unlock", ".coordination.lock") in events
        assert events[-1] == ("close", ".coordination.lock"), "the warning came before the release"

    def test_unlock_byte_returns_its_failure_and_never_logs(self, tmp_path, monkeypatch):
        """It runs under the coordination lock, where logging is not allowed (review fix P7)."""
        target = tmp_path / "x.lock"
        target.write_bytes(b"")
        failure = OSError(errno.EIO, "x")
        _cx_fake_posix(monkeypatch, [failure, None], [])
        warned = []
        monkeypatch.setattr(data_codex, "_warn", lambda *a, **k: warned.append(a))
        with open(target, "rb") as fh:
            assert data_codex._unlock_byte(fh) is failure
            assert data_codex._unlock_byte(fh) is None
        assert warned == []

    @_needs_os_locks
    @pytest.mark.parametrize("target,how", [
        ("coordination", "returns"), ("coordination", "raises"), ("thread", "returns"), ("thread", "raises")])
    def test_a_failing_unlock_still_returns_the_probe_result_and_frees_both_locks(
            self, locks, monkeypatch, target, how):
        """Review fix P8: closing a handle releases its lock, so an unlock that fails (or
        raises) changes nothing about the answer. A held thread must still read True."""
        path = _cx_lockfile(locks, self.SID)
        name = ".coordination.lock" if target == "coordination" else f"{self.SID}.lock"
        real = data_codex._unlock_byte
        warned = []
        monkeypatch.setattr(data_codex, "_warn", lambda k, exc=None, path="", note="": warned.append(k))

        def failing(fh):
            if fh._name == name:
                if how == "raises":
                    raise RuntimeError("boom")
                return OSError(errno.EIO, "boom")
            return real(fh)

        monkeypatch.setattr(data_codex, "_unlock_byte", failing)
        if target == "coordination":           # the thread lock is held, so only the coordination unlock runs
            with _cx_hold(path):
                assert data_codex.session_writer_locked(self.SID) is True
        else:                                  # the thread lock is free, so it is taken and its unlock fails
            assert data_codex.session_writer_locked(self.SID) is False
        assert warned == ["writer_lock.unlock"]
        assert locks.events[-1] == ("close", ".coordination.lock")
        assert ("close", f"{self.SID}.lock") in locks.events
        # neither lock is left held by this process
        with _cx_hold(locks / ".coordination.lock"), _cx_hold(path):
            pass
        assert not data_codex._probe_lock.locked()

    @_needs_os_locks
    def test_the_byte_lock_is_taken_and_released_at_offset_zero_whatever_the_handle_position(self, tmp_path):
        """Review fix P8. A whole-file holder cannot tell offsets apart (it conflicts with
        every one), so a second process asks about byte 0 alone."""
        path = tmp_path / "offset.bin"
        path.write_bytes(b"0123456789")
        with data_codex.open_shared(path, "rb") as fh:
            os.lseek(fh.fileno(), 5, 0)
            assert data_codex._lock_byte(fh) is True
            assert _cx_try_byte0(path) == "busy", "the lock must sit at offset 0, not where the handle was"
            os.lseek(fh.fileno(), 7, 0)
            assert data_codex._unlock_byte(fh) is None
            assert _cx_try_byte0(path) == "free", "the unlock must release offset 0, not where the handle was"

    @_needs_os_locks
    def test_a_read_only_lock_file_still_reads_held(self, locks):
        """Review fix P10: the lock files are opened read-only, so a read-only attribute or
        ACL no longer makes the open fail (which read as not held)."""
        thread = _cx_lockfile(locks, self.SID)
        coord = locks / ".coordination.lock"
        try:
            with _cx_hold(thread):
                for target in (thread, coord):
                    os.chmod(target, stat.S_IREAD)
                assert data_codex.session_writer_locked(self.SID) is True
            deadline = time.monotonic() + 10
            while data_codex.session_writer_locked(self.SID) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert data_codex.session_writer_locked(self.SID) is False
            assert ("open", f"{self.SID}.lock") in locks.events
        finally:
            for target in (thread, coord):
                os.chmod(target, stat.S_IREAD | stat.S_IWRITE)

    def test_both_lock_files_are_opened_read_only(self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        modes = []
        real = data_codex.open_shared
        monkeypatch.setattr(data_codex, "open_shared", lambda p, mode="rb": modes.append(mode) or real(p, mode))
        assert data_codex.session_writer_locked(self.SID) is False
        assert modes == ["rb", "rb"]


# 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 3 (D10):
# the tri-state sibling of session_writer_locked.
class TestCodexWriterState:
    SID = _cx_id(61)

    def test_the_shipped_cache_times_are_five_seconds_one_second_and_sixty(self):
        assert (_CX_STATE_TTL, _CX_STATE_BUSY_TTL, _CX_STATE_STALE_MAX) == (5.0, 1.0, 60.0)

    @_needs_os_locks
    def test_held_by_another_process_is_held(self, locks):
        path = _cx_lockfile(locks, self.SID)
        with _cx_hold(path):
            assert data_codex.session_writer_state(self.SID) == "held"

    def test_an_unlocked_file_is_free_because_the_probe_took_the_lock(self, locks):
        _cx_lockfile(locks, self.SID)
        thread = f"{self.SID}.lock"
        assert data_codex.session_writer_state(self.SID) == "free"
        assert locks.events == [
            ("open", ".coordination.lock"), ("lock", ".coordination.lock"),
            ("open", thread), ("lock", thread), ("unlock", thread), ("close", thread),
            ("unlock", ".coordination.lock"), ("close", ".coordination.lock")]

    def test_an_absent_lock_file_beside_a_coordination_file_is_free_without_opening_anything(self, locks):
        assert data_codex.session_writer_state(self.SID) == "free"
        assert locks.events == []

    def test_an_absent_lock_file_and_an_absent_coordination_file_is_unknown(self, locks):
        (locks / ".coordination.lock").unlink()
        assert data_codex.session_writer_state(self.SID) == "unknown"
        assert not (locks / ".coordination.lock").exists(), "the probe never creates it"

    def test_a_present_lock_file_with_no_coordination_file_is_unknown_and_is_not_opened(self, locks):
        (locks / ".coordination.lock").unlink()
        _cx_lockfile(locks, self.SID)
        assert data_codex.session_writer_state(self.SID) == "unknown"
        assert ("open", f"{self.SID}.lock") not in locks.events
        assert not (locks / ".coordination.lock").exists()

    @pytest.mark.parametrize("bad", [
        "", "abc", "../" + _cx_id(61), _cx_id(61) + "\n", _cx_id(61) + ".lock",
        _cx_id(61)[:-1], "g" + _cx_id(61)[1:], _cx_id(61).upper(), None, 7, b"x"])
    def test_an_invalid_id_is_unknown_without_touching_a_file(self, locks, bad):
        for name in ("abc", _cx_id(61)[:-1]):
            (locks / f"{name}.lock").write_bytes(b"")
        assert data_codex.session_writer_state(bad) == "unknown"
        assert locks.events == []

    def test_a_stat_error_other_than_file_not_found_is_unknown(self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        real = os.stat

        def failing(path, *a, **k):
            if os.path.basename(os.fspath(path)) == f"{self.SID}.lock":
                raise PermissionError(errno.EACCES, "denied", os.fspath(path))
            return real(path, *a, **k)

        monkeypatch.setattr(data_codex.os, "stat", failing)
        assert data_codex.session_writer_state(self.SID) == "unknown"
        assert locks.events == []

    def test_an_oserror_opening_the_thread_file_is_unknown_and_the_coordination_lock_is_released(
            self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        real = data_codex.open_shared

        def failing(p, mode="rb"):
            if os.path.basename(os.fspath(p)) == f"{self.SID}.lock":
                raise PermissionError(errno.EACCES, "denied", os.fspath(p))
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", failing)
        assert data_codex.session_writer_state(self.SID) == "unknown"
        assert locks.events[-2:] == [("unlock", ".coordination.lock"), ("close", ".coordination.lock")]

    def test_an_oserror_locking_the_thread_file_is_unknown(self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        real = data_codex._lock_byte

        def failing(fh):
            if fh._name == f"{self.SID}.lock":
                raise OSError(errno.EBADF, "bad descriptor")
            return real(fh)

        monkeypatch.setattr(data_codex, "_lock_byte", failing)
        assert data_codex.session_writer_state(self.SID) == "unknown"

    def test_a_lock_file_that_vanishes_between_the_stat_and_the_open_is_free(self, locks, monkeypatch):
        path = _cx_lockfile(locks, self.SID)
        real = data_codex.open_shared

        def vanishing(p, mode="rb"):
            if os.path.basename(os.fspath(p)) == path.name:
                os.remove(path)
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", vanishing)
        assert data_codex.session_writer_state(self.SID) == "free"

    @_needs_os_locks
    def test_a_busy_coordination_lock_with_a_cold_cache_is_unknown_never_free(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_STATE_BUSY_TTL", 1.0)
        _cx_lockfile(locks, self.SID)
        with _cx_hold(locks / ".coordination.lock"):
            with patch.object(data_codex.time, "sleep", lambda s: None):
                assert data_codex.session_writer_state(self.SID) == "unknown"
                opened = locks.events.count(("open", ".coordination.lock"))
                assert data_codex.session_writer_state(self.SID) == "unknown", "a second call within 1 s"
        assert opened == 4, "one try and three retries"
        assert locks.events.count(("open", ".coordination.lock")) == opened, "the second call came from the 1 s cache"
        assert not [e for e in locks.events if e[1] == f"{self.SID}.lock"]

    @_needs_os_locks
    def test_a_busy_coordination_lock_falls_back_on_a_definitive_answer_younger_than_60_seconds(
            self, locks):
        _cx_lockfile(locks, self.SID)
        with _cx_hold(locks / ".coordination.lock"), patch.object(data_codex.time, "sleep", lambda s: None):
            data_codex._state_cache.put(self.SID, (time.monotonic() - 30.0, "held"))
            assert data_codex.session_writer_state(self.SID) == "held"
            data_codex._state_busy_cache.clear()
            data_codex._state_cache.put(self.SID, (time.monotonic() - 61.0, "held"))
            assert data_codex.session_writer_state(self.SID) == "unknown", "older than 60 s is not trusted"

    def test_a_probe_that_raises_is_unknown(self, locks, monkeypatch):
        _cx_lockfile(locks, self.SID)
        monkeypatch.setattr(data_codex, "_probe_writer_state", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        assert data_codex.session_writer_state(self.SID) == "unknown"

    @_needs_os_locks
    def test_the_old_caches_are_never_read_or_written(self, locks):
        path = _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic(), False))
        data_codex._busy_cache.put(self.SID, (time.monotonic(), False))
        with _cx_hold(path):
            assert data_codex.session_writer_state(self.SID) == "held", "a planted False does not read as free"
        assert data_codex._lock_cache.get(self.SID)[1] is False
        data_codex._lock_cache.clear()
        data_codex._busy_cache.clear()
        data_codex.session_writer_state(self.SID)
        assert data_codex._lock_cache.get(self.SID) is None and data_codex._busy_cache.get(self.SID) is None

    def test_held_and_free_are_reused_for_five_seconds_and_unknown_for_one(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_STATE_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        assert [data_codex.session_writer_state(self.SID) for _ in range(3)] == ["free"] * 3
        assert locks.events.count(("open", f"{self.SID}.lock")) == 1
        data_codex._state_cache.put(self.SID, (time.monotonic() - 5.5, "held"))
        assert data_codex.session_writer_state(self.SID) == "free", "an expired answer is probed again"
        monkeypatch.setattr(data_codex, "_STATE_BUSY_TTL", 1.0)
        data_codex._state_cache.clear()
        other = _cx_id(62)
        (locks / f"{other}.lock").write_bytes(b"")
        real = data_codex.open_shared

        def failing(p, mode="rb"):
            if os.path.basename(os.fspath(p)) == f"{other}.lock":
                raise PermissionError(errno.EACCES, "denied", os.fspath(p))
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", failing)
        before = locks.events.count(("open", ".coordination.lock"))
        assert [data_codex.session_writer_state(other) for _ in range(2)] == ["unknown"] * 2
        assert locks.events.count(("open", ".coordination.lock")) == before + 1, "the second unknown came from the 1 s cache"

    def test_an_unknown_answer_is_not_kept_for_the_five_second_definitive_window(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_STATE_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        real = data_codex.open_shared
        broken = [True]

        def flaky(p, mode="rb"):
            if broken[0] and os.path.basename(os.fspath(p)) == f"{self.SID}.lock":
                raise PermissionError(errno.EACCES, "denied", os.fspath(p))
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", flaky)
        assert data_codex.session_writer_state(self.SID) == "unknown"
        broken[0] = False
        assert data_codex.session_writer_state(self.SID) == "free", "the unknown was not served as if it were definitive"

    def test_the_second_probe_path_adds_at_most_one_probe_per_row_per_five_seconds(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_STATE_TTL", 5.0)
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        _cx_lockfile(locks, self.SID)
        for _ in range(3):
            data_codex.session_writer_locked(self.SID)
            data_codex.session_writer_state(self.SID)
        assert locks.events.count(("open", f"{self.SID}.lock")) == 2, "one per path"

    def test_session_writer_locked_is_unchanged(self, locks):
        """The Resume gate: a busy or absent probe still reads False, as before."""
        (locks / ".coordination.lock").unlink()
        _cx_lockfile(locks, self.SID)
        assert data_codex.session_writer_locked(self.SID) is False
        assert data_codex.session_writer_state(self.SID) == "unknown"


class _CountingLock:
    """Stands in for data_codex._probe_lock: records every acquire as (blocking, timeout) and
    delegates to a real lock, which a test holds from another thread to play a hung probe.
    `on_timed` runs when a timed wait begins (another thread acting during the wait)."""

    def __init__(self, on_timed=None):
        self.inner = threading.Lock()
        self.calls = []
        self.on_timed = on_timed

    def acquire(self, blocking=True, timeout=-1):
        self.calls.append((blocking, timeout))
        if blocking and timeout > 0 and self.on_timed is not None:
            self.on_timed()
        return self.inner.acquire(blocking, timeout)

    def release(self):
        self.inner.release()

    def locked(self):
        return self.inner.locked()

    def timed_waits(self):
        return [c for c in self.calls if c[0] and c[1] > 0]


@contextlib.contextmanager
def _cx_hung_holder(lock):
    """Another thread holding `lock.inner` (a probe stuck in an open or a lock call)."""
    held, release = threading.Event(), threading.Event()

    def run():
        with lock.inner:
            held.set()
            release.wait(60)

    worker = threading.Thread(target=run)
    worker.start()
    assert held.wait(10)
    try:
        yield
    finally:
        release.set()
        worker.join(timeout=10)


class TestCodexWriterLockStuckProbe:
    """Re-review fixes R1-R6 of 56b44f0, 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3."""
    SID = _cx_id(71)

    @pytest.fixture
    def counted(self, locks, monkeypatch):
        lock = _CountingLock()
        monkeypatch.setattr(data_codex, "_probe_lock", lock)
        return lock

    def _ids(self, locks, n):
        ids = [_cx_id(0x3000 + k) for k in range(n)]
        for sid in ids:
            _cx_lockfile(locks, sid)
        return ids

    # R1 ---------------------------------------------------------------------------

    def test_a_listing_behind_a_hung_probe_lock_waits_once_not_once_per_id(self, locks, counted, monkeypatch):
        """R1: measured on 56b44f0, six uncached ids took 3.0 s and an immediate second pass 3.0 s,
        because the 1 s busy cache is shorter than a listing pass. The property: one timed wait."""
        assert _CX_LOCK_STUCK_TTL == 2.0
        monkeypatch.setattr(data_codex, "_LOCK_STUCK_TTL", _CX_LOCK_STUCK_TTL)
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 0.2)
        ids = self._ids(locks, 6)
        data_codex._lock_cache.put(ids[0], (time.monotonic() - 600, True))   # an expired answer: the fallback
        with _cx_hung_holder(counted):
            for _ in range(2):
                started = time.monotonic()
                got = [data_codex.session_writer_locked(sid) for sid in ids]
                assert got == [True] + [False] * 5, "the last cached value, else False"
                assert time.monotonic() - started < 0.6, "six waits would take 1.2 s"
        assert len(counted.timed_waits()) == 1, counted.calls
        assert not [e for e in locks.events if e[0] == "open"], "nothing was probed"

    def test_the_stuck_marker_ends_at_its_deadline(self, locks, counted, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_STUCK_TTL", _CX_LOCK_STUCK_TTL)
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 0.1)
        _cx_lockfile(locks, self.SID)
        with _cx_hung_holder(counted):
            monkeypatch.setattr(data_codex, "_probe_stuck_until", time.monotonic() + 1.0)
            assert data_codex.session_writer_locked(self.SID) is False
            assert counted.timed_waits() == [], "inside the marker nobody waits"
            assert counted.calls == [(False, -1)], "it only tries"
            monkeypatch.setattr(data_codex, "_probe_stuck_until", time.monotonic() - 0.001)
            data_codex._busy_cache.clear()
            assert data_codex.session_writer_locked(self.SID) is False
            assert len(counted.timed_waits()) == 1, "an expired marker waits once more"
            renewed = data_codex._probe_stuck_until - time.monotonic()
            # A new marker is about one TTL ahead: far above the old one's -0.001 (not renewed), and
            # far enough from both bounds that a 1.5 s stall under load cannot fail it.
            # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, final review B9
            assert _CX_LOCK_STUCK_TTL / 4 < renewed <= _CX_LOCK_STUCK_TTL, "and the timeout sets a new marker"

    def test_a_successful_acquire_clears_the_marker_and_a_failed_try_does_not_extend_it(
            self, locks, counted, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_STUCK_TTL", _CX_LOCK_STUCK_TTL)
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 600, True))
        deadline = time.monotonic() + 1.5
        monkeypatch.setattr(data_codex, "_probe_stuck_until", deadline)
        with _cx_hung_holder(counted):
            assert data_codex.session_writer_locked(self.SID) is True, "the stale answer while stuck"
            assert data_codex._probe_stuck_until == deadline, "a failed try must not push the deadline"
        # the holder is gone, the marker still has 1.5 s to run: the next call must not starve behind it
        data_codex._busy_cache.clear()
        assert data_codex.session_writer_locked(self.SID) is False, "the real probe, not the stale True"
        assert ("open", f"{self.SID}.lock") in locks.events
        assert data_codex._probe_stuck_until == 0.0, "a successful acquire clears the marker"

    def test_the_marker_set_by_one_id_does_not_starve_another_once_it_ends(self, locks, counted, monkeypatch):
        """A timeout met by one id's call must not keep every other id on the fallback past
        the deadline: after it, the first caller waits again and the probe runs when it can."""
        monkeypatch.setattr(data_codex, "_LOCK_STUCK_TTL", 0.3)
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 0.05)
        first, second = self._ids(locks, 2)
        with _cx_hung_holder(counted):
            assert data_codex.session_writer_locked(first) is False
            assert data_codex._probe_stuck_until > time.monotonic()
        # Past the marker's own deadline with a 0.2 s margin (it was 0.05 s over a constant), holder
        # gone; the marker itself must be no longer than its TTL.
        # 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, final review B9
        deadline = data_codex._probe_stuck_until
        assert deadline - time.monotonic() <= 0.3, "the marker is one TTL long, never longer"
        time.sleep(max(0.0, deadline - time.monotonic()) + 0.2)
        assert data_codex.session_writer_locked(second) is False
        assert ("open", f"{second}.lock") in locks.events, "the other id was probed, not starved"

    # R2 ---------------------------------------------------------------------------

    @pytest.mark.parametrize("script,expected,raising,attempted", [
        # held thread: only the coordination unlock fails; its log raises, the answer must stay True
        ([None, BlockingIOError(errno.EAGAIN, "x"), OSError(errno.EIO, "x")], True,
         "writer_lock.unlock", ["writer_lock.unlock"]),
        # two queued items, the first log raises: the second is still attempted, in order
        ([None, OSError(errno.ENOTSUP, "x"), OSError(errno.EIO, "x")], False,
         "writer_lock.lock", ["writer_lock.lock", "writer_lock.unlock"]),
        # the same two, the second raises: the first was logged, the result is unchanged
        ([None, OSError(errno.ENOTSUP, "x"), OSError(errno.EIO, "x")], False,
         "writer_lock.unlock", ["writer_lock.lock", "writer_lock.unlock"]),
    ])
    def test_a_raising_log_neither_changes_the_probe_result_nor_drops_the_other_items(
            self, locks, monkeypatch, script, expected, raising, attempted):
        _cx_lockfile(locks, self.SID)
        _cx_fake_posix(monkeypatch, list(script), [])
        seen = []

        def warn(kind, exc=None, path="", note=""):
            seen.append(kind)
            if kind == raising:
                raise RuntimeError("the log itself failed")

        monkeypatch.setattr(data_codex, "_warn", warn)
        assert data_codex.session_writer_locked(self.SID) is expected
        assert seen == attempted, "every queued item is attempted, and nothing escapes to the caller's wrapper"

    # R3 ---------------------------------------------------------------------------

    def test_a_failed_open_of_the_thread_file_is_cached_and_logged_once_across_ids(
            self, locks, monkeypatch, caplog):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        first, second = self._ids(locks, 2)
        real = data_codex.open_shared

        def failing(p, mode="rb"):
            if os.path.basename(os.fspath(p)).endswith(".lock") and not os.fspath(p).endswith(".coordination.lock"):
                raise PermissionError(errno.EACCES, "denied", os.fspath(p))
            return real(p, mode)

        monkeypatch.setattr(data_codex, "open_shared", failing)
        coord = ("open", ".coordination.lock")
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.session_writer_locked(first) is False
            assert locks.events.count(coord) == 1
            assert data_codex.session_writer_locked(first) is False
            assert locks.events.count(coord) == 1, "the False was cached: no second trip through the coordination lock"
            assert data_codex.session_writer_locked(second) is False
            assert locks.events.count(coord) == 2, "another id has its own answer"
        opens = [r for r in caplog.records if "writer_lock.open" in r.getMessage()]
        assert len(opens) == 1, "throttled to one per minute"
        assert "path=" in opens[0].getMessage()
        assert not [r for r in caplog.records if "session_writer_locked" in r.getMessage()]

    def test_a_directory_at_the_lock_path_is_false_and_not_probed_again_within_five_seconds(self, locks, monkeypatch):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", 5.0)
        (locks / f"{self.SID}.lock").mkdir()
        assert data_codex.session_writer_locked(self.SID) is False
        assert data_codex.session_writer_locked(self.SID) is False
        assert locks.events.count(("open", ".coordination.lock")) == 1

    # R4 ---------------------------------------------------------------------------

    @pytest.mark.parametrize("old,newer", [(False, True), (True, False)])
    def test_the_fallback_serves_an_answer_another_thread_stored_while_it_waited(
            self, locks, monkeypatch, old, newer):
        _cx_lockfile(locks, self.SID)
        data_codex._lock_cache.put(self.SID, (time.monotonic() - 600, old))

        def other_thread_stores_a_fresh_answer():
            data_codex._lock_cache.put(self.SID, (time.monotonic(), newer))

        lock = _CountingLock(on_timed=other_thread_stores_a_fresh_answer)
        monkeypatch.setattr(data_codex, "_probe_lock", lock)
        monkeypatch.setattr(data_codex, "_LOCK_PROBE_WAIT", 0.05)
        with _cx_hung_holder(lock):
            assert data_codex.session_writer_locked(self.SID) is newer
        assert data_codex._busy_cache.get(self.SID)[1] is newer, "the busy cache must not re-cache the old value"

    # R5 ---------------------------------------------------------------------------

    @pytest.mark.parametrize("answer_age,answer,busy_age,busy,expected", [
        (1.0, False, 0.1, True, False),     # a fresh real answer beats a newer busy entry (M14)
        (1.0, True, 0.1, False, True),      # ... both ways round
        (6.0, True, 0.1, False, False),     # an expired answer: the fresh busy entry is served
    ])
    def test_a_fresh_answer_wins_over_a_busy_fallback_entry(
            self, locks, monkeypatch, answer_age, answer, busy_age, busy, expected):
        monkeypatch.setattr(data_codex, "_LOCK_TTL", _CX_LOCK_TTL)
        monkeypatch.setattr(data_codex, "_LOCK_BUSY_TTL", _CX_LOCK_BUSY_TTL)
        _cx_lockfile(locks, self.SID)
        now = time.monotonic()
        data_codex._lock_cache.put(self.SID, (now - answer_age, answer))
        data_codex._busy_cache.put(self.SID, (now - busy_age, busy))
        assert data_codex.session_writer_locked(self.SID) is expected
        assert locks.events == [], "no probe either way"

    def test_failures_queued_before_an_exception_are_still_logged(self, locks, monkeypatch):
        """M15: the queue is drained in a `finally`, so an exception that follows a queued
        failure (here: after the thread unlock failed) does not lose it."""
        _cx_lockfile(locks, self.SID)
        thread = f"{self.SID}.lock"
        tracked_unlock, real_release = data_codex._unlock_byte, data_codex._release
        monkeypatch.setattr(data_codex, "_unlock_byte",
                            lambda fh: OSError(errno.EIO, "x") if fh._name == thread else tracked_unlock(fh))

        def release_then_boom(fh, deferred, path=""):
            real_release(fh, deferred, path)
            if fh._name == thread:
                raise RuntimeError("after the failure was queued")

        monkeypatch.setattr(data_codex, "_release", release_then_boom)
        seen = []
        monkeypatch.setattr(data_codex, "_warn", lambda k, exc=None, path="", note="": seen.append(k))
        assert data_codex.session_writer_locked(self.SID) is False
        assert seen == ["writer_lock.unlock", "session_writer_locked"]

    @pytest.mark.parametrize("script", [
        [None, None, OSError(errno.EIO, "x"), None],        # the thread unlock fails (M25)
        [None, OSError(errno.ENOTSUP, "x"), None],          # the thread lock fails
    ])
    def test_a_queued_thread_file_failure_logs_the_path_of_the_thread_file(self, locks, monkeypatch, script):
        _cx_lockfile(locks, self.SID)
        _cx_fake_posix(monkeypatch, list(script), [])
        seen = []
        monkeypatch.setattr(data_codex, "_warn", lambda k, exc=None, path="", note="": seen.append((k, path)))
        assert data_codex.session_writer_locked(self.SID) is False
        assert len(seen) == 1 and Path(seen[0][1]) == locks / f"{self.SID}.lock", seen

    # R6 ---------------------------------------------------------------------------

    def test_a_coordination_lock_busy_through_the_retry_budget_is_logged_once_a_minute_without_an_id(
            self, locks, monkeypatch, caplog):
        _cx_fake_posix(monkeypatch, [BlockingIOError(errno.EAGAIN, "x")] * 16, [])
        first, second, third = self._ids(locks, 3)

        def busy_logs():
            return [r for r in caplog.records if "writer_lock.busy" in r.getMessage()]

        with caplog.at_level("WARNING", logger="power_atlas.data_codex"), \
                patch.object(data_codex.time, "sleep", lambda s: None):
            assert data_codex.session_writer_locked(first) is False
            assert data_codex.session_writer_locked(second) is False
            assert len(busy_logs()) == 1, "throttled: two ids, one line"
            message = busy_logs()[0].getMessage()
            assert "path=" not in message and first not in message and "writer_lock.wait" not in message
            data_codex._warned["writer_lock.busy"] = time.monotonic() - 59
            assert data_codex.session_writer_locked(third) is False
            assert len(busy_logs()) == 1, "59 s later: still throttled"
            data_codex._warned["writer_lock.busy"] = time.monotonic() - 61
            data_codex._busy_cache.clear()
            assert data_codex.session_writer_locked(third) is False
            assert len(busy_logs()) == 2, "61 s later: logged again"

    def test_a_probe_that_gets_through_logs_nothing(self, locks, caplog):
        _cx_lockfile(locks, self.SID)
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.session_writer_locked(self.SID) is False
        assert not [r for r in caplog.records if "writer_lock" in r.getMessage()]


# --- Codex state database reader (data_codex_state) and quiet_log -----------------
# 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB Phase 1.
# Every database here is built in tmp_path from synthetic rows; no real thread, path
# or id belongs in this file (the repository is public).

_ST_NOW = time.time()
_ST_START = _ST_NOW - 14 * 86400
_ST_SPAWN = '{"subagent":{"thread_spawn":{"parent_thread_id":"%s","depth":1,"agent_nickname":"%s"}}}' % (
    _cx_id(900), "n" * 70)
_ST_GUARDIAN = '{"subagent":{"other":"guardian"}}'
_ST_COLUMNS = ("source TEXT, cwd TEXT, tokens_used INTEGER, created_at_ms INTEGER, "
               "archived INTEGER, updated_at_ms INTEGER")


def _st_row(source=_ST_SPAWN, cwd="C:\\ws", tokens=100, created=None, archived=0, updated=None):
    return (source, cwd, tokens, int((_ST_NOW - 86400) * 1000) if created is None else created,
            archived, int(_ST_NOW * 1000) if updated is None else updated)


def _st_db(home, rows, *, name="state_5.sqlite", columns=_ST_COLUMNS, wal=True, live=False):
    """A state database with a `threads` table. `wal` leaves empty -wal and -shm files beside it
    (Codex running); `live=True` keeps a real WAL writer open and returns it too."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / name
    con = sqlite3.connect(path)
    if live:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(f"CREATE TABLE threads ({columns})")
    names = [c.split()[0] for c in columns.split(",")]
    con.executemany(f"INSERT INTO threads ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                    rows)
    con.commit()
    if live:
        return path, con
    con.close()
    if wal:
        Path(str(path) + "-wal").write_bytes(b"")
        Path(str(path) + "-shm").write_bytes(b"")
    return path, None


def _st_iso(offset_s):
    return datetime.fromtimestamp(_ST_NOW + offset_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _st_fresh(home, *, mtime=None, name=None):
    """One top-level rollout whose activity is `_ST_NOW`, so the freshness guard can pass."""
    return _cx_write(home, _cx_id(1), "C:\\ws", mtime=_ST_NOW if mtime is None else mtime, name=name)


def _st_usage(shown=None, hidden=None, stop_event=None):
    return data_codex_state.subagent_usage(
        _ST_START, shown or (lambda p: True), hidden or (lambda c: False), stop_event)


class TestCodexStateReader:
    def test_kinds_are_split_from_a_long_prefix_and_a_short_one(self, codex_home):
        _st_db(codex_home, [_st_row(_ST_SPAWN, tokens=100), _st_row(_ST_GUARDIAN, tokens=20),
                            _st_row('{"subagent":"memory"}', tokens=3)])
        _st_fresh(codex_home)
        usage, status = _st_usage()
        assert status == "ok"
        assert usage == {"threads": 3, "total": 123, "thread_spawn": 100, "guardian": 20}
        assert len(_ST_SPAWN) > 96, "the fixture must be longer than the cut, like every real thread_spawn source"

    def test_a_source_holding_both_kind_strings_counts_as_thread_spawn(self, codex_home):
        both = '{"subagent":{"thread_spawn":{"a":"guardian"},"other":"guardian"}}'
        _st_db(codex_home, [_st_row(both, tokens=7)])
        _st_fresh(codex_home)
        usage, _ = _st_usage()
        assert usage["thread_spawn"] == 7 and usage["guardian"] == 0

    def test_a_row_that_is_not_a_subagent_or_is_archived_is_left_out(self, codex_home):
        _st_db(codex_home, [_st_row('{"cli":1}'), _st_row(archived=1), _st_row(tokens=5)])
        _st_fresh(codex_home)
        usage, _ = _st_usage()
        assert usage["total"] == 5 and usage["threads"] == 1

    def test_the_window_is_on_created_at_ms(self, codex_home):
        edge = int(_ST_START * 1000)
        _st_db(codex_home, [_st_row(tokens=1, created=edge), _st_row(tokens=10, created=edge - 1),
                            _st_row(tokens=100, created="soon")])
        _st_fresh(codex_home)
        usage, _ = _st_usage()
        assert usage["total"] == 1, "on the boundary counts, a millisecond earlier and a non-integer do not"

    def test_hostile_token_counts_add_nothing_and_the_sum_is_capped(self, codex_home):
        rows = [_st_row(tokens=-5), _st_row(tokens=10 ** 16), _st_row(tokens="many"), _st_row(tokens=4)]
        _st_db(codex_home, rows)
        _st_fresh(codex_home)
        assert _st_usage()[0]["total"] == 4
        data_codex_state.clear_memo()
        _st_db(codex_home, [_st_row(tokens=10 ** 15) for _ in range(10)], name="state_6.sqlite")
        usage, _ = _st_usage()
        assert usage["total"] == 2 ** 53 - 1, "ten rows of 10**15 exceed 2**53 and are capped"

    def test_a_row_with_a_hostile_source_or_a_huge_cwd_is_cut_not_trusted(self, codex_home):
        huge_source = _ST_SPAWN + "x" * 10 ** 6
        _st_db(codex_home, [_st_row(huge_source, tokens=9), _st_row(cwd="C:\\" + "d" * 10 ** 6, tokens=50)])
        _st_fresh(codex_home)
        usage, status = _st_usage()
        assert (status, usage["total"], usage["thread_spawn"]) == ("ok", 9, 9)

    def test_a_cwd_of_260_characters_or_more_is_excluded_including_a_prefixed_hidden_one(
            self, codex_home):
        hidden_257 = "C:\\" + "h" * 254
        assert len(hidden_257) == 257
        rows = [_st_row(cwd="C:\\" + "a" * 257, tokens=1), _st_row(cwd="\\\\?\\" + hidden_257, tokens=2),
                _st_row(cwd=hidden_257, tokens=4)]
        _st_db(codex_home, rows)
        _st_fresh(codex_home)
        seen = []
        usage, _ = _st_usage(hidden=lambda c: seen.append(c) or False)
        assert usage["total"] == 4 and seen == [hidden_257], "the 257-character path is checked, the 260+ ones are not"

    def test_the_extended_prefix_is_stripped_before_the_hidden_check(self, codex_home):
        _st_db(codex_home, [_st_row(cwd="\\\\?\\C:\\secret", tokens=8), _st_row(cwd="C:\\open", tokens=1)])
        _st_fresh(codex_home)
        usage, _ = _st_usage(hidden=lambda c: c == "C:\\secret")
        assert usage["total"] == 1

    def test_a_network_relative_or_missing_cwd_is_excluded_without_a_hidden_check(
            self, codex_home):
        cwds = ["\\\\?\\UNC\\srv\\share\\x", "//srv/share", "\\\\?\\unc\\srv\\share", "ws\\rel", "", None, "   "]
        _st_db(codex_home, [_st_row(cwd=c, tokens=3) for c in cwds] + [_st_row(cwd="C:/fwd", tokens=2)])
        _st_fresh(codex_home)
        seen = []
        usage, _ = _st_usage(hidden=lambda c: seen.append(c) or False)
        assert usage["total"] == 2 and seen == ["C:\\fwd"], "only the drive-letter path reaches the filter"

    def test_a_cwd_on_a_network_or_unknown_drive_is_left_out_without_a_hidden_check(
            self, codex_home, monkeypatch):
        types_by_drive = {"C:\\": 3, "D:\\": 4, "E:\\": 1, "F:\\": 2}
        monkeypatch.setattr(data_codex_state, "_drive_type", lambda root: types_by_drive.get(root, 0))
        _st_db(codex_home, [_st_row(cwd=c, tokens=n) for c, n in
                            (("C:\\a", 1), ("d:\\mapped", 10), ("E:\\gone", 100), ("F:\\usb", 1000), ("G:\\none", 5))])
        _st_fresh(codex_home)
        seen = []
        usage, _ = _st_usage(hidden=lambda c: seen.append(c) or False)
        assert usage["total"] == 1001 and seen == ["C:\\a", "F:\\usb"], seen

    def test_the_real_drive_type_call_answers_for_the_system_drive(self):
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        if sys.platform == "win32":
            assert data_codex_state._drive_type(system_drive) == 3
        else:
            assert data_codex_state._drive_type("C:\\") == 3

    def test_a_non_integer_archived_value_is_left_out(self, codex_home):
        _st_db(codex_home, [_st_row(archived="x", tokens=7), _st_row(archived=None, tokens=9),
                            _st_row(archived=0.5, tokens=11), _st_row(tokens=1)])
        _st_fresh(codex_home)
        assert _st_usage()[0]["total"] == 1

    def test_a_database_over_the_size_cap_is_not_read(self, codex_home, monkeypatch, caplog):
        _st_db(codex_home, [_st_row()])
        _st_fresh(codex_home)
        monkeypatch.setattr(data_codex_state, "_MAX_DB_BYTES", 10)
        monkeypatch.setattr(data_codex_state, "_query", lambda *a: pytest.fail("a database over the cap was opened"))
        with caplog.at_level("WARNING"):
            assert _st_usage() == (None, "error")
        assert [r.getMessage() for r in caplog.records] == ["codex state database is too large to read"]

    def test_the_row_loop_stops_when_asked(self, codex_home):
        _st_db(codex_home, [_st_row(cwd=f"C:\\w{n}", tokens=1) for n in range(600)])
        _st_fresh(codex_home)
        stop = threading.Event()
        seen = []

        def hidden(cwd):
            seen.append(cwd)
            stop.set()
            return False

        assert _st_usage(hidden=hidden, stop_event=stop) == (None, "error")
        assert 0 < len(seen) < 600, "the loop looked at the event again before the end"

    @pytest.mark.skipif(sys.platform != "win32", reason="8.3 short names exist on Windows")
    def test_a_short_8_3_spelling_of_a_hidden_folder_is_excluded_by_the_real_filter(self, codex_home, tmp_path):
        long_dir = tmp_path / "A Rather Long Folder Name"
        long_dir.mkdir()
        buf = ctypes.create_unicode_buffer(520)
        if not ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, 520) or buf.value == str(long_dir):
            pytest.skip("this volume has no 8.3 short names")
        assert "~" in buf.value
        _st_db(codex_home, [_st_row(cwd=buf.value, tokens=40), _st_row(cwd=str(long_dir), tokens=2),
                            _st_row(cwd="C:\\visible", tokens=1)])
        _st_fresh(codex_home)
        key = data_mod._normalize_path(str(long_dir))
        usage, _ = _st_usage(hidden=lambda c: data_mod._normalize_path(c) == key)
        assert usage["total"] == 1, "both spellings of the hidden folder are left out"

    def test_a_unc_cwd_with_a_tilde_never_reaches_the_filesystem_expansion(self, codex_home, monkeypatch):
        calls = []
        monkeypatch.setattr(data_mod, "_expand_short_path", lambda p: calls.append(p) or p)
        _st_db(codex_home, [_st_row(cwd="\\\\srv\\share\\A~1", tokens=3),
                            _st_row(cwd="\\\\?\\UNC\\srv\\share\\B~2", tokens=4), _st_row(tokens=1)])
        _st_fresh(codex_home)
        usage, _ = _st_usage(hidden=lambda c: data_mod._normalize_path(c) == "never")
        assert usage["total"] == 1 and calls == [], calls

    def test_cwd_class_names_each_rejected_shape(self):
        cases = {"C:\\ok": "local", "c:/ok": "local", "\\\\?\\C:\\ok": "local",
                 "\\\\?\\UNC\\s\\x": "network", "\\\\?\\unc\\s\\x": "network", "//s/x": "network",
                 "\\\\.\\pipe\\x": "network", "rel\\x": "relative", "x": "relative",
                 "": "empty", "  ": "empty", None: "empty", 5: "empty", "C:\\" + "a" * 257: "long"}
        for raw, want in cases.items():
            assert data_codex_state.cwd_class(raw) == want, raw
        assert data_codex_state.strip_extended_prefix("\\\\?\\UNC\\s\\x") == "\\\\s\\x"
        assert data_codex_state.strip_extended_prefix("\\\\?\\C:\\x") == "C:\\x"

    def test_a_provider_the_rail_does_not_show_reads_nothing(self, codex_home, monkeypatch):
        _st_db(codex_home, [_st_row()])
        _st_fresh(codex_home)
        monkeypatch.setattr(data_codex_state, "_read", lambda ms: pytest.fail("the database was read"))
        assert _st_usage(shown=lambda p: p != "codex") == (None, "absent")

    # --- opening the file ---------------------------------------------------------

    def test_the_uri_is_read_only_and_never_immutable(self, codex_home, monkeypatch):
        _st_db(codex_home, [_st_row()])
        _st_fresh(codex_home)
        uris = []
        real = sqlite3.connect

        def spy(target, *args, **kwargs):
            uris.append(str(target))
            return real(target, *args, **kwargs)

        monkeypatch.setattr(data_codex_state.sqlite3, "connect", spy)
        assert _st_usage()[1] == "ok"
        assert len(uris) == 1 and "mode=ro" in uris[0] and "immutable" not in uris[0]

    def test_the_connection_is_locked_down_and_the_statements_are_bounded(self, codex_home, monkeypatch):
        _st_db(codex_home, [_st_row()])
        _st_fresh(codex_home)
        executed, kwargs = [], []
        real = sqlite3.connect

        class Spy:
            def __init__(self, con):
                self._con = con

            def execute(self, sql, *args):
                executed.append(sql)
                return self._con.execute(sql, *args)

            def set_progress_handler(self, handler, ops):
                executed.append(("progress", ops))
                return self._con.set_progress_handler(handler, ops)

            def close(self):
                return self._con.close()

        def spy(target, *args, **kw):
            kwargs.append(kw)
            return Spy(real(target, *args, **kw))

        monkeypatch.setattr(data_codex_state.sqlite3, "connect", spy)
        assert _st_usage()[1] == "ok"
        assert "PRAGMA query_only=1" in executed and "PRAGMA trusted_schema=OFF" in executed
        assert ("progress", data_codex_state._PROGRESS_OPS) in executed, "a progress handler bounds the query"
        assert kwargs[0]["timeout"] == 1.0 and kwargs[0]["uri"] is True
        sql = data_codex_state._SUBAGENT_SQL
        assert sql.count("?") == 1 and sql.endswith(f"LIMIT {data_codex_state._ROW_LIMIT + 1}")
        assert "substr(source, 1, 96)" in sql and "substr(cwd, 1, 260)" in sql

    def test_a_folder_with_a_space_hash_and_percent_and_a_relative_folder_both_open(
            self, codex_home, tmp_path, monkeypatch):
        odd = tmp_path / "a b#c%d"
        _st_db(odd, [_st_row(tokens=6)])
        _st_fresh(codex_home)
        monkeypatch.setattr(data_codex_state, "CODEX_STATE_DIR", odd)
        assert _st_usage()[0]["total"] == 6
        data_codex_state.clear_memo()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(data_codex_state, "CODEX_STATE_DIR", Path("a b#c%d"))
        assert _st_usage()[0]["total"] == 6

    def test_a_database_with_no_wal_file_is_not_opened_and_creates_no_file(self, codex_home):
        path, _ = _st_db(codex_home, [_st_row()], wal=False)
        _st_fresh(codex_home)
        before = sorted(p.name for p in codex_home.iterdir())
        assert _st_usage() == (None, "idle")
        assert sorted(p.name for p in codex_home.iterdir()) == before, "no -wal or -shm may appear in Codex's folder"
        assert not Path(str(path) + "-shm").exists()

    def test_a_wal_file_without_a_shm_file_is_idle_and_creates_no_file(self, codex_home):
        path, _ = _st_db(codex_home, [_st_row()])
        Path(str(path) + "-shm").unlink()
        _st_fresh(codex_home)
        before = sorted(p.name for p in codex_home.iterdir())
        assert _st_usage() == (None, "idle")
        assert sorted(p.name for p in codex_home.iterdir()) == before, "opening it would have created the -shm file"

    def test_a_read_leaves_the_folder_and_the_database_file_unchanged(self, codex_home):
        import hashlib
        path, _ = _st_db(codex_home, [_st_row(tokens=5)])
        _st_fresh(codex_home)

        def snapshot():
            return (sorted((p.name, p.stat().st_size) for p in codex_home.iterdir() if p.is_file()),
                    hashlib.sha256(path.read_bytes()).hexdigest())

        before = snapshot()
        assert _st_usage()[0]["total"] == 5
        assert snapshot() == before

    def test_a_directory_with_the_highest_number_is_not_skipped_for_a_lower_file(self, codex_home):
        _st_db(codex_home, [_st_row()], name="state_5.sqlite")
        (codex_home / "state_9.sqlite").mkdir()
        _st_fresh(codex_home)
        assert data_codex_state.state_db_path() is None
        assert _st_usage() == (None, "absent")

    def test_a_row_written_after_the_first_read_shows_on_the_next_read(self, codex_home):
        path, writer = _st_db(codex_home, [_st_row(tokens=1)], live=True)
        try:
            _st_fresh(codex_home)
            assert _st_usage()[0]["total"] == 1
            writer.execute("INSERT INTO threads VALUES (?,?,?,?,?,?)", _st_row(tokens=10))
            writer.commit()
            assert Path(str(path) + "-wal").stat().st_size > 0, "the new row must be only in the WAL"
            data_codex_state.clear_memo()
            assert _st_usage()[0]["total"] == 11, "a read-only open honours the WAL"
        finally:
            writer.close()

    def test_only_the_highest_numbered_state_file_in_the_folder_itself_is_used(self, codex_home):
        _st_db(codex_home, [_st_row(tokens=1)], name="state_5.sqlite")
        _st_db(codex_home, [_st_row(tokens=2)], name="state_7.sqlite")
        _st_db(codex_home / "old", [_st_row(tokens=500)], name="state_99.sqlite")
        for decoy in ("state_8.sqlite.bak", "xstate_9.sqlite", "state_9.sqlite-wal", "state_.sqlite"):
            (codex_home / decoy).write_bytes(b"x")
        _st_fresh(codex_home)
        assert data_codex_state.state_db_path().name == "state_7.sqlite"
        assert _st_usage()[0]["total"] == 2

    def test_a_higher_numbered_file_with_the_wrong_schema_gives_none_with_no_fallback(
            self, codex_home):
        _st_db(codex_home, [_st_row(tokens=1)], name="state_5.sqlite")
        _st_db(codex_home, [("a",)], name="state_999999.sqlite", columns="other TEXT")
        _st_fresh(codex_home)
        assert _st_usage() == (None, "error")

    def test_a_sub_folder_copy_alone_is_not_a_database(self, codex_home):
        _st_db(codex_home / "stale-copy", [_st_row()], name="state_5.sqlite")
        _st_fresh(codex_home)
        assert _st_usage() == (None, "absent") and data_codex_state.state_db_path() is None

    def test_a_missing_file_missing_folder_table_or_column_gives_none(self, codex_home, tmp_path,
                                                                    monkeypatch, caplog):
        _st_fresh(codex_home)
        with caplog.at_level("WARNING"):
            assert _st_usage() == (None, "absent")
            monkeypatch.setattr(data_codex_state, "CODEX_STATE_DIR", tmp_path / "nowhere")
            assert _st_usage() == (None, "absent")
        assert not caplog.records, "an absent database is silent"
        monkeypatch.setattr(data_codex_state, "CODEX_STATE_DIR", codex_home)
        data_codex_state.clear_memo()
        path, _ = _st_db(codex_home, [], columns="a TEXT")
        assert _st_usage() == (None, "error")
        data_codex_state.clear_memo()
        path.unlink()
        _st_db(codex_home, [_st_row()[:5]], columns="source TEXT, cwd TEXT, tokens_used INTEGER, "
                                                     "created_at_ms INTEGER, archived INTEGER")
        caplog.set_level("WARNING")
        caplog.clear()
        quiet_log.reset()
        assert _st_usage() == (None, "error"), "a database lacking updated_at_ms is not used"
        assert [r.getMessage() for r in caplog.records] == ["codex state database has an unexpected schema"], \
            "a schema mismatch is named in the log, not left to a failing statement"

    def test_a_corrupt_file_and_an_invalid_utf8_value_give_none_and_never_raise(self, codex_home):
        _st_fresh(codex_home)
        (codex_home / "state_5.sqlite").write_bytes(b"not a database" * 200)
        Path(str(codex_home / "state_5.sqlite") + "-wal").write_bytes(b"")
        Path(str(codex_home / "state_5.sqlite") + "-shm").write_bytes(b"")
        assert _st_usage() == (None, "error")

    def test_one_row_with_an_undecodable_value_is_left_out_not_the_whole_read(self, codex_home):
        path, _ = _st_db(codex_home, [_st_row(tokens=3), _st_row(tokens=50)])
        _st_fresh(codex_home)
        con = sqlite3.connect(path)
        con.execute("UPDATE threads SET cwd = CAST(x'ffc3' AS TEXT) WHERE tokens_used = 50")
        con.commit()
        con.close()
        Path(str(path) + "-wal").write_bytes(b"")
        Path(str(path) + "-shm").write_bytes(b"")
        usage, status = _st_usage()
        assert status == "ok" and usage["total"] == 3 and usage["threads"] == 1

    def test_a_one_mebibyte_column_that_is_not_selected_does_not_fail_the_query(self, codex_home):
        _st_db(codex_home, [_st_row(tokens=3) + ("t" * (1 << 20),)], columns=_ST_COLUMNS + ", title TEXT")
        _st_fresh(codex_home)
        assert _st_usage()[0]["total"] == 3

    def test_more_rows_than_the_limit_give_none_not_a_partial_total(self, codex_home, monkeypatch):
        monkeypatch.setattr(data_codex_state, "_ROW_LIMIT", 3)
        monkeypatch.setattr(data_codex_state, "_SUBAGENT_SQL",
                            data_codex_state._SUBAGENT_SQL.replace("LIMIT 20001", "LIMIT 4"))
        _st_db(codex_home, [_st_row() for _ in range(5)])
        _st_fresh(codex_home)
        assert _st_usage() == (None, "error")
        data_codex_state.clear_memo()
        con = sqlite3.connect(codex_home / "state_5.sqlite")
        con.execute("DELETE FROM threads WHERE rowid > 3")
        con.commit()
        con.close()
        assert _st_usage()[0]["threads"] == 3, "exactly the limit is a complete result"

    def test_a_locked_database_returns_within_the_timeout(self, codex_home, monkeypatch):
        monkeypatch.setattr(data_codex_state, "_CONNECT_TIMEOUT_S", 0.2)
        path, _ = _st_db(codex_home, [_st_row()])
        _st_fresh(codex_home)
        locker = sqlite3.connect(path, isolation_level=None)
        locker.execute("BEGIN EXCLUSIVE")
        try:
            started = time.monotonic()
            assert _st_usage() == (None, "error")
            assert time.monotonic() - started < 3
        finally:
            locker.close()

    def test_a_query_that_runs_too_long_is_aborted_and_the_next_read_works(self, codex_home,
                                                                           monkeypatch):
        _st_db(codex_home, [_st_row() for _ in range(60)])
        _st_fresh(codex_home)
        monkeypatch.setattr(data_codex_state, "_QUERY_BUDGET_S", -1.0)
        monkeypatch.setattr(data_codex_state, "_PROGRESS_OPS", 1)
        assert _st_usage() == (None, "error")
        monkeypatch.setattr(data_codex_state, "_QUERY_BUDGET_S", 1.5)
        monkeypatch.setattr(data_codex_state, "_PROGRESS_OPS", 1000)
        data_codex_state.clear_memo()
        assert _st_usage()[0]["threads"] == 60

    # --- the freshness guard ------------------------------------------------------

    def test_a_database_far_older_than_the_rollouts_is_stale(self, codex_home):
        _st_db(codex_home, [_st_row(updated=int((_ST_NOW - 3 * 3600) * 1000))])
        _st_fresh(codex_home)
        assert _st_usage() == (None, "stale")

    def test_a_database_within_the_margin_is_fresh(self, codex_home):
        _st_db(codex_home, [_st_row(updated=int((_ST_NOW - 1700) * 1000))])
        _st_fresh(codex_home)
        assert _st_usage()[1] == "ok"

    def test_a_rollout_with_a_frozen_mtime_still_counts_by_its_last_record(self, codex_home):
        _cx_write(codex_home, _cx_id(2), "C:\\ws", records=[_cx_agent("x", ts=_st_iso(-60))],
                  mtime=_ST_NOW - 86400)
        _st_db(codex_home, [_st_row(updated=int((_ST_NOW - 60) * 1000))])
        assert _st_usage()[1] == "ok"
        data_codex_state.clear_memo()
        con = sqlite3.connect(codex_home / "state_5.sqlite")
        con.execute("UPDATE threads SET updated_at_ms = ?", (int((_ST_NOW - 4 * 3600) * 1000),))
        con.commit()
        con.close()
        assert _st_usage() == (None, "stale"), "the record's timestamp, not the old mtime, sets the rollout's age"

    def test_no_rollout_at_all_gives_none(self, codex_home):
        _st_db(codex_home, [_st_row()])
        assert _st_usage() == (None, "stale")

    def test_only_the_twenty_most_recently_modified_rollouts_are_compared(self, codex_home):
        for n in range(20):
            _cx_write(codex_home, _cx_id(10 + n), "C:\\ws", mtime=_ST_NOW - 10 * 3600)
        _cx_write(codex_home, _cx_id(40), "C:\\ws", mtime=_ST_NOW - 20 * 3600,
                  records=[_cx_agent("x", ts=_st_iso(-60))])
        _st_db(codex_home, [_st_row(updated=int((_ST_NOW - 10 * 3600) * 1000))])
        assert _st_usage()[1] == "ok", "the 21st rollout by mtime is not looked at"

    # --- the memo, logging and filters ---------------------------------------------

    def test_the_memo_holds_raw_rows_so_a_new_hidden_tag_applies_at_once(self, codex_home,
                                                                         monkeypatch):
        _st_db(codex_home, [_st_row(cwd="C:\\a", tokens=2), _st_row(cwd="C:\\b", tokens=5)])
        _st_fresh(codex_home)
        reads = []
        real = data_codex_state._read
        monkeypatch.setattr(data_codex_state, "_read", lambda ms: reads.append(ms) or real(ms))
        assert _st_usage()[0]["total"] == 7
        assert _st_usage(hidden=lambda c: c == "C:\\a")[0]["total"] == 5
        assert len(reads) == 1, "the second call came from the memo"
        clock = [time.monotonic()]
        monkeypatch.setattr(data_codex_state, "_clock", lambda: clock[0] + 31)
        assert _st_usage()[0]["total"] == 7 and len(reads) == 2, "after about 30 s the file is read again"

    def test_a_failure_is_memoised_and_logged_once_without_a_path_or_id(self, codex_home,
                                                                         monkeypatch, caplog):
        _st_fresh(codex_home)
        bad = codex_home / "state_5.sqlite"
        bad.write_bytes(b"garbage" * 300)
        Path(str(bad) + "-wal").write_bytes(b"")
        Path(str(bad) + "-shm").write_bytes(b"")
        with caplog.at_level("WARNING"):
            assert _st_usage() == (None, "error")
            assert _st_usage() == (None, "error")
        lines = [r.getMessage() for r in caplog.records]
        assert len(lines) == 1 and "DatabaseError" in lines[0]
        assert str(codex_home) not in lines[0] and "state_5" not in lines[0] and _cx_id(1) not in lines[0]
        reads = []
        monkeypatch.setattr(data_codex_state, "_read", lambda ms: reads.append(ms) or (None, "error"))
        _st_usage()
        assert not reads, "the memoised failure was reused"

    def test_a_refresh_stopped_during_the_read_is_not_memoised(self, codex_home, monkeypatch):
        _st_db(codex_home, [_st_row(tokens=4)])
        _st_fresh(codex_home)
        stop = threading.Event()
        reads = []
        real = data_codex_state._read

        def read_then_stop(ms):
            reads.append(ms)
            stop.set()
            return real(ms)

        monkeypatch.setattr(data_codex_state, "_read", read_then_stop)
        assert _st_usage(stop_event=stop) == (None, "error")
        assert _st_usage()[0]["total"] == 4
        assert len(reads) == 2, "the stopped read left nothing in the memo, so the next call read again"

    def test_a_filter_that_raises_gives_none_and_never_raises(self, codex_home):
        _st_db(codex_home, [_st_row()])
        _st_fresh(codex_home)

        def boom(cwd):
            raise RuntimeError("filter")

        assert _st_usage(hidden=boom) == (None, "error")

    def test_a_stopped_refresh_is_not_memoised(self, codex_home):
        _st_db(codex_home, [_st_row(tokens=4)])
        _st_fresh(codex_home)
        stop = threading.Event()
        stop.set()
        assert _st_usage(stop_event=stop) == (None, "error")
        assert _st_usage()[0]["total"] == 4

    def test_the_module_imports_no_overview_web_or_presence(self):
        source = Path(data_codex_state.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not modules & {"overview", "web", "presence"}
        assert imported & {"_CODEX_HOME", "activity_epoch", "canonical_rollouts"} == {
            "_CODEX_HOME", "activity_epoch", "canonical_rollouts"}


class TestQuietLog:
    def test_a_kind_is_logged_once_a_minute_with_only_the_exception_class(self, monkeypatch, caplog):
        now = [1000.0]
        monkeypatch.setattr(quiet_log, "_clock", lambda: now[0])
        quiet_log.reset()
        logger = __import__("logging").getLogger("power_atlas.test_quiet")
        with caplog.at_level("WARNING", logger="power_atlas.test_quiet"):
            assert quiet_log.warn(logger, "k", OSError("C:\\secret")) is True
            assert quiet_log.warn(logger, "k", OSError("C:\\secret")) is False
            assert quiet_log.warn(logger, "other") is True, "another kind has its own throttle"
            now[0] += 59
            assert quiet_log.warn(logger, "k") is False
            now[0] += 2
            assert quiet_log.warn(logger, "k") is True
        messages = [r.getMessage() for r in caplog.records]
        assert messages == ["k (OSError)", "other", "k"], messages
        assert not any("secret" in m for m in messages)


# --- lock_owner (Phase 3 of 261002_CODEX_LIVE_STATUS_CONTEXT_PRESSURE_AND_SUBAGENT_USAGE_FROM_STATE_DB) ---
# The resolver is driven without a thread: `step()` runs one queued lookup on the calling thread, the
# lookup, the liveness check and the clock are injected, and no real process or file is asked.

class _LoClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _lo_resolver(lookup=None, alive=None, **kw):
    clock = _LoClock()
    calls = []
    answers = {}

    def default_lookup(path):
        calls.append(path)
        return answers.get(path, ((111, 5.0),))

    r = lock_owner._Resolver(lookup=lookup or default_lookup, alive=alive or (lambda pid, start: "alive"),
                             clock=clock, threads=False, self_test=False, **kw)
    r.calls, r.answers, r.clock = calls, answers, clock
    return r


def _lo_run(r, path, seconds=1.0):
    """Let a path's queued lookup run (the gap between lookups elapses first)."""
    r.clock.t += seconds
    assert r.step() is True
    r.clock.t += 0.0


class TestLockOwner:
    P = "C:\\locks\\a.lock"
    Q = "C:\\locks\\b.lock"

    # -- first sighting, the cache and the schedule --

    def test_the_first_sighting_is_pending_and_the_next_call_answers_from_the_cache(self):
        r = _lo_resolver()
        assert r.holders(self.P) is lock_owner.PENDING
        _lo_run(r, self.P)
        assert r.holders(self.P) == ((111, 5.0),)
        assert r.holders(self.P) == ((111, 5.0),) and r.calls == [self.P], "the second call did not look again"

    def test_a_lookup_that_raises_gives_unknown_and_is_not_repeated_for_30_seconds(self):
        def boom(path):
            raise RuntimeError("x")
        r = _lo_resolver(lookup=boom)
        assert r.holders(self.P) is lock_owner.PENDING
        _lo_run(r, self.P)
        assert r.holders(self.P) is None
        r.clock.t += 29
        assert r.holders(self.P) is None and r.step() is False
        r.clock.t += 2
        assert r.holders(self.P) is lock_owner.PENDING, "after 30 s it is looked up again"

    def test_a_failed_lookup_result_none_is_unknown(self):
        r = _lo_resolver(lookup=lambda path: None)
        r.holders(self.P)
        _lo_run(r, self.P)
        assert r.holders(self.P) is None

    def test_a_terminal_style_entry_is_re_resolved_at_30_seconds_and_3_minutes_then_every_5_minutes(self):
        r = _lo_resolver()
        r.holders(self.P)
        _lo_run(r, self.P)
        first = r.clock.t
        looks = lambda: len(r.calls)
        assert looks() == 1
        r.clock.t = first + 29
        r.holders(self.P)
        assert r.step() is False and looks() == 1, "not yet"
        r.clock.t = first + 31
        r.holders(self.P)
        assert r.step() is True and looks() == 2, "30 s after the first resolution"
        r.clock.t = first + 170
        r.holders(self.P)
        assert r.step() is False
        r.clock.t = first + 181
        r.holders(self.P)
        assert r.step() is True and looks() == 3, "3 minutes after the first"
        last = r.clock.t
        r.clock.t = last + 299
        r.holders(self.P)
        assert r.step() is False
        r.clock.t = last + 301
        r.holders(self.P)
        assert r.step() is True and looks() == 4, "then every 5 minutes"

    def test_a_holder_swap_with_a_still_live_pid_is_caught_by_the_re_resolve(self):
        r = _lo_resolver()
        r.holders(self.P)
        _lo_run(r, self.P)
        r.answers[self.P] = ((222, 9.0),)
        r.clock.t += 31
        assert r.holders(self.P) == ((111, 5.0),), "the old answer is used meanwhile"
        assert r.step() is True
        assert r.holders(self.P) == ((222, 9.0),)

    # -- liveness --

    def test_a_cached_holder_that_is_gone_gives_unknown_and_a_top_priority_re_resolve(self):
        states = {"v": "alive"}
        r = _lo_resolver(alive=lambda pid, start: states["v"])
        r.holders(self.P)
        _lo_run(r, self.P)
        r.holders(self.Q)                                  # another path waiting in the queue
        r.clock.t += 11                                    # the lookup is older than the 10 s gap
        states["v"] = "gone"
        assert r.holders(self.P) is None
        r.answers[self.P] = ((333, 7.0),)
        r.clock.t += 1
        assert r.step() is True and r.calls[-1] == self.P, "the gone holder's path goes before the new sighting"

    def test_pid_reuse_is_unknown(self):
        r = _lo_resolver(alive=lambda pid, start: "alive" if start == 5.0 else "gone")
        r.holders(self.P)
        _lo_run(r, self.P)
        assert r.holders(self.P) == ((111, 5.0),)
        r._alive = lambda pid, start: "gone"
        assert r.holders(self.P) is None

    def test_an_unverifiable_holder_keeps_its_answer_and_queues_nothing(self):
        r = _lo_resolver(alive=lambda pid, start: "unknown")
        r.holders(self.P)
        _lo_run(r, self.P)
        r.clock.t += 400
        assert r.holders(self.P) == ((111, 5.0),)
        assert r.step() is False, "no re-resolve even though one was due"

    def test_a_gone_holder_re_resolves_at_most_once_per_10_seconds_per_path(self):
        r = _lo_resolver(alive=lambda pid, start: "gone")   # every lookup finds a holder that is gone at once
        r.holders(self.P)
        started = r.clock.t
        for _ in range(26):                                 # a poll and a worker step every second
            r.clock.t += 1
            r.holders(self.P)
            r.step()
        assert len(r.calls) <= 4, f"{len(r.calls)} lookups in {r.clock.t - started:.0f} s"
        assert len(r.calls) >= 2, "it does look again once the gap has passed"

    def test_a_path_that_keeps_giving_no_usable_holder_backs_off_30s_5min_30min(self):
        r = _lo_resolver(lookup=lambda path: r.calls.append(path) or ())
        r.holders(self.P)
        gaps = []
        for expected in lock_owner.BACKOFF:
            _lo_run(r, self.P)
            stamp = r.clock.t
            assert r.holders(self.P) is None
            assert r._cache[self.P].not_before == pytest.approx(stamp + expected)
            gaps.append(expected)
            r.clock.t = stamp + expected + lock_owner.FAILED_TTL + 1
            r.holders(self.P)
        assert gaps == [30.0, 300.0, 1800.0]
        _lo_run(r, self.P)
        assert r._cache[self.P].not_before == pytest.approx(r.clock.t + 1800.0), "the last step holds"
        assert r.status() == "backing-off"

    def test_forget_drops_the_path_and_its_queued_lookup(self):
        r = _lo_resolver()
        r.holders(self.P)
        r.forget(self.P)
        assert r._queued == {}, "the queued lookup went with it"
        r.clock.t += 2
        assert r.step() is False and r.calls == []
        assert r.holders(self.P) is lock_owner.PENDING, "a forgotten path is a first sighting again"

    # -- order, bounds --

    def test_first_sightings_are_served_before_re_resolves(self):
        r = _lo_resolver()
        paths = [f"C:\\locks\\{n}.lock" for n in range(20)]
        for path in paths:
            r.holders(path)
            r.clock.t += 1
            while r.step():
                r.clock.t += 1
        r.clock.t += 400
        for path in paths[:7]:
            r.holders(path)                                 # seven re-resolves queued
        new = "C:\\locks\\new.lock"
        r.holders(new)
        r.clock.t += 1
        before = len(r.calls)
        assert r.step() is True and r.calls[before] == new, "the new path went first"

    def test_the_queue_holds_at_most_eight_and_an_overflow_stays_pending(self):
        r = _lo_resolver()
        paths = [f"C:\\locks\\q{n}.lock" for n in range(12)]
        assert all(r.holders(path) is lock_owner.PENDING for path in paths)
        assert len(r._queued) == lock_owner.MAX_QUEUE
        assert r.holders(paths[-1]) is lock_owner.PENDING, "retried later, still pending"

    def test_at_most_256_paths_are_cached_and_an_evicted_one_is_pending_again(self):
        r = _lo_resolver()
        for n in range(lock_owner.MAX_PATHS + 5):
            r.holders(f"C:\\locks\\c{n}.lock")
        assert len(r._cache) == lock_owner.MAX_PATHS
        assert r.holders("C:\\locks\\c0.lock") is lock_owner.PENDING

    def test_lookups_are_at_least_a_second_apart(self):
        r = _lo_resolver()
        r.holders(self.P)
        r.holders(self.Q)
        r.clock.t += 1
        assert r.step() is True
        r.clock.t += 0.5
        assert r.step() is False
        r.clock.t += 0.6
        assert r.step() is True

    # -- the wedge rule and the abandoned workers --

    def _wedge(self, r):
        """Pretend the current worker has been inside a lookup for longer than the bound."""
        r._busy = (r._gen, self.P, r.clock.t)
        r.clock.t += lock_owner.LOOKUP_WEDGE_SECONDS + 1
        return r._gen

    # -- the same rules under real threads: a replacement worker, and recovery after an abandoned one returns --

    @staticmethod
    def _wait(predicate, seconds=5.0):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        return predicate()

    def _gated_resolver(self, monkeypatch, gates):
        """Lookups of a path with a gate block until it is set; every other lookup answers at once."""
        monkeypatch.setattr(lock_owner, "LOOKUP_WEDGE_SECONDS", 0.15)
        monkeypatch.setattr(lock_owner, "MIN_LOOKUP_GAP", 0.0)
        monkeypatch.setattr(lock_owner, "WEDGE_WINDOW_SECONDS", 0.05)   # the two stalls below are not "within" it
        entered = []

        def lookup(path):
            entered.append(path)
            if path in gates:
                gates[path].wait(30)
            return ((1, 1.0),)

        r = lock_owner._Resolver(lookup=lookup, alive=lambda p, s: "alive", threads=True, self_test=False)
        r.entered = entered
        return r

    def test_a_stalled_lookup_gets_a_replacement_worker_and_the_stuck_one_exits_when_it_returns(self, monkeypatch):
        gate = threading.Event()
        r = self._gated_resolver(monkeypatch, {self.P: gate})
        try:
            assert r.holders(self.P) is lock_owner.PENDING
            assert self._wait(lambda: self.P in r.entered)
            time.sleep(0.3)                                      # past the 0.15 s bound
            assert r.holders(self.Q) is lock_owner.PENDING        # any call notices the stall
            assert self._wait(lambda: r.holders(self.Q) == ((1, 1.0),)), "the replacement worker looked it up"
            assert r._abandoned == 1
            gate.set()
            assert self._wait(lambda: r._abandoned == 0)
            def workers():
                return [th for th in threading.enumerate() if th.name == "lock-owner" and th.is_alive()]
            assert self._wait(lambda: len(workers()) == 1), "the abandoned worker left its loop"
        finally:
            gate.set()
            r.stop()

    def test_a_status_poll_alone_starts_the_replacement_worker(self, monkeypatch):
        gate = threading.Event()
        r = self._gated_resolver(monkeypatch, {self.P: gate})
        try:
            r.holders(self.P)
            assert self._wait(lambda: self.P in r.entered)
            r.holders(self.Q)                                    # queued behind the blocked worker
            time.sleep(0.3)
            r.status()                                           # only a status poll notices the stall
            assert self._wait(lambda: r._cache[self.Q].holders is not None), "the replacement looked it up"
        finally:
            gate.set()
            r.stop()

    def test_with_two_abandoned_workers_the_lookup_resumes_when_one_returns_not_when_both_do(self, monkeypatch):
        gate_a, gate_b = threading.Event(), threading.Event()
        r = self._gated_resolver(monkeypatch, {self.P: gate_a, self.Q: gate_b})
        third = "C:\\locks\\c.lock"
        try:
            r.holders(self.P)
            assert self._wait(lambda: self.P in r.entered)
            time.sleep(0.3)
            r.holders(self.Q)                                    # abandons the first worker, starts a second
            assert self._wait(lambda: self.Q in r.entered)
            time.sleep(0.3)
            assert r.holders(third) is None and r.status() == "disabled", "two blocked workers: disabled, no third thread"
            gate_a.set()                                         # one returns
            assert self._wait(lambda: r._abandoned == 1)
            assert self._wait(lambda: r.holders(third) == ((1, 1.0),)), "a new worker serves it while the other is still blocked"
            assert not gate_b.is_set()
        finally:
            gate_a.set()
            gate_b.set()
            r.stop()

    def test_an_abandoned_worker_pops_nothing(self):
        r = _lo_resolver()
        r.holders(self.P)
        old = r._gen
        r._gen += 1
        r.clock.t += 2
        assert r.step(gen=old) is False and r.calls == []
        assert r.step() is True

    def test_lookups_that_keep_failing_show_as_backing_off_and_a_success_clears_it(self):
        results = {"v": None}
        r = _lo_resolver(lookup=lambda path: results["v"])
        for n in range(lock_owner.FAIL_STREAK):
            r.holders(f"C:\\locks\\f{n}.lock")
            _lo_run(r, f"C:\\locks\\f{n}.lock")
        assert r.status() == "backing-off"
        results["v"] = ((1, 1.0),)
        r.holders("C:\\locks\\ok.lock")
        _lo_run(r, "C:\\locks\\ok.lock")
        assert r.status() == "ok"

    def test_a_library_that_could_not_be_loaded_reports_disabled(self, monkeypatch):
        monkeypatch.setattr(lock_owner, "_lib_failed", True)
        monkeypatch.setattr(lock_owner, "_resolver", lock_owner._Resolver(threads=False, self_test=False))
        assert lock_owner.status() == "disabled"

    def test_a_thread_that_cannot_be_started_leaves_the_fall_back_in_place(self, monkeypatch):
        class NoThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

        monkeypatch.setattr(lock_owner.threading, "Thread", NoThread)
        r = lock_owner._Resolver(lookup=lambda p: None, threads=True, self_test=False)
        assert r.holders(self.P) is lock_owner.PENDING and r._worker is None

    def test_a_wedged_lookup_abandons_its_worker_and_the_late_result_is_dropped(self):
        r = _lo_resolver()
        r.holders(self.P)
        old = self._wedge(r)
        r.holders(self.Q)                                   # a caller notices (any call does)
        assert r._gen == old + 1 and r._abandoned == 1
        r._finish(old, self.P, ((999, 1.0),))               # the stuck call returns at last
        assert r._abandoned == 0
        assert r._cache[self.P].holders is None, "an abandoned worker's result is dropped"

    def test_two_wedges_within_ten_minutes_disable_the_lookup_and_a_recovery_re_enables_it(self):
        r = _lo_resolver()
        r.holders(self.P)
        first = self._wedge(r)
        r.status()
        r._finish(first, self.P, None)
        r.clock.t += 60
        second = self._wedge(r)
        assert r.status() == "disabled"
        assert r.holders(self.Q) is None, "a disabled lookup answers unknown"
        r._finish(second, self.P, None)
        r.clock.t += lock_owner.WEDGE_WINDOW_SECONDS + 1
        assert r.status() == "ok", "after 10 minutes it may run again"
        assert r.holders(self.Q) is lock_owner.PENDING

    def test_two_wedges_further_apart_than_ten_minutes_do_not_disable_it(self):
        r = _lo_resolver()
        r.holders(self.P)
        first = self._wedge(r)
        r.status()
        r._finish(first, self.P, None)
        r.clock.t += lock_owner.WEDGE_WINDOW_SECONDS + 5
        self._wedge(r)
        assert r.status() in ("ok", "backing-off")

    def test_two_still_blocked_abandoned_workers_disable_it_with_no_third_thread_and_one_returning_re_enables(self):
        r = _lo_resolver()
        r._threads = True                                   # count the threads it would start
        starts = []
        r._ensure_worker = lambda: starts.append(1)
        r.holders(self.P)
        one = self._wedge(r)
        r.status()
        r.clock.t += lock_owner.WEDGE_WINDOW_SECONDS + 5     # the first wedge ages out of the rate window
        two = self._wedge(r)
        r.status()
        assert r._abandoned == 2 and r.status() == "disabled"
        before = len(starts)
        r.holders(self.Q)
        assert len(starts) == before, "no replacement worker while two are still blocked"
        r._finish(one, self.P, None)
        r.clock.t += lock_owner.WEDGE_WINDOW_SECONDS + 5
        assert r.status() != "disabled", "one returned, and the rate window passed"
        r._finish(two, self.P, None)

    def test_the_self_test_runs_before_any_lookup_and_a_list_with_the_own_pid_passes(self, tmp_path):
        seen = []

        def lookup(path):
            seen.append(path)
            return ((os.getpid(), 1.0), (4242, 2.0))        # this process, and a scanner

        clock = _LoClock()
        r = lock_owner._Resolver(lookup=lookup, alive=lambda p, s: "alive", clock=clock, threads=False)
        assert r.holders(self.P) is lock_owner.PENDING, "callers get PENDING while the worker tests"
        clock.t += 2
        assert r.step() is True and len(seen) == 1 and not os.path.exists(seen[0]), "the temp file is gone"
        assert r._self_tested is True
        clock.t += 2
        assert r.step() is True and seen[-1] == self.P

    def test_a_self_test_that_does_not_list_this_process_disables_the_lookup(self):
        clock = _LoClock()
        r = lock_owner._Resolver(lookup=lambda path: ((4242, 2.0),), alive=lambda p, s: "alive", clock=clock,
                                 threads=False)
        r.holders(self.P)
        clock.t += 2
        r.step()
        assert r.status() == "disabled"
        assert r.holders(self.Q) is None
        clock.t += lock_owner.WEDGE_WINDOW_SECONDS + 1
        assert r.status() == "ok"

    def test_a_self_test_whose_lookup_raises_disables_the_lookup(self):
        def boom(path):
            raise OSError("x")
        clock = _LoClock()
        r = lock_owner._Resolver(lookup=boom, alive=lambda p, s: "alive", clock=clock, threads=False)
        r.holders(self.P)
        clock.t += 2
        r.step()
        assert r.status() == "disabled"

    def test_no_worker_is_started_while_the_lookup_is_disabled(self):
        r = lock_owner._Resolver(lookup=lambda p: None, threads=True, self_test=False)
        r._disabled_until = r._clock() + 100
        r._ensure_worker()
        assert r._worker is None
        r.stop()

    def test_the_worker_is_a_daemon_thread_and_resolves_in_the_background(self, monkeypatch):
        monkeypatch.setattr(lock_owner, "MIN_LOOKUP_GAP", 0.0)
        r = lock_owner._Resolver(lookup=lambda path: ((1, 1.0),), alive=lambda p, s: "alive", threads=True,
                                 self_test=False)
        try:
            assert r.holders(self.P) is lock_owner.PENDING
            assert r._worker is not None and r._worker.daemon is True
            deadline = time.monotonic() + 10
            while r.holders(self.P) is lock_owner.PENDING and time.monotonic() < deadline:
                time.sleep(0.02)
            assert r.holders(self.P) == ((1, 1.0),)
        finally:
            r.stop()

    # -- the module functions --

    @pytest.mark.skipif(sys.platform != "win32", reason="off Windows the module function answers unknown")
    def test_the_module_functions_use_the_one_resolver_and_status_reports_it(self):
        assert lock_owner.status() == "ok"
        assert lock_owner.holders(self.P) is lock_owner.PENDING
        lock_owner.forget(self.P)
        assert lock_owner.holders(self.P) is lock_owner.PENDING

    def test_off_windows_the_module_function_answers_unknown_and_find_holders_returns_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(lock_owner, "_resolver", lock_owner._Resolver(threads=False, self_test=False))
        assert lock_owner.find_holders(self.P) is None
        assert lock_owner.holders(self.P) is None


class _RmFake:
    """A stand-in for the Restart Manager library that records what it was asked."""

    def __init__(self, list_codes=(0,), count=1, needed=1, start_code=0, register_code=0):
        self.calls, self.list_codes, self.count, self.needed = [], list(list_codes), count, needed
        self.start_code, self.register_code = start_code, register_code
        self.RmStartSession = self._start
        self.RmRegisterResources = self._register
        self.RmGetList = self._get
        self.RmEndSession = self._end

    def _start(self, session, flags, key):
        self.calls.append("start")
        session._obj.value = 7
        return self.start_code

    def _register(self, session, n, names, a, b, c, d):
        self.calls.append("register")
        return self.register_code

    def _get(self, session, needed, count, infos, reasons):
        self.calls.append("list")
        code = self.list_codes.pop(0) if self.list_codes else 0
        needed._obj.value = self.needed
        if code == 0:
            count._obj.value = self.count
            for i in range(min(self.count, len(infos))):
                infos[i].Process.dwProcessId = 100 + i
        return code

    def _end(self, session):
        self.calls.append("end")
        return 0


@pytest.mark.skipif(sys.platform != "win32", reason="the Restart Manager is Windows only")
class TestRestartManagerCalls:
    def _patched(self, monkeypatch, fake):
        monkeypatch.setattr(lock_owner, "_lib", fake)
        monkeypatch.setattr(lock_owner, "_lib_failed", False)

    def test_the_structure_and_buffer_sizes_are_pinned(self):
        unique, info = lock_owner._structures()
        assert ctypes.sizeof(unique) == 12 and ctypes.sizeof(info) == 668

    def test_a_list_is_returned_as_pid_and_start_time_pairs_and_the_session_is_ended(self, monkeypatch):
        fake = _RmFake(count=2)
        self._patched(monkeypatch, fake)
        found = lock_owner.find_holders("C:\\x.lock")
        assert [pid for pid, _s in found] == [100, 101]
        assert fake.calls == ["start", "register", "list", "end"]

    def test_more_than_64_entries_give_unknown(self, monkeypatch):
        fake = _RmFake(count=65)
        self._patched(monkeypatch, fake)
        assert lock_owner.find_holders("C:\\x.lock") is None and fake.calls[-1] == "end"
        fake = _RmFake(list_codes=(234,), needed=65)
        self._patched(monkeypatch, fake)
        assert lock_owner.find_holders("C:\\x.lock") is None and fake.calls[-1] == "end"

    def test_more_data_is_retried_at_most_three_times(self, monkeypatch):
        fake = _RmFake(list_codes=(234, 234, 234, 234), needed=9)
        self._patched(monkeypatch, fake)
        assert lock_owner.find_holders("C:\\x.lock") is None
        assert fake.calls.count("list") == 3 and fake.calls[-1] == "end"
        fake = _RmFake(list_codes=(234, 0), needed=9, count=9)
        self._patched(monkeypatch, fake)
        assert len(lock_owner.find_holders("C:\\x.lock")) == 9

    @pytest.mark.parametrize("kw", [{"register_code": 5}, {"list_codes": (5,)}])
    def test_the_session_is_ended_on_every_error_path(self, monkeypatch, kw):
        fake = _RmFake(**kw)
        self._patched(monkeypatch, fake)
        assert lock_owner.find_holders("C:\\x.lock") is None
        assert fake.calls[-1] == "end"

    def test_a_session_that_does_not_start_is_unknown_and_not_ended(self, monkeypatch):
        fake = _RmFake(start_code=5)
        self._patched(monkeypatch, fake)
        assert lock_owner.find_holders("C:\\x.lock") is None and fake.calls == ["start"]

    def test_the_library_is_loaded_by_absolute_path_from_the_system_folder(self, monkeypatch):
        seen = []

        def loader(path):
            seen.append(path)
            raise OSError("not here")

        monkeypatch.setattr(lock_owner, "_lib", None)
        monkeypatch.setattr(lock_owner, "_lib_failed", False)
        monkeypatch.setattr(lock_owner, "_load_library", loader)
        assert lock_owner.find_holders("C:\\x.lock") is None
        assert len(seen) == 1 and os.path.isabs(seen[0]) and seen[0].lower().endswith("system32\\rstrtmgr.dll")
        assert lock_owner.find_holders("C:\\x.lock") is None and len(seen) == 1, "a failed load is remembered"

    def test_the_system_folder_comes_from_the_os_never_from_the_environment(self, monkeypatch):
        seen = []
        monkeypatch.setenv("SystemRoot", "D:\\evil")
        monkeypatch.setattr(lock_owner, "_lib", None)
        monkeypatch.setattr(lock_owner, "_lib_failed", False)
        monkeypatch.setattr(lock_owner, "_load_library", lambda path: seen.append(path) or (_ for _ in ()).throw(OSError("x")))
        assert lock_owner.find_holders("C:\\x.lock") is None
        assert len(seen) == 1 and "evil" not in seen[0].lower()
        assert os.path.dirname(seen[0]).lower() == lock_owner._system_directory().lower()

    @pytest.mark.parametrize("folder", [None, "", "system32", "..\\system32"])
    def test_a_missing_or_relative_system_folder_loads_nothing(self, monkeypatch, folder):
        seen = []
        monkeypatch.setattr(lock_owner, "_lib", None)
        monkeypatch.setattr(lock_owner, "_lib_failed", False)
        monkeypatch.setattr(lock_owner, "_system_directory", lambda: folder)
        monkeypatch.setattr(lock_owner, "_load_library", lambda path: seen.append(path))
        assert lock_owner.find_holders("C:\\x.lock") is None and seen == []

    def test_the_library_is_loaded_with_the_system32_search_flag(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(lock_owner.ctypes, "WinDLL", lambda path, **kw: seen.update(path=path, kw=kw) or "lib")
        assert lock_owner._load_library("C:\\Windows\\System32\\rstrtmgr.dll") == "lib"
        assert seen["kw"] == {"winmode": 0x800}

    def test_a_filetime_converts_to_unix_seconds(self):
        base = 116444736000000000
        assert lock_owner._filetime_epoch(base & 0xFFFFFFFF, base >> 32) == 0.0
        assert lock_owner._filetime_epoch((base + 10 ** 7) & 0xFFFFFFFF, (base + 10 ** 7) >> 32) == 1.0
        assert lock_owner._filetime_epoch(0, 0) == -11644473600.0

    @pytest.mark.parametrize("times_ok,code,want", [(True, 259, "alive"), (True, 1, "gone"), (False, 259, "unknown")])
    def test_the_process_handle_is_closed_on_every_path(self, monkeypatch, times_ok, code, want):
        closed = []

        class Kernel32:
            def OpenProcess(self, access, inherit, pid):
                return 77

            def GetProcessTimes(self, handle, *rest):
                return 1 if times_ok else 0

            def GetExitCodeProcess(self, handle, out):
                out._obj.value = code
                return 1

            def CloseHandle(self, handle):
                closed.append(handle)
                return 1

        monkeypatch.setattr(lock_owner, "_kernel32", Kernel32())
        assert lock_owner._process_state(1234, lock_owner._filetime_epoch(0, 0)) == want
        assert closed == [77]

    def test_the_real_library_lists_this_process_for_a_file_it_holds_open(self, tmp_path):
        target = tmp_path / "held.bin"
        target.write_bytes(b"x")
        with open(target, "rb"):
            found = lock_owner.find_holders(str(target))
        assert found is not None and os.getpid() in {pid for pid, _s in found}
        assert os.getpid() not in {pid for pid, _s in (lock_owner.find_holders(str(target)) or ())}

    def test_process_state_tells_alive_gone_and_a_reused_pid(self):
        me = lock_owner.find_holders  # noqa: F841 (the module is loaded)
        import psutil
        proc = psutil.Process(os.getpid())
        assert lock_owner._process_state(proc.pid, proc.create_time()) == "alive"
        assert lock_owner._process_state(proc.pid, proc.create_time() - 3600) == "gone", "same pid, other start time"
        assert lock_owner._process_state(2 ** 22 + 12345, 1.0) == "gone"


class TestLockOwnerIsolation:
    def test_there_is_no_top_level_windll_or_msvcrt_import(self):
        tree = ast.parse(Path(lock_owner.__file__).read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        assert not [n for n in top if any(a.name.split(".")[0] in ("msvcrt", "winreg") for a in getattr(n, "names", []))]
        src = Path(lock_owner.__file__).read_text(encoding="utf-8")
        assert "windll" not in src.replace("WinDLL", ""), "no ctypes.windll anywhere"

    def test_it_imports_only_the_standard_library_and_quiet_log(self):
        tree = ast.parse(Path(lock_owner.__file__).read_text(encoding="utf-8"))
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add(("." * n.level) + (n.module or ""))
                if n.level:
                    names |= {"." * n.level + a.name for a in n.names}
        allowed = {"__future__", "collections", "ctypes", "heapq", "logging", "os", "sys", "tempfile", "threading",
                   "time", ".", ".quiet_log"}
        assert names <= allowed, sorted(names - allowed)

    def test_importing_acp_loads_neither_lock_owner_nor_data_codex_state(self):
        code = ("import sys; import power_atlas.acp; "
                "print(sorted(m for m in ('power_atlas.lock_owner', 'power_atlas.data_codex_state') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                             cwd=str(Path(lock_owner.__file__).parents[2]),
                             env=dict(os.environ, PYTHONPATH=str(Path(lock_owner.__file__).parents[1])))
        assert out.stdout.strip() == "[]", out.stdout + out.stderr

    def test_no_log_line_carries_a_pid_or_a_path(self, caplog):
        def boom(path):
            raise PermissionError(13, "denied", "C:\\secret\\path.lock")
        r = _lo_resolver(lookup=boom)
        with caplog.at_level("WARNING"):
            r.holders("C:\\secret\\path.lock")
            _lo_run(r, "C:\\secret\\path.lock")
        text = " ".join(rec.getMessage() for rec in caplog.records)
        assert "PermissionError" in text and "secret" not in text and "path.lock" not in text


