"""Tests for data module."""

import ast
import builtins
import contextlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from power_atlas import data as data_mod
from power_atlas import data_codex
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
    data_codex._clear_caches()
    yield home
    data_codex._clear_caches()


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

    def test_open_shared_never_creates_and_rejects_text_modes(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            data_codex.open_shared(tmp_path / "absent.lock", "r+b")
        with pytest.raises(ValueError):
            data_codex.open_shared(tmp_path / "x", "r")


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

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows file-sharing semantics")
    def test_read_write_handle_does_not_block_removal_either(self, tmp_path):
        """The two lock files of 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 3
        are opened `r+b`; Codex removes them."""
        target = tmp_path / "x.lock"
        target.write_bytes(b"")
        with data_codex.open_shared(target, "r+b") as fh:
            fh.seek(0)
            os.remove(target)  # same process: needs FILE_SHARE_DELETE on the handle
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
        assert data_codex.is_subagent_rollout(codex_home / "missing.jsonl") is False
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
        assert data_codex._exit_success(text) is expected

    def test_a_bool_exit_code_in_json_is_not_a_number(self):
        assert data_codex._exit_success('{"metadata":{"exit_code":true}}') is None
        assert data_codex._exit_success('{"metadata":{"exit_code":false}}') is None
        assert data_codex._exit_success('{"metadata":{"exit_code":0}}') is True
        assert data_codex._exit_success('{"metadata":{"exit_code":3}}') is False

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
        real = data_codex._exit_success

        def exit_success(text):
            if text == "BOOM":
                raise RuntimeError("boom")
            return real(text)
        monkeypatch.setattr(data_codex, "_exit_success", exit_success)
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
        cwd = "C:\\Big"
        count = 4100  # above the 4096 floor
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

    def test_the_warning_names_the_path_given_to_a_path_function(self, codex_home, monkeypatch, caplog):
        path = _cx_write(codex_home, _cx_id(1), "C:\\W", [_cx_user("SECRET-PROMPT-TEXT")])
        monkeypatch.setattr(data_codex, "_read_meta_payload",
                            lambda p: (_ for _ in ()).throw(RuntimeError("SECRET-PROMPT-TEXT")))
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            data_codex.read_meta(path)
        (message,) = [r.getMessage() for r in caplog.records]
        assert f"path={path}" in message and "SECRET-PROMPT-TEXT" not in message

    def test_the_warning_names_the_path_an_os_error_carries(self, codex_home, monkeypatch, caplog):
        sid, cwd = _cx_id(1), "C:\\W"
        path = _cx_write(codex_home, sid, cwd, [_cx_user("a")])
        assert data_codex.rollout_path(sid) == path  # verdict cached: only the transcript read fails below

        def broken(p, mode="rb"):
            raise PermissionError(13, "denied", os.fspath(p))
        monkeypatch.setattr(data_codex, "open_shared", broken)
        with caplog.at_level("WARNING", logger="power_atlas.data_codex"):
            assert data_codex.get_full_transcript(sid, cwd) == []
        assert any(f"path={path}" in r.getMessage() for r in caplog.records)


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
        assert data_codex._loads(bomb) is None          # a RecursionError is not a ValueError
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
