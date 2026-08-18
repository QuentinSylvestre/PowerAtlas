"""Tests for the kiro-cli v3 session adapter (data_kiro_v3.py)."""

import itertools
import json
import os
import time
from pathlib import Path

import pytest

import power_atlas.data_kiro_v3 as dv3
from power_atlas.data import _FileInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MTIME_BASE = time.time() + 120
_mtime_ticks = itertools.count()


def _bump_mtime(*paths: Path) -> None:
    """Stamp paths with a monotonically increasing mtime distinct from real clock."""
    stamp = _MTIME_BASE + next(_mtime_ticks) * 0.01
    for p in paths:
        os.utime(p, (stamp, stamp))


def _make_session(
    root: Path,
    hash_name: str,
    sess_dir_name: str,
    cwd: str,
    *,
    session_id: str | None = None,
    title: str = "Test Session",
    created_at: str = "2026-01-01T00:00:00Z",
    updated_at: str = "2026-01-02T00:00:00Z",
    agent_mode: str = "kiro_default",
    messages: list[str] | None = None,
) -> tuple[Path, Path]:
    """Create a minimal v3 session directory with session.json + optional messages.jsonl.

    Returns (session_json_path, messages_jsonl_path).
    """
    if session_id is None:
        session_id = sess_dir_name if sess_dir_name.startswith("sess_") else f"sess_{sess_dir_name}"

    sess_dir = root / hash_name / sess_dir_name
    sess_dir.mkdir(parents=True, exist_ok=True)

    session_json = sess_dir / "session.json"
    session_json.write_text(
        json.dumps({
            "id": session_id,
            "title": title,
            "workspacePaths": [cwd],
            "createdAt": created_at,
            "lastModifiedAt": updated_at,
            "agentMode": agent_mode,
        }),
        encoding="utf-8",
    )

    messages_jsonl = sess_dir / "messages.jsonl"
    if messages is not None:
        messages_jsonl.write_text("\n".join(messages), encoding="utf-8")
    else:
        messages_jsonl.write_text("", encoding="utf-8")

    _bump_mtime(session_json, messages_jsonl, sess_dir)
    return session_json, messages_jsonl


def _user_line(text: str) -> str:
    """Build a v3 messages.jsonl user-payload line."""
    return json.dumps({
        "id": "msg1",
        "timestamp": "2026-01-01T00:00:00Z",
        "payload": {"type": "user", "content": text},
    })


def _assistant_line(text: str) -> str:
    """Build a v3 messages.jsonl assistant-payload line."""
    return json.dumps({
        "id": "msg2",
        "timestamp": "2026-01-01T00:01:00Z",
        "payload": {"type": "assistant", "content": text},
    })


# ---------------------------------------------------------------------------
# Autouse cache reset fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_v3_caches():
    """Reset all module-level caches before and after each test."""
    def _reset():
        dv3._root_mtime = None
        dv3._session_json_mtimes = {}
        dv3._cwd_index = {}
        dv3._norm_cwd_to_hash = {}
        dv3._prompts_cache.clear()
        dv3._tail_cache.clear()
        dv3._first_prompt_cache.clear()

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# TestKiroV3IsAvailable
# ---------------------------------------------------------------------------

class TestKiroV3IsAvailable:
    def test_not_available_when_root_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", tmp_path / "nonexistent")
        assert dv3.is_available() is False

    def test_not_available_when_root_is_empty(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        assert dv3.is_available() is False

    def test_not_available_when_only_cli_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "cli").mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        assert dv3.is_available() is False

    def test_available_with_hash_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "abcd1234abcd1234").mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        assert dv3.is_available() is True

    def test_available_ignores_cli_counts_other_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "cli").mkdir()
        (root / "abcdef1234567890").mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        assert dv3.is_available() is True


# ---------------------------------------------------------------------------
# TestKiroV3DiscoverWorkspaces
# ---------------------------------------------------------------------------

class TestKiroV3DiscoverWorkspaces:
    def test_single_session(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "abc123", "sess_aaa", "C:\\Work", updated_at="2026-06-01T10:00:00Z")

        results = dv3.discover_workspaces()
        assert len(results) == 1
        assert results[0][0] == "C:\\Work"
        assert results[0][1] == 1
        assert results[0][2] == "2026-06-01T10:00:00Z"

    def test_multiple_sessions_same_workspace(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "abc123", "sess_aaa", "C:\\Work", updated_at="2026-06-01T10:00:00Z")
        _make_session(root, "abc123", "sess_bbb", "C:\\Work", updated_at="2026-06-02T10:00:00Z")

        results = dv3.discover_workspaces()
        assert len(results) == 1
        assert results[0][1] == 2
        assert results[0][2] == "2026-06-02T10:00:00Z"  # max updated_at

    def test_multiple_workspaces_sorted_by_recency(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "hash1", "sess_a1", "C:\\OlderProject", updated_at="2026-05-01T00:00:00Z")
        _make_session(root, "hash2", "sess_b1", "C:\\NewerProject", updated_at="2026-06-01T00:00:00Z")

        results = dv3.discover_workspaces()
        assert results[0][0] == "C:\\NewerProject"
        assert results[1][0] == "C:\\OlderProject"

    def test_malformed_session_json_skipped(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # Malformed session
        bad_dir = root / "badhash" / "sess_bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "session.json").write_text("not valid json {{{", encoding="utf-8")

        # Good session
        _make_session(root, "goodhash", "sess_ok", "C:\\GoodProject")

        results = dv3.discover_workspaces()
        assert len(results) == 1
        assert results[0][0] == "C:\\GoodProject"

    def test_missing_workspace_paths_skipped(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # Session with empty workspacePaths
        sess_dir = root / "somehash" / "sess_nopaths"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({"id": "sess_nopaths", "workspacePaths": []}),
            encoding="utf-8",
        )

        # Good session
        _make_session(root, "goodhash", "sess_ok", "C:\\GoodProject")

        results = dv3.discover_workspaces()
        assert len(results) == 1

    def test_excludes_cli_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        (root / "cli").mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "realhash", "sess_real", "C:\\Real")

        results = dv3.discover_workspaces()
        assert len(results) == 1

    def test_discover_workspaces_null_json_skipped(self, tmp_path, monkeypatch):
        """session.json containing JSON null should be skipped, not crash."""
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", tmp_path)
        hash_dir = tmp_path / "abc123"
        sess_dir = hash_dir / "sess_null"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text("null", encoding="utf-8")
        (sess_dir / "messages.jsonl").write_text("", encoding="utf-8")
        result = dv3.discover_workspaces()
        assert result == []  # null session skipped

    def test_discover_workspaces_missing_last_modified(self, tmp_path, monkeypatch):
        """Sessions with no lastModifiedAt sort stably (empty string sorts before any ISO date)."""
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", tmp_path)
        hash_dir = tmp_path / "abc123"
        sess_dir = hash_dir / "sess_nolm"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            '{"id":"sess_nolm","workspacePaths":["C:\\\\Work"],"title":"no lm",'
            '"createdAt":"2026-01-01T00:00:00Z","agentMode":"kiro_default"}',
            encoding="utf-8",
        )
        (sess_dir / "messages.jsonl").write_text("", encoding="utf-8")
        result = dv3.discover_workspaces()
        assert len(result) == 1
        assert "Work" in result[0][0]  # cwd present


# ---------------------------------------------------------------------------
# TestKiroV3LoadSessions
# ---------------------------------------------------------------------------

class TestKiroV3LoadSessions:
    def test_basic_load(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(
            root, "h1", "sess_abc",
            "C:\\MyProject",
            title="My Session",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-02T00:00:00Z",
            agent_mode="kiro_default",
            messages=[_user_line("Hello world"), _assistant_line("Hi there")],
        )

        sessions, file_stats = dv3.load_sessions("C:\\MyProject")
        assert len(sessions) == 1
        s = sessions[0]
        assert s.title == "My Session"
        assert s.cwd == "C:\\MyProject"
        assert s.created_at == "2026-01-01T00:00:00Z"
        assert s.updated_at == "2026-01-02T00:00:00Z"
        assert s.extra_fields == {"agentMode": "kiro_default"}
        assert s.first_prompt == "Hello world"
        assert s.last_reply_tail == "Hi there"

    def test_file_stats_populated(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        sj, msgs = _make_session(root, "h1", "sess_abc", "C:\\MyProject",
                                 messages=[_user_line("hi")])

        _, file_stats = dv3.load_sessions("C:\\MyProject")
        # Exact keys: only session.json and messages.jsonl should be tracked
        assert set(file_stats.keys()) == {str(sj), str(msgs)}

    def test_missing_messages_jsonl_still_loads(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        sess_dir = root / "h1" / "sess_abc"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({
                "id": "sess_abc",
                "title": "No msgs",
                "workspacePaths": ["C:\\MyProject"],
                "createdAt": "2026-01-01T00:00:00Z",
                "lastModifiedAt": "2026-01-02T00:00:00Z",
                "agentMode": "",
            }),
            encoding="utf-8",
        )
        _bump_mtime(sess_dir / "session.json", sess_dir)
        # No messages.jsonl created

        sessions, _ = dv3.load_sessions("C:\\MyProject")
        assert len(sessions) == 1
        assert sessions[0].title == "No msgs"
        assert sessions[0].first_prompt == ""

    def test_empty_messages_jsonl(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_abc", "C:\\MyProject", messages=[])

        sessions, _ = dv3.load_sessions("C:\\MyProject")
        assert len(sessions) == 1
        assert sessions[0].first_prompt == ""
        assert sessions[0].last_reply_tail == ""

    def test_prompt_extraction_v3_format(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        messages = [
            json.dumps({"id": "x", "timestamp": "t", "payload": {"type": "tool_call", "content": "tool stuff"}}),
            _user_line("First user prompt"),
            _assistant_line("First assistant reply"),
            _user_line("Second user prompt"),
            _assistant_line("Final reply"),
        ]
        _make_session(root, "h1", "sess_abc", "C:\\MyProject", messages=messages)

        sessions, _ = dv3.load_sessions("C:\\MyProject")
        s = sessions[0]
        assert s.first_prompt == "First user prompt"
        assert s.last_prompt == "Second user prompt"
        assert s.last_reply_tail == "Final reply"

    def test_sorts_by_updated_at_descending(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_older", "C:\\Work", updated_at="2026-01-01T00:00:00Z")
        _make_session(root, "h1", "sess_newer", "C:\\Work", updated_at="2026-01-03T00:00:00Z")
        _make_session(root, "h1", "sess_mid",   "C:\\Work", updated_at="2026-01-02T00:00:00Z")

        sessions, _ = dv3.load_sessions("C:\\Work")
        assert sessions[0].updated_at == "2026-01-03T00:00:00Z"
        assert sessions[-1].updated_at == "2026-01-01T00:00:00Z"

    def test_list_content_type_extracted(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # user message with content as array of blocks
        user_line = json.dumps({
            "id": "m1", "timestamp": "t",
            "payload": {
                "type": "user",
                "content": [
                    {"type": "text", "text": "Part one"},
                    {"type": "image", "data": "..."},
                    {"type": "text", "text": "Part two"},
                ],
            },
        })
        _make_session(root, "h1", "sess_abc", "C:\\W", messages=[user_line])

        sessions, _ = dv3.load_sessions("C:\\W")
        assert sessions[0].first_prompt == "Part one Part two"

    def test_session_id_falls_back_to_dir_stem(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # session.json without "id" field
        sess_dir = root / "h1" / "sess_fallback"
        sess_dir.mkdir(parents=True)
        (sess_dir / "session.json").write_text(
            json.dumps({
                "title": "No ID",
                "workspacePaths": ["C:\\W"],
                "createdAt": "2026-01-01T00:00:00Z",
                "lastModifiedAt": "2026-01-01T00:00:00Z",
                "agentMode": "",
            }),
            encoding="utf-8",
        )
        (sess_dir / "messages.jsonl").write_text("", encoding="utf-8")
        _bump_mtime(sess_dir / "session.json", sess_dir / "messages.jsonl", sess_dir)

        sessions, _ = dv3.load_sessions("C:\\W")
        assert sessions[0].session_id == "sess_fallback"

    def test_load_sessions_cache_hit_returns_same_result(self, tmp_path, monkeypatch):
        """Second load_sessions call without file changes returns same result from cache."""
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(
            root, "h1", "sess_cache",
            "C:\\W",
            messages=[_user_line("cached q"), _assistant_line("cached a")],
        )

        sessions1, stats1 = dv3.load_sessions("C:\\W")
        sessions2, stats2 = dv3.load_sessions("C:\\W")

        assert len(sessions1) == len(sessions2) == 1
        assert sessions1[0].first_prompt == sessions2[0].first_prompt == "cached q"
        assert sessions1[0].last_reply_tail == sessions2[0].last_reply_tail == "cached a"
        # File stats should be identical (no extra reads)
        assert stats1 == stats2


# ---------------------------------------------------------------------------
# TestKiroV3GetSessionTail
# ---------------------------------------------------------------------------

class TestKiroV3GetSessionTail:
    def test_basic_tail(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        messages = [
            _user_line("Q1"),
            _assistant_line("A1"),
            _user_line("Q2"),
            _assistant_line("A2"),
        ]
        _make_session(root, "h1", "sess_tail", "C:\\W", messages=messages)

        tail = dv3.get_session_tail("sess_tail", "C:\\W")
        assert "A1" in tail
        assert "A2" in tail

    def test_returns_empty_for_unknown_session(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        assert dv3.get_session_tail("sess_nonexistent", "C:\\W") == []

    def test_ttl_cache_returns_same_result(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_abc", "C:\\W",
                      messages=[_user_line("Q"), _assistant_line("Cached answer")])

        r1 = dv3.get_session_tail("sess_abc", "C:\\W")
        r2 = dv3.get_session_tail("sess_abc", "C:\\W")
        assert r1 == r2
        assert "Cached answer" in r1

    def test_mtime_invalidation_reloads(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _, msgs_path = _make_session(root, "h1", "sess_inv", "C:\\W",
                                     messages=[_assistant_line("Original")])

        # Expire TTL by clearing cache
        dv3._tail_cache.clear()

        r1 = dv3.get_session_tail("sess_inv", "C:\\W")
        assert "Original" in r1

        # Update file content + mtime
        msgs_path.write_text(
            _user_line("Q") + "\n" + _assistant_line("Updated answer"),
            encoding="utf-8",
        )
        _bump_mtime(msgs_path)
        dv3._tail_cache.clear()

        r2 = dv3.get_session_tail("sess_inv", "C:\\W")
        assert "Updated answer" in r2


# ---------------------------------------------------------------------------
# TestKiroV3GetFirstPrompt
# ---------------------------------------------------------------------------

class TestKiroV3GetFirstPrompt:
    def test_basic_first_prompt(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_fp", "C:\\W",
                      messages=[_user_line("My first question"), _assistant_line("Answer")])

        assert dv3.get_first_prompt("sess_fp") == "My first question"

    def test_returns_empty_for_unknown_session(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        assert dv3.get_first_prompt("sess_nonexistent") == ""

    def test_fallback_when_no_user_in_first_50_lines(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # 51 non-user lines before the first user line
        tool_lines = [
            json.dumps({"id": f"t{i}", "timestamp": "t", "payload": {"type": "tool_call", "content": "x"}})
            for i in range(51)
        ]
        tool_lines.append(_user_line("Late prompt"))
        _make_session(root, "h1", "sess_late", "C:\\W", messages=tool_lines)

        # The user line is past position 50, so first_prompt should be ""
        result = dv3.get_first_prompt("sess_late")
        assert result == ""

    def test_caches_result(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_cached", "C:\\W",
                      messages=[_user_line("Cached question")])

        r1 = dv3.get_first_prompt("sess_cached")
        r2 = dv3.get_first_prompt("sess_cached")
        assert r1 == r2 == "Cached question"

    def test_negative_result_cached(self, tmp_path, monkeypatch):
        """Empty (negative) result is also cached to avoid re-scanning on every TTL miss."""
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # Session with no user messages at all
        _make_session(root, "h1", "sess_empty", "C:\\W", messages=[
            json.dumps({"id": "t1", "timestamp": "t", "payload": {"type": "tool_call", "content": "x"}}),
        ])

        r1 = dv3.get_first_prompt("sess_empty")
        assert r1 == ""

        # Cache should now hold a negative entry — verify it is present
        cached = dv3._first_prompt_cache.get("sess_empty")
        assert cached is not None
        _ts, _mtime, result = cached
        assert result == ""


# ---------------------------------------------------------------------------
# TestKiroV3RefreshStale
# ---------------------------------------------------------------------------

class TestKiroV3RefreshStale:
    def test_unchanged_files_not_stale(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_stable", "C:\\W")

        sessions, file_stats = dv3.load_sessions("C:\\W")
        # Populate the index (required for refresh_stale to find the hash dir)
        norm = dv3._normalize_path("C:\\W")

        result = dv3.refresh_stale_entries_for_cwd(norm, file_stats)
        assert result is False

    def test_changed_mtime_is_stale(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        sj, msgs = _make_session(root, "h1", "sess_change", "C:\\W",
                                 messages=[_user_line("original")])

        sessions, file_stats = dv3.load_sessions("C:\\W")
        norm = dv3._normalize_path("C:\\W")

        # Mutate messages.jsonl + bump mtime
        msgs.write_text(_user_line("updated"), encoding="utf-8")
        _bump_mtime(msgs)

        result = dv3.refresh_stale_entries_for_cwd(norm, file_stats)
        assert result is True

    def test_deleted_file_is_stale(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        sj, msgs = _make_session(root, "h1", "sess_del", "C:\\W")
        sessions, file_stats = dv3.load_sessions("C:\\W")
        norm = dv3._normalize_path("C:\\W")

        msgs.unlink()
        result = dv3.refresh_stale_entries_for_cwd(norm, file_stats)
        assert result is True

    def test_new_session_added_is_stale(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_original", "C:\\W")
        sessions, file_stats = dv3.load_sessions("C:\\W")
        norm = dv3._normalize_path("C:\\W")

        # Add a new session to the same hash dir
        _make_session(root, "h1", "sess_new", "C:\\W")

        result = dv3.refresh_stale_entries_for_cwd(norm, file_stats)
        assert result is True

    def test_missing_root_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", tmp_path / "nonexistent")
        result = dv3.refresh_stale_entries_for_cwd("c:\\w", {})
        assert result is True

    def test_unknown_cwd_returns_true(self, tmp_path, monkeypatch):
        """cwd not in _norm_cwd_to_hash causes True (index not built for this cwd)."""
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)
        # Don't call load_sessions — index never built
        result = dv3.refresh_stale_entries_for_cwd(
            dv3._normalize_path("C:\\Unknown"), {}
        )
        assert result is True

    def test_sibling_cwd_session_added_does_not_trigger_stale(self, tmp_path, monkeypatch):
        """Adding a new session for cwd B in the same hash dir does not mark cwd A as stale.

        The H1 fix: refresh_stale uses cached_sess_names (cwd-filtered) rather than
        comparing all subdirs in the hash dir.
        """
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        # Two sessions for two different cwds in the same hash dir
        _make_session(root, "shared_hash", "sess_cwd_a", "C:\\CwdA")
        _make_session(root, "shared_hash", "sess_cwd_b", "C:\\CwdB")

        # Load cwd A to populate the index
        sessions_a, file_stats_a = dv3.load_sessions("C:\\CwdA")
        norm_a = dv3._normalize_path("C:\\CwdA")

        # Add a new session for cwd B in the SAME hash dir
        _make_session(root, "shared_hash", "sess_cwd_b2", "C:\\CwdB")

        # cwd A should NOT be stale — the new session belongs to cwd B
        result = dv3.refresh_stale_entries_for_cwd(norm_a, file_stats_a)
        assert result is False


# ---------------------------------------------------------------------------
# TestKiroV3FindSessionWorkspace
# ---------------------------------------------------------------------------

class TestKiroV3FindSessionWorkspace:
    def test_finds_workspace_for_known_session(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_find", "C:\\FindProject",
                      session_id="sess_find")

        result = dv3.find_session_workspace("sess_find")
        assert result == "C:\\FindProject"

    def test_returns_none_for_unknown_session(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        result = dv3.find_session_workspace("sess_unknown")
        assert result is None

    def test_sess_prefix_normalization(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        _make_session(root, "h1", "sess_abc123", "C:\\PrefixTest",
                      session_id="sess_abc123")

        # Call without the sess_ prefix — should still find it
        result = dv3.find_session_workspace("abc123")
        assert result == "C:\\PrefixTest"

    def test_returns_none_when_root_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", tmp_path / "nonexistent")
        result = dv3.find_session_workspace("sess_abc")
        assert result is None

    def test_skips_cli_dir(self, tmp_path, monkeypatch):
        root = tmp_path / "sessions"
        root.mkdir()
        # Put a fake session under cli/ — should be ignored
        cli = root / "cli" / "sess_cli_fake"
        cli.mkdir(parents=True)
        (cli / "session.json").write_text(
            json.dumps({
                "id": "sess_cli_fake",
                "workspacePaths": ["C:\\ShouldNotFind"],
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(dv3, "V3_SESSIONS_ROOT", root)

        result = dv3.find_session_workspace("sess_cli_fake")
        assert result is None


# ---------------------------------------------------------------------------
# TestKiroV3ExtractContent (internal helper)
# ---------------------------------------------------------------------------

class TestKiroV3ExtractContent:
    def test_extracts_string_content(self):
        line = json.dumps({
            "id": "x", "timestamp": "t",
            "payload": {"type": "user", "content": "simple text"},
        })
        assert dv3._extract_v3_content(line, "user") == "simple text"

    def test_extracts_list_text_blocks(self):
        line = json.dumps({
            "id": "x", "timestamp": "t",
            "payload": {
                "type": "assistant",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "world"},
                ],
            },
        })
        assert dv3._extract_v3_content(line, "assistant") == "Hello world"

    def test_returns_empty_for_wrong_type(self):
        line = json.dumps({
            "id": "x", "timestamp": "t",
            "payload": {"type": "tool_call", "content": "tool stuff"},
        })
        assert dv3._extract_v3_content(line, "user") == ""

    def test_returns_empty_for_invalid_json(self):
        assert dv3._extract_v3_content("not json {{{", "user") == ""

    def test_image_only_list_returns_empty(self):
        line = json.dumps({
            "id": "x", "timestamp": "t",
            "payload": {
                "type": "user",
                "content": [{"type": "image", "data": "..."}],
            },
        })
        assert dv3._extract_v3_content(line, "user") == ""

    def test_none_content_returns_empty(self):
        line = json.dumps({
            "id": "x", "timestamp": "t",
            "payload": {"type": "user", "content": None},
        })
        assert dv3._extract_v3_content(line, "user") == ""
