"""Tests for web module."""

import asyncio
import base64
import datetime as dt
import errno
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from power_atlas.data import Session
from power_atlas.web import app
from power_atlas import launcher


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config I/O for **every** test in this file, regardless of intent.

    Module-level and unconditional on purpose. Scoping this to the tests that
    mean to write is the shape that already failed: a test asserting a
    *refusal* reaches `save_config` the moment the guard it probes is removed,
    which is precisely what a mutation run does. This phase's own verification
    wrote `acp_max_sessions = 17`, `acp_idle_ttl_seconds = 86401`,
    `acp_prompt_silence_seconds = 7201` and a stray `remote-secret` into the
    real `%LOCALAPPDATA%\\power-atlas` that way.

    Three separate exposures motivate the widening past `TestSettingsSurface`:

    * `TestSameOriginGuard`'s four refusal tests and
      `test_guard_applies_to_multiple_endpoints` POST real payloads to
      `/api/save-setting` and ~7 other routes with `save_config` unpatched.
    * `test_save_setting_port_bool_rejected` and
      `test_save_setting_port_out_of_range` patch `load_config` to return a
      **default** `Config()` while leaving `save_config` real — so a bypass
      there does not write one wrong key, it writes an entirely default config
      over the user's populated one, destroying custom launchers, pinned
      sessions and pinned folders.
    * `memory/MEMORY.md` already records 18 tests that *read* the developer's
      real config.toml and names "a shared autouse fixture" as the durable fix.

    A test that wants a populated config still writes one into `tmp_path`; a
    test that wants the real one no longer gets it by accident.
    """
    from power_atlas import config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "REMOTE_SECRET_PATH", tmp_path / "remote-secret")
    return tmp_path


@pytest.fixture
def client():
    """TestClient with default Origin header for same-origin guard.

    The base URL is loopback because ``_ALLOWED_HOSTS`` only admits real
    loopback names; TestClient's own default (``http://testserver``) is a
    single-label host an attacker can win on the local network, so it must not
    be allowlisted just to make this suite pass. With the remote bind enabled
    that set gains exactly one further name — the configured IP, taught to it
    by ``web.set_remote_host`` at startup — and every hostile host below is
    still refused, which ``TestRemoteBindDoesNotWidenTheHostAllowlist`` pins.

    ``client=`` is loopback for the same reason and a separate one:
    ``RemoteAccessGuard`` classifies a peer from ``scope["client"]``, and
    TestClient's own default of ``("testclient", 50000)`` is unparseable as an
    address, so it reads as **remote** and every route outside the remote
    allowlist answers 403. A test that means "a request from this machine" has
    to say so in the scope.
    """
    c = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    # Patch the post method to add Origin by default
    _original_post = c.post
    def _post_with_origin(*args, **kwargs):
        headers = kwargs.get("headers", {})
        if "Origin" not in headers and "origin" not in headers:
            headers["Origin"] = "http://127.0.0.1"
            kwargs["headers"] = headers
        return _original_post(*args, **kwargs)
    c.post = _post_with_origin
    return c


def test_post_rejected_for_non_loopback_host():
    """DNS-rebinding defense: a POST arriving with a non-loopback Host is refused (403),
    even when the Origin matches the (attacker-controlled) Host."""
    # A loopback peer on purpose: this pins the *Host* guard, so the request
    # must reach it rather than being refused earlier by `RemoteAccessGuard`
    # for coming from a non-loopback address.
    rebind_client = TestClient(app, base_url="http://evil.com",
                               client=("127.0.0.1", 50000))
    resp = rebind_client.post(
        "/api/pin-folder",
        json={"folder": "C:\\projects\\myapp"},
        headers={"Origin": "http://evil.com"},
    )
    assert resp.status_code == 403


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


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = [_make_session(cwd=workspace)]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert workspace in resp.text or Path(workspace).name in resp.text
    assert "1</span>" in resp.text or "card-count" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_empty(mock_discover, mock_providers, mock_config, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    mock_providers.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "No workspaces found" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_stale(mock_discover, mock_providers, mock_sessions, client):
    mock_discover.return_value = [("C:\\nonexistent\\path\\xyz", 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = [_make_session(cwd="C:\\nonexistent\\path\\xyz")]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "stale" in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_error(mock_discover, mock_providers, client):
    mock_discover.side_effect = RuntimeError("db unavailable")
    mock_providers.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "Error" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_filters(mock_discover, mock_config, client, tmp_path):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    workspace = str(tmp_path)
    mock_discover.return_value = [
        (workspace, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
        ("C:\\other\\project", 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]

    resp = client.get(f"/search?q={Path(workspace).name}")
    assert resp.status_code == 200
    assert Path(workspace).name in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_no_results(mock_discover, mock_config, client, tmp_path):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "", "kiro-cli")]

    resp = client.get("/search?q=zzzznotfound")
    assert resp.status_code == 200
    assert "No results" in resp.text


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_settings(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "-a --verbose",
        "color": "",
        "enabled": True,
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert saved.provider_settings["kiro-cli"]["default_args"] == "-a --verbose"
    assert saved.provider_settings["kiro-cli"]["enabled"] is True


@patch("power_atlas.web.load_config")
def test_get_provider_settings(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a", "color": "", "enabled": True},
    })

    resp = client.get("/api/provider/kiro-cli")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "kiro-cli"
    assert body["default_args"] == "-a"
    assert body["enabled"] is True


@patch("power_atlas.web.load_config")
def test_get_provider_settings_default(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.get("/api/provider/claude-code")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "claude-code"
    assert body["default_args"] == ""
    assert body["enabled"] is True


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces")
def test_session_row_shows_all_fields(mock_discover, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [_make_session(
        cwd=workspace, title="my title",
        first_prompt="first question", last_prompt="last question",
        last_reply_tail="final answer",
    )]

    resp = client.get("/partials/sessions", params={"cwd": workspace})
    assert "my title" in resp.text
    assert "first question" in resp.text
    assert "last question" in resp.text or "final answer" in resp.text  # new template shows last_reply not last_prompt
    assert "final answer" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_folder_empty_sessions(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 0, "", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = []

    resp = client.get("/partials/workspaces")
    assert "Loading" in resp.text or "workspace-card" in resp.text



@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_pin_session(mock_sessions, mock_config, mock_save, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_sessions.return_value = []
    resp = client.post("/api/pin-session", json={"session_id": "sess-1"},
                       headers={"X-Workspace": "C:\\app", "Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "sess-1" in saved.pinned_sessions


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_unpin_session(mock_sessions, mock_config, mock_save, client):
    from power_atlas.config import Config
    mock_config.return_value = Config(pinned_sessions=["sess-1", "sess-2"])
    mock_sessions.return_value = []
    resp = client.post("/api/unpin-session", json={"session_id": "sess-1"},
                       headers={"X-Workspace": "C:\\app", "Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "sess-1" not in saved.pinned_sessions


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_folders_merged(mock_discover, mock_sessions, mock_config, mock_providers, client, tmp_path):
    from power_atlas.config import Config
    workspace = str(tmp_path)
    pinned = "C:\\my-pinned-workspace"
    mock_config.return_value = Config(pinned_folders=[pinned])
    mock_discover.return_value = [(workspace, 0, "", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = []
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Pinned workspace appears even though not in discovery results (0 sessions)
    assert pinned in resp.text or "my-pinned-workspace" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_pinned_workspaces_provider_filter(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Provider filter on unified workspaces panel shows only matching pinned workspaces."""
    from power_atlas.config import Config
    ws_kiro = str(tmp_path / "kiro-proj")
    ws_claude = str(tmp_path / "claude-proj")
    mock_config.return_value = Config(pinned_folders=[ws_kiro, ws_claude])
    mock_discover.return_value = [
        (ws_kiro, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws_claude, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Filter by kiro-cli — only kiro workspace should appear
    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    assert "kiro-proj" in resp.text
    assert "claude-proj" not in resp.text

    # Filter by claude-code — only claude workspace should appear
    resp = client.get("/partials/workspaces?provider=claude-code")
    assert resp.status_code == 200
    assert "claude-proj" in resp.text
    assert "kiro-proj" not in resp.text

    # No filter (all) — both should appear
    resp = client.get("/partials/workspaces?provider=all")
    assert resp.status_code == 200
    assert "kiro-proj" in resp.text
    assert "claude-proj" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.discover_workspaces")
def test_pinned_sessions_sorted_first(mock_discover, mock_sessions, mock_config, client, tmp_path):
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(pinned_sessions=["sess-2"])
    mock_discover.return_value = [workspace]
    mock_sessions.return_value = [
        _make_session(session_id="sess-1", title="unpinned", cwd=workspace),
        _make_session(session_id="sess-2", title="pinned", cwd=workspace),
    ]
    resp = client.get("/partials/sessions", params={"cwd": workspace})
    assert resp.status_code == 200
    # Pinned should appear before unpinned
    assert resp.text.index("pinned") < resp.text.index("unpinned")


# --- Phase 1: Simplified pin/unpin and provider=all sessions ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_pin_folder_simple(mock_load, mock_save, client):
    """Pin folder API accepts just folder path (no provider)."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/pin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert "C:\\projects\\myapp" in saved.pinned_folders


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_pin_folder_no_duplicate(mock_load, mock_save, client):
    """Pin folder API does not duplicate already-pinned paths."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\projects\\myapp"])
    resp = client.post("/api/pin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_unpin_folder_simple(mock_load, mock_save, client):
    """Unpin folder API removes path from list."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\projects\\myapp", "C:\\other"])
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\projects\\myapp"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert "C:\\projects\\myapp" not in saved.pinned_folders
    assert "C:\\other" in saved.pinned_folders


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_unpin_folder_not_present(mock_load, mock_save, client):
    """Unpin folder API is no-op for non-pinned path."""
    from power_atlas.config import Config
    mock_load.return_value = Config(pinned_folders=["C:\\other"])
    resp = client.post("/api/unpin-folder", json={"folder": "C:\\nonexistent"},
                       headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    mock_save.assert_not_called()


@patch("power_atlas.web.data.PROVIDERS")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_partials_sessions_provider_all(mock_get_sessions, mock_config, mock_providers, client, tmp_path):
    """provider=all merges sessions from all providers sorted by updated_at."""
    from power_atlas.config import Config
    from unittest.mock import MagicMock

    workspace = str(tmp_path)
    mock_config.return_value = Config()

    # Create mock providers
    kiro_mod = MagicMock()
    kiro_mod.is_available.return_value = True
    claude_mod = MagicMock()
    claude_mod.is_available.return_value = True

    mock_providers.items.return_value = [("kiro-cli", kiro_mod), ("claude-code", claude_mod)]

    # get_sessions returns different sessions per provider
    def side_effect(cwd, prov):
        if prov == "kiro-cli":
            return [_make_session(session_id="k1", title="kiro session", updated_at="2026-06-17T14:00:00")]
        elif prov == "claude-code":
            return [_make_session(session_id="c1", title="claude session", updated_at="2026-06-17T15:00:00")]
        return []

    mock_get_sessions.side_effect = side_effect

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "all"})
    assert resp.status_code == 200
    # Both sessions present
    assert "kiro session" in resp.text
    assert "claude session" in resp.text
    # Claude session (newer) should appear first
    assert resp.text.index("claude session") < resp.text.index("kiro session")


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_sessions")
def test_partials_sessions_single_provider(mock_get_sessions, mock_config, client, tmp_path):
    """provider=kiro-cli still works (single provider mode)."""
    from power_atlas.config import Config

    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_get_sessions.return_value = [
        _make_session(session_id="k1", title="kiro only", cwd=workspace),
    ]

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "kiro-cli"})
    assert resp.status_code == 200
    assert "kiro only" in resp.text


class TestSaveSettingAllowlist:
    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_unknown_key(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "__class__", "value": "evil"},
                           headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "unknown" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_terminal_command(self, mock_load, mock_save, client):
        """terminal_command is no longer a valid setting — profile edits go through /api/launch-profile/save."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "terminal_command", "value": "wt.exe"},
                           headers={"Origin": "http://127.0.0.1"})
        body = resp.json()
        assert body["ok"] is False
        assert "unknown" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_rejects_wrong_type(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "port", "value": "not_int"},
                           headers={"Origin": "http://127.0.0.1"})
        body = resp.json()
        assert body["ok"] is False
        assert "type" in body["error"].lower()
        mock_save.assert_not_called()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_accepts_valid_setting(self, mock_load, mock_save, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/save-setting", json={"key": "peek_hotkey", "value": "ctrl+shift+x"},
                           headers={"Origin": "http://127.0.0.1"})
        body = resp.json()
        assert body["ok"] is True
        mock_save.assert_called_once()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_valid(mock_load, mock_save, client):
    """Valid port value is accepted and saved."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 8080}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is True
    saved = mock_save.call_args[0][0]
    assert saved.port == 8080


@patch("power_atlas.web.load_config")
def test_save_setting_port_bool_rejected(mock_load, client):
    """Boolean value for port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": True}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.load_config")
def test_save_setting_port_out_of_range(mock_load, client):
    """Out-of-range port is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 99999}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is False


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_port_zero_accepted(mock_load, mock_save, client):
    """Port 0 (random mode) is accepted."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/save-setting", json={"key": "port", "value": 0}, headers={"Origin": "http://127.0.0.1"})
    assert resp.json()["ok"] is True


# --- Phase 4: session-tail endpoint ---


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="**hello** user")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_returns_messages(mock_tail, mock_first, mock_cache, client):
    mock_tail.return_value = ["message one", "message two"]
    mock_cache.get.return_value = [
        Session(session_id="aabbccdd-1234-5678-abcd-ef0123456789", title="My Session", cwd="C:\\Projects\\myapp",
                created_at="", updated_at="", first_prompt="", last_prompt="fix the bug", last_reply_tail=""),
    ]
    resp = client.get("/partials/session-tail?sid=aabbccdd-1234-5678-abcd-ef0123456789&cwd=C%3A%5CProjects%5Cmyapp")
    assert resp.status_code == 200
    assert "message one" in resp.text
    assert "message two" in resp.text
    assert "tail-line" in resp.text
    assert "tail-header" in resp.text
    assert "tail-workspace" in resp.text
    assert "myapp" in resp.text
    assert "tail-label" in resp.text
    assert "My Session" in resp.text
    assert "tail-session-id" in resp.text
    assert "aabbccdd" in resp.text        # session_id[:8] rendered
    assert "fix the bug" in resp.text     # last_prompt rendered
    assert "User last message" in resp.text
    assert "<p>" in resp.text             # mistune rendered markdown (not raw text)
    assert "<strong>" in resp.text        # **hello** → <strong>hello</strong>


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_renders_markdown_table(mock_tail, mock_first, mock_cache, client):
    """Pipe tables reach the tooltip as a table, which needs mistune's `table` plugin.

    Tables are GFM and not CommonMark, so `create_markdown()` alone leaves them
    as literal pipes that `.tail-md`'s `white-space: normal` then collapses onto
    one line — which is what an agent's benchmark table looked like in the
    tooltip before the plugin was enabled. Asserting the delimiter row is gone
    is the half that fails if the plugin is ever dropped: the cell text itself
    survives either way.
    """
    mock_tail.return_value = [
        "Here is the breakdown:\n\n"
        "| model | p50 | p90 |\n"
        "|---|---|---|\n"
        "| claude-sonnet-4.6 | 1.46 | 2.85 |\n"
    ]
    mock_cache.get.return_value = None
    resp = client.get("/partials/session-tail?sid=aabbccdd-1234-5678-abcd-ef0123456789&cwd=C%3A%5CTest")
    assert resp.status_code == 200
    assert "<table>" in resp.text
    assert "<th>model</th>" in resp.text
    assert "<td>claude-sonnet-4.6</td>" in resp.text
    assert "|---|" not in resp.text        # delimiter row consumed, not shown as prose
    assert "Here is the breakdown" in resp.text  # prose around the table survives


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="hello user")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_graceful_no_cache(mock_tail, mock_first, mock_cache, client):
    """When session is not in cache, title is empty but tooltip still renders."""
    mock_tail.return_value = ["agent reply"]
    mock_cache.get.return_value = None  # Cache miss
    resp = client.get("/partials/session-tail?sid=aabbccdd-1234-5678-abcd-ef0123456789&cwd=C%3A%5CProjects%5Cmyapp")
    assert resp.status_code == 200
    assert "agent reply" in resp.text
    assert "tail-workspace" in resp.text  # workspace name from Path(cwd).name still shows
    assert "myapp" in resp.text
    assert "tail-title" not in resp.text  # no title when not in cache
    assert "User last message" in resp.text
    assert "\u2014" in resp.text  # em-dash fallback rendered for empty first_prompt and last_prompt


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_empty(mock_tail, mock_first, mock_cache, client):
    mock_tail.return_value = []
    mock_cache.get.return_value = None
    resp = client.get("/partials/session-tail?sid=aabbccdd-1234-5678-abcd-ef0123456789")
    assert resp.status_code == 200
    assert "tail-empty" in resp.text
    assert "No recent output" in resp.text


@patch("power_atlas.web.data.session_cache")
@patch("power_atlas.web.data.get_first_prompt", return_value="<script>alert(1)</script>")
@patch("power_atlas.web.data.get_session_tail")
def test_session_tail_xss_stripped(mock_tail, mock_first, mock_cache, client):
    """mistune escape=True entity-encodes raw HTML tags; JS-URL hrefs (javascript:) are sanitized via mistune's HTMLRenderer.safe_url() unconditionally. Output is safe for | safe filter.

    The third message puts both payloads inside table cells. A new container is
    exactly where an allowlist gets bypassed, so the guarantees are asserted on
    the path the `table` plugin added rather than only on the paragraph path
    they were first measured on.
    """
    mock_tail.return_value = [
        "<script>evil()</script>",
        "[click](javascript:alert(1))",
        "| a | b |\n|---|---|\n| <script>cell()</script> | [x](javascript:alert(2)) |\n",
    ]
    mock_cache.get.return_value = None
    resp = client.get("/partials/session-tail?sid=deadbeef-dead-beef-dead-beefdeadbeef&cwd=C%3A%5CTest")
    assert resp.status_code == 200
    assert "<script>" not in resp.text          # raw tags not present
    assert "&lt;script&gt;" in resp.text        # entity-encoded form IS present (confirms _md was invoked)
    assert "javascript:alert" not in resp.text  # mistune's HTMLRenderer.safe_url() replaces javascript: href with #harmful-link
    assert "<td>" in resp.text                  # the table branch really ran, so the three assertions above cover it too
    assert "&lt;script&gt;cell()" in resp.text  # the in-cell payload specifically, entity-encoded


def test_session_tail_invalid_sid(client):
    """Invalid sid format returns 400 without calling data functions."""
    resp = client.get("/partials/session-tail?sid=not-a-uuid&cwd=C%3A%5CTest")
    assert resp.status_code == 400
    assert "Invalid session id" in resp.text


# --- Phase 3: custom launcher CRUD ---


@patch("power_atlas.web.icons.extract_icon")
@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_launcher_create(mock_load, mock_save, mock_extract, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/launcher/create", json={
        "name": "Dev Server", "command": "npm", "custom_args": "start", "cwd": "C:\\proj", "color": "#ef4444"
    })
    assert resp.status_code == 200
    assert "created" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert len(saved.custom_launchers) == 1
    assert saved.custom_launchers[0]["name"] == "Dev Server"
    assert saved.custom_launchers[0]["id"]  # UUID generated


@patch("power_atlas.web.icons.remove_icon")
@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_launcher_delete(mock_load, mock_save, mock_remove_icon, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[{"id": "abc", "name": "x", "command": "y"}])
    resp = client.post("/api/launcher/delete", json={"id": "abc"})
    assert resp.status_code == 200
    assert "deleted" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert len(saved.custom_launchers) == 0


@patch("power_atlas.web.launcher.launch_custom")
@patch("power_atlas.web.load_config")
def test_launcher_run(mock_load, mock_launch, client, tmp_path):
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))
    resp = client.post("/api/launcher/run", json={
        "name": "test", "command": "npm", "custom_args": "start", "cwd": str(tmp_path)
    })
    assert resp.status_code == 200
    assert "started" in resp.text.lower()
    mock_launch.assert_called_once()


# --- Machine-driven session origin -----------------------------------------
#
# `kind` and `entrypoint` together say whether a person is sitting in front of
# a session. Measured on Claude Code 2.1.221 (2026-08-04): `kind` alone is not
# enough, because it is read straight from CLAUDE_CODE_SESSION_KIND and so
# stays "interactive" for a plain `claude -p`. Both real observations are
# pinned below so a future refactor cannot quietly regress to `kind` only.

def test_session_origin_plain_sdk_run_is_badged():
    """The observed `claude -p` sidecar: kind=interactive, entrypoint=sdk-cli.

    This is the case keying on `kind` alone would miss, and it is the common
    one — every script, hook and CI invocation looks like this.
    """
    from power_atlas.web import _session_origin
    assert _session_origin("interactive", "sdk-cli") == "sdk"


def test_session_origin_bg_kind_wins_over_entrypoint():
    """The observed CLAUDE_CODE_SESSION_KIND=bg sidecar: kind=bg, entry=sdk-cli.

    `kind` is the more specific claim, so it wins when both are informative.
    """
    from power_atlas.web import _session_origin
    assert _session_origin("bg", "sdk-cli") == "bg"


def test_session_origin_daemon_kinds():
    from power_atlas.web import _session_origin
    assert _session_origin("daemon", "cli") == "daemon"
    assert _session_origin("daemon-worker", "cli") == "daemon-worker"


def test_session_origin_ordinary_interactive_session_is_not_badged():
    """The observed terminal sidecar: kind=interactive, entrypoint=cli."""
    from power_atlas.web import _session_origin
    assert _session_origin("interactive", "cli") == ""


def test_session_origin_defers_on_unknown_and_absent():
    """Unknown values yield no badge, matching _map_reported_status's contract.

    "" for both is the kiro-cli case: its lock carries neither field.
    """
    from power_atlas.web import _session_origin
    assert _session_origin("", "") == ""
    assert _session_origin("some-future-kind", "some-future-entrypoint") == ""
    assert _session_origin("", "mcp") == ""  # real value, deliberately unlisted


def test_row_origin_reads_the_snapshot():
    from types import SimpleNamespace
    from power_atlas.presence import Snapshot
    from power_atlas.web import _row_origin
    snap = Snapshot(set(), set(), {}, {}, {},
                    {("claude-code", "s1"): "bg"},
                    {("claude-code", "s1"): "sdk-cli"})
    assert _row_origin(snap, SimpleNamespace(session_id="s1"), "claude-code") == "bg"
    # A session the snapshot knows nothing about — the historical-row case.
    assert _row_origin(snap, SimpleNamespace(session_id="s2"), "claude-code") == ""


def _render_session_row(**over):
    """Render partials/session_row.html with the minimum viable context."""
    from types import SimpleNamespace
    from power_atlas.web import templates
    ctx = dict(
        request=None,
        session=SimpleNamespace(session_id="s1", title="A session",
                                updated_at="2026-08-04T12:00:00",
                                first_prompt="", last_reply_tail="", cwd="C:\\p"),
        cwd="C:\\p", stale=False, pinned_sessions=[], provider_name="claude-code",
        provider_color="", show_workspace=False, workspace_name="",
        status="working", waiting_detail=("", ""),
    )
    ctx.update(over)
    return templates.get_template("partials/session_row.html").render(**ctx)


def test_session_row_renders_the_origin_badge():
    html = _render_session_row(origin="bg")
    assert 'class="session-origin"' in html
    assert ">bg<" in html
    # The badge must not swallow the title beside it.
    assert "A session" in html


def test_session_row_omits_the_badge_for_interactive_sessions():
    html = _render_session_row(origin="")
    assert "session-origin" not in html
    assert "A session" in html


def test_session_row_survives_a_missing_origin_key():
    """Every render site passes `origin`, but the template must not hard-depend.

    Three call sites pass it today; a fourth added later that forgets would
    otherwise raise at render time rather than simply omitting the badge.
    """
    from types import SimpleNamespace
    from power_atlas.web import templates
    html = templates.get_template("partials/session_row.html").render(
        request=None,
        session=SimpleNamespace(session_id="s1", title="A session",
                                updated_at="", first_prompt="",
                                last_reply_tail="", cwd=""),
        cwd="", stale=True, pinned_sessions=[], provider_name="claude-code",
        provider_color="", status="", waiting_detail=("", ""),
    )
    assert "session-origin" not in html
    assert "A session" in html


def test_origin_badge_class_is_styled():
    """The badge is typographic, so an unstyled one renders as stray text.

    style.css and the template are edited in different files by different
    changes; this is the cheapest guard that they stayed in step.
    """
    from pathlib import Path
    import power_atlas
    css = (Path(power_atlas.__file__).parent / "static" / "style.css").read_text(
        encoding="utf-8")
    assert ".session-origin" in css


def test_snapshot_origin_fields_are_trailing_and_optional():
    """Older positional construction sites must keep working unchanged.

    tests/test_web.py builds Snapshot positionally and tests/test_data.py by
    keyword; a field inserted anywhere but the end silently rebinds an existing
    argument at every positional site.
    """
    from power_atlas.presence import Snapshot
    snap = Snapshot(set(), set(), {}, {"k": "v"}, {"r": "w"})
    assert snap.session_kind("claude-code", "s1") == ""
    assert snap.session_entrypoint("claude-code", "s1") == ""


# --- Launcher env is not served in bulk ------------------------------------
#
# A custom launcher's `env` holds production credentials on the real machine.
# It used to travel on three unauthenticated paths; these pin that it travels
# on none of them, and that the one route which does serve it is POST (so
# `same_origin_guard`'s Origin check applies) and per-launcher.

_LAUNCHER_WITH_SECRET = {
    "id": "prod-1", "name": "Prod", "command": "app.exe",
    "env": {"AUTH_TOKEN_PRODUCTION": "s3cret-value"},
}


@patch("power_atlas.web.load_config")
def test_api_launchers_omits_env(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    resp = client.get("/api/launchers")
    assert resp.status_code == 200
    assert "s3cret-value" not in resp.text
    assert "env" not in resp.json()[0]
    # The rest of the entry still arrives — this strips one key, not the row.
    assert resp.json()[0]["name"] == "Prod"


@patch("power_atlas.web.load_config")
def test_api_settings_omits_launcher_env(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert "s3cret-value" not in resp.text
    assert "env" not in resp.json()["custom_launchers"][0]


@patch("power_atlas.web.load_config")
def test_index_bootstrap_omits_launcher_env(mock_load, client):
    """The `|tojson` payload lands in page source, so this is the view-source case."""
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    resp = client.get("/")
    assert resp.status_code == 200
    assert "s3cret-value" not in resp.text


@patch("power_atlas.web.load_config")
def test_launchers_without_env_does_not_mutate_config(mock_load, client):
    """The stripped dicts must be copies of the live config, never the config.

    `custom_launchers` entries are the process's own state. Popping `env` in
    place would empty it here and the next `save_config` would write the
    credentials out of the file entirely — turning a disclosure fix into data
    loss.
    """
    from power_atlas.config import Config
    cfg = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    mock_load.return_value = cfg
    client.get("/api/launchers")
    client.get("/api/settings")
    client.get("/")
    assert cfg.custom_launchers[0]["env"] == {"AUTH_TOKEN_PRODUCTION": "s3cret-value"}


@patch("power_atlas.web.load_config")
def test_launcher_env_route_returns_one_launchers_env(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    resp = client.post("/api/launcher/env", json={"id": "prod-1"})
    assert resp.status_code == 200
    assert resp.json() == {"env": {"AUTH_TOKEN_PRODUCTION": "s3cret-value"}}


@patch("power_atlas.web.load_config")
def test_launcher_env_route_404s_unknown_id(mock_load, client):
    """404, not an empty env.

    The modal writes back whatever it renders, so "this launcher is gone" and
    "this launcher has no variables" must not look the same on the wire — an
    empty answer to a stale id would let a save wipe a real launcher.
    """
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    resp = client.post("/api/launcher/env", json={"id": "nope"})
    assert resp.status_code == 404


@patch("power_atlas.web.load_config")
def test_launcher_env_route_rejects_missing_id(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    assert client.post("/api/launcher/env", json={}).status_code == 400
    assert client.post("/api/launcher/env", json={"id": ""}).status_code == 400


@patch("power_atlas.web.launcher.launch_custom")
@patch("power_atlas.web.load_config")
def test_launcher_run_resolves_env_from_stored_launcher(mock_load, mock_launch, client):
    """Running by id uses the stored env, ignoring whatever the body claims.

    The page no longer holds `env`, so without this the launcher would start
    with none — the regression this whole change would otherwise have shipped.
    """
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config(custom_launchers=[dict(_LAUNCHER_WITH_SECRET)])
    mock_launch.return_value = LaunchResult(True, None, "C:\\tmp")
    resp = client.post("/api/launcher/run", json={
        "id": "prod-1", "command": "app.exe", "env": {"SPOOFED": "from-the-body"},
    })
    assert resp.status_code == 200
    assert mock_launch.call_args.kwargs["env"] == {"AUTH_TOKEN_PRODUCTION": "s3cret-value"}


@patch("power_atlas.web.launcher.launch_custom")
@patch("power_atlas.web.load_config")
def test_launcher_run_without_id_still_takes_body_env(mock_load, mock_launch, client):
    """The ad-hoc path (no stored launcher) keeps its existing contract."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, "C:\\tmp")
    resp = client.post("/api/launcher/run", json={
        "command": "npm", "env": {"NODE_ENV": "development"},
    })
    assert resp.status_code == 200
    assert mock_launch.call_args.kwargs["env"] == {"NODE_ENV": "development"}


@patch("power_atlas.web.icons.has_icon", return_value=False)
@patch("power_atlas.web.load_config")
def test_launcher_icon_fallback_terminal(mock_load, mock_has, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[{"id": "abc", "terminal": True, "command": "kiro-cli"}])
    resp = client.get("/api/launcher-icon/abc")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert "polyline" in resp.text  # terminal icon has polyline


@patch("power_atlas.web.icons.has_icon", return_value=False)
@patch("power_atlas.web.load_config")
def test_launcher_icon_fallback_app(mock_load, mock_has, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[{"id": "xyz", "terminal": False, "command": "app.exe"}])
    resp = client.get("/api/launcher-icon/xyz")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert "circle" in resp.text  # app icon has circle


@patch("power_atlas.web.icons.icon_path")
@patch("power_atlas.web.icons.has_icon", return_value=True)
def test_launcher_icon_serves_png(mock_has, mock_path, client, tmp_path):
    # Create a fake PNG file
    fake_png = tmp_path / "test.png"
    fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    mock_path.return_value = fake_png
    resp = client.get("/api/launcher-icon/abc")
    assert resp.status_code == 200
    assert "image/png" in resp.headers["content-type"]


# --- Phase 2: Provider tabs and filtering ---


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_provider_filter(mock_discover, mock_providers, client, tmp_path):
    """Filtering by provider shows only groups containing that provider, but preserves multi-provider info."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 3, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    # Cards no longer have data-provider; verify the workspace is rendered with the provider icon
    assert 'provider--kiro-cli' in resp.text
    assert 'workspace-card' in resp.text
    # Always discovers all providers, then filters post-grouping
    mock_discover.assert_any_call(provider=None)


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_partials_workspaces_all_tab(mock_discover, mock_providers, client, tmp_path):
    """All tab shows cards from all providers interleaved."""
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    ws3 = str(tmp_path / "proj3")
    mock_discover.return_value = [
        (ws1, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws2, 1, "2026-01-01T00:00:00Z", "claude-code"),
        (ws3, 1, "2026-01-03T00:00:00Z", "kiro-ide"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code", "kiro-ide"]

    resp = client.get("/partials/workspaces?provider=all")
    assert resp.status_code == 200
    # Cards no longer have data-provider; verify all provider icons appear
    assert 'provider--kiro-cli' in resp.text
    assert 'provider--claude-code' in resp.text
    assert 'provider--kiro-ide' in resp.text
    # Verify discover was called with provider=None (all)
    mock_discover.assert_any_call(provider=None)


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tab_hidden_single_provider(mock_discover, mock_providers, client, tmp_path):
    """When only one provider available, no tab bar rendered in partials (tabs now static in index.html)."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Tab bar no longer rendered inline by partials_workspaces (moved to static HTML)
    assert "provider-tabs" not in resp.text
    assert "provider-filter" not in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tab_shown_multiple_providers(mock_discover, mock_providers, client, tmp_path):
    """When multiple providers available, /api/available-providers returns them for the static filter."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Tab bar no longer rendered inline by partials_workspaces
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "provider-tabs" not in resp.text

    # Instead, /api/available-providers returns the provider list for JS-rendered filter
    resp = client.get("/api/available-providers")
    assert resp.status_code == 200
    providers = resp.json()
    names = [p["name"] for p in providers]
    assert "kiro-cli" in names
    assert "claude-code" in names
    assert all("display" in p and "color" in p for p in providers)


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_has_data_provider(mock_discover, mock_providers, client, tmp_path):
    """Workspace cards no longer have data-provider; they show provider icons and colored border."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "claude-code")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # data-provider removed; card is workspace-level now
    assert 'data-provider=' not in resp.text
    # Provider color shown via gradient span (solid color for single provider)
    assert "provider-gradient" in resp.text
    assert "#c2590f" in resp.text
    # Provider icon badge
    assert "provider-icon-badge" in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_has_provider_icon_img(mock_discover, mock_providers, client, tmp_path):
    """Workspace cards include provider icon img tag with fallback badge."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'provider-icon-badge' in resp.text
    assert 'src="/api/launcher-icon/provider--kiro-cli"' in resp.text
    assert 'provider-badge-fallback' in resp.text
    # Title now uses the display name
    assert 'title="kiro-cli"' in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_uses_user_configured_color(mock_discover, mock_providers, mock_config, client, tmp_path):
    """When user sets a custom color in provider_settings, workspace card uses it."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "", "color": "#ff0000", "enabled": True},
    })
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "#ff0000" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_empty_provider_tab_shows_helper(mock_discover, mock_providers, mock_config, client):
    """When a filtered provider has no results, a helper message is shown."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = []
    mock_providers.return_value = ["kiro-cli", "claude-code", "kiro-ide"]

    resp = client.get("/partials/workspaces?provider=claude-code")
    assert resp.status_code == 200
    assert "No Claude Code sessions found" in resp.text

    resp = client.get("/partials/workspaces?provider=kiro-ide")
    assert resp.status_code == 200
    assert "No Kiro IDE sessions found" in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_active_tab_class(mock_discover, mock_providers, client, tmp_path):
    """Provider filter is now client-side (via /api/available-providers); tabs no longer in partials."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Request kiro-cli filtered view — no tab bar in response (tabs are static HTML now)
    resp = client.get("/partials/workspaces?provider=kiro-cli")
    assert resp.status_code == 200
    assert "provider-tabs" not in resp.text
    # The endpoint still filters correctly by provider
    assert "workspace-card" in resp.text


# --- Phase 4: Selection-aware launcher batch ---


@patch("power_atlas.web.launcher.launch_custom_batch")
@patch("power_atlas.web.load_config")
def test_launcher_run_batch_endpoint(mock_load, mock_batch, client, tmp_path):
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    lid = "test-launcher-id"
    mock_load.return_value = Config(custom_launchers=[{
        "id": lid, "name": "Dev", "command": "npm", "custom_args": "start",
        "cwd": "", "env": {}, "terminal": True, "use_selected_workspaces": True,
    }])
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_batch.return_value = [
        LaunchResult(True, None, ws1),
        LaunchResult(True, None, ws2),
    ]
    resp = client.post("/api/launcher/run-batch", json={"id": lid, "workspaces": [ws1, ws2]})
    assert resp.status_code == 200
    assert "Launched 2" in resp.text
    mock_batch.assert_called_once()
    call_kwargs = mock_batch.call_args
    assert call_kwargs[1]["workspaces"] == [ws1, ws2]


@patch("power_atlas.web.load_config")
def test_launcher_run_batch_not_found(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(custom_launchers=[])
    resp = client.post("/api/launcher/run-batch", json={"id": "nonexistent", "workspaces": ["C:\\proj"]})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower()


# --- Phase 5: Provider settings and default_args ---


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_launch_uses_provider_default_args(mock_load, mock_launch, client, tmp_path):
    """Launch endpoint passes default_args from provider_settings to launch_session."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a --verbose", "color": "", "enabled": True},
    })
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    mock_launch.assert_called_once()
    call_kwargs = mock_launch.call_args[1]
    assert call_kwargs["default_args"] == "-a --verbose"


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_launch_no_provider_settings_uses_empty_default_args(mock_load, mock_launch, client, tmp_path):
    """Launch endpoint passes empty default_args when no provider_settings configured."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    call_kwargs = mock_launch.call_args[1]
    assert call_kwargs["default_args"] == ""


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_disabled_provider_hidden_from_tabs(mock_discover, mock_config, mock_providers, client, tmp_path):
    """Disabling a provider via provider_settings hides it from the tab bar."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(provider_settings={
        "claude-code": {"default_args": "", "color": "", "enabled": False},
    })
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # claude-code tab should not be rendered
    assert "provider=claude-code" not in resp.text
    # kiro-cli tab should still be there (but single provider = no tabs)
    # With only one enabled provider, no tab bar at all
    assert "provider-tabs" not in resp.text


# --- Phase 3 (unification): Provider-launcher tiles in grid ---


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_partials_launchers_provider_tiles_before_custom(mock_load, mock_providers, client):
    """Provider tiles render before custom launchers in the grid."""
    from power_atlas.config import Config
    mock_load.return_value = Config(
        custom_launchers=[{"id": "custom-1", "name": "My Script", "command": "python",
                           "custom_args": "", "cwd": "", "env": {}, "color": "",
                           "terminal": True, "use_selected_workspaces": False}],
        provider_settings={"kiro-cli": {"default_args": "-a", "color": "", "enabled": True}},
    )
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    # Provider tile comes first
    kiro_pos = resp.text.find('data-id="provider--kiro-cli"')
    custom_pos = resp.text.find('data-id="custom-1"')
    assert kiro_pos >= 0, "Provider tile not found"
    assert custom_pos >= 0, "Custom tile not found"
    assert kiro_pos < custom_pos, "Provider tile should appear before custom tile"


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_provider_tile_has_correct_data_id(mock_load, mock_providers, client):
    """Provider tile has data-id='provider--kiro-cli'."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    assert 'data-id="provider--kiro-cli"' in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_disabled_provider_not_in_launcher_grid(mock_load, mock_providers, client):
    """Disabled providers don't appear in the launcher grid."""
    from power_atlas.config import Config
    mock_load.return_value = Config(
        provider_settings={"kiro-cli": {"default_args": "", "color": "", "enabled": False}},
    )
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    assert 'provider--kiro-cli' not in resp.text


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tab_bar_no_gear_icons(mock_discover, mock_providers, client, tmp_path):
    """Tab bar no longer has gear icons (no tab-gear class)."""
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "tab-gear" not in resp.text


@patch("power_atlas.web.launcher.launch_custom_batch")
@patch("power_atlas.web.load_config")
def test_launcher_run_batch_passes_workspace_arg_for_non_terminal(mock_load, mock_batch, client):
    """Non-terminal selection-aware launchers pass pass_workspace_arg=True."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config(
        custom_launchers=[{"id": "gui-1", "name": "IDE", "command": "code",
                           "custom_args": "", "cwd": "", "env": {}, "color": "",
                           "terminal": False, "use_selected_workspaces": True}],
    )
    mock_batch.return_value = [LaunchResult(True, None, "/tmp")]
    resp = client.post("/api/launcher/run-batch", json={"id": "gui-1", "workspaces": ["/tmp"]})
    assert resp.status_code == 200
    mock_batch.assert_called_once()
    call_kwargs = mock_batch.call_args
    assert call_kwargs.kwargs.get("pass_workspace_arg") is True or call_kwargs[1].get("pass_workspace_arg") is True


# --- Phase 3: 3-provider gradient + resume button UX ---


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_three_provider_gradient_has_all_colors(mock_discover, mock_providers, client, tmp_path):
    """Workspace card with 3 providers renders gradient with all three colors and gradient-3plus class."""
    workspace = str(tmp_path)
    mock_discover.return_value = [
        (workspace, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
        (workspace, 1, "2026-01-01T00:00:00Z", "claude-code"),
        (workspace, 1, "2026-01-01T00:00:00Z", "kiro-ide"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code", "kiro-ide"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # All three provider colors present
    assert "#7138cc" in resp.text  # kiro-cli
    assert "#c2590f" in resp.text  # claude-code
    assert "#8b5cf6" in resp.text  # kiro-ide
    # Gradient class for 3+ providers
    assert "gradient-3plus" in resp.text
    # Multi-provider class present
    assert "multi-provider" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_resume_button_kiro_ide_tooltip(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    """Kiro IDE sessions show 'Open workspace in Kiro IDE' tooltip; others show 'Resume session'."""
    from power_atlas.data import Session
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-ide")]
    mock_providers.return_value = ["kiro-ide"]
    mock_sessions.return_value = [Session(
        session_id="test-ide-session",
        title="Test IDE Session",
        cwd=workspace,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        first_prompt="Hello",
        last_prompt="",
        last_reply_tail="",
    )]

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "kiro-ide"})
    assert resp.status_code == 200
    assert 'title="Open workspace in Kiro IDE"' in resp.text
    assert 'aria-label="Open in Kiro IDE"' in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_resume_button_terminal_provider_tooltip(mock_discover, mock_providers, mock_sessions, client, tmp_path):
    """Terminal providers (kiro-cli, claude-code) show 'Resume session' tooltip."""
    from power_atlas.data import Session
    workspace = str(tmp_path)
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]
    mock_sessions.return_value = [Session(
        session_id="test-cli-session",
        title="Test CLI Session",
        cwd=workspace,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        first_prompt="Hello",
        last_prompt="",
        last_reply_tail="",
    )]

    resp = client.get("/partials/sessions", params={"cwd": workspace, "provider": "kiro-cli"})
    assert resp.status_code == 200
    assert 'title="Resume session"' in resp.text
    assert 'aria-label="Resume"' in resp.text


# --- /api/settings endpoint tests ---


@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_api_settings_returns_expected_keys(mock_load, mock_autostart, client):
    """GET /api/settings returns 200 with all expected keys (no terminal_command)."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_autostart.return_value = False

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    expected_keys = {"active_launch_profile", "launch_profiles", "peek_hotkey", "port", "default_directory", "provider_settings", "custom_launchers", "autostart",
                     "acp_max_sessions", "acp_idle_ttl_seconds", "acp_prompt_silence_seconds",
                     "remote_bind_address", "restart_to_apply", "in_force",
                     "restart_pending"}
    assert set(body.keys()) == expected_keys
    assert body["autostart"] is False
    assert "terminal_command" not in body
    # The device secret is served by /api/remote-access alone: this payload is
    # fetched broadly by the dashboard and a credential does not belong in it.
    assert "secret" not in body


@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_api_settings_reflects_config_values(mock_load, mock_autostart, client):
    """GET /api/settings reflects pre-populated config values."""
    from power_atlas.config import Config, LaunchProfile
    mock_load.return_value = Config(
        launch_profiles=[LaunchProfile(id="prod", name="Production", terminal_command="wt.exe")],
        active_launch_profile="prod",
        peek_hotkey="ctrl+shift+z",
        port=8080,
        provider_settings={
            "kiro-cli": {"default_args": "-a", "color": "#ff0000", "enabled": True},
        },
        custom_launchers=[{"name": "my-launcher", "command": "echo hi"}],
    )
    mock_autostart.return_value = True

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_launch_profile"] == "prod"
    assert len(body["launch_profiles"]) == 1
    assert body["launch_profiles"][0]["name"] == "Production"
    assert body["launch_profiles"][0]["terminal_command"] == "wt.exe"
    assert body["peek_hotkey"] == "ctrl+shift+z"
    assert body["port"] == 8080
    assert body["provider_settings"]["kiro-cli"]["default_args"] == "-a"
    assert body["provider_settings"]["kiro-cli"]["color"] == "#ff0000"
    assert body["custom_launchers"] == [{"name": "my-launcher", "command": "echo hi"}]
    assert body["autostart"] is True


@patch("power_atlas.web.autostart.is_enabled")
@patch("power_atlas.web.load_config")
def test_api_settings_autostart_exception_returns_false(mock_load, mock_autostart, client):
    """GET /api/settings returns autostart=False when is_enabled() raises."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_autostart.side_effect = RuntimeError("registry unavailable")

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["autostart"] is False


# --- Phase 3 (Launch Profiles): Same-origin guard tests ---


@pytest.fixture
def raw_client():
    """TestClient WITHOUT default Origin header, for testing the guard itself.

    Loopback ``client=`` for the reason the ``client`` fixture spells out:
    TestClient's default peer is unparseable and reads as remote.
    """
    return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


class TestSameOriginGuard:
    def test_origin_null_rejected(self, raw_client):
        """Origin: null is always rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Origin": "null"})
        assert resp.status_code == 403
        assert "Forbidden" in resp.json()["error"]

    def test_both_absent_rejected(self, raw_client):
        """Request with neither Origin nor Referer is rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080})
        assert resp.status_code == 403

    def test_mismatched_origin_rejected(self, raw_client):
        """Origin from different host is rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Origin": "http://evil.example.com"})
        assert resp.status_code == 403

    def test_mismatched_referer_rejected(self, raw_client):
        """Referer from different host (no Origin) is rejected."""
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Referer": "http://evil.example.com/path"})
        assert resp.status_code == 403

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_valid_origin_accepted(self, mock_load, mock_save, raw_client):
        """Matching Origin header passes the guard."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_valid_referer_only_accepted(self, mock_load, mock_save, raw_client):
        """Matching Referer (without Origin) passes the guard."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Referer": "http://127.0.0.1/some/page"})
        assert resp.status_code == 200

    def test_guard_applies_to_multiple_endpoints(self, raw_client):
        """Multiple POST endpoints are all protected by the guard."""
        endpoints = [
            "/api/pin-folder",
            "/api/unpin-folder",
            "/api/pin-session",
            "/api/unpin-session",
            "/api/launch",
            "/api/provider/save",
            "/api/launch-profile/activate",
            "/api/launch-profile/save",
            "/api/launch-profile/delete",
        ]
        for endpoint in endpoints:
            resp = raw_client.post(endpoint, json={},
                                   headers={"Origin": "null"})
            assert resp.status_code == 403, f"{endpoint} should reject Origin: null"


class TestAcpNavigationGuard:
    """``GET /acp`` is state-changing, which the guard's POST-only scope was
    justified by it not being.

    Rendering the page seeds a socket that sends ``subscribe``, is answered
    ``unknown_session``, and sends ``load`` — which reaches ``ensure_started``
    and spawns ``kiro-cli acp -a``, trust-all-tools. A cross-origin top-level
    navigation was therefore enough to start an agent with no user gesture.
    """

    def test_the_dashboard_row_action_still_works(self, raw_client):
        """``location.href = '/acp?sid=…'`` is a same-origin top-level
        navigation: **no** ``Origin`` at all, because browsers attach it only
        to navigations that are not GET/HEAD, plus a same-origin ``Referer``.
        Copying the POST rule verbatim would have refused this."""
        resp = raw_client.get("/acp?sid=abc", headers={
            "Referer": "http://127.0.0.1/", "Sec-Fetch-Site": "same-origin"})
        assert resp.status_code == 200

    def test_a_bookmark_or_a_typed_address_still_works(self, raw_client):
        """Neither header, which is what a user-initiated load sends. This is
        the case a rule demanding an Origin would break."""
        resp = raw_client.get("/acp", headers={"Sec-Fetch-Site": "none"})
        assert resp.status_code == 200

    def test_a_client_that_sends_no_fetch_metadata_still_works(self, raw_client):
        resp = raw_client.get("/acp")
        assert resp.status_code == 200

    def test_a_cross_origin_fetch_is_refused(self, raw_client):
        resp = raw_client.get("/acp?sid=abc", headers={
            "Origin": "http://evil.example.com", "Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403
        assert _ACP_TOKEN not in resp.text

    def test_a_cross_origin_referer_is_refused(self, raw_client):
        resp = raw_client.get("/acp?sid=abc",
                              headers={"Referer": "https://evil.example.com/x"})
        assert resp.status_code == 403

    def test_a_cross_site_navigation_with_no_referrer_is_refused(self, raw_client):
        """One ``Referrer-Policy: no-referrer`` on the attacker's page strips
        the only header an Origin/Referer rule could have caught this with,
        leaving it indistinguishable from a bookmark. ``Sec-Fetch-Site`` is set
        by the browser and page content cannot influence it."""
        resp = raw_client.get("/acp?sid=abc",
                              headers={"Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 403
        assert _ACP_TOKEN not in resp.text

    def test_origin_null_is_refused(self, raw_client):
        resp = raw_client.get("/acp", headers={"Origin": "null"})
        assert resp.status_code == 403

    @patch("power_atlas.web.autostart.is_enabled")
    @patch("power_atlas.web.load_config")
    def test_the_rule_is_scoped_to_acp_rather_than_every_get(
            self, mock_load, mock_autostart, raw_client):
        """Every other GET here only reads, and browsers omit Origin on
        ordinary navigations — widening the rule to all of them would refuse
        traffic that has no way to declare itself."""
        from power_atlas.config import Config
        mock_load.return_value = Config()
        mock_autostart.return_value = False
        resp = raw_client.get("/api/settings", headers={
            "Referer": "https://evil.example.com/x",
            "Sec-Fetch-Site": "cross-site"})
        assert resp.status_code == 200


class TestHostAllowlistCoversGetRequests:
    """The Host allowlist used to live inside the guard's ``method == "POST"``
    branch, so no GET route was ever Host-checked. A rebound page is same-origin
    with what it fetches, so it could read workspace paths, session titles and
    settings straight out of the response bodies."""

    @pytest.mark.parametrize("host", ["evil.com", "testserver"])
    @pytest.mark.parametrize("path", [
        "/", "/partials/workspaces", "/api/settings", "/search?q=a",
        "/api/tags", "/partials/all-sessions",
    ])
    def test_get_rejected_for_non_loopback_host(self, raw_client, path, host):
        resp = raw_client.get(path, headers={"Host": host})
        assert resp.status_code == 403, f"GET {path} should reject Host: {host}"
        assert resp.json() == {"error": "Forbidden"}

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:4915", "localhost", "[::1]:4915"])
    @patch("power_atlas.web.autostart.is_enabled")
    @patch("power_atlas.web.load_config")
    def test_api_settings_served_on_loopback(self, mock_load, mock_autostart, raw_client, host):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        mock_autostart.return_value = False
        resp = raw_client.get("/api/settings", headers={"Host": host})
        assert resp.status_code == 200, f"GET /api/settings should accept Host: {host}"

    @patch("power_atlas.web.load_config")
    @patch("power_atlas.web.data.available_providers")
    @patch("power_atlas.web.data.discover_workspaces_with_counts")
    def test_partials_workspaces_served_on_loopback(self, mock_discover, mock_providers,
                                                    mock_config, raw_client):
        from power_atlas.config import Config
        mock_config.return_value = Config()
        mock_discover.return_value = []
        mock_providers.return_value = []
        resp = raw_client.get("/partials/workspaces", headers={"Host": "localhost"})
        assert resp.status_code == 200


# --- ACP surface: DNS-rebinding and token-check regressions ---

from starlette.websockets import WebSocketDisconnect
from power_atlas.web import (
    _ACP_TOKEN, _acp_token_ok, _host_allowed, _ws_origin_ok)


class TestSingleLabelHostRejected:
    """``testserver`` was allowlisted so this suite would pass, which made every
    guarded route reachable from a rebound single-label host."""

    def test_post_rejected_for_single_label_host(self, raw_client):
        resp = raw_client.post("/api/save-setting", json={"key": "port", "value": 8080},
                               headers={"Host": "testserver", "Origin": "http://testserver"})
        assert resp.status_code == 403

    def test_acp_page_rejected_for_non_loopback_host(self, raw_client):
        """End-to-end: no rebound Host reaches the token, whichever check stops
        it. Which one actually did is a question this cannot answer, because the
        middleware runs first — see ``TestAcpInlineHostCheck`` for the route's
        own check, tested with the middleware out of the way."""
        for host in ("testserver", "evil.com"):
            resp = raw_client.get("/acp", headers={"Host": host})
            assert resp.status_code == 403, f"GET /acp should reject Host: {host}"
            assert _ACP_TOKEN not in resp.text

    def test_acp_page_served_on_loopback(self, raw_client):
        resp = raw_client.get("/acp")
        assert resp.status_code == 200
        assert _ACP_TOKEN in resp.text


class TestAcpBackLinkMatchesReachability:
    """Phase 5b. The topbar's ``Main dashboard`` link points at ``/``, which
    is not on ``_REMOTE_ALLOWED_PATHS`` and never will be (SC-4) — so from a
    phone it was a control whose only possible outcome was a 403 page with no
    way back to the conversation.

    Derived from ``scope["client"]`` and not the ``Host`` header (D26): a remote
    peer may legitimately send ``Host: 127.0.0.1:4915``, and reading the header
    would hand exactly that peer the link it cannot follow. Nothing here is a
    security decision — the guard has already admitted or refused the request —
    so a wrong reading costs a link, not a boundary.

    The logo is deliberately *not* on this branch. It is served from
    ``/static``, which is on the allowlist, and naming the product is not
    navigation — so it renders for both viewers, and that is what lets the link
    be dropped without the remote page losing its identity.
    """

    def test_a_loopback_viewer_keeps_the_dashboard_link(self, raw_client):
        text = raw_client.get("/acp").text
        link = re.search(r'<a\b[^>]*\bclass="[^"]*topbar-nav[^"]*"[^>]*>', text)
        assert link, "the loopback viewer has no dashboard link in the topbar"
        assert 'href="/"' in link.group(0), (
            f"the dashboard link no longer points at `/`: {link.group(0)}")

    def test_a_remote_viewer_is_not_handed_a_link_they_cannot_follow(
            self, remote_enabled):
        status, body, _ = _peer_http(_ACP_PATH_FOR_BACKLINK, [_cookie_header()])
        assert status == 200, "the page itself must still be served remotely"
        text = body.decode()
        assert 'href="/"' not in text, (
            "the phone was handed a link to a loopback-only page")
        assert "topbar-nav" not in text, (
            "the dashboard link is rendered remotely in some other form")
        # What replaced the old plain-text arm: the logo is unconditional, so
        # dropping the link above costs the page its navigation and not its
        # name. Both renderings are asserted because CSS picks between them by
        # width and only one of the two is on screen at a time.
        assert "poweratlas-banner.png" in text and "acp-wordmark" in text, (
            "the product name vanished entirely for the remote viewer")
        # The positive control for the whole reason this branch exists.
        assert _peer_http("/", [_cookie_header()])[0] == 403

    def test_the_logo_is_reachable_by_the_viewer_it_is_rendered_for(
            self, remote_enabled):
        """The half the assertion above cannot make. Dropping the link is only
        safe if what replaces it actually loads: the banner is an ``/static``
        URL, and if that mount ever leaves ``_REMOTE_ALLOWED_PATHS`` the remote
        page keeps its ``<img>`` and renders a broken one."""
        assert _peer_http(
            "/static/poweratlas-banner.png", [_cookie_header()])[0] == 200


_ACP_PATH_FOR_BACKLINK = "/acp"


class TestTheDashboardLinksToTheAgentPage:
    """The other half of the pair above. `/acp` has linked back to `/` since
    Phase 5b; nothing linked forward, so reaching the agent surface from the
    dashboard meant knowing the path and typing it — which is how a shipped
    feature ends up believed absent.

    Read off the parsed topbar rather than out of a substring of the page,
    because a substring pins the text of a line and not its effect. What has to
    hold is that the anchor is **in the topbar**, that its target is **a route
    that answers**, and that it is an anchor at all — a `<button>` with
    `location.href` looks identical in the markup diff and silently drops
    middle-click, Ctrl-click and "copy link address".
    """

    _LINK = re.compile(r'<a\b([^>]*\bhref="/acp"[^>]*)>(.*?)</a>', re.S)

    def _topbar(self, client):
        page = client.get("/").text
        start = page.find('<div class="topbar">')
        end = page.find('<div class="search-area">')
        assert start >= 0 and end > start, \
            "the dashboard's topbar could not be located; this check is measuring nothing"
        return page[start:end]

    def test_the_topbar_carries_a_link_to_the_agent_page(self, client):
        found = self._LINK.findall(self._topbar(client))
        assert len(found) == 1, (
            "the dashboard topbar has no link to /acp, so the agent surface is "
            "reachable only by typing the path")
        attrs, label = found[0]
        assert "topbar-profile-btn" in attrs, (
            "the link does not wear the topbar's control shape, so it reads as "
            "stray text beside two pill buttons")
        assert "aria-label=" in attrs, "the link is unlabelled for a screen reader"
        assert label.strip(), "the link renders no visible label at all"

    def test_the_link_sits_with_the_logo_and_not_among_the_settings(self, client):
        """Where it is *is* the feature. This link spent its first life in the
        right-hand cluster, which is settings and state — launch profile, remote
        access, autostart, last refresh — and a page link wearing the same pill
        as four settings reads as a fifth one. `topbar-spacer` is the divider
        between the two halves, so "before the spacer" is the machine-checkable
        form of "in the top-left corner, beside the logo", and it does not pin
        the label, the emoji or the order of the settings beside it."""
        topbar = self._topbar(client)
        link = topbar.find('href="/acp"')
        spacer = topbar.find('class="topbar-spacer"')
        banner = topbar.find('class="topbar-banner"')
        assert -1 < banner < link < spacer, (
            "the /acp link is not between the logo and the spacer; it is back "
            f"among the settings cluster (banner={banner}, link={link}, "
            f"spacer={spacer})")

    def test_the_link_points_at_a_route_that_answers(self, raw_client):
        """The half a substring check cannot make: the href has to name a live
        route. A typo'd path is a link to a 404 and looks correct in a diff."""
        href = self._LINK.findall(self._topbar(raw_client))[0][0]
        target = re.search(r'href="([^"]+)"', href).group(1)
        assert raw_client.get(target).status_code == 200, \
            f"the topbar links to {target}, which does not serve"

    def test_the_link_is_absent_from_the_page_it_points_at(self, raw_client):
        """`/acp` has its own back link and does not need a forward one to
        itself. This is the positive control for the check above: without it,
        a page-wide search would pass on `/acp`'s own markup."""
        assert 'href="/acp"' not in raw_client.get("/acp").text


class TestAcpPageIsNotCacheable:
    """``GET /acp`` renders the live ACP token, so its response is a credential.

    Nothing may retain a copy of it — not the browser's disk cache, not an
    intermediary. The token rotates per launch and the page recovers from a
    stale one, so this is depth rather than a live hole.
    """

    def test_the_credential_bearing_response_says_no_store(self, raw_client):
        resp = raw_client.get("/acp")
        assert _ACP_TOKEN in resp.text, "the assertion below would be vacuous"
        assert resp.headers["cache-control"] == "no-store"

    def test_no_other_route_gained_a_caching_header(self, raw_client):
        """``StaticFiles`` deliberately sets none, and the dashboard carries no
        secret. Widening the header beyond ``/acp`` was out of scope."""
        for path in ("/static/style.css", "/api/settings"):
            resp = raw_client.get(path)
            assert resp.status_code == 200, path
            assert "cache-control" not in resp.headers, path


# --- Host header: parsed here, never taken from starlette's URL ---

from fastapi.middleware.asyncexitstack import AsyncExitStackMiddleware

# ``app`` minus every middleware except the one FastAPI's own route handler
# asserts on. Routing and the endpoint run; ``same_origin_guard`` does not, so a
# route's own checks can be tested without the middleware answering ahead of
# them. Wrapping the router rather than reaching into the built stack keeps this
# to one documented FastAPI class, which fails loudly if it ever moves.
_ROUTER_ONLY = AsyncExitStackMiddleware(app.router)


def _raw_asgi(asgi_app, path: str, raw_headers: list[tuple[bytes, bytes]],
              method: str = "GET") -> tuple[int, bytes]:
    """Call an ASGI app with exactly the headers given, byte for byte.

    Both HTTP clients within reach synthesise ``Host`` from the URL they are
    handed and will not send a request without one, so "no Host at all" and
    "two Host headers" are unreachable through them — yet an HTTP/1.0 client
    produces the first by accident and a raw socket sends either without
    complaint. This builds the scope uvicorn would build instead.

    ``asgi_app`` selects the entry point: ``app`` runs ``same_origin_guard``,
    ``_ROUTER_ONLY`` skips it and reaches a route's own checks, which is
    the only way to tell the two apart.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 4915),
        "app": app,
        "state": {},
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(asgi_app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent
                    if m["type"] == "http.response.body")
    return status, body


# Every one of these must be refused. The trailing note on each names the way
# `request.url.hostname` — the value both Host checks used to read — mishandled
# it. Starlette discards a Host that fails its `_HOST_RE` and rebuilds the URL
# from `scope["server"]`, so a rejected Host read back as plain loopback.
_HOSTILE_HOSTS = [
    "evil.com",                  # ordinary rebinding target
    "a_b.evil.com",              # underscore is absent from `_HOST_RE`, so this
    "evil_1.attacker.com",       # read back as 127.0.0.1 and was served
    "[evil",                     # unmatched bracket: `urlsplit` raised
    "[::1",                      # ValueError, i.e. an unauthenticated 500
    "[::1]extra",                # bytes trailing the closing bracket
    "[]",                        # bracketed empty name
    "evil.com@127.0.0.1:4915",   # userinfo: `urlsplit` keeps the last "@" part
    "evil.com@localhost",
    "127.0.0.1:4915.evil.com",   # a hostname smuggled in as the port
    "127.0.0.1:",                # empty port
    "127.0.0.1:1:2",             # two ports
    "::1",                       # bare IPv6, which HTTP requires bracketed
    "localhost.evil.com",        # allowlisted name as a label of another domain
    "testserver",                # single-label name, winnable over LLMNR/mDNS
]

# One route per kind of thing a rebound page could take: the dashboard (which
# embeds `custom_launchers`, whose `env` holds cleartext credentials), the ACP
# token's delivery page, a JSON API, and the static mount — the last because
# `StaticFiles` sits behind the same middleware and was answering 500.
_GUARDED_PATHS = ["/", "/acp", "/api/settings", "/static/style.css"]


class TestHostHeaderIsParsedNotTrusted:
    @pytest.mark.parametrize("host", _HOSTILE_HOSTS)
    @pytest.mark.parametrize("path", _GUARDED_PATHS)
    def test_hostile_host_is_forbidden(self, path, host):
        status, body = _raw_asgi(app, path, [(b"host", host.encode())])
        assert status == 403, f"GET {path} with Host: {host} answered {status}"
        assert b"Forbidden" in body
        assert _ACP_TOKEN.encode() not in body

    @pytest.mark.parametrize("path", _GUARDED_PATHS)
    def test_absent_host_is_forbidden(self, path):
        """A Host-less request used to read as loopback: with no Host header at
        all, starlette's fallback to ``scope["server"]`` is the *only* thing
        left, so ``url.hostname`` was 127.0.0.1 by construction."""
        status, body = _raw_asgi(app, path, [])
        assert status == 403, f"GET {path} without a Host answered {status}"
        assert _ACP_TOKEN.encode() not in body

    @pytest.mark.parametrize("order", [
        [b"127.0.0.1", b"evil.com"],
        [b"evil.com", b"127.0.0.1"],
    ])
    def test_duplicate_host_is_forbidden(self, order):
        """Two Host headers is a smuggling shape, never a browser: which one is
        authoritative differs between hops, so neither is trusted."""
        status, _ = _raw_asgi(app, "/", [(b"host", h) for h in order])
        assert status == 403

    @pytest.mark.parametrize("host", [
        "127.0.0.1", "127.0.0.1:4915", "127.0.0.1:8080",
        "localhost", "localhost:8080", "LOCALHOST", "LocalHost:8080",
        "[::1]", "[::1]:4915",
    ])
    def test_loopback_forms_accepted(self, host):
        status, _ = _raw_asgi(app, "/api/last-refresh", [(b"host", host.encode())])
        assert status == 200, f"Host: {host} is loopback and must be served"

    @pytest.mark.parametrize("raw", _HOSTILE_HOSTS + [
        None, "", "   ", "[", "]", ":", "@", "]:80", "\x00", "\U0001f600",
        "127.0.0.1:٤٩١٥",  # non-ASCII digits: `str.isdigit()` calls these a port
    ])
    def test_unparseable_host_is_rejected_never_raised(self, raw):
        assert _host_allowed(raw) is False


class TestAcpInlineHostCheck:
    """``GET /acp`` repeats the Host check the middleware already runs, because
    it is the ACP token's only delivery vehicle and a narrowing of the
    middleware must not silently un-protect it.

    Every other test of that route goes through the middleware, which answers
    first — so deleting the inline check left the whole suite green. These probe
    ``_ROUTER_ONLY``: routing and the endpoint run, ``same_origin_guard`` does
    not, and the inline check is the only thing that can produce a 403.
    """

    def test_router_alone_serves_acp_on_loopback(self):
        """Anchors the three below: without this, a 403 from the router could
        just as well mean the route never ran."""
        status, body = _raw_asgi(_ROUTER_ONLY, "/acp", [(b"host", b"127.0.0.1:4915")])
        assert status == 200
        assert _ACP_TOKEN.encode() in body

    @pytest.mark.parametrize("host", [
        "evil.com", "a_b.evil.com", "[::1", "evil.com@127.0.0.1", "testserver",
    ])
    def test_inline_check_refuses_without_the_middleware(self, host):
        status, body = _raw_asgi(_ROUTER_ONLY, "/acp", [(b"host", host.encode())])
        assert status == 403, f"/acp served Host: {host} with no middleware above it"
        assert _ACP_TOKEN.encode() not in body

    def test_inline_check_refuses_absent_host(self):
        status, body = _raw_asgi(_ROUTER_ONLY, "/acp", [])
        assert status == 403
        assert _ACP_TOKEN.encode() not in body


class TestWsOriginUnaffectedByTheHostFallback:
    """``_ws_origin_ok`` runs the raw ``Host`` header through ``_host_allowed``,
    the same parser the HTTP path uses, and reads no part of ``ws.url``.

    Middleware never sees an upgrade request, so this is the WebSocket's whole
    defense. Deriving both halves from ``ws.url`` was argued safe on the grounds
    that they then agree with each other; they do not, and which of them is
    wrong depends on the Starlette in front of it — see
    ``TestWsOriginReadsTheRawHostOnEveryStarlette`` below, which is where that
    is pinned in a form this interpreter can observe.

    These cases hold on both: a Host that Starlette 1.3.1 discards takes the
    netloc with it, and the raw parser rejects it outright.
    """

    @staticmethod
    def _ws(host: str | None, origin: str):
        from starlette.websockets import WebSocket
        headers = [(b"origin", origin.encode())]
        if host is not None:
            headers.insert(0, (b"host", host.encode()))
        return WebSocket({
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws",
            "path": "/ws/acp", "raw_path": b"/ws/acp", "query_string": b"",
            "root_path": "", "headers": headers,
            "client": ("127.0.0.1", 54321), "server": ("127.0.0.1", 4915),
        }, receive=None, send=None)

    @pytest.mark.parametrize("host", ["a_b.evil.com", "evil_1.attacker.com"])
    def test_underscore_host_still_fails_its_own_origin(self, host):
        """The exact bypass shape: Host that Starlette discards, Origin that
        matches it. The discarded Host takes the netloc with it."""
        assert _ws_origin_ok(self._ws(host, f"http://{host}")) is False

    def test_loopback_host_with_attacker_origin_rejected(self):
        assert _ws_origin_ok(self._ws("127.0.0.1:4915", "http://evil.com")) is False

    def test_loopback_host_with_matching_origin_accepted(self):
        """Positive control: the rejections above are not a broken helper."""
        assert _ws_origin_ok(self._ws("127.0.0.1:4915", "http://127.0.0.1:4915")) is True

    def test_absent_and_malformed_host_do_not_raise(self):
        """Whatever the verdict, an unparseable Host must not become a traceback
        on the handshake path."""
        assert _ws_origin_ok(self._ws(None, "http://evil.com")) is False
        assert _ws_origin_ok(self._ws("[::1", "http://evil.com")) is False


class _RawWs:
    """Everything ``_ws_origin_ok`` may legitimately read, and nothing else.

    ``url`` raises on purpose. A verdict derived from ``ws.url`` is a verdict
    that differs between Starlette versions, and the two that matter here are
    not the same one: the suite runs on 1.3.1 while the application runs on
    0.37.2 (``__main__.py`` launches uvicorn on the interpreter PowerAtlas is
    installed into, not on the venv). Forbidding the attribute is what makes
    these cases mean the same thing on both.
    """

    def __init__(self, host, origin, scheme="ws", hosts=None):
        from starlette.datastructures import Headers
        headers = []
        for value in (hosts if hosts is not None else ([] if host is None else [host])):
            headers.append((b"host", value.encode()))
        if origin is not None:
            headers.append((b"origin", origin.encode()))
        self.scope = {"type": "websocket", "scheme": scheme, "headers": headers}
        self.headers = Headers(scope=self.scope)

    @property
    def url(self):
        raise AssertionError(
            "_ws_origin_ok read ws.url; its verdict must come from the raw "
            "Host header, which is the only thing both Starlette versions "
            "agree about")


class TestWsOriginReadsTheRawHostOnEveryStarlette:
    """The userinfo trap, and the bracket that raises.

    ``_host_allowed`` exists because ``urlsplit`` keeps only what follows the
    last ``@`` and because ``hostname`` throws on an unmatched bracket. The
    WebSocket route did not use it, and the same two inputs measured
    differently on the two interpreters this project runs on::

                                                             0.37.2   1.3.1
        Host 'evil.com@127.0.0.1:4915' + matching Origin  ->  True     False
        Host '[::1'                    + loopback Origin  ->  ValueError  True

    On 0.37.2 the raw Host goes straight into the URL, so ``hostname`` reads
    ``127.0.0.1`` while ``netloc`` keeps the userinfo and reproduces the
    attacker's Origin exactly — the two halves disagree and the check passes.
    Practical exposure is nil (browsers never emit userinfo in ``Host``, and a
    local process that can set headers already holds the token); what was broken
    is a stated invariant and the fact that no test could observe it, because
    1.3.1's ``_HOST_RE`` refuses both before the function is reached.
    """

    @pytest.mark.parametrize("host, origin, expected", [
        # The 0.37.2 bypass, in the shape that measured True.
        ("evil.com@127.0.0.1:4915", "http://evil.com@127.0.0.1:4915", False),
        ("evil.com@127.0.0.1:4915", "http://127.0.0.1:4915", False),
        # The 0.37.2 ValueError, in the shape that measured a 500.
        ("[::1", "http://127.0.0.1:4915", False),
        ("[::1", "http://[::1", False),
        # Underscores: what 1.3.1 discards and 0.37.2 keeps.
        ("a_b.evil.com", "http://a_b.evil.com", False),
        # A hostname smuggled into the port, and bytes past the bracket.
        ("127.0.0.1:4915.evil.com", "http://127.0.0.1:4915.evil.com", False),
        ("[::1]extra", "http://[::1]extra", False),
        # Loopback, matching and not.
        ("127.0.0.1:4915", "http://127.0.0.1:4915", True),
        ("127.0.0.1:4915", "http://evil.com", False),
        ("127.0.0.1:4915", None, False),
        ("localhost:4915", "http://localhost:4915", True),
        ("[::1]:4915", "http://[::1]:4915", True),
        ("127.0.0.1", "http://127.0.0.1", True),
        # The allowlist is case-insensitive, so the origin comparison has to be
        # too, or a Host the allowlist accepts fails against its own Origin.
        ("LocalHost:4915", "http://localhost:4915", True),
        ("localhost:4915", "HTTP://LocalHost:4915", True),
        # Optional whitespace around a field value is legal HTTP, and
        # ``_host_allowed`` strips it — the expected origin must be built from
        # the same stripped value rather than the padded one.
        (" 127.0.0.1:4915 ", "http://127.0.0.1:4915", True),
    ])
    def test_the_verdict_comes_from_the_header(self, host, origin, expected):
        assert _ws_origin_ok(_RawWs(host, origin)) is expected

    def test_a_secure_socket_expects_a_secure_origin(self):
        assert _ws_origin_ok(
            _RawWs("127.0.0.1:4915", "https://127.0.0.1:4915", scheme="wss")) is True
        assert _ws_origin_ok(
            _RawWs("127.0.0.1:4915", "http://127.0.0.1:4915", scheme="wss")) is False

    @pytest.mark.parametrize("hosts", [[], ["127.0.0.1:4915", "evil.com"]])
    def test_anything_but_exactly_one_host_header_is_refused(self, hosts):
        """The counts ``_request_host_allowed`` rules out for the same reasons:
        with none sent there was nothing left but the ``scope["server"]``
        fallback, and two is a smuggling shape where which copy is authoritative
        differs between hops."""
        assert _ws_origin_ok(
            _RawWs(None, "http://127.0.0.1:4915", hosts=hosts)) is False

    def test_the_http_and_websocket_paths_share_one_parser(self, monkeypatch):
        """One home for the rule, so the two cannot drift into disagreeing
        about what a loopback Host is. The header is handed over **raw** —
        stripping or lowercasing it first would move part of the parse out of
        the parser, which is where every one of these traps was found."""
        from power_atlas import web as web_mod
        seen = []

        def spy(raw_host):
            seen.append(raw_host)
            return False

        monkeypatch.setattr(web_mod, "_host_allowed", spy)
        # Padded and mixed-case on purpose: a caller that trimmed or lowered it
        # first would have moved part of the parse out of the parser, and this
        # is the only assertion that can see that.
        assert _ws_origin_ok(
            _RawWs(" LocalHost:4915 ", "http://localhost:4915")) is False
        assert seen == [" LocalHost:4915 "]


class _FakeWs:
    """A socket whose only behaviour is to fail the way the writer must survive."""

    def __init__(self, failure: BaseException) -> None:
        self._failure = failure
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, text: str) -> None:
        raise self._failure

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


class TestAcpWriterTeardown:
    """Every writer exit must leave the socket closed, not merely deregistered.

    The routine-disconnect arm caught ``RuntimeError`` and retired with an empty
    reason, which skipped the close — leaving a socket the server still held
    open with no writer behind it, silently swallowing every frame queued at it.
    That is precisely the zombie ``_retire`` was added to remove, surviving in
    the one arm that reaches it most often.
    """

    @pytest.mark.parametrize("failure, expected_code", [
        # Routine: the peer went away mid-send. Closes with "going away".
        (RuntimeError('Cannot call "send" once a close message has been sent.'), 1001),
        (ConnectionResetError(), 1001),
        # Unexpected: a bug in a frame we built. Closes with 1011.
        (ValueError("unserialisable frame"), 1011),
    ])
    def test_writer_exit_closes_and_deregisters(self, failure, expected_code):
        from power_atlas import acp as acp_mod

        async def run():
            ws = _FakeWs(failure)
            conn = acp_mod._Connection(ws)
            acp_mod._registry.connections.add(conn)
            try:
                conn.send({"type": "meta", "payload": {}})
                await conn._write_loop()
            finally:
                acp_mod._registry.connections.discard(conn)
            return ws, conn

        ws, conn = asyncio.run(run())
        assert ws.closed, "the writer exited leaving the socket open"
        assert ws.closed[0][0] == expected_code
        assert conn not in acp_mod._registry.connections

    def test_close_failure_does_not_escape_the_writer(self):
        """A peer that is genuinely gone makes ``close()`` raise too. The writer
        is a bare task: an exception here would surface only as a stray
        "Task exception was never retrieved"."""
        from power_atlas import acp as acp_mod

        class _DeadWs(_FakeWs):
            async def close(self, code: int = 1000, reason: str = "") -> None:
                raise RuntimeError("socket already closed")

        async def run():
            conn = acp_mod._Connection(_DeadWs(ConnectionResetError()))
            acp_mod._registry.connections.add(conn)
            try:
                conn.send({"type": "meta", "payload": {}})
                await conn._write_loop()
            finally:
                acp_mod._registry.connections.discard(conn)

        asyncio.run(run())


class _ScriptedWs:
    """A socket that plays a fixed script of ASGI messages, then hangs up."""

    def __init__(self, messages=()) -> None:
        self._messages = list(messages)
        self.sent: list[str] = []
        self.closed: list[tuple[int, str]] = []

    async def receive(self) -> dict:
        if self._messages:
            return self._messages.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _logged_socket_ids(caplog, verb: str) -> list[str]:
    """Every socket id a line of the form ``ACP socket <id> <verb>`` carried.

    Extracted rather than substring-matched: ``s1`` is a substring of ``s10``,
    and the ids are process-global, so a test that asked "is this id in that
    line" would pass or fail on how many sockets earlier tests had opened.
    """
    pattern = re.compile(r"ACP socket (\S+) " + verb)
    found = []
    for record in caplog.records:
        match = pattern.search(record.getMessage())
        if match:
            found.append(match.group(1))
    return found


class TestAcpSocketCorrelationId:
    """Socket lifecycle lines reported only counts, which cannot correlate.

    ``MAX_CONNECTIONS`` allows eight sockets at once and two browser tabs on one
    session produce two routinely, so "socket open (2/8)" followed later by
    "socket closed (1 open)" leaves no way to say which of the two went.
    """

    def test_open_and_close_name_the_same_socket(self, caplog):
        from power_atlas import acp as acp_mod
        assert not acp_mod._registry.connections, "an earlier test left sockets"
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"):
            asyncio.run(acp_mod.serve_socket(_ScriptedWs()))
        opened = _logged_socket_ids(caplog, "open")
        closed = _logged_socket_ids(caplog, "closed")
        assert len(opened) == 1 and len(closed) == 1
        assert opened == closed

    def test_two_sockets_are_told_apart(self, caplog):
        """The whole point: one id per socket, not one id per process."""
        from power_atlas import acp as acp_mod
        assert not acp_mod._registry.connections, "an earlier test left sockets"
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"):
            asyncio.run(acp_mod.serve_socket(_ScriptedWs()))
            asyncio.run(acp_mod.serve_socket(_ScriptedWs()))
        opened = _logged_socket_ids(caplog, "open")
        assert len(opened) == 2
        assert opened[0] != opened[1]

    def test_a_retire_names_the_socket_that_retired(self, caplog):
        """A writer failure retires one socket while the others stay up. The
        count in the line says how many are left, never which one went."""
        from power_atlas import acp as acp_mod

        async def run():
            healthy = acp_mod._Connection(_SinkWs())
            doomed = acp_mod._Connection(_FakeWs(ConnectionResetError()))
            acp_mod._registry.connections.update({healthy, doomed})
            try:
                doomed.send({"type": "meta", "payload": {}})
                await doomed._write_loop()
            finally:
                acp_mod._registry.connections.discard(healthy)
                acp_mod._registry.connections.discard(doomed)
            return healthy, doomed

        with caplog.at_level(logging.INFO, logger="power_atlas.acp"):
            healthy, doomed = asyncio.run(run())
        assert _logged_socket_ids(caplog, "retired by writer") == [doomed.cid]
        assert healthy.cid != doomed.cid

    def test_the_id_stays_short_enough_to_read(self):
        """A local single-user app's log, not a distributed trace."""
        from power_atlas import acp as acp_mod
        conn = acp_mod._Connection(_SinkWs())
        assert 0 < len(conn.cid) <= 8

    def test_a_throttled_socket_is_named(self, acp_session, caplog):
        """The replay floor is deliberately per-socket, so which socket tripped
        it is the only thing that makes the line actionable."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._handle_subscribe(conn, sid)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            acp_mod._handle_subscribe(conn, sid)
        assert any(f"socket={conn.cid} " in r.getMessage() for r in caplog.records), \
            [r.getMessage() for r in caplog.records]


class TestAcpTransportFrameCap:
    """``MAX_MESSAGE_BYTES`` refuses at the protocol layer, which is after
    uvicorn has decoded the whole frame. Left at uvicorn's default a client
    could make the server buffer 16 MiB before the 256 KiB refusal fired.
    """

    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def test_the_transport_ceiling_is_not_below_the_protocol_cap(self):
        """Below it, a legitimate 256 KiB prompt would die at the transport
        with a generic close instead of the typed 1009 the page explains."""
        from power_atlas import acp as acp_mod
        assert self._main().WS_MAX_SIZE_BYTES >= acp_mod.MAX_MESSAGE_BYTES

    def test_the_transport_ceiling_is_below_uvicorns_default(self):
        import inspect

        import uvicorn
        default = inspect.signature(
            uvicorn.Config.__init__).parameters["ws_max_size"].default
        assert self._main().WS_MAX_SIZE_BYTES < default

    def test_the_installed_uvicorn_accepts_and_keeps_it(self):
        """The keyword is only worth passing if the uvicorn actually running
        the app honours it; a version that dropped it raises here."""
        import uvicorn
        config = uvicorn.Config(
            "power_atlas.web:app", host="127.0.0.1", port=0, log_config=None,
            ws_max_size=self._main().WS_MAX_SIZE_BYTES)
        assert config.ws_max_size == self._main().WS_MAX_SIZE_BYTES

    def test_every_server_config_carries_it(self):
        """One call site since the dual bind (D23): both listeners are handed
        to a single ``Server`` via ``run(sockets=…)``, and the random-port
        fallback now happens at ``_bind`` rather than by building a second
        ``uvicorn.Config``. Two ``Server`` instances would run lifespan twice."""
        import ast

        source = Path(self._main().__file__).read_text(encoding="utf-8")
        calls = [node for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "Config"]
        assert len(calls) == 1, "a uvicorn.Config call site appeared or went"
        for call in calls:
            assert "ws_max_size" in {kw.arg for kw in call.keywords}, \
                f"uvicorn.Config at line {call.lineno} leaves the 16 MiB default"


class TestAcpTokenCheck:
    @pytest.mark.parametrize("supplied", [
        "", "é", "é" * 60, "�", "A" * 4000, "not-the-token",
    ])
    def test_wrong_token_rejected_without_raising(self, supplied):
        assert _acp_token_ok(supplied) is False

    def test_correct_token_accepted(self):
        assert _acp_token_ok(_ACP_TOKEN) is True

    def test_non_ascii_token_closes_socket(self, raw_client):
        """``?t=%C3%A9`` URL-decodes to a non-ASCII str, which ``compare_digest``
        used to reject with TypeError — an unauthenticated 500 on the auth path."""
        with pytest.raises(WebSocketDisconnect) as exc:
            with raw_client.websocket_connect("/ws/acp?t=%C3%A9"):
                pass
        assert exc.value.code == 1008


# --- ACP phase 4: prompt, streaming fan-out, and reconnect replay ---


class _SinkWs:
    """A socket that accepts everything, so a test can read what was queued."""

    def __init__(self) -> None:
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, text: str) -> None:
        pass

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _queued(conn) -> list[dict]:
    """Everything sitting in a connection's outbound queue, in order.

    The queue holds ``(weight, frame)``; the weight is the send side's byte
    budget bookkeeping and no test asserts on it directly.
    """
    frames = []
    while True:
        try:
            frames.append(conn._out.get_nowait()[1])
        except asyncio.QueueEmpty:
            return frames


@pytest.fixture
def acp_session():
    """A live ACP session on the real supervisor singleton, torn down after.

    The registry and supervisor are module globals; leaving either dirty would
    make the next test's ``subscribe`` answer from another test's state.
    """
    from power_atlas import acp as acp_mod

    sid = "sess-phase4"
    acp_mod._supervisor.sessions[sid] = {
        "cwd": r"C:\scratch", "created": 0.0, "models": {}, "modes": {}}
    acp_mod._supervisor.history[sid] = acp_mod._History()
    try:
        yield acp_mod, sid
    finally:
        acp_mod._supervisor.sessions.pop(sid, None)
        acp_mod._supervisor.history.pop(sid, None)
        acp_mod._supervisor.inflight.discard(sid)
        acp_mod._supervisor.closing.discard(sid)
        # The half-finished bubble a test left open. Module-global like the two
        # above, and prose left in it would be prepended to the next test's
        # first rendering.
        acp_mod._bubbles.clear()
        for conn in tuple(acp_mod._registry.connections):
            acp_mod._registry.detach(conn)
        acp_mod._registry.connections.clear()
        acp_mod._registry.subscribers.clear()
        acp_mod._registry.loading.clear()
        # A crew a test attributed to this session, and everything it
        # registered — same module-singleton leak risk as `sessions`/`history`
        # above, just for the sub-agent dicts they gained alongside them.
        acp_mod._supervisor.crews.pop(sid, None)
        acp_mod._supervisor.subagent_sessions.clear()
        acp_mod._supervisor.subagent_history.clear()


class TestAcpServerTypeGuard:
    """``SERVER_TYPES`` was declared in phase 3a and never consulted.

    Every call site passes a literal, so a type it refuses is a typo — and an
    unrecognised frame does not fail loudly on the page, it renders as a line
    of raw JSON in the transport log that reads like agent noise.
    """

    def test_undeclared_type_raises(self):
        from power_atlas import acp as acp_mod
        with pytest.raises(ValueError):
            acp_mod.envelope("chunkk", {"text": "x"})

    def test_every_declared_type_is_accepted(self):
        """Positive control: the guard rejects typos, not the real vocabulary."""
        from power_atlas import acp as acp_mod
        for type_ in acp_mod.SERVER_TYPES:
            assert acp_mod.envelope(type_)["type"] == type_

    def test_error_frame_still_builds(self):
        from power_atlas import acp as acp_mod
        frame = acp_mod.error_frame("bad_payload", "no", "s1")
        assert frame == {"type": "error", "sessionId": "s1",
                         "payload": {"code": "bad_payload", "message": "no"}}


class TestAcpHistoryBuffer:
    """The ring buffer is bounded twice, and both bounds must announce a drop.

    A ``deque`` discards its oldest entry in silence, so a replay degraded from
    "the conversation" to "its last N events" is indistinguishable from a
    complete one unless something records that it happened.
    """

    def _chunk(self, acp_mod, text):
        return acp_mod.envelope("chunk", {"role": "agent", "text": text}, "s1")

    def test_count_bound_holds_and_reports_the_drop(self):
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        for i in range(acp_mod.HISTORY_MAXLEN + 25):
            history.append(self._chunk(acp_mod, "e%d" % i))
        events = history.events()
        assert len(events) == acp_mod.HISTORY_MAXLEN
        assert history.truncated is True
        # The oldest went and the newest stayed — a ring, not a prefix.
        assert events[0]["payload"]["text"] == "e25"
        assert events[-1]["payload"]["text"] == "e%d" % (acp_mod.HISTORY_MAXLEN + 24)

    def test_under_the_cap_nothing_is_marked_truncated(self):
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        for i in range(acp_mod.HISTORY_MAXLEN):
            history.append(self._chunk(acp_mod, "e%d" % i))
        assert len(history.events()) == acp_mod.HISTORY_MAXLEN
        assert history.truncated is False

    def test_byte_budget_evicts_before_the_count_would(self):
        """Few but enormous events are the case the count bound does not cover:
        HISTORY_MAXLEN of them at the reader's line ceiling is hundreds of MB."""
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        big = "x" * (acp_mod.HISTORY_MAX_BYTES // 4)
        for _ in range(12):
            history.append(self._chunk(acp_mod, big))
        assert len(history.events()) < acp_mod.HISTORY_MAXLEN
        assert len(history.events()) <= 5
        assert history.truncated is True

    def test_the_budget_is_bytes_and_not_characters(self):
        """``len()`` on a ``str`` counts characters: an astral-plane buffer
        accounted at 2,087,398 was measured holding 8.24 MB. Each character
        below is one ``len()`` unit and four UTF-8 bytes, so a character count
        would fit eight of these under a budget that holds two."""
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        big = "\U0001f600" * (acp_mod.HISTORY_MAX_BYTES // 4)
        assert len(big) * 4 == len(big.encode("utf-8")) == acp_mod.HISTORY_MAX_BYTES
        history.append(self._chunk(acp_mod, big))
        assert history.truncated is False
        history.append(self._chunk(acp_mod, big))
        assert len(history.events()) == 1
        assert history.truncated is True

    def test_nested_payload_strings_are_weighed(self):
        """A top-level-only walk priced a ``history`` frame — and, from this
        phase, a ``tool_call`` carrying a command — at the envelope allowance
        however large it really was."""
        from power_atlas import acp as acp_mod
        nested = acp_mod.envelope("history", {"events": [
            acp_mod.envelope("chunk", {"role": "agent", "text": "z" * 5000})]})
        assert acp_mod._frame_weight(nested) > 5000

    def test_one_oversized_event_is_never_evicted_to_empty(self):
        """A reload finding an empty replay reads as a lost conversation; a
        suffix of one event reads as what it is."""
        from power_atlas import acp as acp_mod
        history = acp_mod._History()
        history.append(self._chunk(acp_mod, "y" * (acp_mod.HISTORY_MAX_BYTES * 2)))
        assert len(history.events()) == 1


class TestAcpSendQueueByteBound:
    """The send queue kept the defect the history buffer shed.

    ``SEND_QUEUE_MAXSIZE`` bounds frames, not bytes, and nothing caps a queued
    frame below ``MAX_AGENT_LINE_BYTES`` — so one socket could hold 256 MiB and
    ``MAX_CONNECTIONS`` of them 2 GiB, against 256 KiB in the other direction.
    """

    def _conn(self, acp_mod):
        return acp_mod._Connection(_SinkWs())

    def test_a_stalled_socket_cannot_pin_unbounded_memory(self):
        from power_atlas import acp as acp_mod
        conn = self._conn(acp_mod)
        big = "q" * (256 * 1024)
        for _ in range(acp_mod.SEND_QUEUE_MAXSIZE):
            conn.send(acp_mod.envelope("chunk", {"role": "agent", "text": big}))
        assert conn._overflowed is True
        assert conn._queued_bytes <= acp_mod.SEND_QUEUE_MAX_BYTES
        # The count bound alone would have accepted every one of them.
        assert conn._out.qsize() < acp_mod.SEND_QUEUE_MAXSIZE

    def test_ordinary_traffic_never_trips_the_byte_bound(self):
        """Positive control: a full queue of streamed chunks is normal."""
        from power_atlas import acp as acp_mod
        conn = self._conn(acp_mod)
        for _ in range(acp_mod.SEND_QUEUE_MAXSIZE):
            conn.send(acp_mod.envelope("chunk", {"role": "agent", "text": "tick"}))
        assert conn._overflowed is False
        assert conn._out.qsize() == acp_mod.SEND_QUEUE_MAXSIZE

    def test_a_lone_frame_over_the_budget_is_still_delivered(self):
        """Refusing it onto an empty queue would leave the writer parked on
        ``get()`` with an overflow flag it never wakes up to read."""
        from power_atlas import acp as acp_mod
        conn = self._conn(acp_mod)
        conn.send(acp_mod.envelope(
            "chunk", {"role": "agent",
                      "text": "w" * (acp_mod.SEND_QUEUE_MAX_BYTES + 4096)}))
        assert conn._overflowed is False
        assert conn._out.qsize() == 1

    def test_the_writer_releases_the_bytes_it_has_sent(self):
        """Without this the budget is a lifetime total and every long-lived
        socket eventually refuses to send anything at all."""
        from power_atlas import acp as acp_mod

        async def drive():
            conn = self._conn(acp_mod)
            conn.start()
            conn.send(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "u" * (1024 * 1024)}))
            assert conn._queued_bytes > 1024 * 1024
            await conn.drain()
            await conn.stop()
            return conn

        conn = asyncio.run(drive())
        assert conn._queued_bytes == 0
        assert conn._overflowed is False

    def test_a_full_byte_budget_replay_does_not_retire_its_own_socket(
            self, acp_session):
        """The bound must sit above the largest frame the server itself builds:
        a ``history`` replay carries up to ``HISTORY_MAX_BYTES`` of text."""
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        chunk = "h" * 64 * 1024
        while not history.truncated:
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": chunk}, sid))

        conn = self._conn(acp_mod)
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert conn._overflowed is False
        assert [f["type"] for f in _queued(conn)] == [
            "session", "history_truncated", "history"]


class TestAcpReplayOnSubscribe:
    def test_replay_is_one_frame_and_cannot_overflow_the_socket(self, acp_session):
        """``SEND_QUEUE_MAXSIZE`` is 256 and a full queue retires the socket, so
        an event-per-frame replay of a full buffer would kill the very socket
        the replay exists to serve — and only for sessions worth replaying."""
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        for i in range(acp_mod.HISTORY_MAXLEN):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)

        frames = _queued(conn)
        assert conn._overflowed is False
        assert [f["type"] for f in frames] == ["session", "history"]
        events = frames[1]["payload"]["events"]
        assert len(events) == acp_mod.HISTORY_MAXLEN
        assert events[0]["payload"]["text"] == "e0"
        assert events[-1]["payload"]["text"] == "e%d" % (acp_mod.HISTORY_MAXLEN - 1)

    def test_replay_carries_the_recorded_conversation_in_order(self, acp_session):
        acp_mod, sid = acp_session
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "user", "text": "hello"}, sid))
        acp_mod._emit(sid, acp_mod.envelope("meta", {"turn": "start"}, sid))
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "hi "}, sid))
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "there"}, sid))

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)

        frames = _queued(conn)
        events = frames[-1]["payload"]["events"]
        assert [(e["type"], e["payload"].get("text") or e["payload"].get("turn"))
                for e in events] == [
            ("chunk", "hello"), ("meta", "start"),
            ("chunk", "hi "), ("chunk", "there")]

    def test_truncated_marker_precedes_the_replay(self, acp_session):
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        for i in range(acp_mod.HISTORY_MAXLEN + 1):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)

        assert [f["type"] for f in _queued(conn)] == [
            "session", "history_truncated", "history"]

    def test_intact_buffer_emits_no_truncation_marker(self, acp_session):
        """Positive control for the marker: it must mean something."""
        acp_mod, sid = acp_session
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "only"}, sid))
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert [f["type"] for f in _queued(conn)] == ["session", "history"]

    def test_a_live_event_after_subscribe_is_not_also_replayed(self, acp_session):
        """Attach and replay are atomic because ``_handle_subscribe`` never
        awaits; an event broadcast afterwards must therefore arrive exactly
        once, live, and not a second time inside the history frame."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        acp_mod._emit(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "live"}, sid))

        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["session", "history", "chunk"]
        assert frames[1]["payload"]["events"] == []
        assert frames[2]["payload"]["text"] == "live"

    def test_subscribe_is_synchronous_by_construction(self):
        """The atomicity above is "no ``await`` between ``attach`` and the
        ``history`` frame", and nothing enforced it: converting this function
        to ``async def`` together with the tests that drive it would leave
        every frame-ordering assertion passing."""
        import inspect
        from power_atlas import acp as acp_mod
        assert not inspect.iscoroutinefunction(acp_mod._handle_subscribe)
        # Its only call site, for the same reason: a coroutine `_dispatch`
        # would put a suspension point either side of the call.
        assert not inspect.iscoroutinefunction(acp_mod._dispatch)

    def test_subscribe_reports_a_turn_the_server_still_holds(self, acp_session):
        """The page's only other source is a replayed ``meta {"turn":
        "start"}`` — a frame the ring buffer is built to evict."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.inflight.add(sid)
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["turnActive"] is True

    def test_subscribe_reports_an_idle_session(self, acp_session):
        """Positive control: the flag must mean something."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["turnActive"] is False

    def test_the_turn_flag_survives_a_buffer_that_evicted_its_marker(
            self, acp_session):
        """The whole point: a turn emitting more than ``HISTORY_MAXLEN`` chunks
        replays with no ``turn: start`` left in it."""
        acp_mod, sid = acp_session
        history = acp_mod._supervisor.history[sid]
        history.append(acp_mod.envelope("meta", {"turn": "start"}, sid))
        for i in range(acp_mod.HISTORY_MAXLEN):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))
        acp_mod._supervisor.inflight.add(sid)

        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        replayed = frames[-1]["payload"]["events"]
        assert not any(e["payload"].get("turn") == "start" for e in replayed)
        assert frames[0]["payload"]["turnActive"] is True

    def test_an_unknown_session_leaves_a_server_side_trace(self, acp_session,
                                                           caplog):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            acp_mod._handle_subscribe(conn, "no-such-session")
        assert any("unknown_session" in r.getMessage() for r in caplog.records)


class TestAcpNotificationFanout:
    def test_agent_message_chunk_reaches_subscribers_and_the_buffer(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)

        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk",
                # Measured shape for kiro-cli 2.14.2: one object, not a list.
                "content": {"type": "text", "text": "partial"}}},
        })

        assert _queued(conn) == [{
            "type": "chunk", "sessionId": sid,
            "payload": {"role": "agent", "text": "partial"}}]
        assert acp_mod._supervisor.history[sid].events()[0][
            "payload"]["text"] == "partial"

    def test_chunk_for_another_session_does_not_cross_over(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": "someone-else", "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "not yours"}}},
        })
        assert _queued(conn) == []

    def test_agent_thought_chunk_reaches_subscribers_as_a_thought_frame(self, acp_session):
        """Never observed on the wire (plans/ROADMAP.md: zero across 1,200
        measured runs), handled anyway on the chance a build does send it —
        see the branch's own comment in `_on_notification`."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)

        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "considering the diff"}}},
        })

        assert _queued(conn) == [{
            "type": "thought", "sessionId": sid,
            "payload": {"text": "considering the diff"}}]
        assert acp_mod._supervisor.history[sid].events()[0][
            "payload"]["text"] == "considering the diff"

    def test_agent_thought_chunk_with_no_text_emits_nothing(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)

        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": ""}}},
        })
        assert _queued(conn) == []


class TestAcpToolCallVisibility:
    """Under ``-a`` there is no permission gate, and the accepted justification
    for removing it was a human watching the run. A tool call that reaches only
    a log file — one the app writes on a single launch path — is not something
    anyone is watching: a ``shell`` call was observed reading and writing a
    session store file outside its own session's cwd, unseen.
    """

    def _attached(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def _notify(self, acp_mod, sid, update):
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": update},
        })

    def test_a_tool_call_reaches_the_page_and_the_replay_buffer(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t1",
            "title": "Set the tab title", "kind": "execute",
            "status": "pending",
            "rawInput": {"command": "Get-Content ~/.kiro/sessions/cli/x.json"},
        })

        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["tool_call"]
        assert frames[0]["payload"] == {
            "toolCallId": "t1", "title": "Set the tab title",
            "kind": "execute", "status": "pending",
            "command": "Get-Content ~/.kiro/sessions/cli/x.json"}
        # Recorded too, or a reload would show a conversation with no sign
        # that anything ran during it.
        assert acp_mod._supervisor.history[sid].events() == frames

    def test_an_update_arrives_as_its_own_declared_type(self, acp_session):
        """``tool_call_update`` is ACP's name; ``tool_update`` is the wire type
        this module declared for it in ``SERVER_TYPES``."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call_update", "toolCallId": "t1",
            "status": "completed"})
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["tool_update"]
        assert frames[0]["payload"]["status"] == "completed"
        assert frames[0]["payload"]["toolCallId"] == "t1"

    def test_a_content_only_update_is_not_forwarded(self, acp_session):
        """Measured 2026-08-11 against a real kiro-cli 2.16.2 subprocess:
        every toolCallId gets exactly one intermediate `tool_call_update`
        shaped like this — only `content`, none of title/kind/status/
        rawInput — before the terminal one with `status: "completed"`
        arrives. `_tool_payload` does not read `content` at all, so
        forwarding this one would send an all-blank `tool_update` that could
        flash an already-populated row empty right before the real state
        lands a moment later."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call_update", "toolCallId": "t1",
            "content": [{"type": "content",
                         "content": {"type": "text", "text": "24\n"}}]})
        assert _queued(conn) == []
        assert acp_mod._supervisor.history[sid].events() == []

    def test_the_command_is_logged_as_well_as_forwarded(self, acp_session, caplog):
        """The log line is the trace that outlives the tab. The test it
        replaces asserted only that forwarding did *not* happen, while its
        name claimed it covered the logging."""
        acp_mod, sid = acp_session
        self._attached(acp_mod, sid)
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"):
            self._notify(acp_mod, sid, {
                "sessionUpdate": "tool_call", "toolCallId": "t9",
                "title": "shell", "kind": "execute", "status": "in_progress",
                "rawInput": {"command": "Remove-Item -Recurse C:/tmp"}})
        messages = [r.getMessage() for r in caplog.records]
        assert any("ACP tool tool_call" in m and "t9" in m
                   and "Remove-Item -Recurse C:/tmp" in m for m in messages), messages

    def test_a_long_command_is_clipped_and_says_so(self, acp_session):
        """A command clipped to look complete would be worse than none, so the
        bound travels with the frame and the page states it."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        command = "echo " + "a" * (acp_mod.MAX_TOOL_INPUT_CHARS * 2)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t2",
            "title": "shell", "rawInput": {"command": command}})
        payload = _queued(conn)[0]["payload"]
        assert payload["commandTruncated"] is True
        assert payload["commandLength"] == len(command)
        assert payload["command"] == command[:acp_mod.MAX_TOOL_INPUT_CHARS]

    def test_a_short_command_carries_no_truncation_marker(self, acp_session):
        """Positive control: the marker must mean something."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t3",
            "rawInput": {"command": "dir"}})
        assert "commandTruncated" not in _queued(conn)[0]["payload"]

    def test_an_input_with_no_command_key_is_still_reported(self, acp_session):
        """``rawInput`` has no schema and differs per tool. "A tool ran and we
        cannot say what it did" is the state this rendering removes."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": "t4",
            "title": "fs_write", "rawInput": {"mode": "create", "bytes": 42}})
        assert _queued(conn)[0]["payload"]["command"] == (
            '{"bytes": 42, "mode": "create"}')

    def test_non_string_fields_do_not_reach_the_page_untyped(self, acp_session):
        """Every field is agent-authored and none is schema-checked on the way
        in; the renderer reads these as text and nothing else."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, {
            "sessionUpdate": "tool_call", "toolCallId": {"nested": 1},
            "title": ["shell"], "kind": 7, "status": None})
        payload = _queued(conn)[0]["payload"]
        assert payload["toolCallId"] == ""
        assert payload["title"] == ""
        assert payload["kind"] == ""
        assert payload["status"] == ""

    def test_a_tool_call_for_another_session_does_not_cross_over(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, "someone-else", {
            "sessionUpdate": "tool_call", "toolCallId": "t5", "title": "shell"})
        assert _queued(conn) == []
        assert acp_mod._supervisor.history[sid].events() == []


class TestAcpPromptDispatch:
    def _conn(self, acp_mod, sid=None):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        if sid:
            acp_mod._registry.attach(conn, sid)
        return conn

    @pytest.mark.parametrize("payload, code", [
        ({}, "bad_payload"),
        ({"prompt": ""}, "bad_payload"),
        ({"prompt": "   \n "}, "bad_payload"),
        ({"prompt": 17}, "bad_payload"),
        ({"prompt": ["hi"]}, "bad_payload"),
        ({"prompt": {"text": "hi"}}, "bad_payload"),
    ])
    def test_bad_payloads_are_refused_with_a_typed_frame(self, acp_session,
                                                         payload, code):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, sid, payload))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == code
        assert acp_mod._supervisor.history[sid].events() == []

    def test_missing_session_id_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, None, {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "bad_envelope"

    def test_unknown_session_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, "no-such-session",
                                           {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "unknown_session"

    def test_unattached_socket_is_refused(self, acp_session):
        """It would start a turn and then receive none of the stream it began,
        which on the page looks like an agent that never answered."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod)
        asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "not_subscribed"

    def test_second_prompt_during_a_turn_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert _queued(conn)[0]["payload"]["code"] == "turn_in_progress"

    @pytest.mark.parametrize("attach, sid_arg, payload, code", [
        (True, None, {"prompt": "hi"}, "bad_envelope"),
        (True, "self", {"prompt": ""}, "bad_payload"),
        (True, "no-such-session", {"prompt": "hi"}, "unknown_session"),
        (False, "self", {"prompt": "hi"}, "not_subscribed"),
    ])
    def test_every_refusal_leaves_a_server_side_trace(self, acp_session, caplog,
                                                      attach, sid_arg, payload,
                                                      code):
        """``not_subscribed`` is what a reconnect subscribing with a stale
        session id looks like from the client, and it left no trace at all."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid if attach else None)
        target = sid if sid_arg == "self" else sid_arg
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            asyncio.run(acp_mod._handle_prompt(conn, target, payload))
        assert _queued(conn)[0]["payload"]["code"] == code
        assert any(code in r.getMessage() for r in caplog.records), \
            [r.getMessage() for r in caplog.records]

    def test_the_turn_in_progress_refusal_is_logged(self, acp_session, caplog):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert any("turn_in_progress" in r.getMessage() for r in caplog.records)

    def test_a_turn_streams_to_every_subscriber_and_is_recorded(self, acp_session):
        """The stub sits at the JSON-RPC transport, so the request this asserts
        is the one the supervisor really builds, and the chunk travels the real
        notification path while the request is still outstanding."""
        acp_mod, sid = acp_session
        calls = []

        async def fake_request(self, method, params,
                               timeout=acp_mod.REQUEST_TIMEOUT_SECONDS):
            calls.append((method, params, timeout))
            self._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "answer"}}},
            })
            return {"stopReason": "end_turn"}

        sup = acp_mod._supervisor
        conn_a = self._conn(acp_mod, sid)
        conn_b = self._conn(acp_mod, sid)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_prompt(conn_a, sid, {"prompt": "ping"}))

        # The third element is the `_INACTIVITY` sentinel, not a number: the
        # prompt path is the one request bounded by agent silence rather than
        # by wall clock, and it asks for that through the existing `timeout`
        # slot so `_request`'s signature — and the ~19 fixed-signature stubs
        # in this file that replace it — stay valid.
        assert calls == [("session/prompt",
                          {"sessionId": sid,
                           "prompt": [{"type": "text", "text": "ping"}]},
                          acp_mod._INACTIVITY)]
        expected = [
            ("chunk", {"role": "user", "text": "ping"}),
            ("meta", {"turn": "start"}),
            ("chunk", {"role": "agent", "text": "answer"}),
            # The bubble's markdown, parsed once at the end of it. The chunk
            # above still carries the plain text and still arrives first: the
            # page streams exactly what it always did and reflows afterwards.
            ("rendered", {"tokens": [
                {"type": "paragraph",
                 "children": [{"type": "text", "raw": "answer"}]}]}),
            ("meta", {"turn": "end", "stopReason": "end_turn"}),
        ]
        # Both tabs see the same turn, including the prompt neither of them
        # typed — that is what makes a second tab a view and not a rival.
        for conn in (conn_a, conn_b):
            assert [(f["type"], f["payload"]) for f in _queued(conn)] == expected
        assert [(e["type"], e["payload"])
                for e in sup.history[sid].events()] == expected
        assert sid not in sup.inflight

    def test_an_agent_failure_ends_the_turn_rather_than_hanging_it(self, acp_session):
        """The page derives "still answering" from the turn boundary, so a
        refused prompt that emitted no end would leave Send disabled forever."""
        acp_mod, sid = acp_session

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        conn = self._conn(acp_mod, sid)
        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "ping"}))

        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["chunk", "meta", "error", "meta"]
        assert frames[2]["payload"]["code"] == "agent_timeout"
        assert frames[3]["payload"] == {"turn": "end", "stopReason": "error"}
        assert sid not in acp_mod._supervisor.inflight


class TestAcpPromptImages:
    """Image attachments on a prompt.

    The blobs below carry a real signature and filler after it, which is
    exactly what the server checks — `_image_bytes_match` reads the first
    bytes and does not decode the picture. Using a real PNG here would assert
    something the production path never looks at.
    """

    PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"pixels").decode()
    JPEG = base64.b64encode(b"\xff\xd8\xff" + b"pixels").decode()
    WEBP = base64.b64encode(b"RIFF\x00\x00\x00\x00WEBP" + b"pixels").decode()

    def _conn(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def _run(self, acp_mod, sid, payload):
        """Drive one turn against a stubbed transport, returning what the
        agent was asked and what the subscribers saw."""
        calls = []

        async def fake_request(self, method, params,
                               timeout=acp_mod.REQUEST_TIMEOUT_SECONDS):
            calls.append((method, params, timeout))
            return {"stopReason": "end_turn"}

        conn = self._conn(acp_mod, sid)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_prompt(conn, sid, payload))
        return calls, _queued(conn)

    def test_an_image_only_prompt_reaches_the_agent(self, acp_session):
        acp_mod, sid = acp_session
        calls, frames = self._run(acp_mod, sid, {
            "images": [{"mimeType": "image/png", "data": self.PNG}]})
        assert calls[0][0] == "session/prompt"
        assert calls[0][1]["prompt"] == [
            {"type": "text", "text": "[Image 1]"},
            {"type": "image", "mimeType": "image/png", "data": self.PNG},
        ]
        chunk = next(f for f in frames if f["type"] == "chunk")
        assert chunk["payload"] == {"role": "user", "text": "[Image 1]"}

    def test_text_and_images_are_numbered_in_paste_order(self, acp_session):
        acp_mod, sid = acp_session
        calls, frames = self._run(acp_mod, sid, {
            "prompt": "what changed?",
            "images": [{"mimeType": "image/png", "data": self.PNG},
                       {"mimeType": "image/webp", "data": self.WEBP}]})
        blocks = calls[0][1]["prompt"]
        # The agent reads the same numbering the user does, so a prompt saying
        # "compare image 1 with image 2" names something it can see.
        assert blocks[0] == {"type": "text",
                             "text": "what changed?\n\n[Image 1] [Image 2]"}
        assert [b["mimeType"] for b in blocks[1:]] == ["image/png", "image/webp"]

    def test_the_transcript_frame_never_carries_the_bytes(self, acp_session):
        """Base64 in a `chunk` would be charged at full weight by the replay
        buffer, and about eight of them evict a whole 2 MiB conversation."""
        acp_mod, sid = acp_session
        self._run(acp_mod, sid, {
            "prompt": "look", "images": [
                {"mimeType": "image/jpeg", "data": self.JPEG}]})
        recorded = json.dumps(acp_mod._supervisor.history[sid].events())
        assert self.JPEG not in recorded
        assert "[Image 1]" in recorded

    @pytest.mark.parametrize("images, fragment", [
        ("not-a-list", "must be a list"),
        ([{"mimeType": "image/png", "data": PNG}] * 5, "At most"),
        (["not-an-object"], "not an object"),
        ([{"mimeType": "image/tiff", "data": PNG}], "unsupported type"),
        ([{"mimeType": "image/png"}], "no base64 data"),
        ([{"mimeType": "image/png", "data": "!!!not-base64!!!"}],
         "not valid base64"),
        ([{"mimeType": "image/jpeg", "data": PNG}], "declared type disagree"),
    ])
    def test_bad_images_are_refused_with_a_typed_frame(self, acp_session,
                                                       images, fragment):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(
            conn, sid, {"prompt": "hi", "images": images}))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "bad_payload"
        assert fragment in frames[0]["payload"]["message"]
        # Refused before anything was emitted, so a rejected prompt leaves no
        # half-turn in the transcript a reload would replay.
        assert acp_mod._supervisor.history[sid].events() == []

    def test_the_byte_cap_counts_decoded_bytes_across_every_image(self, acp_session):
        acp_mod, sid = acp_session
        half = acp_mod.MAX_PROMPT_IMAGE_BYTES // 2 + 64
        blob = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * half).decode()
        conn = self._conn(acp_mod, sid)
        asyncio.run(acp_mod._handle_prompt(conn, sid, {
            "prompt": "hi",
            "images": [{"mimeType": "image/png", "data": blob},
                       {"mimeType": "image/png", "data": blob}]}))
        frames = _queued(conn)
        assert frames[0]["payload"]["code"] == "bad_payload"
        assert "between them" in frames[0]["payload"]["message"]

    def test_the_socket_advertises_the_budget_it_will_enforce(self, acp_session):
        """The page rations itself against these. A copy written into the
        template would be a second source free to drift, and it would drift in
        the direction that costs the user work: a page believing the cap is
        higher stages an image, then loses it to a refusal after the fact."""
        acp_mod, _sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        conn.send(acp_mod.envelope("meta", {
            "connected": True,
            "maxMessageBytes": acp_mod.MAX_MESSAGE_BYTES,
            "maxConnections": acp_mod.MAX_CONNECTIONS,
            "maxPromptImages": acp_mod.MAX_PROMPT_IMAGES,
            "maxPromptImageBytes": acp_mod.MAX_PROMPT_IMAGE_BYTES,
        }))
        # The real ack is built in `serve_socket`, which needs a live socket;
        # what this pins is that both fields are on the frame the page reads,
        # and the source text below is what pins that it is the same builder.
        import inspect
        source = inspect.getsource(acp_mod.serve_socket)
        assert '"maxPromptImages": MAX_PROMPT_IMAGES' in source
        assert '"maxPromptImageBytes": MAX_PROMPT_IMAGE_BYTES' in source
        assert _queued(conn)[0]["payload"]["maxPromptImages"] == \
            acp_mod.MAX_PROMPT_IMAGES

    def test_the_server_cap_sits_below_what_the_frame_admits(self):
        """The typed refusal has to be reachable. Above this the frame check in
        `serve_socket` wins instead, and that one closes the socket with 1009
        rather than answering — losing the prompt with it."""
        from power_atlas import acp as acp_mod
        assert acp_mod.MAX_PROMPT_IMAGE_BYTES < acp_mod.MAX_MESSAGE_BYTES

    def test_a_replayed_image_only_turn_still_separates_the_bubbles(self,
                                                                    acp_session):
        """`session/load` replays an image turn as its text alone, and an
        image-only turn therefore has no text at all — measured against
        kiro-cli 2.16.0 on 2026-08-04, which returned no non-text block in a
        replay. Before the markers that made the arm emit nothing *and* skip
        the flush, merging the answers either side into one bubble."""
        acp_mod, sid = acp_session
        sup = acp_mod._supervisor
        acp_mod._bubble_append(sid, "the answer before the image")
        sup._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "user_message_chunk",
                "content": [{"type": "image", "mimeType": "image/png",
                             "data": "irrelevant"}]}},
        })
        kinds = [f["type"] for f in sup.history[sid].events()]
        # `rendered` is the flush: the bubble that was open got closed before
        # the user's own turn was written after it.
        assert kinds == ["rendered", "chunk"]
        chunk = sup.history[sid].events()[-1]
        assert chunk["payload"] == {"role": "user", "text": "[Image 1]"}


class TestAcpMarkdownRendering:
    """The agent writes markdown; the transcript used to show it as source.

    Parsing happens **here, into a token tree**, and the tree is built into
    elements on the page. The split is the security control rather than a
    layering preference: ``/acp`` is reachable off the loopback, it fronts an
    agent running with every tool pre-approved, and the page's one hard rule is
    that nothing on it parses markup. A tree of tokens can be walked with
    ``createElement`` and ``textContent``; a string of HTML cannot, and a
    server that emitted HTML would have to be trusted line by line forever
    instead of being checked once by a regex over the template.

    Nothing on this side sanitizes anything. ``create_markdown(renderer=None)``
    never reaches ``escape=`` (that argument belongs to ``HTMLRenderer``) and
    never applies ``safe_url()`` (it lives in the HTML renderer too), so raw
    ``<script>`` and ``javascript:`` URLs arrive on the wire intact. The page's
    allowlist is the entire boundary, and
    ``test_the_tree_is_not_sanitized_and_the_page_is_the_boundary`` pins that
    so nobody relaxes the allowlist on the strength of a filter here.
    """

    def _attached(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    @staticmethod
    def _says(text, role="agent"):
        return {"sessionUpdate": "%s_message_chunk" % role,
                "content": {"type": "text", "text": text}}

    @staticmethod
    def _calls(tool_id):
        return {"sessionUpdate": "tool_call", "toolCallId": tool_id,
                "title": "shell", "kind": "execute", "status": "pending"}

    def _notify(self, acp_mod, sid, update):
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": update},
        })

    def _turn(self, acp_mod, sid, conn, script, stop="end_turn"):
        """One real ``_handle_prompt`` whose agent emits ``script`` mid-request.

        The stub sits at the JSON-RPC transport, so every notification travels
        the real path and the turn's boundaries are the real ones.
        """
        async def fake_request(self_, method, params, timeout=None):
            for update in script:
                self_._on_notification({
                    "method": "session/update",
                    "params": {"sessionId": sid, "update": update},
                })
            return {"stopReason": stop}

        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "alive", lambda self_: True):
            asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "go"}))
        return _queued(conn)

    def test_the_end_of_a_turn_renders_the_bubble(self, acp_session):
        """The whole feature, at its ordinary shape.

        The ``rendered`` frame sits immediately in front of the turn-end
        marker, and that position is load-bearing: the page applies it to
        whichever bubble is open, and the end marker is what closes that
        bubble. Behind it, the frame reaches a bubble that no longer exists.
        """
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        frames = self._turn(acp_mod, sid, conn, [self._says(
            "# Findings\n\nIt is **fine**, see `run.py`:\n\n"
            "```py\nx = 1\n```\n\n- first\n- second\n")])
        types = [f["type"] for f in frames]
        assert types == ["chunk", "meta", "chunk", "rendered", "meta"]
        assert frames[-1]["payload"]["turn"] == "end"
        tokens = frames[3]["payload"]["tokens"]
        kinds = [t["type"] for t in tokens]
        assert "heading" in kinds, kinds
        assert "block_code" in kinds, kinds
        assert "list" in kinds, kinds
        para = next(t for t in tokens if t["type"] == "paragraph")
        inline = [k["type"] for k in para["children"]]
        assert "strong" in inline and "codespan" in inline, inline
        # The plain text is still streamed exactly as it was, and the rendering
        # is an addition to it rather than a replacement for it: a client that
        # ignores the new frame renders precisely what it rendered before.
        assert frames[2]["payload"]["text"].startswith("# Findings")
        # And the accumulator is empty again, so a session cannot carry one
        # turn's prose into the next.
        assert sid not in acp_mod._bubbles

    def test_a_tool_call_splits_one_turn_into_two_bubbles(self, acp_session):
        """A **bubble** is the unit, not a turn.

        A bubble is what the page's ``agentBody`` tracks, and a tool call ends
        one: ``addToolCall`` nulls it so the prose after a call is a new
        paragraph rather than a continuation of the sentence it interrupted.
        Parsing per turn instead would hand the page one tree for two boxes —
        the second bubble would be rendered with the first one's content in it
        and the reader would see the first answer twice.

        It also decides how a code fence that spans a tool call is read: as two
        unterminated documents, which is exactly what was on the screen.
        """
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        frames = self._turn(acp_mod, sid, conn, [
            self._says("Running it:\n\n```sh\nls -la\n"),
            self._calls("t1"),
            self._says("It printed **three** files.\n"),
        ])
        assert [f["type"] for f in frames] == [
            "chunk", "meta", "chunk", "rendered", "tool_call", "chunk",
            "rendered", "meta"]
        first = frames[3]["payload"]["tokens"]
        second = frames[6]["payload"]["tokens"]
        # The first bubble's fence is never closed, and mistune reads it as a
        # code block running to the end of the bubble — the same thing a reader
        # watching the stream saw.
        assert [t["raw"] for t in first if t["type"] == "block_code"] == ["ls -la\n"]
        # And the prose after the call is its own document. Accumulated per
        # turn it would have landed *inside* that block instead, never
        # emphasised and never a paragraph.
        para = next(t for t in second if t["type"] == "paragraph")
        assert any(k["type"] == "strong" for k in para["children"]), para
        assert not any(t["type"] == "block_code" for t in second), second
        # Nothing of the first bubble is in the second one.
        assert "ls -la" not in json.dumps(second)

    def test_a_pipe_table_reaches_the_page_as_table_tokens(self, acp_session):
        """Pipe tables are GFM and not CommonMark, and mistune ships only the
        latter — so ``create_markdown`` without the ``table`` plugin emits no
        table token at all. The rows come back as a paragraph of literal pipes,
        which the page's fall-through arm then flattens onto one line, and that
        is what an agent's comparison table looked like on ``/acp``. ``web.py``
        reached the same conclusion for the dashboard tooltip.

        Asserting the *token types* is the half that fails when the plugin is
        dropped. The cell text survives either way, which is exactly why this
        was invisible to every check that looked for content.
        """
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        frames = self._turn(acp_mod, sid, conn, [self._says(
            "| Integration | Rows |\n|---|---:|\n| Classic Aruba | 12 |\n")])
        rendered = next(f for f in frames if f["type"] == "rendered")
        tokens = rendered["payload"]["tokens"]
        table = next((t for t in tokens if t["type"] == "table"), None)
        assert table is not None, tokens
        assert {t["type"] for t in table["children"]} == {"table_head",
                                                          "table_body"}, table
        # The `---:` delimiter's alignment rides along on the cell. The client
        # turns it into a class, and it cannot do that from a token that never
        # carried one.
        head = next(t for t in table["children"] if t["type"] == "table_head")
        assert [c["attrs"]["align"] for c in head["children"]] == [None, "right"]
        # The failure mode, stated positively: no row survived as prose.
        assert not any(t["type"] == "paragraph" for t in tokens), tokens

    def test_a_tool_update_does_not_split_a_bubble(self, acp_session):
        """The mirror of the check above, and the reason it is ``tool_call``
        alone. ``addToolCall`` returns early for an id it already drew, leaving
        the open bubble alone — so flushing on an update would split a bubble
        the page never split, and the second half would render into a box the
        first half is still in.
        """
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        frames = self._turn(acp_mod, sid, conn, [
            self._says("Working"),
            {"sessionUpdate": "tool_call_update", "toolCallId": "t1",
             "status": "completed"},
            self._says(" on **it**."),
        ])
        assert [f["type"] for f in frames] == [
            "chunk", "meta", "chunk", "tool_update", "chunk", "rendered",
            "meta"]
        assert "Working on " in json.dumps(frames[5]["payload"]["tokens"])

    def test_a_failed_turn_still_renders_what_arrived(self, acp_session):
        """In the ``finally``, above the end marker, for the same reason
        ``stop_reason`` defaults to ``interrupted``: a turn that was cancelled
        or that died mid-answer leaves real text on the page, and refusing to
        render it would make failure the one case where the transcript is
        worse than it used to be."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)

        async def boom(self_, method, params, timeout=None):
            self_._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid,
                           "update": self._says("Partial **answer**")},
            })
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "alive", lambda self_: True):
            asyncio.run(acp_mod._handle_prompt(conn, sid, {"prompt": "go"}))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == [
            "chunk", "meta", "chunk", "error", "rendered", "meta"]
        assert frames[-1]["payload"] == {"turn": "end", "stopReason": "error"}
        assert "answer" in json.dumps(frames[4]["payload"]["tokens"])

    def test_the_text_streams_as_it_did_and_renders_once(self, acp_session):
        """Measured against a real turn: 185 chunks, a median of 10 characters
        each, 17 ms apart — and **156 of the 184 boundaries between them fell
        inside an open code fence**. Rendering per chunk would therefore have
        been parsing an unterminated document 85% of the time, so it is not
        merely cheaper to wait for the end of the bubble, it is the only point
        at which there is a document to parse. The chunks themselves are
        untouched."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        pieces = ["```py\n", "x", " = ", "1", "\n", "```", "\n"]
        frames = self._turn(acp_mod, sid, conn,
                            [self._says(piece) for piece in pieces])
        assert [f["payload"]["text"] for f in frames
                if f["type"] == "chunk"][1:] == pieces
        rendered = [f for f in frames if f["type"] == "rendered"]
        assert len(rendered) == 1
        assert [t["raw"] for t in rendered[0]["payload"]["tokens"]
                if t["type"] == "block_code"] == ["x = 1\n"]

    def test_a_user_chunk_closes_the_bubble_on_the_replay_path(self, acp_session):
        """``session/load`` replays a whole conversation as alternating chunks
        with no turn marker anywhere in it, so the user's own message is the
        only boundary between one answer and the next — and it is one the page
        already observes (``appendChunk`` hands a non-agent role to
        ``addMessage`` and nulls ``agentBody``). Without it the first answer
        would be re-rendered into the second answer's bubble, and a reader
        coming back to a loaded session would see it twice."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, self._says("First **answer**."))
        self._notify(acp_mod, sid, self._says("next question", role="user"))
        self._notify(acp_mod, sid, self._says("Second *answer*."))
        # What `_handle_load` does once the agent's replay is complete: the
        # last bubble has no boundary behind it and is the one being read.
        acp_mod._flush_bubble(sid)
        frames = _queued(conn)
        assert [f["type"] for f in frames] == [
            "chunk", "rendered", "chunk", "chunk", "rendered"]
        assert "First" in json.dumps(frames[1]["payload"]["tokens"])
        assert "First" not in json.dumps(frames[4]["payload"]["tokens"])
        assert "Second" in json.dumps(frames[4]["payload"]["tokens"])

    def test_the_tree_is_not_sanitized_and_the_page_is_the_boundary(self, acp_session):
        """Two halves of one property, both asserted rather than commented.

        **No HTML on the wire**: nothing in the frame is a string of markup, so
        the page never has anything to parse — which is why token mode was
        chosen over server-rendered HTML for a surface this exposed.

        **No sanitizing here**: with ``renderer=None`` mistune's ``escape=``
        and ``safe_url()`` are both out of the path, so the raw ``<script>``
        and the ``javascript:`` URL are *present in the tree*. Pinned because a
        reader who assumed this side filtered would be free to relax the page's
        allowlist, which is the only thing that actually refuses them.
        """
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        frames = self._turn(acp_mod, sid, conn, [self._says(
            "<script>alert(1)</script>\n\n[x](javascript:alert(1))\n")])
        tokens = frames[3]["payload"]["tokens"]
        raw = next(t for t in tokens if t["type"] == "block_html")
        assert raw["raw"] == "<script>alert(1)</script>\n"
        link = next(k for t in tokens if t["type"] == "paragraph"
                    for k in t["children"] if k["type"] == "link")
        assert link["attrs"]["url"] == "javascript:alert(1)"
        # Every token is a typed node. Nothing here is a rendered fragment the
        # page would have to trust.
        assert all(isinstance(t, dict) and isinstance(t.get("type"), str)
                   for t in tokens)

    def test_without_mistune_the_transcript_is_what_it_always_was(
            self, acp_session, monkeypatch, caplog):
        """The import is guarded like ``web.py``'s import of this module, and
        for the same reason. Absent, ``/acp`` loses the rendering and keeps the
        conversation.

        And it loses it *quietly*. The explicit ``_markdown is None`` test looks
        redundant beside the ``except Exception`` below it — without the test,
        calling ``None`` raises and the same handler swallows it — but the two
        are not the same outcome: the handler logs, so a machine missing an
        optional dependency would write a traceback per bubble per turn into
        ``orchestrator.log`` for the life of the process. A degradation is
        supposed to be invisible, not merely survivable.
        """
        acp_mod, sid = acp_session
        monkeypatch.setattr(acp_mod, "_markdown", None)
        conn = self._attached(acp_mod, sid)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            frames = self._turn(acp_mod, sid, conn, [self._says("# still here")])
        assert [f["type"] for f in frames] == ["chunk", "meta", "chunk", "meta"]
        assert frames[2]["payload"]["text"] == "# still here"
        assert [r.getMessage() for r in caplog.records] == []

    def test_a_parser_failure_costs_the_rendering_and_nothing_else(
            self, acp_session, monkeypatch, caplog):
        """The flush runs inside ``_handle_prompt``'s ``finally``. Raising
        there would replace the turn's end marker with a traceback and leave
        the page's Send button disabled for the rest of the session."""
        acp_mod, sid = acp_session

        def explode(_text):
            raise RuntimeError("the parser fell over")

        monkeypatch.setattr(acp_mod, "_markdown", explode)
        conn = self._attached(acp_mod, sid)
        with caplog.at_level(logging.ERROR, logger="power_atlas.acp"):
            frames = self._turn(acp_mod, sid, conn, [self._says("hello")])
        assert [f["type"] for f in frames] == ["chunk", "meta", "chunk", "meta"]
        assert frames[-1]["payload"] == {"turn": "end", "stopReason": "end_turn"}
        assert any("the parser fell over" in r.getMessage() or r.exc_info
                   for r in caplog.records)

    def test_a_bubble_over_the_cap_stays_plain_text(self, acp_session,
                                                    monkeypatch, caplog):
        """``_markdown`` runs on the event loop and the tree it produces is
        recorded in the replay buffer beside the chunks it summarises, so the
        accumulator is bounded. Over the cap the bubble keeps the plain text it
        already has, which is a rendering nobody gets rather than a page that
        breaks."""
        acp_mod, sid = acp_session
        monkeypatch.setattr(acp_mod, "MAX_BUBBLE_CHARS", 8)
        conn = self._attached(acp_mod, sid)
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"):
            frames = self._turn(acp_mod, sid, conn,
                                [self._says("**far past the cap**")])
        assert [f["type"] for f in frames] == ["chunk", "meta", "chunk", "meta"]
        assert any("over the" in r.getMessage() for r in caplog.records)
        assert sid not in acp_mod._bubbles

    def test_closing_a_session_drops_its_half_written_bubble(self, acp_session):
        """The accumulator is keyed by session id and nothing else reaches it,
        so one left behind is agent-authored text resident for the life of the
        process with no path that could ever read or drop it — the same
        argument the ring buffer is released on."""
        acp_mod, sid = acp_session
        acp_mod._bubble_append(sid, "half an answer")

        async def answered(self_, method, params, timeout=None):
            return {}

        with patch.object(acp_mod._Supervisor, "_request", answered), \
                patch.object(acp_mod._Supervisor, "alive", lambda self_: True):
            asyncio.run(acp_mod._supervisor.close_session(sid))
        assert sid not in acp_mod._bubbles

    def test_closing_a_session_publishes_the_new_live_set(self, acp_session):
        """The publish that closes D32, driven through the real close path.

        `presence` can only reject a lock our own agent orphaned if it is told
        which sessions that agent still holds. That telling is five call sites
        rather than a property of the type, so it is asserted where it matters
        — after a real `close_session`, not by reading the source.
        """
        acp_mod, sid = acp_session
        published = []

        async def answered(self_, method, params, timeout=None):
            return {}

        previous = acp_mod.sessions_changed_hook
        try:
            acp_mod.set_sessions_changed_hook(
                lambda ids, pid: published.append((ids, pid)))
            assert published and sid in published[-1][0], (
                "installing the hook did not publish the current set, so a "
                "consumer holds its no-answer default until something changes")
            with patch.object(acp_mod._Supervisor, "_request", answered), \
                    patch.object(acp_mod._Supervisor, "alive", lambda self_: True):
                asyncio.run(acp_mod._supervisor.close_session(sid))
        finally:
            acp_mod.sessions_changed_hook = previous
        assert sid not in published[-1][0], (
            "the session was closed and nothing was published, so presence "
            "keeps reporting its orphaned lock live for the agent's lifetime")

    def test_a_failed_load_republishes_after_rolling_back(self, acp_store):
        """The single most D32-shaped path there is.

        `session/load` makes the agent write a lock naming itself, and *then*
        the request can fail. The rollback drops the record; if nothing is
        published, `presence` still believes the agent holds that session, so
        the lock it just orphaned is not suppressed and reads live for the
        agent's whole lifetime — which is D32 exactly, reinstated by the one
        code path that creates it.
        """
        acp_mod, store = acp_store
        published = []

        async def refuses(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("nope")

        previous = acp_mod.sessions_changed_hook
        try:
            acp_mod.sessions_changed_hook = lambda ids, pid: published.append(ids)
            with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                    patch.object(acp_mod._Supervisor, "_request", refuses), \
                    pytest.raises(acp_mod.AgentRejected):
                asyncio.run(acp_mod._supervisor.load_session(
                    "rollback-0001", str(store)))
        finally:
            acp_mod.sessions_changed_hook = previous
            acp_mod._supervisor.sessions.pop("rollback-0001", None)
            acp_mod._supervisor.history.pop("rollback-0001", None)
        assert published, "a failed load published nothing at all"
        assert "rollback-0001" not in published[-1], (
            "the rolled-back session is still in the published set, so the "
            "lock the agent wrote for it is not recognised as an orphan")

    def test_a_hook_that_raises_cannot_break_a_close(self, acp_session):
        """This runs on the create/load/close paths. A consumer that throws
        must cost a stale dashboard dot, never a session that will not close."""
        acp_mod, sid = acp_session

        async def answered(self_, method, params, timeout=None):
            return {}

        def boom(ids, pid):
            raise RuntimeError("the consumer is broken")

        previous = acp_mod.sessions_changed_hook
        try:
            acp_mod.set_sessions_changed_hook(boom)
            with patch.object(acp_mod._Supervisor, "_request", answered), \
                    patch.object(acp_mod._Supervisor, "alive", lambda self_: True):
                asyncio.run(acp_mod._supervisor.close_session(sid))
        finally:
            acp_mod.sessions_changed_hook = previous
        assert sid not in acp_mod._supervisor.sessions

    def test_the_sweeper_republishes_as_a_backstop(self, acp_session):
        """Every mutation publishes, but that is five call sites and a sixth
        added later would drift silently — with the symptom being a wrong dot
        for as long as the agent lives. The tick makes any miss self-heal."""
        acp_mod, sid = acp_session
        published = []
        previous = acp_mod.sessions_changed_hook
        try:
            acp_mod.sessions_changed_hook = lambda ids, pid: published.append(ids)
            # Mutate behind the mechanism's back, exactly as a missed call site
            # would, then run one tick.
            acp_mod._supervisor.sessions["sneaked-in"] = {"cwd": "C:\\x"}
            asyncio.run(acp_mod._sweep_once())
        finally:
            acp_mod.sessions_changed_hook = previous
            acp_mod._supervisor.sessions.pop("sneaked-in", None)
        assert published, "the sweeper tick published nothing"
        assert "sneaked-in" in published[0], (
            "the backstop did not republish, so a missed call site stays wrong "
            "until the process restarts")

    def test_a_dead_agent_drops_every_bubble(self, acp_session):
        acp_mod, sid = acp_session
        acp_mod._bubble_append(sid, "half an answer")
        acp_mod._supervisor._detach("the agent stopped answering")
        assert acp_mod._bubbles == {}


class _BlockReader:
    """A pipe that hands out pre-set blocks, one per ``read1``."""

    def __init__(self, blocks, on_read=None) -> None:
        self._blocks = list(blocks)
        self._on_read = on_read
        self.sizes: list[int] = []

    def read1(self, size):
        self.sizes.append(size)
        if self._on_read is not None:
            self._on_read()
        return self._blocks.pop(0) if self._blocks else b""


class _StdoutOf:
    def __init__(self, reader) -> None:
        self.buffer = reader


class _FakeProc:
    def __init__(self, reader) -> None:
        self.stdout = _StdoutOf(reader)
        self.pid = -1

    def poll(self):
        return 0


def _run_reader(acp_mod, blocks, on_read=None):
    """Drive the real reader loop over ``blocks`` and return what it posted."""
    posted = []
    sup = acp_mod._Supervisor()
    sup._post = lambda fn, *args: posted.append((fn.__name__, args))
    reader = _BlockReader(blocks, on_read)
    sup._reader_loop(_FakeProc(reader))
    return [args[0] for name, args in posted if name == "_on_message"], reader


class TestAcpReaderLineCap:
    """The agent→client direction had no size cap at all.

    ``for line in proc.stdout`` — and ``readline()``, which is the same thing —
    accumulates until a newline arrives however long that takes. Phase 4 is
    when it becomes live, because tool output under ``-a`` arrives on it.
    """

    def test_lines_split_across_blocks_are_reassembled(self):
        from power_atlas import acp as acp_mod
        msgs, _ = _run_reader(acp_mod, [b'{"id":1}\n{"id"', b':2}\n'])
        assert msgs == [{"id": 1}, {"id": 2}]

    def test_a_multibyte_character_split_across_blocks_survives(self):
        """Bytes are accumulated and decoded only at the newline, so a UTF-8
        sequence straddling a block boundary is not two replacement chars."""
        from power_atlas import acp as acp_mod
        msgs, _ = _run_reader(acp_mod, [b'{"t":"\xc3', b'\xa9"}\n'])
        assert msgs == [{"t": "\u00e9"}]

    def test_an_overlong_line_is_dropped_and_the_channel_continues(self):
        from power_atlas import acp as acp_mod
        with patch.object(acp_mod, "MAX_AGENT_LINE_BYTES", 64):
            msgs, _ = _run_reader(acp_mod, [
                b'{"first":1}\n',
                b'{"junk":"' + b"z" * 500,          # no newline: over the cap
                b'more junk still no newline' * 10,  # the tail is swallowed too
                b'"}\n{"after":2}\n',
            ])
        # Only the tail of the rejected line was discarded — not the good line
        # that shared its final block, which is what makes this a cap and not a
        # channel reset.
        assert msgs == [{"first": 1}, {"after": 2}]

    def test_the_cap_is_not_simply_dropping_everything(self):
        """Positive control: a line just under the cap still gets through."""
        from power_atlas import acp as acp_mod
        payload = ('{"t":"' + "z" * 40 + '"}').encode()
        assert len(payload) < 64
        with patch.object(acp_mod, "MAX_AGENT_LINE_BYTES", 64):
            msgs, _ = _run_reader(acp_mod, [payload + b"\n"])
        assert len(msgs) == 1

    def test_reads_are_bounded_blocks_delivered_as_they_arrive(self):
        """A buffered ``read(n)`` loops until it has all n bytes, so it would
        hold every chunk of a streamed answer until 64 KiB had accumulated.
        ``read1`` returns one OS read, which is what makes streaming visible."""
        from power_atlas import acp as acp_mod
        seen_at_each_read = []
        posted = []
        sup = acp_mod._Supervisor()
        sup._post = lambda fn, *args: posted.append(fn.__name__)
        reader = _BlockReader(
            [b'{"id":1}\n', b'{"id":2}\n'],
            on_read=lambda: seen_at_each_read.append(
                posted.count("_on_message")))
        sup._reader_loop(_FakeProc(reader))

        assert reader.sizes and set(reader.sizes) == {acp_mod.READ_BLOCK_BYTES}
        # By the second read the first message had already been delivered.
        assert seen_at_each_read[:2] == [0, 1]

    def test_non_json_noise_is_tolerated(self):
        from power_atlas import acp as acp_mod
        msgs, _ = _run_reader(acp_mod, [b'banner text\n\n{"id":1}\n'])
        assert msgs == [{"id": 1}]


# --- ACP phase 4 fixes: CSP, wire encoding, subscribe throttle ---


class TestAcpContentSecurityPolicy:
    """``/acp`` renders agent-authored prose and agent-authored tool commands,
    and the agent behind it runs trust-all-tools. Until now the only control on
    that was the page's own no-innerHTML discipline — a rule every future line
    has to keep. A nonce policy is the control that does not depend on it.
    """

    def _policy(self, resp) -> str:
        header = resp.headers.get("content-security-policy")
        assert header, "GET /acp served no Content-Security-Policy at all"
        return header

    def _nonce(self, policy: str) -> str:
        match = re.search(r"script-src 'nonce-([^']+)'", policy)
        assert match, "the policy is not nonce-based: %s" % policy
        return match.group(1)

    def test_the_page_carries_a_nonce_policy(self, raw_client):
        resp = raw_client.get("/acp")
        assert resp.status_code == 200
        policy = self._policy(resp)
        self._nonce(policy)
        # `'unsafe-inline'` would permit both an injected <script> and an
        # <img onerror=…>, which is the whole vector this exists against.
        assert "'unsafe-inline'" not in policy
        assert "'unsafe-eval'" not in policy
        for directive in ("default-src 'self'", "object-src 'none'",
                          "base-uri 'none'", "frame-ancestors 'none'"):
            assert directive in policy, "%r missing from %s" % (directive, policy)

    def test_thumbnails_are_admitted_and_only_as_blobs(self, raw_client):
        """`default-src 'self'` governs images without this, and `'self'` does
        not cover a `blob:` — so the attachment tray would render nothing.

        `data:` stays out. It is the wider grant of the two and the
        non-revocable one, and the page has no use for it: every thumbnail is
        an object URL it minted and hands back when the turn starts."""
        policy = self._policy(raw_client.get("/acp"))
        assert "img-src 'self' blob:" in policy, policy
        assert "img-src" in policy and "data:" not in policy, policy

    def test_the_header_nonce_is_the_one_on_the_page(self, raw_client):
        """A mismatched nonce is a blank page, and it passes every assertion
        that only checks the header exists."""
        resp = raw_client.get("/acp")
        nonce = self._nonce(self._policy(resp))
        # The page's own inline block, which is the one the policy would blank.
        assert '<script nonce="%s">' % nonce in resp.text
        # And the shared <head> tag, which would otherwise log a violation on a
        # page whose console has to stay readable.
        assert ('<script nonce="%s" src="/static/htmx.min.js">' % nonce) in resp.text
        # The vendored highlighter. An external script rides the same nonce as
        # an inline one, which is why adding it needed no widening of
        # `script-src` — and why it is blanked just as silently without one.
        assert ('<script nonce="%s" src="/static/prism.js">' % nonce) in resp.text
        tags = re.findall(r"<script\b[^>]*>", resp.text)
        assert len(tags) == 3, "a script tag was added without a nonce: %s" % tags
        # Every tag, not only the three named above. The count makes adding a
        # script a deliberate act; this makes a nonce-less one a failing test
        # rather than a feature that quietly does nothing in the browser.
        for tag in tags:
            assert ('nonce="%s"' % nonce) in tag, "a script tag has no nonce: %s" % tag

    def test_the_highlighter_is_served_and_is_inert_on_load(self, raw_client):
        """The page names /static/prism.js; a 404 there is a silent downgrade.

        ``manual`` is the property that makes vendoring it safe at all: Prism's
        core reads it while building itself, and without it, loading Prism hooks
        ``DOMContentLoaded`` and rewrites every ``<pre>`` on the page through
        ``innerHTML`` — the one sink /acp does not have. The page calls
        ``Prism.tokenize()`` and never hands Prism a DOM node.
        """
        resp = raw_client.get("/static/prism.js")
        assert resp.status_code == 200
        assert "window.Prism = { manual: true };" in resp.text
        # Ahead of prism-core, or core reads it too late to matter.
        assert resp.text.index("window.Prism = { manual: true };") < \
            resp.text.index("/* --- prism-core --- */")

    def test_the_nonce_is_regenerated_per_response(self, raw_client):
        first = self._nonce(self._policy(raw_client.get("/acp")))
        second = self._nonce(self._policy(raw_client.get("/acp")))
        assert first != second

    def test_connect_src_admits_the_socket_the_page_opens(self, raw_client):
        """The easiest way to break this page with a CSP. ``/ws/acp`` is what
        the whole feature runs on, and every server-side test still passes when
        ``connect-src`` forbids it."""
        resp = raw_client.get("/acp", headers={"Host": "127.0.0.1:4915"})
        assert resp.status_code == 200
        match = re.search(r"connect-src ([^;]+)", self._policy(resp))
        assert match, "no connect-src directive at all"
        sources = match.group(1).split()
        assert "ws://127.0.0.1:4915" in sources
        assert "wss://127.0.0.1:4915" in sources

    def test_the_policy_names_the_host_actually_served(self, raw_client):
        """A hardcoded port would be wrong on most launches: config.py defaults
        `port` to 0 and the OS assigns one."""
        resp = raw_client.get("/acp", headers={"Host": "localhost:53119"})
        assert "ws://localhost:53119" in self._policy(resp)

    def test_no_policy_leaks_onto_the_dashboard(self, raw_client):
        """base.html is shared. index.html carries substantial inline script and
        static/htmx.min.js binds at DOMContentLoaded, so this policy would risk
        the dashboard for no gain — it renders no agent-authored text."""
        resp = raw_client.get("/")
        assert resp.status_code == 200
        assert "content-security-policy" not in resp.headers
        # Rendered, not assumed: the shared template must be byte-identical
        # where it was touched.
        assert '<script src="/static/htmx.min.js"></script>' in resp.text


class _EncodingWs:
    """A socket that encodes what it is handed, the way the transport does.

    ``_SinkWs`` accepts any ``str``. uvicorn's websockets layer encodes to UTF-8
    before framing, and that encode is where a lone surrogate raises — inside
    ``_write_loop``, whose catch-all then retires the socket.
    """

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed: list[tuple[int, str]] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text.encode("utf-8"))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _write_through(acp_mod, *frames):
    """Put frames through the real writer task; returns ``(ws, conn)``."""

    async def drive():
        ws = _EncodingWs()
        conn = acp_mod._Connection(ws)
        conn.start()
        for frame in frames:
            conn.send(frame)
        await conn.drain()
        await conn.stop()
        return ws, conn

    return asyncio.run(drive())


class TestAcpWireEncoding:
    """The outbound serializer defaulted to ``ensure_ascii=True``, so every
    non-ASCII character the agent produced left as a 6- or 12-byte escape.
    """

    def test_non_ascii_reaches_the_wire_as_utf8(self):
        from power_atlas import acp as acp_mod
        text = "héllo — 🌍" * 200
        frame = acp_mod.envelope("chunk", {"role": "agent", "text": text})
        ws, _ = _write_through(acp_mod, frame)
        assert ws.closed == []
        assert len(ws.sent) == 1
        assert b"\\u" not in ws.sent[0]
        assert text.encode("utf-8") in ws.sent[0]
        # The saving, measured rather than asserted as an inequality that a
        # one-byte difference would satisfy.
        assert len(ws.sent[0]) < 0.6 * len(json.dumps(frame).encode("utf-8"))

    def test_a_lone_surrogate_does_not_retire_a_healthy_socket(self):
        """``json.loads`` turns a ``\\ud800`` escape in the agent's own output
        into a lone surrogate, and UTF-8 cannot represent one. Unguarded, that
        encode raises where the writer's catch-all takes the socket down."""
        from power_atlas import acp as acp_mod
        text = json.loads(r'"\ud800 tail"')
        frame = acp_mod.envelope("chunk", {"role": "agent", "text": text})
        ws, _ = _write_through(acp_mod, frame)
        assert ws.closed == [], "an encodable frame retired the socket"
        assert len(ws.sent) == 1, "the frame never reached the wire"
        assert json.loads(ws.sent[0].decode("utf-8"))["payload"]["text"] == text

    def test_the_fallback_is_per_frame_and_not_per_socket(self):
        """Positive control: a poisoned frame must not send the rest of the
        stream back to the escaped form for the socket's lifetime."""
        from power_atlas import acp as acp_mod
        poisoned = acp_mod.envelope(
            "chunk", {"role": "agent", "text": json.loads(r'"\udfff"')})
        ordinary = acp_mod.envelope("chunk", {"role": "agent", "text": "🌍" * 50})
        ws, _ = _write_through(acp_mod, poisoned, ordinary)
        assert ws.closed == []
        assert len(ws.sent) == 2
        assert b"\\u" in ws.sent[0]
        assert b"\\u" not in ws.sent[1]

    def test_the_byte_budget_now_prices_what_the_wire_carries(self):
        """The send queue and the history buffer both charge UTF-8 bytes. While
        the wire was escaped they bounded roughly a third of the memory they
        were sizing."""
        from power_atlas import acp as acp_mod
        frame = acp_mod.envelope("chunk", {"role": "agent", "text": "🌍" * 5000})
        wire = len(acp_mod._dumps_frame(frame).encode("utf-8"))
        assert wire <= acp_mod._frame_weight(frame) * 1.1


class TestAcpSubscribeThrottle:
    """A ~60-byte ``subscribe`` rebuilds the whole replay buffer — up to
    HISTORY_MAX_BYTES — into one ``history`` frame, and ``_dispatch`` applied no
    throttle at all, so SEND_QUEUE_MAXSIZE of them could be queued on the event
    loop that also serves the dashboard.
    """

    def _fill(self, acp_mod, sid, events=50):
        history = acp_mod._supervisor.history[sid]
        for i in range(events):
            history.append(acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))

    def _conn(self, acp_mod):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        return conn

    def test_a_legitimate_reload_is_never_throttled(self, acp_session):
        """The page sends exactly one ``subscribe`` per socket, from onopen."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        assert [f["type"] for f in _queued(conn)] == ["session", "history"]

    def test_a_repeat_is_refused_with_a_typed_error(self, acp_session, caplog):
        """Typed rather than silent: silence on this page is indistinguishable
        from a server that stopped answering."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "subscribe_throttled"
        assert any("throttled" in r.getMessage() for r in caplog.records)

    def test_the_replay_is_not_rebuilt_while_throttled(self, acp_session):
        """The re-serialisation is the cost this exists to stop, so a refused
        call must produce no ``history`` frame at all."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid, acp_mod.HISTORY_MAXLEN)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        for _ in range(20):
            acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        assert len(frames) == 20
        assert {f["type"] for f in frames} == {"error"}

    def test_a_fresh_socket_is_not_throttled_by_its_predecessor(self, acp_session):
        """A reconnect is a new socket, and throttling it would break the one
        recovery path the page has."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        acp_mod._handle_subscribe(self._conn(acp_mod), sid)
        second = self._conn(acp_mod)
        acp_mod._handle_subscribe(second, sid)
        assert [f["type"] for f in _queued(second)] == ["session", "history"]

    def test_the_window_expires_rather_than_latching(self, acp_session):
        """Positive control: a rate, not a one-replay-per-socket cap."""
        acp_mod, sid = acp_session
        self._fill(acp_mod, sid)
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        with patch.object(acp_mod, "SUBSCRIBE_MIN_INTERVAL_SECONDS", 0.0):
            acp_mod._handle_subscribe(conn, sid)
        assert [f["type"] for f in _queued(conn)] == ["session", "history"]

    def test_the_refusal_paths_keep_their_own_codes(self, acp_session):
        """The two cheap refusals sit above the throttle: each costs one small
        frame and the send queue already bounds them, so answering one of them
        ``subscribe_throttled`` would only make the page harder to read."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        _queued(conn)
        acp_mod._handle_subscribe(conn, "no-such-session")
        assert _queued(conn)[0]["payload"]["code"] == "unknown_session"


# --- ACP phase 5: resume a session this process never created ---


async def _no_spawn(self):
    """``ensure_started`` without an agent. Every load test stubs the
    transport, so spawning a real one would only add ~1 s and a process tree."""
    return None


def _lock_time(when) -> str:
    """A lock timestamp in the shape kiro-cli writes: RFC 3339, nanoseconds."""
    return when.astimezone(dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f") + "000Z"


@pytest.fixture
def acp_store(tmp_path, monkeypatch):
    """An empty kiro-cli session store, and a supervisor left clean after.

    The supervisor and registry are module globals: a load test that left a
    session registered would make the next test's ``subscribe`` answer from it.
    """
    from power_atlas import acp as acp_mod
    monkeypatch.setattr(acp_mod, "KIRO_SESSION_DIR", tmp_path)
    try:
        yield acp_mod, tmp_path
    finally:
        acp_mod._supervisor.sessions.clear()
        acp_mod._supervisor.history.clear()
        acp_mod._supervisor.inflight.clear()
        acp_mod._supervisor.closing.clear()
        acp_mod._supervisor._reserved = 0
        acp_mod._supervisor.crews.clear()
        acp_mod._supervisor.subagent_sessions.clear()
        acp_mod._supervisor.subagent_history.clear()
        for conn in tuple(acp_mod._registry.connections):
            acp_mod._registry.detach(conn)
        acp_mod._registry.connections.clear()
        acp_mod._registry.subscribers.clear()
        # `loading` was leaking between tests: a session left in it makes the
        # next test's `_handle_close`, `_handle_load` and now the sweeper take
        # their "a load is in flight" branch on a session that does not exist,
        # which reads as the code under test silently doing nothing.
        acp_mod._registry.loading.clear()
        # The sweeper's per-session failure counts are keyed by session id, and
        # these tests reuse ids: a count left behind would make the next test's
        # first failure log the short "failed again" line instead of the
        # traceback it asserts.
        acp_mod._sweep_failures.clear()


def _acp_conn(acp_mod):
    conn = acp_mod._Connection(_SinkWs())
    acp_mod._registry.connections.add(conn)
    return conn


class TestAcpSessionIdValidation:
    """The id arrives from the browser, is joined into the kiro-cli session
    store to read a lock file, and is then sent to an agent running
    trust-all-tools. Nothing downstream re-checks it.
    """

    @pytest.mark.parametrize("sid", [
        "../../../../Windows/System32/config/SAM",
        "..",
        ".",
        "a/b",
        "a\\b",
        "C:\\Windows",
        "sess:1",
        "sess.1",
        "sess 1",
        "sess\x00",
        # `$` also matches just before a trailing newline, so the shared
        # pattern used with `match` accepts this one.
        "sess\n",
        "",
        "x" * 129,
    ])
    def test_hostile_shapes_are_rejected(self, sid):
        from power_atlas import acp as acp_mod
        assert not acp_mod._valid_session_id(sid)

    @pytest.mark.parametrize("sid", [None, 17, 1.5, ["a"], {"a": 1}, b"abc"])
    def test_non_strings_are_rejected(self, sid):
        from power_atlas import acp as acp_mod
        assert not acp_mod._valid_session_id(sid)

    @pytest.mark.parametrize("sid", [
        "001b4195-ee19-4633-b1b2-488574cad044",
        "sess_phase4",
        "x" * 128,
    ])
    def test_real_session_ids_are_accepted(self, sid):
        """Positive control: the guard refuses hostile shapes, not the ids the
        store is actually full of."""
        from power_atlas import acp as acp_mod
        assert acp_mod._valid_session_id(sid)

    def test_a_rejected_id_reaches_neither_a_path_nor_the_wire(self, acp_store):
        acp_mod, _ = acp_store
        touched = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder",
                          lambda sid: touched.append(("lock", sid))), \
                patch.object(acp_mod._Supervisor, "_request",
                             lambda *a, **k: touched.append(("wire",))):
            asyncio.run(acp_mod._handle_load(conn, "../../etc/passwd"))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "bad_session_id"
        assert touched == []

    def test_a_rejected_id_leaves_a_server_side_trace(self, acp_store, caplog):
        acp_mod, _ = acp_store
        conn = _acp_conn(acp_mod)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            asyncio.run(acp_mod._handle_load(conn, "a/b"))
        assert any("bad_session_id" in r.getMessage() for r in caplog.records)


class TestAcpLockPreflight:
    """A hint, never the gate. Measured on this machine's store: 803 lock
    files, 22 naming a pid that still exists — and all 22 were recycled pids
    (svchost, firefox, RuntimeBroker), each created weeks after its lock was
    written. A pre-flight resting on pid liveness alone would have refused 22
    loadable sessions and been wrong every time it fired.
    """

    def _lock(self, store, sid, **fields):
        (store / (sid + ".lock")).write_text(json.dumps(fields))

    def test_a_live_lock_names_its_holder(self, acp_store):
        acp_mod, store = acp_store
        # This test process: alive, and started before a lock written now.
        self._lock(store, "held", pid=os.getpid(),
                   started_at=_lock_time(dt.datetime.now(dt.timezone.utc)))
        assert acp_mod._lock_holder("held") == os.getpid()

    def test_a_recycled_pid_is_not_a_holder(self, acp_store):
        """The 22-of-803 case: the pid exists, but the process behind it
        started long after the lock was written, so it is not the holder."""
        acp_mod, store = acp_store
        self._lock(store, "stale", pid=os.getpid(),
                   started_at="2020-01-01T00:00:00.000000000Z")
        assert acp_mod._lock_holder("stale") is None

    def test_a_dead_pid_is_not_a_holder(self, acp_store):
        acp_mod, store = acp_store
        self._lock(store, "gone", pid=2 ** 31 - 1,
                   started_at=_lock_time(dt.datetime.now(dt.timezone.utc)))
        assert acp_mod._lock_holder("gone") is None

    def test_a_missing_lock_is_not_a_holder(self, acp_store):
        acp_mod, _ = acp_store
        assert acp_mod._lock_holder("never-locked") is None

    @pytest.mark.parametrize("body", [
        "not json at all",
        "[]",
        '{"pid": "4242", "started_at": "2026-01-01T00:00:00.0Z"}',
        '{"pid": -1, "started_at": "2026-01-01T00:00:00.0Z"}',
        '{"started_at": "2026-01-01T00:00:00.0Z"}',
        '{"pid": %d}' % os.getpid(),
        '{"pid": %d, "started_at": "whenever"}' % os.getpid(),
    ])
    def test_an_unreadable_lock_grants_nothing(self, acp_store, body):
        """A hint may only ever add a refusal. Every branch that cannot
        establish a holder has to fall through to the agent, which is the
        authority."""
        acp_mod, store = acp_store
        (store / "odd.lock").write_text(body)
        assert acp_mod._lock_holder("odd") is None

    def test_the_preflight_refuses_before_the_wire(self, acp_store):
        acp_mod, store = acp_store
        self._lock(store, "busy-0001", pid=os.getpid(),
                   started_at=_lock_time(dt.datetime.now(dt.timezone.utc)))
        wire = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          lambda *a, **k: wire.append(a)):
            asyncio.run(acp_mod._handle_load(conn, "busy-0001"))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "session_in_use"
        assert str(os.getpid()) in frames[0]["payload"]["message"]
        assert wire == []
        assert "busy-0001" not in acp_mod._supervisor.sessions


class TestAcpSessionLoad:
    """``session/load`` is what reaches a session this process never created —
    one made in a terminal, or before a restart.
    """

    def _stored(self, store, sid, cwd):
        (store / (sid + ".json")).write_text(
            json.dumps({"session_id": sid, "cwd": str(cwd), "title": "t"}))

    def _replay(self, acp_mod, sid, events, calls):
        """A ``session/load`` that answers the way the agent does: by replaying
        the conversation as notifications while the request is outstanding."""
        async def fake_request(self, method, params,
                               timeout=acp_mod.REQUEST_TIMEOUT_SECONDS):
            calls.append((method, params))
            for kind, text in events:
                self._on_notification({
                    "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": kind,
                        "content": {"type": "text", "text": text}}},
                })
            return {}
        return fake_request

    def test_the_replayed_conversation_arrives_as_one_history_frame(
            self, acp_store):
        """Both halves of the conversation, in order, coalesced. Frame per
        event would put a whole conversation on a queue that retires the socket
        at SEND_QUEUE_MAXSIZE — and only for sessions long enough to matter."""
        acp_mod, store = acp_store
        calls = []
        sid = "load-me-0001"
        self._stored(store, sid, store)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid, [
                              ("user_message_chunk", "what is 2+2"),
                              ("agent_message_chunk", "4"),
                          ], calls)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))

        assert calls == [(
            "session/load",
            {"sessionId": sid, "cwd": str(Path(store).resolve()),
             "mcpServers": []},
        )]
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert frames[0]["payload"] == {
            "pending": "load",
            "timeoutSeconds": acp_mod.REQUEST_TIMEOUT_SECONDS}
        assert frames[1]["payload"]["sessionId"] == sid
        assert frames[1]["payload"]["created"] is False
        assert [(e["type"], e["payload"])
                for e in frames[2]["payload"]["events"]] == [
            ("chunk", {"role": "user", "text": "what is 2+2"}),
            ("chunk", {"role": "agent", "text": "4"}),
            # The replay's own last answer, rendered. Nothing behind it closes
            # the bubble — the agent sends the conversation and stops — so
            # `_handle_load` flushes once the replay is complete. Without it
            # the one bubble a returning reader is looking at is the one still
            # showing markdown source.
            ("rendered", {"tokens": [
                {"type": "paragraph",
                 "children": [{"type": "text", "raw": "4"}]}]}),
        ]

    def test_a_replayed_tool_call_survives_the_load(self, acp_store):
        """Tool calls already forward and render; a loaded history full of them
        has to travel the same path rather than a second one."""
        acp_mod, store = acp_store
        sid = "load-tools-01"
        self._stored(store, sid, store)

        async def fake_request(self, method, params, timeout=None):
            self._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "tc-1", "title": "shell", "kind": "execute",
                    "status": "completed",
                    "rawInput": {"command": "git status"}}},
            })
            return {}

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        events = _queued(conn)[2]["payload"]["events"]
        assert [e["type"] for e in events] == ["tool_call"]
        assert events[0]["payload"]["command"] == "git status"

    def test_the_replay_is_not_fanned_out_while_it_is_being_built(
            self, acp_store):
        """A socket left attached from an earlier life of the session would
        otherwise receive the conversation event by event."""
        acp_mod, store = acp_store
        sid = "load-me-0002"
        self._stored(store, sid, store)
        stale = _acp_conn(acp_mod)
        acp_mod._registry.attach(stale, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid,
                                       [("agent_message_chunk", "x")] * 5,
                                       [])), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert _queued(stale) == []
        assert stale.session_id is None

    def test_the_wire_error_shape_2160_answers_with_carries_the_pid(self):
        """The refusal as 2.16.0 actually sends it, through `_on_response`.

        The test below hand-builds the `AgentRejected`, which is how the drift
        this covers went unnoticed: it asserted what `_load_failure` does with
        a message shape, and nothing asserted that the client could *produce*
        that shape from the wire. It could not. `_on_response` built its text
        from `message` and `code` only and dropped `data`, so `_IN_USE_MARKER`
        — the constant that exists to match this exact string — was
        unmatchable, and every in-use refusal fell through to the unattributed
        branch.

        The frame here is the one measured on kiro-cli 2.16.0 on 2026-08-03,
        copied from the probe output rather than imagined.
        """
        from power_atlas import acp as acp_mod
        sup = acp_mod._Supervisor.__new__(acp_mod._Supervisor)
        sup._pending = {}
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            sup._pending[7] = fut
            sup._on_response({
                "jsonrpc": "2.0", "id": 7,
                "error": {
                    "code": -32603,
                    "message": "Internal error",
                    "data": "Failed to start session: Session is active in "
                            "another process (PID 22264)",
                },
            })
            with pytest.raises(acp_mod.AgentRejected) as caught:
                fut.result()
        finally:
            loop.close()
        text = str(caught.value)
        assert acp_mod._IN_USE_MARKER in text, (
            "the marker that exists to recognise this refusal still cannot "
            "match what the agent actually sends")
        assert "22264" in text, "the pid the agent supplied was dropped"
        assert "(code -32603)" in text
        # And the whole point: `_load_failure` now attributes it with no lock
        # file involved at all.
        code, message = acp_mod._load_failure(caught.value, None)
        assert code == "session_in_use"
        assert "22264" in message

    def test_an_error_without_data_still_reads_as_before(self):
        """A build that says nothing must be unchanged — 2.14.2 did exactly
        this, so the fallback is a live path and not legacy."""
        from power_atlas import acp as acp_mod
        sup = acp_mod._Supervisor.__new__(acp_mod._Supervisor)
        sup._pending = {}
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            sup._pending[1] = fut
            sup._on_response({"jsonrpc": "2.0", "id": 1,
                              "error": {"code": -32603,
                                        "message": "Internal error"}})
            with pytest.raises(acp_mod.AgentRejected) as caught:
                fut.result()
        finally:
            loop.close()
        assert str(caught.value) == "Internal error (code -32603)"
        assert acp_mod._IN_USE_MARKER not in str(caught.value)

    def test_the_error_detail_is_bounded(self):
        """`data` is agent-controlled text on its way to a message the user
        reads, so a hostile or looping agent cannot make one refusal large."""
        from power_atlas import acp as acp_mod
        sup = acp_mod._Supervisor.__new__(acp_mod._Supervisor)
        sup._pending = {}
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            sup._pending[2] = fut
            sup._on_response({"jsonrpc": "2.0", "id": 2,
                              "error": {"code": -32603, "message": "Internal error",
                                        "data": "x" * 50_000}})
            with pytest.raises(acp_mod.AgentRejected) as caught:
                fut.result()
        finally:
            loop.close()
        assert str(caught.value).count("x") == acp_mod.MAX_ERROR_DETAIL_CHARS

    @pytest.mark.parametrize("data", [{"pid": 1}, ["a"], 42, None, "", "   "])
    def test_a_data_field_that_is_not_usable_text_is_ignored(self, data):
        """`data` is typed `any` by JSON-RPC, so it is not necessarily a string.

        The `isinstance` guard is not defensive noise: without it a dict or a
        list reaches `.strip()` and raises **inside `_on_response`**, which runs
        on the reader thread's callback path — so the failure is not a bad
        message, it is an exception where the code that delivers every response
        lives. Falling back to `message` is the same text this built before
        `data` was read at all.
        """
        from power_atlas import acp as acp_mod
        sup = acp_mod._Supervisor.__new__(acp_mod._Supervisor)
        sup._pending = {}
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            sup._pending[3] = fut
            err = {"code": -32603, "message": "Internal error"}
            if data is not None:
                err["data"] = data
            sup._on_response({"jsonrpc": "2.0", "id": 3, "error": err})
            with pytest.raises(acp_mod.AgentRejected) as caught:
                fut.result()
        finally:
            loop.close()
        assert str(caught.value) == "Internal error (code -32603)"

    def test_a_spoken_in_use_refusal_becomes_a_readable_message(
            self, acp_store):
        """The plan recorded the agent naming the holder itself, measured
        before it self-updated. Builds that still do are read as such."""
        acp_mod, store = acp_store
        sid = "load-me-0003"
        self._stored(store, sid, store)

        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected(
                "Session is active in another process (PID 4242) (code -32603)")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", refused), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta", "error"]
        assert frames[1]["payload"]["code"] == "session_in_use"
        assert "4242" in frames[1]["payload"]["message"]

    def test_a_silent_refusal_is_named_by_reading_the_lock_again(
            self, acp_store):
        """Measured on kiro-cli 2.14.2: a session held elsewhere is refused
        with a bare ``-32603 Internal error`` — no pid, and nothing to tell it
        from any other failure. The lock is the only thing left that can name
        the process. Also covers a lock taken after the pre-flight ran."""
        acp_mod, store = acp_store
        sid = "load-me-0009"
        self._stored(store, sid, store)
        first = []

        def one_shot_preflight(session_id):
            # The pre-flight sees nothing; the re-read after the failure does.
            if not first:
                first.append(session_id)
                return None
            return 4242

        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("Internal error (code -32603)")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", one_shot_preflight), \
                patch.object(acp_mod._Supervisor, "_request", refused), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert frames[1]["payload"]["code"] == "session_in_use"
        assert "4242" in frames[1]["payload"]["message"]

    def test_another_agent_error_keeps_its_own_code(self, acp_store):
        """Positive control: with no lock and no marker, a refusal keeps the
        code it came with rather than being reported as an occupied session."""
        acp_mod, store = acp_store
        sid = "load-me-0004"
        self._stored(store, sid, store)

        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("No such session (code -32602)")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", refused), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert _queued(conn)[1]["payload"]["code"] == "agent_error"

    def test_a_failed_load_registers_nothing(self, acp_store):
        """A half-loaded session left behind would count against MAX_SESSIONS
        and be answered by ``subscribe`` with an empty transcript."""
        acp_mod, store = acp_store
        sid = "load-me-0005"
        self._stored(store, sid, store)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert sid not in acp_mod._supervisor.sessions
        assert sid not in acp_mod._supervisor.history
        assert acp_mod._supervisor._reserved == 0
        assert _queued(conn)[1]["payload"]["code"] == "agent_timeout"

    def test_a_session_already_live_here_is_answered_from_the_buffer(
            self, acp_session):
        """A second agent-side replay would append the conversation to itself."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.history[sid].append(
            acp_mod.envelope("chunk", {"role": "agent", "text": "old"}, sid))
        wire = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          lambda *a, **k: wire.append(a)):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["session", "history"]
        assert len(frames[1]["payload"]["events"]) == 1
        assert wire == []

    def test_a_missing_store_entry_falls_back_to_the_neutral_cwd(
            self, acp_store, monkeypatch, tmp_path):
        """A workspace that has been moved or deleted does not make the
        conversation unreadable, so it must not make the load fail."""
        acp_mod, store = acp_store
        calls = []
        neutral = tmp_path / "neutral"
        neutral.mkdir()
        monkeypatch.setattr(acp_mod, "_neutral_cwd", lambda: neutral)
        sid = "load-me-0006"
        self._stored(store, sid, store / "deleted-workspace")
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid, [], calls)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert calls[0][1]["cwd"] == str(neutral)

    def test_a_load_is_never_throttled_by_an_earlier_replay(self, acp_store):
        """The throttle rations a buffer rebuild a client can ask for freely.
        This replay was paid for with an agent round-trip, and refusing it
        would leave a loaded session rendering nothing at all."""
        acp_mod, store = acp_store
        sid = "load-me-0008"
        self._stored(store, sid, store)
        acp_mod._supervisor.sessions["other"] = {"cwd": ""}
        acp_mod._supervisor.history["other"] = acp_mod._History()
        conn = _acp_conn(acp_mod)
        acp_mod._handle_subscribe(conn, "other")
        _queued(conn)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay(acp_mod, sid,
                                       [("agent_message_chunk", "x")], [])), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        # The one replayed chunk, plus the rendering of the bubble it closed.
        assert [e["type"] for e in frames[2]["payload"]["events"]] == [
            "chunk", "rendered"]

    def test_the_session_cap_still_binds(self, acp_store):
        acp_mod, store = acp_store
        sid = "load-me-0007"
        self._stored(store, sid, store)
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        wire = []
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          lambda *a, **k: wire.append(a)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        frames = _queued(conn)
        # One frame, and no pending label ahead of it. The cap is read before
        # the load spends anything on a session it is going to refuse, so the
        # page is never told a load is running that never was.
        assert [f["type"] for f in frames] == ["error"], frames
        assert frames[0]["payload"]["code"] == "too_many_sessions"
        assert wire == []

    def test_dispatch_routes_load_off_the_synchronous_path(self, acp_store):
        """``_handle_subscribe`` must stay free of ``await``; ``load`` is the
        half that needs one, which is why it is a separate frame type."""
        acp_mod, _ = acp_store
        seen = []

        async def fake_load(conn, session_id):
            seen.append(session_id)

        async def drive():
            conn = _acp_conn(acp_mod)
            acp_mod._dispatch(conn, {"type": "load", "sessionId": "abc",
                                     "payload": {}})
            await asyncio.sleep(0)

        assert "load" in acp_mod.CLIENT_TYPES
        with patch.object(acp_mod, "_handle_load", fake_load):
            asyncio.run(drive())
        assert seen == ["abc"]
        assert not asyncio.iscoroutinefunction(acp_mod._handle_subscribe)


def _stored_session(store, sid):
    """The minimum a load needs from the kiro-cli store: a recoverable cwd."""
    (store / (sid + ".json")).write_text(
        json.dumps({"session_id": sid, "cwd": str(store)}))


class TestAcpLoadServesOneReplayToEverySocket:
    """A socket that asks for a session while its ``session/load`` is running.

    ``load_session`` has to register the session before the round-trip — the
    agent replays the conversation as notifications while the request is
    outstanding, and ``record`` drops frames for a session with no buffer — and
    ``_emit`` both records *and* broadcasts. So for the duration of the load the
    registry saw a live session and attached anything that asked, handing it the
    replay frame by frame: at 1200 events a second socket subscribing halfway
    queued SEND_QUEUE_MAXSIZE frames and set ``_overflowed``. One click reaches
    it, because the dashboard row action opens ``/acp?sid=…`` and two tabs on
    one session is a supported shape.

    Suppressing the broadcast for the duration is the trap, not the fix: that
    socket would receive a ``history`` frame of the events so far and then never
    see the rest, trading a retired socket — visible — for a silently truncated
    conversation.
    """

    # Above SEND_QUEUE_MAXSIZE (256), which is the number that decides this:
    # at 256 queued frames `_Connection.send` sets `_overflowed` and the writer
    # retires the socket.
    EVENTS = 300

    def _replay_with(self, sid, count, halfway):
        """A ``session/load`` that replays ``count`` events and runs ``halfway``
        in the middle of them — where another socket's frame would land."""
        async def fake_request(self, method, params, timeout=None):
            for i in range(count):
                if i == count // 2:
                    halfway()
                self._on_notification({
                    "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "e%d" % i}}},
                })
            return {}
        return fake_request

    def test_a_socket_subscribing_mid_load_gets_one_frame_and_the_whole_tail(
            self, acp_store):
        acp_mod, store = acp_store
        sid = "load-mid-0001"
        _stored_session(store, sid)
        loader = _acp_conn(acp_mod)
        second = _acp_conn(acp_mod)

        with patch.object(
                acp_mod._Supervisor, "_request",
                self._replay_with(
                    sid, self.EVENTS,
                    lambda: acp_mod._handle_subscribe(second, sid))), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(loader, sid))

        frames = _queued(second)
        # No `chunk` of its own anywhere: every event arrived inside the replay.
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert frames[0]["payload"]["pending"] == "load"
        events = frames[2]["payload"]["events"]
        # Both halves of the property. The head proves the replay was not
        # started late; the tail proves the events broadcast after this socket
        # asked were not merely dropped.
        # Every replayed chunk, and behind them the one `rendered` frame the
        # completed load flushes for the bubble the whole replay is.
        assert len(events) == self.EVENTS + 1
        assert events[0]["payload"]["text"] == "e0"
        assert events[-1]["type"] == "rendered"
        assert events[-2]["payload"]["text"] == "e%d" % (self.EVENTS - 1)
        assert second._overflowed is False
        assert second.session_id == sid

    def test_the_socket_that_asked_is_served_the_same_replay(self, acp_store):
        """Positive control: parking the other socket must not cost this one
        the answer it is waiting for."""
        acp_mod, store = acp_store
        sid = "load-mid-0002"
        _stored_session(store, sid)
        loader = _acp_conn(acp_mod)
        second = _acp_conn(acp_mod)
        with patch.object(
                acp_mod._Supervisor, "_request",
                self._replay_with(
                    sid, 4, lambda: acp_mod._handle_subscribe(second, sid))), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(loader, sid))
        frames = _queued(loader)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        # Four replayed chunks and the rendering of the bubble they built.
        assert len(frames[2]["payload"]["events"]) == 5

    def test_nothing_is_attached_while_the_load_runs(self, acp_store):
        """The invariant the ``_registry.loading`` key carries, asserted where
        it holds rather than only through its consequences."""
        acp_mod, store = acp_store
        sid = "load-mid-0003"
        _stored_session(store, sid)
        seen = []
        second = _acp_conn(acp_mod)

        def halfway():
            acp_mod._handle_subscribe(second, sid)
            seen.append(set(acp_mod._registry.subscribers.get(sid, ())))

        with patch.object(acp_mod._Supervisor, "_request",
                          self._replay_with(sid, 4, halfway)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(_acp_conn(acp_mod), sid))
        assert seen == [set()]


class TestAcpConcurrentLoad:
    """The second ``load`` for one session used to be refused
    ``AgentRejected("That session is already loaded.")`` — relabelled
    ``session_in_use`` with "exit that one first", against a page whose
    ``loadTried`` guard then stopped it retrying on that socket. Nothing
    subscribed it to the buffer the refusal's own comment called the better
    answer.
    """

    async def _drive(self, acp_mod, sid, winner, loser):
        first = asyncio.ensure_future(acp_mod._handle_load(winner, sid))
        for _ in range(100):
            if sid in acp_mod._registry.loading:
                break
            await asyncio.sleep(0)
        assert sid in acp_mod._registry.loading, "the load never claimed it"
        await acp_mod._handle_load(loser, sid)
        await first

    def test_the_second_load_is_served_the_first_ones_replay(self, acp_store):
        acp_mod, store = acp_store
        sid = "load-race-001"
        _stored_session(store, sid)
        winner, loser = _acp_conn(acp_mod), _acp_conn(acp_mod)

        async def fake_request(self, method, params, timeout=None):
            self._on_notification({
                "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello"}}},
            })
            return {}

        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(self._drive(acp_mod, sid, winner, loser))

        frames = _queued(loser)
        assert [f["type"] for f in frames] == ["meta", "session", "history"]
        assert frames[2]["payload"]["events"][0]["payload"]["text"] == "hello"
        assert loser.session_id == sid

    def test_only_one_round_trip_is_spent(self, acp_store):
        """A second agent-side replay would append the whole conversation to
        itself, which is why the loser waits rather than asking again."""
        acp_mod, store = acp_store
        sid = "load-race-002"
        _stored_session(store, sid)
        calls = []

        async def fake_request(self, method, params, timeout=None):
            calls.append(method)
            return {}

        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(self._drive(acp_mod, sid, _acp_conn(acp_mod),
                                    _acp_conn(acp_mod)))
        assert calls == ["session/load"]

    def test_a_failure_reaches_everyone_who_waited(self, acp_store):
        """A waiter left holding a pending label is the failure mode parking
        them introduces, so the failure path has to reach them too."""
        acp_mod, store = acp_store
        sid = "load-race-003"
        _stored_session(store, sid)
        loser = _acp_conn(acp_mod)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(self._drive(acp_mod, sid, _acp_conn(acp_mod), loser))
        frames = _queued(loser)
        assert [f["type"] for f in frames] == ["meta", "error"]
        assert frames[1]["payload"]["code"] == "agent_timeout"


class TestAcpLoadSlotAccounting:
    """``new_session`` records only after its round-trip, so its reservation and
    its record never overlap. ``load_session`` must record first, and holding
    the reservation as well counted the loading session twice: reproduced with
    one pre-existing session, where a concurrent ``new`` was refused
    ``too_many_sessions`` while only two existed.

    The cap is pinned to 3 inside these tests rather than read from the module.
    It was 3 when they were written and is 8 now, and it is configurable from
    this release on — so the arithmetic they check ("one pre-existing session
    plus one loading plus one new fills exactly three slots") has to name its
    own number instead of tracking a default that moves.
    """

    def test_an_in_flight_load_takes_one_slot_not_two(self, acp_store,
                                                      monkeypatch):
        acp_mod, store = acp_store
        monkeypatch.setattr(acp_mod, "MAX_SESSIONS", 3)
        sid = "load-slot-001"
        _stored_session(store, sid)
        acp_mod._supervisor.sessions["already-live"] = {"cwd": ""}
        acp_mod._supervisor.history["already-live"] = acp_mod._History()
        created = []

        async def fake_request(self, method, params, timeout=None):
            if method == "session/load":
                # A `new` landing while the load is outstanding. It is the third
                # slot of three and must not be refused.
                created.append(await self.new_session(str(store)))
                return {}
            return {"sessionId": "brand-new-01"}

        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request", fake_request), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))

        assert [f["type"] for f in _queued(conn)] == [
            "meta", "session", "history"]
        assert created == [{"sessionId": "brand-new-01", "cwd": str(store)}]
        assert acp_mod._supervisor._reserved == 0
        assert len(acp_mod._supervisor.sessions) == acp_mod.MAX_SESSIONS

    def test_a_failed_load_releases_its_slot(self, acp_store):
        """Positive control: the release moved, it did not disappear."""
        acp_mod, store = acp_store
        sid = "load-slot-002"
        _stored_session(store, sid)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(_acp_conn(acp_mod), sid))
        assert acp_mod._supervisor._reserved == 0
        assert acp_mod._supervisor.sessions == {}


class TestAcpLockReadIsBoundedAndOffTheLoop:
    """The lock directory is written by the agent running trust-all-tools, so a
    lock file's size is not ours to assume, and ``MemoryError`` is in no caught
    set on this path. One line away, ``_stored_session_cwd`` already reads a
    bounded prefix through ``asyncio.to_thread``.
    """

    def _padded(self, pad):
        return " " * pad + json.dumps({
            "pid": os.getpid(),
            "started_at": _lock_time(dt.datetime.now(dt.timezone.utc))})

    def test_a_lock_is_not_read_past_the_cap(self, acp_store):
        """The pid here sits beyond LOCK_MAX_BYTES, so a bounded read cannot
        see it and an unbounded one can."""
        acp_mod, store = acp_store
        (store / "huge.lock").write_text(self._padded(acp_mod.LOCK_MAX_BYTES))
        assert acp_mod._lock_holder("huge") is None

    def test_the_same_lock_inside_the_cap_still_names_its_holder(self, acp_store):
        """Positive control: the cap refused above, not the padding itself."""
        acp_mod, store = acp_store
        (store / "small.lock").write_text(self._padded(16))
        assert acp_mod._lock_holder("small") == os.getpid()

    def test_the_lock_is_read_off_the_event_loop(self, acp_store):
        """Twice per load — the pre-flight and the re-read after a failure —
        each a file read plus a psutil query."""
        acp_mod, store = acp_store
        sid = "load-thread-1"
        _stored_session(store, sid)
        threads = []

        def spy(session_id):
            threads.append(threading.current_thread().ident)
            return None

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod, "_lock_holder", spy), \
                patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(_acp_conn(acp_mod), sid))
        assert len(threads) == 2
        assert threading.main_thread().ident not in threads


class TestAcpLoadFailureAttribution:
    """``_load_failure`` preferred the re-read lock over the actual exception,
    unconditionally and for every ``AcpError`` subclass. A load that timed out
    on our side was reported as "Session is active in another process (PID n) …
    exit that one first" — and the pid it named was PowerAtlas's own agent,
    because ``session/load`` makes the agent write that lock itself.
    """

    def _one_shot(self, holder):
        """A pre-flight that sees nothing and a re-read that sees ``holder``."""
        seen = []

        def _lock_holder(session_id):
            seen.append(session_id)
            return None if len(seen) == 1 else holder
        return _lock_holder

    def _refuse_with(self, exc_factory):
        async def refused(self, method, params, timeout=None):
            raise exc_factory()
        return refused

    def test_a_lock_naming_our_own_agent_is_not_a_holder(
            self, acp_store, monkeypatch):
        """Confirmed against the real store: ``73a40df3….lock`` was written at
        14:10 naming pid 21452, while that session's ``.json`` was created at
        10:36 — so the lock came from a load, not from a creation. With the load
        then failing on our side, every retry was refused at the pre-flight for
        the life of the agent process, naming a process the operator cannot
        exit because it is ours."""
        acp_mod, store = acp_store

        class _OurAgent:
            pid = os.getpid()

        (store / "ours.lock").write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": _lock_time(dt.datetime.now(dt.timezone.utc))}))
        assert acp_mod._lock_holder("ours") == os.getpid()
        monkeypatch.setattr(acp_mod._supervisor, "_proc", _OurAgent())
        assert acp_mod._lock_holder("ours") is None

    @pytest.mark.parametrize("factory, code, fragment", [
        (lambda: __import__("power_atlas.acp", fromlist=["x"]).AgentTimeout(
            "The agent did not answer 'session/load' within 90s."),
         "agent_timeout", "within 90s"),
        (lambda: __import__("power_atlas.acp", fromlist=["x"]).AgentDied(
            "The agent stopped answering; its channel closed."),
         "agent_died", "channel closed"),
    ])
    def test_a_local_failure_keeps_its_own_code(
            self, acp_store, factory, code, fragment):
        """A timeout or a dead agent says nothing about who holds the session.
        Relabelling them ``session_in_use`` hid the failure that did happen
        behind advice about a process that was not there."""
        acp_mod, store = acp_store
        sid = "load-attr-001"
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", self._one_shot(4242)), \
                patch.object(acp_mod._Supervisor, "_request",
                             self._refuse_with(factory)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        payload = _queued(conn)[1]["payload"]
        assert payload["code"] == code
        assert fragment in payload["message"]
        assert "4242" not in payload["message"]

    def test_the_session_cap_is_not_reported_as_an_occupied_session(
            self, acp_store):
        acp_mod, store = acp_store
        sid = "load-attr-002"
        _stored_session(store, sid)
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        conn = _acp_conn(acp_mod)
        reads = []

        def counting_lock_holder(session_id):
            reads.append(session_id)
            return 4242

        with patch.object(acp_mod, "_lock_holder", counting_lock_holder), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        payload = _queued(conn)[0]["payload"]
        assert payload["code"] == "too_many_sessions"
        assert "4242" not in payload["message"]
        # And the lock is never read at all now: the cap is consulted before the
        # two thread hops a refused load used to pay for anyway.
        assert reads == []

    def test_a_named_holder_still_wins_over_an_agent_refusal(self, acp_store):
        """Positive control: the re-read is still what turns the one cause an
        operator can act on into a sentence — it is only its precedence over
        unrelated failures that was wrong."""
        acp_mod, store = acp_store
        sid = "load-attr-003"
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod, "_lock_holder", self._one_shot(4242)), \
                patch.object(
                    acp_mod._Supervisor, "_request",
                    self._refuse_with(
                        lambda: acp_mod.AgentRejected(
                            "Internal error (code -32603)"))), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        payload = _queued(conn)[1]["payload"]
        assert payload["code"] == "session_in_use"
        assert "4242" in payload["message"]


class TestAcpUnattributedRefusal:
    """``_lock_holder`` returns ``None`` on eight conditions — psutil absent,
    the lock missing, unreadable, not a JSON object, no pid, an unparseable
    timestamp, a psutil error, the skew guard — and on kiro-cli 2.14.2 the
    agent contributes only a bare ``-32603 "Internal error"``. Every one of
    those eight therefore landed on a message saying neither what happened nor
    what to do, and a kiro-cli that changed its lock format would silently
    revert every in-use refusal to it.
    """

    def _refused(self, acp_mod, message):
        async def refused(self, method, params, timeout=None):
            raise acp_mod.AgentRejected(message)
        return refused

    def _load(self, acp_mod, store, sid, message):
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "_request",
                          self._refused(acp_mod, message)), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        return _queued(conn)[1]["payload"]

    def test_an_opaque_refusal_still_says_what_is_known(self, acp_store):
        acp_mod, store = acp_store
        payload = self._load(acp_mod, store, "load-opaque-1",
                             "Internal error (code -32603)")
        assert payload["code"] == "session_in_use"
        message = payload["message"]
        assert "-32603" in message
        # No pid is claimed, because none could be established.
        assert "PID" not in message
        # Both remedies, one of which is the only thing that frees a session.
        assert "kiro-cli" in message
        assert "restart PowerAtlas" in message

    def test_a_refusal_that_names_a_cause_is_passed_through(self, acp_store):
        """Positive control: only the code that names nothing is rewritten."""
        acp_mod, store = acp_store
        payload = self._load(acp_mod, store, "load-opaque-2",
                             "No such session (code -32602)")
        assert payload["code"] == "agent_error"
        assert payload["message"] == "No such session (code -32602)"

    def test_the_refusal_names_its_session_and_what_the_page_was_told(
            self, acp_store, caplog):
        """Both failed-load log lines omitted the session id, alone among this
        module's refusals, and one paired the *substituted* code with the
        *original* message — observed verbatim as ``[session_in_use] The agent
        did not answer 'session/load' within 90s``."""
        acp_mod, store = acp_store
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            self._load(acp_mod, store, "load-opaque-3",
                       "Internal error (code -32603)")
        line = [r.getMessage() for r in caplog.records
                if "session/load refused" in r.getMessage()]
        assert len(line) == 1
        assert "[session_in_use]" in line[0]
        assert "session=load-opaque-3" in line[0]
        assert "-32603" in line[0]
        # The agent's own words are kept, but as the agent's, not as ours.
        assert "(agent: Internal error (code -32603))" in line[0]


class TestAcpSessionCapMessage:
    """This message has been wrong in both directions. It said "close one
    first" while ``close`` answered ``not_implemented``; Phase 5 corrected it
    to name a PowerAtlas restart; Phase 6 built the close control and made the
    correction wrong the other way. Naming a remedy is not enough — the remedy
    has to be one the running build actually offers, and the cap is reachable
    by *browsing* three sessions from the dashboard.
    """

    def _fill(self, acp_mod):
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}

    def _only_error(self, acp_mod, conn):
        """The refusal, and the assertion that it is the *whole* answer.

        Both cap paths used to send their "creating…" / "loading…" pending
        frame first and refuse afterwards, so the page briefly claimed to be
        doing work it had already decided against. The cap is now read before
        either frame, which makes the frame list itself part of the contract.
        """
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"], frames
        return frames[0]["payload"]

    def _assert_names_the_close_control(self, acp_mod, payload):
        assert payload["code"] == "too_many_sessions"
        text = payload["message"].lower()
        assert "close" in text
        # The figure this message quotes, pinned to the measurement rather than
        # to whatever it said last. Re-measured in the plan's final QA against
        # kiro-cli 2.16.0 at the cap that now ships: eight sessions cost 24
        # processes and 1288.6 MiB above a 5-process / 531.6 MiB baseline —
        # 3.0 processes and 161.1 MiB marginal per session. The ~178 MB it
        # replaces was an earlier eight-session run; the ~254 MB before that
        # was a two-session reading of 2.14.x.
        assert "161 mb" in text, payload["message"]
        assert "178" not in text, payload["message"]
        assert "254" not in text, payload["message"]
        assert "306" not in text, payload["message"]
        # The message must not instruct an arrangement that exhausts the socket
        # budget. "Close one from its tab" presumed one tab per session, which
        # was true at MAX_SESSIONS = 3 and false once the rail let one socket
        # drive many sessions: eight sessions in eight tabs is eight sockets,
        # and the ninth is refused by MAX_CONNECTIONS with close code 1013 — so
        # the page that would explain *this* cap cannot connect. The remedy has
        # to be reachable from the tab the reader already has.
        assert "from its tab" not in text, payload["message"]
        assert "session list" in text, payload["message"]
        # The two claims this message has actually shipped falsely, pinned by
        # their own words. A generic "mentions closing" assertion passes on
        # "nothing closes a session yet", which is the wording being retired.
        assert "nothing closes a session" not in text
        assert "only way to free one" not in text
        # The cheaper lever is named first: a restart releases every session,
        # including the two the operator was not trying to free.
        assert text.index("close") < text.index("restart")
        # Tied to the control actually being routed. `close` answering
        # `not_implemented` is exactly the state this message described
        # falsely for two phases, and no other test reads the message's truth.
        assert "close" in acp_mod.CLIENT_TYPES
        conn = _acp_conn(acp_mod)

        async def dispatch():
            acp_mod._dispatch(conn, {"type": "close", "sessionId": "nope",
                                     "payload": {}})
            await asyncio.gather(*acp_mod._tasks)

        asyncio.run(dispatch())
        codes = [f["payload"].get("code") for f in _queued(conn)]
        assert "not_implemented" not in codes, codes

    def test_the_new_path_names_a_remedy_that_exists(self, acp_store, tmp_path):
        acp_mod, _ = acp_store
        self._fill(acp_mod)
        conn = _acp_conn(acp_mod)
        asyncio.run(acp_mod._handle_new(conn, {"cwd": str(tmp_path)}))
        self._assert_names_the_close_control(
            acp_mod, self._only_error(acp_mod, conn))

    def test_the_load_path_names_a_remedy_that_exists(self, acp_store):
        acp_mod, store = acp_store
        _stored_session(store, "load-cap-0001")
        self._fill(acp_mod)
        conn = _acp_conn(acp_mod)
        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, "load-cap-0001"))
        self._assert_names_the_close_control(
            acp_mod, self._only_error(acp_mod, conn))

    def test_the_socket_cap_is_documented_for_the_world_the_rail_made(self):
        """``MAX_CONNECTIONS``'s comment and the cap message have to describe
        the same model, and for two phases they did not.

        The comment ("one tab in practice, two while comparing") was written
        when ``MAX_SESSIONS`` was 3 and a session effectively meant a tab. The
        rail made one socket drive many sessions, so one tab became the normal
        case regardless of how many sessions are live — and the message that
        still said "close one from its tab" was describing an arrangement that
        exhausts this very cap. The number is not what went stale; the reading
        of it was. Pinned here because a comment is the only thing that carries
        why a constant is the value it is, and this one has now been read
        wrongly once.
        """
        import inspect

        from power_atlas import acp as acp_mod

        src = inspect.getsource(acp_mod)
        comment = src.split("MAX_CONNECTIONS = ")[0].rsplit("\n\n", 1)[-1]
        assert comment.lstrip().startswith("#"), (
            "the block before MAX_CONNECTIONS is not a comment, so this test "
            "is reading the wrong thing")
        assert "rail" in comment.lower(), (
            "the socket cap's comment does not mention the rail, so it is "
            "still explaining a one-tab-per-session world")
        # The retired reading may still appear — quoting it in order to retract
        # it is more useful to the next reader than deleting it, and is what
        # this comment now does. What must not survive is the phrase standing
        # as the claim, which is the state that shipped.
        retired = "one tab in practice, two while comparing"
        if retired in comment:
            assert f'"{retired}"' in comment, (
                "the retired reading appears unquoted, i.e. as the claim this "
                "comment is still making")
            assert "no longer exists" in comment, (
                "the retired reading is quoted with nothing retracting it, so "
                "a reader gets it as the current one")
        # And the two must not be silently coupled: tying the socket cap to the
        # session cap would re-assert exactly the model the rail removed.
        assert acp_mod.MAX_CONNECTIONS == 8
        acp_mod_sessions = acp_mod.MAX_SESSIONS
        try:
            acp_mod.MAX_SESSIONS = 3
            assert acp_mod.MAX_CONNECTIONS == 8, (
                "the socket cap moved with the session cap; they answer "
                "different questions and are independent by design")
        finally:
            acp_mod.MAX_SESSIONS = acp_mod_sessions


class TestAcpLoadPageRecovery:
    """After a failed load nothing on the page was pressable. ``Send`` is
    disabled, and ``Reconnect``/``Reload page`` are unhidden only from
    socket-close handlers that do not fire, because the socket is healthy — it
    is the session that is not. Enter was not disabled either: ``sendPrompt``
    gated only on ``sessionId``, so it reached the server and came back "It may
    belong to an earlier PowerAtlas process — create a new one", which is the
    one thing the user was avoiding by opening an existing session.
    """

    def _page(self) -> str:
        from power_atlas.web import templates
        return templates.env.loader.get_source(templates.env, "acp.html")[0]

    def test_enter_is_refused_before_the_wrong_advice_is_earned(self):
        src = self._page()
        body = src.split("function sendPrompt()", 1)[1].split("\n  function ", 1)[0]
        assert "if (loadFailed)" in body
        # Ahead of the `sessionId` gate, which is the one that used to let it
        # through: after a failed load `sessionId` is still set from ?sid=.
        assert body.index("if (loadFailed)") < body.index("if (!sessionId)")

    def test_a_failed_load_states_its_recovery_and_offers_a_control(self):
        src = self._page()
        body = src.split("function reportLoadFailure()", 1)[1].split(
            "\n  function ", 1)[0]
        assert "loadFailed = true" in body
        assert "reloadBtn.hidden = false" in body
        # In the transcript, not only the 120 px log strip.
        assert "addMessage('note'" in body
        assert "Reload the page" in body

    def test_the_error_handler_reaches_it_only_for_a_load(self):
        src = self._page()
        assert "if (failedLoad) reportLoadFailure();" in src
        assert "var failedLoad = loadingSid !== null" in src

    def test_a_session_frame_clears_the_flag_again(self):
        """A load that succeeds on a retry must not leave the page refusing to
        send to a session it is now subscribed to."""
        src = self._page()
        # Scoped to the main `handle(frame)` function specifically: `handleSub`
        # (the read-only sub-agent panel's own, much smaller dispatcher) has its
        # own `if (type === 'session') {` earlier in the file, and a plain
        # first-match split would find that one instead.
        handler = src.split("function handle(frame) {", 1)[1]
        session_branch = handler.split("if (type === 'session') {", 1)[1]
        assert "loadFailed = false;" in session_branch.split("return;", 1)[0]

    def test_the_send_button_is_disabled_by_the_flag(self):
        src = self._page()
        assert "sendBtn.disabled = active || !sessionId || loadFailed;" in src


class TestAcpLoadStatesItsCeiling:
    """A slow load and a wedged one look identical until the ceiling expires:
    the agent says nothing at all before the whole conversation, so an
    unchanging pill and a blank transcript is the entire signal for up to
    REQUEST_TIMEOUT_SECONDS — 90 seconds. The sibling ``new`` path sets an
    expectation ("the agent takes several seconds"); this one did not.
    """

    def test_the_pending_frame_carries_the_ceiling(self):
        from power_atlas import acp as acp_mod
        frame = acp_mod._load_pending_frame("s1")
        assert frame["type"] == "meta"
        assert frame["sessionId"] == "s1"
        assert frame["payload"] == {
            "pending": "load",
            "timeoutSeconds": acp_mod.REQUEST_TIMEOUT_SECONDS}

    def test_the_page_renders_the_ceiling_rather_than_hardcoding_one(self):
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        branch = src.split("payload.pending === 'load'", 1)[1].split(
            "return;", 1)[0]
        assert "payload.timeoutSeconds" in branch
        # In the transcript as well as the log strip: a blank transcript is
        # exactly what makes a wedged load unreadable.
        assert "addMessage('note'" in branch


class TestAcpDashboardRowAction:
    """The control has to live inside ``.session-actions``: that container is
    what the row's own ``onclick`` excludes, so anywhere else a click on it
    toggles multi-select instead of opening the session. The same collision is
    recorded at ``CLOSED_INVESTIGATIONS.md:90``.
    """

    def _row(self) -> str:
        from power_atlas.web import templates

        class _Session:
            session_id = "001b4195-ee19-4633-b1b2-488574cad044"
            title = "t"
            updated_at = "2026-07-26T10:00:00"
            first_prompt = ""
            last_reply_tail = ""

        return templates.get_template("partials/session_row.html").render(
            session=_Session(), cwd=r"C:\scratch", pinned_sessions=[],
            provider_name="kiro-cli", provider_color="", stale=False,
            status="closed", waiting_detail=("", ""))

    def test_the_action_sits_inside_the_excluded_container(self):
        html = self._row()
        actions = html.split('<div class="session-actions">', 1)
        assert len(actions) == 2, "the row lost its .session-actions block"
        assert "openInAcp(this)" in actions[1].split("</div>", 1)[0]

    def test_the_action_opens_acp_for_this_row(self):
        from power_atlas.web import templates
        index = templates.env.loader.get_source(templates.env, "index.html")[0]
        assert "function openInAcp(btn)" in index
        assert "'/acp?sid='+encodeURIComponent(row.dataset.sid)" in index

    def test_a_non_kiro_provider_gets_no_acp_action(self):
        """`/acp` speaks ACP to kiro-cli; no other provider has a session it
        could load, so offering the control would only ever fail."""
        from power_atlas.web import templates

        class _Session:
            session_id = "abc"
            title = "t"
            updated_at = ""
            first_prompt = ""
            last_reply_tail = ""

        html = templates.get_template("partials/session_row.html").render(
            session=_Session(), cwd="", pinned_sessions=[],
            provider_name="claude-code", provider_color="", stale=False,
            status="closed", waiting_detail=("", ""))
        assert "openInAcp" not in html


# --- ACP phase 6: cancel, close, and context-window telemetry ---


def _sent(acp_mod, method_calls):
    """A ``_write`` stand-in that records the JSON-RPC objects built for it.

    Stubbed at ``_write`` rather than at ``_request``/``_notify``: the shape
    under test is the *line* the supervisor puts on the pipe, and whether it
    carries an ``id`` is exactly what separates a request from a notification.
    """
    def write(self, obj):
        method_calls.append(obj)
        loop = self._loop
        # Answer our own request from the reader's side, the way the agent
        # would, so the awaiting future completes.
        if "id" in obj and loop is not None:
            loop.call_soon_threadsafe(
                self._on_response, {"id": obj["id"], "result": {}})
    return write


def _run_bound(acp_mod, factory):
    """Run a coroutine with the supervisor bound to the running loop.

    ``_request`` refuses outright when ``_loop`` is unset, so a test that only
    stubs ``_write`` never reaches the request it is checking. Binding here is
    what the real ``ensure_started`` does, and it lets ``_on_response`` match
    the future the same way the reader thread does.
    """
    async def run():
        acp_mod._supervisor._loop = asyncio.get_running_loop()
        try:
            return await factory()
        finally:
            acp_mod._supervisor._loop = None
            acp_mod._supervisor._pending.clear()
    return asyncio.run(run())


class TestAcpCancel:
    """A turn under ``-a`` can run tools for minutes, and until now nothing
    could end one. ``session/cancel`` is a **notification** on kiro-cli 2.14.2 —
    measured: a request would have parked the Stop button on a future the agent
    never answers.
    """

    def _conn(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def test_cancel_is_a_notification_naming_the_session(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert written == [{"jsonrpc": "2.0", "method": "session/cancel",
                            "params": {"sessionId": sid}}]
        # No id: a notification cannot be answered, and awaiting an answer
        # would hold the page for REQUEST_TIMEOUT_SECONDS after a cancellation
        # that has already happened.
        assert "id" not in written[0]

    def test_cancel_emits_no_turn_boundary_of_its_own(self, acp_session):
        """The outstanding ``session/prompt`` returns ``cancelled`` and the task
        awaiting it emits the boundary. A second one here would leave the
        transcript with two ends to one turn."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert _queued(conn) == []
        assert acp_mod._supervisor.history[sid].events() == []
        # And the session survives its own cancellation.
        assert sid in acp_mod._supervisor.sessions

    def test_cancel_without_a_turn_reaches_the_agent_not_at_all(
            self, acp_session, caplog):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        written = []
        with caplog.at_level(logging.INFO, logger="power_atlas.acp"), \
                patch.object(acp_mod._Supervisor, "_write",
                             _sent(acp_mod, written)):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert written == []
        assert "not running a turn" in caplog.text

    def test_cancel_without_a_session_id_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)
        asyncio.run(acp_mod._handle_cancel(conn, None))
        assert _queued(conn)[0]["payload"]["code"] == "bad_envelope"

    def test_a_socket_watching_another_session_cannot_cancel_this_turn(
            self, acp_session):
        """``prompt`` has required the socket to be subscribed to the session it
        names since Phase 4. ``cancel`` did not, so one tab could end a turn it
        cannot see — and the boundary that turn emits goes to the session's
        watchers, which do not include the socket that asked."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.sessions["other"] = {"cwd": ""}
        acp_mod._supervisor.history["other"] = acp_mod._History()
        conn = self._conn(acp_mod, "other")
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        written = []
        try:
            with patch.object(acp_mod._Supervisor, "_write",
                              _sent(acp_mod, written)), \
                    patch.object(acp_mod._Supervisor, "alive", lambda self: True):
                asyncio.run(acp_mod._handle_cancel(conn, sid))
            assert written == []
            assert [f["payload"]["code"] for f in _queued(conn)] == ["not_subscribed"]
            assert sid in acp_mod._supervisor.inflight
        finally:
            acp_mod._supervisor.sessions.pop("other", None)
            acp_mod._supervisor.history.pop("other", None)

    def test_an_agent_failure_reaches_the_page_typed(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)

        def boom(self, obj):
            raise acp_mod.AgentDied("the agent is not running")

        with patch.object(acp_mod._Supervisor, "_write", boom), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        assert _queued(conn)[0]["payload"]["code"] == "agent_died"

    def test_the_page_shows_the_cancellation_and_reopens_send(self):
        """`stopReason: "cancelled"` is not `end_turn`, so the turn-end branch
        writes it into the transcript — the log strip is 120 px tall and is not
        replayed, so a cancellation reaching only it disappears on reload."""
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        branch = src.split("payload.turn === 'end'", 1)[1].split("return;", 1)[0]
        assert "addMessage('note', 'turn ended: ' + payload.stopReason)" in branch
        assert "setTurn(false)" in branch
        stop = src.split("stopBtn.addEventListener", 1)[1].split("});", 1)[0]
        assert "send('cancel'" in stop


class TestAcpSessionClose:
    """The lever the whole memory budget rests on: §4 and §6 accept ~161 MB a
    session on the strength of a close control existing.

    Re-measured in Phase 2 on kiro-cli 2.16.0 — closing one session released 3
    processes and 169.7 MB, and removed the session's ``.lock``.
    ``session/close`` is **not** the method that does it: the agent answers
    that one ``-32601 Method not found``.
    """

    def _conn(self, acp_mod, sid=None):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        if sid:
            acp_mod._registry.attach(conn, sid)
        return conn

    def test_close_asks_the_agent_before_dropping_anything(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
        assert [(o["method"], o["params"]) for o in written] == [
            (acp_mod.CLOSE_METHOD, {"sessionId": sid})]
        assert acp_mod.CLOSE_METHOD == "_kiro.dev/session/terminate"
        assert sid not in acp_mod._supervisor.sessions

    def test_the_ring_buffer_goes_with_the_session(self, acp_session):
        """Keyed by session id and reachable from nowhere else, so a buffer left
        behind is up to HISTORY_MAX_BYTES resident for the app's lifetime."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        for i in range(50):
            acp_mod._emit(sid, acp_mod.envelope(
                "chunk", {"role": "agent", "text": "e%d" % i}, sid))
        assert len(acp_mod._supervisor.history[sid]) == 50
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
        assert sid not in acp_mod._supervisor.history

    def test_a_failed_close_keeps_the_session(self, acp_session):
        """A kiro-cli without the extension method answers -32601. Dropping our
        own record then would report a memory saving that did not happen and
        leave ~3 processes unreachable for the agent's whole life."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)

        async def refuse(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("Method not found (code -32601)")

        with patch.object(acp_mod._Supervisor, "_request", refuse), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_close(conn, sid))
        assert sid in acp_mod._supervisor.sessions
        assert sid in acp_mod._supervisor.history
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["error"]
        assert frames[0]["payload"]["code"] == "agent_error"
        assert sid not in acp_mod._supervisor.closing

    def test_every_watching_socket_is_told_and_detached(self, acp_session):
        """A second tab is holding a transcript that no longer has a session
        behind it, and a subscriber entry for a session that is gone would
        outlive every frame that could clear it."""
        acp_mod, sid = acp_session
        a = self._conn(acp_mod, sid)
        b = self._conn(acp_mod, sid)
        _queued(a), _queued(b)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(a, sid))
        for conn in (a, b):
            frames = _queued(conn)
            assert [f["type"] for f in frames] == ["session_closed"]
            assert frames[0]["payload"]["sessionId"] == sid
            assert conn.session_id is None
        assert sid not in acp_mod._registry.subscribers

    def test_a_socket_watching_another_session_cannot_close_this_one(
            self, acp_session):
        """``prompt`` has required the socket to be subscribed to the session it
        names since Phase 4; ``close`` and ``cancel`` did not. Same trust
        boundary, so nothing crosses a privilege line — but it made the
        prompt-during-close race an ordinary two-tab interaction rather than a
        socket racing itself, and it let one tab release what another is
        holding."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.sessions["other"] = {"cwd": ""}
        acp_mod._supervisor.history["other"] = acp_mod._History()
        conn = self._conn(acp_mod, "other")
        _queued(conn)
        written = []
        try:
            with patch.object(acp_mod._Supervisor, "_write",
                              _sent(acp_mod, written)), \
                    patch.object(acp_mod._Supervisor, "alive", lambda self: True):
                _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
            assert [f["payload"]["code"] for f in _queued(conn)] == ["not_subscribed"]
            # Nothing reached the agent, the session survives, and the socket
            # keeps the session it *is* watching — taking that away would leave
            # the tab it belongs to receiving nothing.
            assert written == []
            assert sid in acp_mod._supervisor.sessions
            assert conn.session_id == "other"
        finally:
            acp_mod._supervisor.sessions.pop("other", None)
            acp_mod._supervisor.history.pop("other", None)

    def test_a_subscribed_socket_still_closes(self, acp_session):
        """Positive control for the guard above: the page's own socket is
        attached to the session its Close button names."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
        assert [f["type"] for f in _queued(conn)] == ["session_closed"]
        assert sid not in acp_mod._supervisor.sessions

    def test_close_during_a_turn_is_refused(self, acp_session):
        """The outstanding `session/prompt` would sit on a session the agent no
        longer has until the inactivity ceiling gives up on it — up to
        PROMPT_SILENCE_SECONDS plus a tick, and up to
        PROMPT_ABSOLUTE_MAX_SECONDS if the agent keeps talking."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)):
            asyncio.run(acp_mod._handle_close(conn, sid))
        assert written == []
        assert _queued(conn)[0]["payload"]["code"] == "turn_in_progress"
        assert sid in acp_mod._supervisor.sessions

    def test_close_during_a_load_is_refused(self, acp_session):
        """``_Registry.loading`` bars attachment for the whole of a
        ``session/load``; closing under one would strand the parked sockets."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._registry.loading[sid] = []
        _queued(conn)
        written = []
        try:
            with patch.object(acp_mod._Supervisor, "_write",
                              _sent(acp_mod, written)):
                asyncio.run(acp_mod._handle_close(conn, sid))
        finally:
            acp_mod._registry.loading.pop(sid, None)
        assert written == []
        assert _queued(conn)[0]["payload"]["code"] == "session_loading"
        assert sid in acp_mod._supervisor.sessions

    def test_a_session_this_server_does_not_hold_is_refused(self, acp_session):
        acp_mod, _sid = acp_session
        conn = self._conn(acp_mod, "no-such-session")
        _queued(conn)
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)):
            asyncio.run(acp_mod._handle_close(conn, "no-such-session"))
        # One frame and nothing on the wire: a refusal that reports and then
        # carries on reaches the agent anyway and answers the page twice, with
        # the second answer contradicting the first.
        assert [f["payload"]["code"] for f in _queued(conn)] == ["nothing_to_close"]
        assert written == []

    def test_a_refused_close_does_not_wear_the_adopt_me_code(self, acp_session):
        """``unknown_session`` means "this server does not hold it — try
        adopting it" everywhere else, and the page answers it by sending
        ``load``. Emitted from ``close`` it would have a refused Close spawn an
        agent and re-adopt the session, spending again the memory the button
        exists to free. Only WebSocket frame ordering prevented that."""
        from power_atlas.web import templates
        acp_mod, _sid = acp_session
        conn = self._conn(acp_mod, "no-such-session")
        _queued(conn)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])):
            asyncio.run(acp_mod._handle_close(conn, "no-such-session"))
        code = _queued(conn)[0]["payload"]["code"]
        assert code != "unknown_session"
        # Tied to the branch that would act on it, so this cannot pass by the
        # page having stopped auto-loading instead.
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        adopt = src.split("if (payload.code === 'unknown_session'", 1)[1]
        assert "send('load'" in adopt.split("return;", 1)[0]
        assert code not in adopt.split("return;", 1)[0]

    def test_a_second_close_cannot_overtake_the_first(self, acp_session):
        """Two `close` frames become two tasks. The second would be refused by
        an agent that no longer has the session, and reach the page as a failure
        to close something already closed."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        _queued(conn)
        started = asyncio.Event()

        async def slow(self, method, params, timeout=None):
            started.set()
            await asyncio.sleep(0.05)
            return {}

        async def both():
            with patch.object(acp_mod._Supervisor, "_request", slow), \
                    patch.object(acp_mod._Supervisor, "alive",
                                 lambda self: True):
                first = asyncio.ensure_future(acp_mod._handle_close(conn, sid))
                await started.wait()
                await acp_mod._handle_close(conn, sid)
                await first

        asyncio.run(both())
        codes = [f["payload"].get("code") for f in _queued(conn)]
        assert "close_in_progress" in codes

    def test_the_closed_session_frees_a_slot(self, acp_session):
        """Which is the entire point: the cap is the only thing between one
        socket and memory exhaustion at ~161 MB a session."""
        acp_mod, sid = acp_session
        for i in range(acp_mod.MAX_SESSIONS - 1):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        conn = self._conn(acp_mod, sid)
        try:
            assert (len(acp_mod._supervisor.sessions)
                    >= acp_mod.MAX_SESSIONS)
            with patch.object(acp_mod._Supervisor, "_write",
                              _sent(acp_mod, [])), \
                    patch.object(acp_mod._Supervisor, "alive",
                                 lambda self: True):
                _run_bound(acp_mod, lambda: acp_mod._handle_close(conn, sid))
            assert len(acp_mod._supervisor.sessions) < acp_mod.MAX_SESSIONS
        finally:
            for i in range(acp_mod.MAX_SESSIONS - 1):
                acp_mod._supervisor.sessions.pop("filler%d" % i, None)

    # `test_the_page_clears_the_session_and_the_url` used to sit here. It split
    # the template on `type === 'session_closed'` and asserted that three
    # statements appeared as literal source text inside that branch, which
    # pinned *where the code was written* rather than what it did — so the
    # duplication between that arm and the terminal `close_in_progress` one
    # could not be hoisted without breaking it, and twice was not.
    #
    # Retired once the property was covered by behaviour instead. The six
    # clears now live in `releaseSession()` and are driven end to end in
    # tests/acp_page.test.mjs, each verified by deleting it from the branch and
    # confirming the harness goes red — which this test could not have done,
    # since deleting a statement is exactly what it was watching for and
    # nothing else was.
    def test_close_is_off_while_a_turn_runs(self):
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        body = src.split("function setTurn(active)", 1)[1].split("\n  }", 1)[0]
        assert "closeBtn.disabled = active || !sessionId" in body


class TestAcpContextWindow:
    """``_kiro.dev/metadata`` is kiro-private and arrives with **no**
    ``sessionUpdate`` field, so it is matched on the JSON-RPC method name —
    the opposite rule from tool calls, where one method carries six update
    kinds and only ``update.sessionUpdate`` separates them.
    """

    def _attached(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def _notify(self, acp_mod, sid, percent):
        acp_mod._supervisor._on_notification({
            "method": acp_mod.METADATA_METHOD,
            "params": {"sessionId": sid,
                       acp_mod.CONTEXT_PERCENT_KEY: percent,
                       "totalTokens": 12345},
        })

    def test_the_percentage_reaches_the_page(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        self._notify(acp_mod, sid, 5.8399)
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["meta"]
        # Rounded on the wire, so the number sent and the number rendered are
        # the same one. The agent sends four decimals.
        assert frames[0]["payload"] == {"contextPercent": 5.8}

    def test_it_is_matched_on_the_method_not_the_update(self, acp_session):
        """Keying off ``update.sessionUpdate`` — the tool-call rule — would drop
        every one of these, because they carry no ``update`` at all."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        acp_mod._supervisor._on_notification({
            "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk",
                acp_mod.CONTEXT_PERCENT_KEY: 42.0,
                "content": {"type": "text", "text": "hi"}}},
        })
        assert [f["payload"].get("contextPercent") for f in _queued(conn)] == [None]

    def test_it_is_not_recorded_in_the_replay_buffer(self, acp_session):
        """A level, not an event: recording each would spend an eviction per
        turn on a number a later frame supersedes."""
        acp_mod, sid = acp_session
        self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, 7.0)
        assert acp_mod._supervisor.history[sid].events() == []

    def test_subscribe_carries_the_latest_reading(self, acp_session):
        """Which is why not recording it is safe: the buffer is built to evict,
        and this is the reconnecting socket's only other source."""
        acp_mod, sid = acp_session
        self._attached(acp_mod, sid)
        self._notify(acp_mod, sid, 11.25)
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["contextPercent"] == 11.2

    def test_a_session_with_no_turn_yet_reports_null(self, acp_session):
        """Rather than 0%, which would draw an empty bar claiming a measurement
        that has not been taken."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._handle_subscribe(conn, sid)
        assert _queued(conn)[0]["payload"]["contextPercent"] is None

    @pytest.mark.parametrize("value", [
        None, "5.8", True, False, -1, 101, float("nan"), float("inf"),
        {"percent": 5}])
    def test_a_value_that_is_not_a_percentage_is_dropped(self, acp_session,
                                                         value):
        """This number ends up as a CSS width. ``True`` is the one that looks
        harmless: it is an ``int`` in Python and would round-trip as 1%."""
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        self._notify(acp_mod, sid, value)
        assert _queued(conn) == []
        assert "contextPercent" not in acp_mod._supervisor.sessions[sid]

    def test_a_reading_for_an_unknown_session_is_dropped(self, acp_session):
        acp_mod, sid = acp_session
        conn = self._attached(acp_mod, sid)
        _queued(conn)
        self._notify(acp_mod, "someone-else", 9.0)
        assert _queued(conn) == []

    def test_the_page_narrows_the_value_again_before_the_style_sink(self):
        """The one attribute sink on a page whose whole defence is that nothing
        agent-derived reaches one."""
        from power_atlas.web import templates
        src = templates.env.loader.get_source(templates.env, "acp.html")[0]
        body = src.split("function setContext(percent)", 1)[1].split(
            "\n  }", 1)[0]
        assert "typeof percent !== 'number'" in body
        assert "isFinite(percent)" in body
        assert "percent < 0 || percent > 100" in body
        assert "contextFill.style.width = percent + '%'" in body


class TestAcpDeclaredTypesAreRouted:
    """``cancel`` and ``close`` answered ``not_implemented`` for three phases
    while the page had no control for either. Now that both are routed, the
    fallback is a server bug rather than a phase boundary — and a control the
    page draws while the server silently ignores it is the worst of the three
    states.
    """

    def test_no_client_type_falls_through_to_not_implemented(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)

        async def no_agent(self):
            # `new` and `load` reach `ensure_started`, which spawns a real
            # `kiro-cli acp` and writes a real session into the user's store —
            # this test drove ten of them there before this stub existed.
            # Refusing here keeps every branch on its typed-error path, which
            # is the only thing being asserted.
            raise acp_mod.AgentUnavailable("no agent under test")

        async def dispatch():
            for type_ in sorted(acp_mod.CLIENT_TYPES):
                acp_mod._dispatch(conn, {"type": type_, "sessionId": sid,
                                         "payload": {"prompt": "x"}})
            await asyncio.gather(*acp_mod._tasks)

        with patch.object(acp_mod._Supervisor, "ensure_started", no_agent), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: False):
            asyncio.run(dispatch())
        codes = [f["payload"].get("code") for f in _queued(conn)]
        assert "not_implemented" not in codes, codes


# --- ACP closing review: loop blocking, the close/prompt race, dead state ---


class TestAcpNewDoesNotBlockTheLoop:
    """``_resolve_session_cwd`` is two blocking filesystem calls —
    ``Path.resolve()`` and ``is_dir()`` — on a path the page's "session
    directory" box supplies. Measured against a UNC path to an unreachable host
    (``\\\\10.255.255.1\\share\\x``): **42.16 s in a single call**, on the event
    loop, during which uvicorn serves nothing at all — no dashboard, no status
    polling, no other ACP socket.

    The sibling ``load`` path has resolved its cwd through
    ``asyncio.to_thread`` since Phase 5, and ``_load_session_cwd``'s own
    docstring says "Blocking; call off the loop". One ``new`` frame is enough.
    """

    def _conn(self, acp_mod):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        return conn

    def test_the_loop_keeps_running_while_the_resolve_blocks(self, acp_store):
        """The property, not the call shape: another coroutine must keep being
        scheduled for the whole of a resolve that blocks."""
        acp_mod, store = acp_store
        conn = self._conn(acp_mod)
        block = 0.30
        ticks = []

        def slow_resolve(raw):
            # An unreachable UNC path, in miniature. The real one is two orders
            # of magnitude longer.
            time.sleep(block)
            return str(store)

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.005)

        async def agent_answers(self, method, params, timeout=None):
            return {"sessionId": "new-off-loop-01"}

        async def run():
            acp_mod._supervisor._loop = asyncio.get_running_loop()
            beat = asyncio.ensure_future(ticker())
            try:
                await acp_mod._handle_new(conn, {"cwd": r"\\10.255.255.1\share\x"})
            finally:
                beat.cancel()
                acp_mod._supervisor._loop = None

        with patch.object(acp_mod, "_resolve_session_cwd", slow_resolve), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", agent_answers):
            asyncio.run(run())

        assert "new-off-loop-01" in acp_mod._supervisor.sessions
        # A synchronous resolve parks the loop for the whole `block`, so the
        # ticker gets one iteration. Off the loop it gets ~60; ten is far below
        # that and far above what a stalled loop can produce.
        assert len(ticks) > 10, (
            f"the loop was served {len(ticks)} time(s) during a {block:.2f}s "
            "resolve — a `new` frame stalls the whole application")

    def test_the_resolve_runs_off_the_main_thread(self, acp_store):
        acp_mod, store = acp_store
        conn = self._conn(acp_mod)
        threads = []

        def spy(raw):
            threads.append(threading.current_thread().ident)
            return str(store)

        async def agent_answers(self, method, params, timeout=None):
            return {"sessionId": "new-off-loop-02"}

        with patch.object(acp_mod, "_resolve_session_cwd", spy), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", agent_answers):
            _run_bound(acp_mod, lambda: acp_mod._handle_new(conn, {"cwd": "x"}))
        assert threads
        assert threading.main_thread().ident not in threads

    def test_at_the_cap_nothing_is_resolved_at_all(self, acp_store, tmp_path):
        """MAX_SESSIONS did not bound this: the cap was first read inside
        ``new_session``, *after* the resolve, so the stall was payable
        indefinitely with no session ever created."""
        acp_mod, _store = acp_store
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["filler%d" % i] = {"cwd": ""}
        conn = self._conn(acp_mod)
        resolves = []

        def spy(raw):
            resolves.append(raw)
            return str(tmp_path)

        with patch.object(acp_mod, "_resolve_session_cwd", spy):
            asyncio.run(acp_mod._handle_new(conn, {"cwd": str(tmp_path)}))
        assert resolves == []
        assert [f["payload"]["code"] for f in _queued(conn)] == ["too_many_sessions"]


class TestAcpPromptDuringAnInFlightClose:
    """``_handle_close`` guards against ``loading``, an unknown session, a live
    turn and a concurrent ``close``. ``_handle_prompt`` checked ``sessions``,
    the subscription and ``inflight`` — and never ``closing``.

    Reproduced against a stubbed transport: with the close claim taken and the
    agent not yet answered, a prompt was accepted, so both
    ``_kiro.dev/session/terminate`` and ``session/prompt`` went on the wire.
    The composed consequence is the one the close path's own turn guard exists
    to prevent — the prompt future sits in ``_pending`` for the whole 600 s
    ceiling — plus ``close_session`` discarding the live turn's ``inflight``
    marker and the close dropping the ring buffer and detaching every watcher,
    so the surviving turn's chunks and tool calls reach neither the page nor the
    replay. Under ``-a`` that is ungated tools running with nothing watching.

    Reachable without adversarial intent: ``closeBtn`` disables itself on click
    but ``sendBtn`` and Enter do not, and two tabs on one session is a supported
    shape.
    """

    def test_a_prompt_arriving_during_a_close_is_refused(self, acp_session):
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        _queued(conn)
        methods = []
        claimed = asyncio.Event()
        release = asyncio.Event()

        async def request(self, method, params, timeout=None):
            methods.append(method)
            if method == acp_mod.CLOSE_METHOD:
                claimed.set()
                await release.wait()
            return {}

        async def both():
            closing = asyncio.ensure_future(acp_mod._handle_close(conn, sid))
            await claimed.wait()
            assert sid in acp_mod._supervisor.closing
            # The prompt would return immediately if it were accepted, so a
            # regression here fails rather than hanging.
            await acp_mod._handle_prompt(conn, sid, {"prompt": "one more thing"})
            release.set()
            await closing

        with patch.object(acp_mod._Supervisor, "_request", request), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, both)

        # The assertion that would have caught it: the close reached the agent
        # and the prompt did not.
        assert methods == [acp_mod.CLOSE_METHOD], methods
        codes = [f["payload"].get("code")
                 for f in _queued(conn) if f["type"] == "error"]
        assert codes == ["close_in_progress"], codes
        # And no turn was left behind on a session that no longer exists.
        assert sid not in acp_mod._supervisor.inflight
        assert sid not in acp_mod._supervisor.sessions
        assert acp_mod._supervisor._pending == {}

    def test_a_prompt_outside_the_close_window_still_runs(self, acp_session):
        """Positive control: the guard reads ``closing``, not "there was a
        close once"."""
        acp_mod, sid = acp_session
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        methods = []

        async def request(self, method, params, timeout=None):
            methods.append(method)
            return {"stopReason": "end_turn"}

        with patch.object(acp_mod._Supervisor, "_request", request), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod,
                       lambda: acp_mod._handle_prompt(conn, sid, {"prompt": "hi"}))
        assert methods == ["session/prompt"]


class TestAcpLoadFloor:
    """``subscribe`` has had a per-socket replay floor since Phase 4; ``load``
    costs strictly more and had none — two blocking calls on the shared thread
    pool the dashboard's own status polling uses, a registry claim, a pending
    frame and an agent round-trip, all for ~60 bytes of client frame.
    """

    def test_a_second_load_within_the_floor_is_refused(self, acp_store):
        acp_mod, store = acp_store
        _stored_session(store, "floor-0001")
        _stored_session(store, "floor-0002")
        conn = _acp_conn(acp_mod)
        reads = []

        def counting(session_id):
            reads.append(session_id)
            return None

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod, "_lock_holder", counting), \
                patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, "floor-0001"))
            first = len(reads)
            _queued(conn)
            asyncio.run(acp_mod._handle_load(conn, "floor-0002"))
        assert [f["payload"]["code"] for f in _queued(conn)] == ["load_throttled"]
        # Nothing was spent on the throttled frame: the floor is above the two
        # thread hops, not below them.
        assert len(reads) == first

    def test_the_floor_is_per_socket(self, acp_store):
        """A reload is a new socket and must not be throttled by the one it
        replaces — the same rule the replay floor follows."""
        acp_mod, store = acp_store
        _stored_session(store, "floor-0003")
        first, second = _acp_conn(acp_mod), _acp_conn(acp_mod)

        async def boom(self, method, params, timeout=None):
            raise acp_mod.AgentTimeout("the agent did not answer")

        with patch.object(acp_mod._Supervisor, "_request", boom), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(first, "floor-0003"))
            asyncio.run(acp_mod._handle_load(second, "floor-0003"))
        codes = [f["payload"].get("code")
                 for f in _queued(second) if f["type"] == "error"]
        assert "load_throttled" not in codes
        assert codes == ["agent_timeout"], codes

    def test_a_served_load_still_leaves_the_floor_armed(self, acp_store):
        """``_deliver_load`` clears ``replayed_at`` on purpose — the replay a
        load paid an agent round-trip for must not be throttled away — and
        clearing the load floor with it would remove the floor from the frame
        that costs the most."""
        acp_mod, store = acp_store
        sid = "floor-0004"
        _stored_session(store, sid)
        conn = _acp_conn(acp_mod)

        async def answers(self, method, params, timeout=None):
            return {}

        with patch.object(acp_mod._Supervisor, "_request", answers), \
                patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn):
            asyncio.run(acp_mod._handle_load(conn, sid))
        assert conn.replayed_at is not None
        assert conn.loaded_at is not None


class TestAcpSessionRecordHoldsNoDeadState:
    """``models`` and ``modes`` were written on every session record and read
    nowhere in ``src/``; ``_Supervisor.agent_info`` was stored, cleared on every
    teardown, and read only inline in the log call that set it. Both are
    agent-authored dicts of no bounded size, held for the session's whole life
    for nobody.
    """

    def test_a_created_session_is_published_before_the_call_returns(self, acp_store):
        """The create half of the D32 publish, driven through `new_session`.

        Ordering is the substance. `presence` scans on a worker thread at a
        cadence nothing here controls, so a session that exists but has not
        been published is a window in which its own freshly-written lock — pid
        equal to our agent's, forward delta — is indistinguishable from an
        orphan and gets suppressed. That is a *missing* live dot for a live
        session, which is the failure direction this whole mechanism was
        careful to avoid everywhere else.
        """
        acp_mod, store = acp_store
        published = []

        async def created(self, method, params, timeout=None):
            return {"sessionId": "published-0001"}

        previous = acp_mod.sessions_changed_hook
        try:
            acp_mod.sessions_changed_hook = lambda ids, pid: published.append(ids)
            with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                    patch.object(acp_mod._Supervisor, "_request", created):
                asyncio.run(acp_mod._supervisor.new_session(str(store)))
        finally:
            acp_mod.sessions_changed_hook = previous
            acp_mod._supervisor.sessions.pop("published-0001", None)
            acp_mod._supervisor.history.pop("published-0001", None)
        assert published, "creating a session published nothing"
        assert "published-0001" in published[-1], (
            "the new session was not in the published set, so presence would "
            "read its own agent's fresh lock as an orphan and hide a live "
            "session from the dashboard")

    def test_a_created_session_records_only_what_is_read(self, acp_store):
        acp_mod, store = acp_store

        async def verbose(self, method, params, timeout=None):
            return {"sessionId": "records-0001",
                    "models": {"available": ["a"] * 500},
                    "modes": {"current": "x", "available": ["y"] * 500}}

        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", verbose):
            asyncio.run(acp_mod._supervisor.new_session(str(store)))
        assert set(acp_mod._supervisor.sessions["records-0001"]) == {
            "cwd", "created", "last_used", "last_activity"}

    def test_a_loaded_session_records_only_what_is_read(self, acp_store):
        acp_mod, store = acp_store

        async def answers(self, method, params, timeout=None):
            return {}

        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", answers):
            asyncio.run(acp_mod._supervisor.load_session("records-0002", str(store)))
        assert set(acp_mod._supervisor.sessions["records-0002"]) == {
            "cwd", "created", "last_used", "last_activity"}

    def test_the_supervisor_keeps_no_agent_info(self):
        from power_atlas import acp as acp_mod
        assert not hasattr(acp_mod._supervisor, "agent_info")

    @pytest.mark.parametrize("returned", [
        "../../etc/passwd", "a/b", "a\\b", "", "with space", "x" * 200,
        "trailing\n", None, 17,
    ])
    def test_an_agent_session_id_passes_the_client_gate(self, acp_store, returned):
        """Every client-supplied id passes ``_valid_session_id``; the agent's
        did not, though it is written straight back into ``?sid=`` — so a reload
        after a restart routes it through ``load``, which refuses exactly what
        this admitted, leaving the page holding an id it can never reopen."""
        acp_mod, store = acp_store

        async def answers(self, method, params, timeout=None):
            return {"sessionId": returned}

        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", answers):
            with pytest.raises(acp_mod.AgentRejected):
                asyncio.run(acp_mod._supervisor.new_session(str(store)))
        assert acp_mod._supervisor.sessions == {}
        assert acp_mod._supervisor._reserved == 0

    def test_a_usable_agent_session_id_is_still_accepted(self, acp_store):
        """Positive control: the gate is ``_valid_session_id``, not a refusal
        of everything. Real ids are hyphenated hex."""
        acp_mod, store = acp_store

        async def answers(self, method, params, timeout=None):
            return {"sessionId": "73a40df3-2f1c-4e6a-9c11-0b7e6a2d5f88"}

        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", answers):
            info = asyncio.run(acp_mod._supervisor.new_session(str(store)))
        assert info["sessionId"] == "73a40df3-2f1c-4e6a-9c11-0b7e6a2d5f88"
        assert acp_mod._valid_session_id(info["sessionId"])


class TestAcpPageHarnessIsCommitted:
    """``acp.html`` carries the XSS control, the turn state machine, reconnect
    and the auto-load loop, and every Python assertion on it is a substring
    check against the template source — which pins the *text* of a line, not
    what it does. The behavioural coverage lives in a Node harness beside this
    file; this test only keeps it from being lost, since nothing else in the
    Python suite references it.
    """

    def test_the_harness_exists_and_names_its_command(self):
        harness = Path(__file__).with_name("acp_page.test.mjs")
        assert harness.is_file()
        src = harness.read_text(encoding="utf-8")
        assert "node tests/acp_page.test.mjs" in src, (
            "the harness must document the one-line command that runs it")


# --- Phase 3 (Launch Profiles): Profile endpoint tests ---


class TestLaunchProfileEndpoints:
    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_activate_valid_profile(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(
            launch_profiles=[LaunchProfile(id="default"), LaunchProfile(id="fast", name="Fast")],
            active_launch_profile="default",
        )
        resp = client.post("/api/launch-profile/activate", json={"id": "fast"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        saved = mock_save.call_args[0][0]
        assert saved.active_launch_profile == "fast"

    @patch("power_atlas.web.load_config")
    def test_activate_nonexistent_profile(self, mock_load, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
        resp = client.post("/api/launch-profile/activate", json={"id": "nonexistent"})
        assert resp.status_code == 404
        assert resp.json()["ok"] is False

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_save_new_profile(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
        resp = client.post("/api/launch-profile/save", json={
            "id": "__new__",
            "name": "My Custom Profile",
            "terminal_command": "wt.exe",
            "wt_profile": "PowerShell",
        })
        assert resp.status_code == 200
        assert "saved" in resp.text.lower()
        saved = mock_save.call_args[0][0]
        assert len(saved.launch_profiles) == 2
        assert saved.launch_profiles[1].name == "My Custom Profile"

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_save_update_existing(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile(id="default", name="Default")])
        resp = client.post("/api/launch-profile/save", json={
            "id": "default",
            "name": "Renamed",
            "terminal_command": "",
            "wt_profile": "PowerShell",
        })
        assert resp.status_code == 200
        assert "saved" in resp.text.lower()
        saved = mock_save.call_args[0][0]
        assert len(saved.launch_profiles) == 1
        assert saved.launch_profiles[0].name == "Renamed"

    @patch("power_atlas.web.load_config")
    def test_save_invalid_name_empty(self, mock_load, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
        resp = client.post("/api/launch-profile/save", json={
            "id": "__new__", "name": "", "terminal_command": "",
            "wt_profile": "PowerShell",
        })
        assert resp.status_code == 200
        assert "1-80" in resp.text

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_delete_profile(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(
            launch_profiles=[LaunchProfile(id="a", name="A"), LaunchProfile(id="b", name="B")],
            active_launch_profile="a",
        )
        resp = client.post("/api/launch-profile/delete", json={"id": "b"})
        assert resp.status_code == 200
        assert "deleted" in resp.text.lower()
        saved = mock_save.call_args[0][0]
        assert len(saved.launch_profiles) == 1
        assert saved.launch_profiles[0].id == "a"

    @patch("power_atlas.web.load_config")
    def test_delete_last_profile_rejected(self, mock_load, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(launch_profiles=[LaunchProfile(id="only")])
        resp = client.post("/api/launch-profile/delete", json={"id": "only"})
        assert resp.status_code == 200
        assert "cannot delete" in resp.text.lower()

    @patch("power_atlas.web.save_config")
    @patch("power_atlas.web.load_config")
    def test_delete_active_reassigns(self, mock_load, mock_save, client):
        from power_atlas.config import Config, LaunchProfile
        mock_load.return_value = Config(
            launch_profiles=[LaunchProfile(id="a"), LaunchProfile(id="b")],
            active_launch_profile="a",
        )
        resp = client.post("/api/launch-profile/delete", json={"id": "a"})
        assert resp.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved.active_launch_profile == "b"


# --- Phase 3 (Launch Profiles): default_args validation ---


class TestDefaultArgsValidation:
    @patch("power_atlas.web.load_config")
    def test_default_args_control_chars_rejected(self, mock_load, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/provider/save", json={
            "provider": "kiro-cli",
            "default_args": "valid\x01evil",
            "color": "",
            "enabled": True,
        })
        assert resp.status_code == 200
        assert "control" in resp.text.lower()

    @patch("power_atlas.web.load_config")
    def test_default_args_too_long_rejected(self, mock_load, client):
        from power_atlas.config import Config
        mock_load.return_value = Config()
        resp = client.post("/api/provider/save", json={
            "provider": "kiro-cli",
            "default_args": "x" * 257,
            "color": "",
            "enabled": True,
        })
        assert resp.status_code == 200
        assert "too long" in resp.text.lower()


# --- Phase 3 (Launch Profiles): Launch profile propagation ---


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_launch_uses_active_profile(mock_load, mock_launch, client, tmp_path):
    """Launch endpoint passes get_active_launch_profile(config) to launcher."""
    from power_atlas.config import Config, LaunchProfile
    from power_atlas.launcher import LaunchResult
    profile = LaunchProfile(id="custom", name="Custom", terminal_command="wt.exe")
    mock_load.return_value = Config(
        launch_profiles=[profile],
        active_launch_profile="custom",
    )
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path))

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    call_kwargs = mock_launch.call_args[1]
    assert call_kwargs["launch_profile"].terminal_command == "wt.exe"
    assert call_kwargs["launch_profile"].name == "Custom"


# --- Phase 3 (Launch Profiles): Batch warning aggregation ---


@patch("power_atlas.web.launcher.launch_batch")
@patch("power_atlas.web.load_config")
def test_launch_batch_warning_aggregation(mock_load, mock_batch, client, tmp_path):
    """When all launches succeed but some warn, aggregate into persistent warning toast."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_batch.return_value = [
        LaunchResult(True, None, str(tmp_path), warning="MCP-safe failed; launched directly"),
        LaunchResult(True, None, str(tmp_path)),
        LaunchResult(True, None, str(tmp_path), warning="MCP-safe failed; launched directly"),
    ]
    resp = client.post("/api/launch-batch", json={
        "sessions": [
            {"session_id": "s1", "workspace": str(tmp_path), "provider": "kiro-cli"},
            {"session_id": "s2", "workspace": str(tmp_path), "provider": "kiro-cli"},
            {"session_id": "s3", "workspace": str(tmp_path), "provider": "kiro-cli"},
        ],
    })
    assert resp.status_code == 200
    assert "2 launches used fallback" in resp.text
    assert "toast-persistent" in resp.text
    assert "toast-warning" in resp.text


@patch("power_atlas.web.launcher.launch_session")
@patch("power_atlas.web.load_config")
def test_single_launch_warning_persistent(mock_load, mock_launch, client, tmp_path):
    """Single launch with warning renders persistent warning toast."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    mock_load.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, str(tmp_path), warning="MCP-safe failed")

    resp = client.post("/api/launch", json={
        "workspace": str(tmp_path),
        "provider": "kiro-cli",
    })
    assert resp.status_code == 200
    assert "MCP-safe failed" in resp.text
    assert "toast-persistent" in resp.text
    assert "toast-warning" in resp.text


# --- Phase 3 (Launch Profiles): Metacharacter profile name round-trip ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_profile_metacharacter_name_roundtrip(mock_load, mock_save, client):
    """Profile with HTML metacharacters in name survives save round-trip."""
    from power_atlas.config import Config, LaunchProfile
    mock_load.return_value = Config(launch_profiles=[LaunchProfile()])
    xss_name = '<script>alert(1)</script>'
    resp = client.post("/api/launch-profile/save", json={
        "id": "__new__",
        "name": xss_name,
        "terminal_command": "",
        "wt_profile": "PowerShell",
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert len(saved.launch_profiles) == 2
    assert saved.launch_profiles[1].name == xss_name



# --- Phase 4 (findings fixes): disabled provider cards and unknown provider 404 ---


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_disabled_provider_hidden_from_cards(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Disabling a provider hides its workspace cards from the main listing."""
    from power_atlas.config import Config
    ws_kiro = str(tmp_path / "kiro-proj")
    ws_claude = str(tmp_path / "claude-proj")
    mock_config.return_value = Config(provider_settings={
        "claude-code": {"default_args": "", "color": "", "enabled": False},
    })
    mock_discover.return_value = [
        (ws_kiro, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
        (ws_claude, 3, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # kiro workspace should be visible
    assert "kiro-proj" in resp.text
    # claude workspace should be hidden because its only provider is disabled
    assert "claude-proj" not in resp.text


def test_get_provider_settings_unknown_404(client):
    """GET /api/provider/bogus returns 404 for unknown providers."""
    resp = client.get("/api/provider/bogus")
    assert resp.status_code == 404


# --- default_directory ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_settings_with_default_directory(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "-a",
        "color": "#ff0000",
        "enabled": True,
        "default_directory": "/home/user/work",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert saved.provider_settings["kiro-cli"]["default_directory"] == "/home/user/work"


@patch("power_atlas.web.load_config")
def test_get_provider_settings_includes_default_directory(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == ""


@patch("power_atlas.web.load_config")
def test_get_provider_settings_returns_saved_default_directory(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a", "color": "", "enabled": True, "default_directory": "/my/path"},
    })

    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert data["default_directory"] == "/my/path"


@patch("power_atlas.web.load_config")
def test_get_provider_settings_legacy_entry_gets_default_directory(mock_load, client):
    """Legacy provider settings without default_directory still return the field."""
    from power_atlas.config import Config
    mock_load.return_value = Config(provider_settings={
        "kiro-cli": {"default_args": "-a", "color": "", "enabled": True},
    })

    resp = client.get("/api/provider/kiro-cli")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == ""


@patch("power_atlas.web.load_config")
def test_settings_includes_default_directory(mock_load, client):
    from power_atlas.config import Config
    mock_load.return_value = Config(default_directory="/global/path")

    resp = client.get("/api/settings")
    data = resp.json()
    assert "default_directory" in data
    assert data["default_directory"] == "/global/path"


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_default_directory(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/save-setting", json={
        "key": "default_directory",
        "value": "/new/default/path",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    saved = mock_save.call_args[0][0]
    assert saved.default_directory == "/new/default/path"


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_default_directory_too_long(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/save-setting", json={
        "key": "default_directory",
        "value": "x" * 513,
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "too long" in body["error"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_setting_default_directory_control_chars(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/save-setting", json={
        "key": "default_directory",
        "value": "/path/\x01bad",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "control characters" in body["error"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_directory_too_long(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "",
        "color": "",
        "enabled": True,
        "default_directory": "x" * 513,
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "too long" in resp.text.lower()
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_save_provider_directory_control_chars(mock_load, mock_save, client):
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/provider/save", json={
        "provider": "kiro-cli",
        "default_args": "",
        "color": "",
        "enabled": True,
        "default_directory": "/path/\x01bad",
    }, headers={"Origin": "http://127.0.0.1"})
    assert resp.status_code == 200
    assert "control characters" in resp.text.lower()
    mock_save.assert_not_called()


# --- Phase 3 (panel restructure): All-sessions endpoint ---


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_basic(mock_paginated, mock_config, client, tmp_path):
    """Basic /partials/all-sessions returns session rows with workspace name."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [(_make_session(cwd=workspace, title="My Session"), "kiro-cli")],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "My Session" in resp.text
    assert tmp_path.name in resp.text  # workspace_name shown
    assert "session-row" in resp.text
    assert "load-more-btn" not in resp.text  # has_more=False


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_pagination(mock_paginated, mock_config, client, tmp_path):
    """Load more button rendered when has_more=True."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [(_make_session(cwd=workspace, title="Page 1 Session"), "kiro-cli")],
        True,
    )
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert "Page 1 Session" in resp.text
    assert "load-more-btn" in resp.text
    assert "loadMoreSessions(2)" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_provider_filter(mock_paginated, mock_config, client, tmp_path):
    """Provider filter is passed through to data layer."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [(_make_session(cwd=workspace, title="Claude Only"), "claude-code")],
        False,
    )
    resp = client.get("/partials/all-sessions?provider=claude-code")
    assert resp.status_code == 200
    assert "Claude Only" in resp.text
    # Verify provider filter was passed
    call_kwargs = mock_paginated.call_args[1]
    assert call_kwargs["provider"] == "claude-code"


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_search(mock_paginated, mock_config, client, tmp_path):
    """Search filter on all-sessions filters by title/prompt/cwd."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="s1", title="matching title"), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="s2", title="other session"), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions?q=matching")
    assert resp.status_code == 200
    assert "matching title" in resp.text
    assert "other session" not in resp.text
    assert "load-more-btn" not in resp.text  # Search disables pagination


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_empty(mock_paginated, mock_config, client):
    """Empty state when no sessions found."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "No sessions found" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_pinned_at_top(mock_paginated, mock_config, client, tmp_path):
    """Pinned sessions appear at top with pin icon."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(pinned_sessions=["pinned-sess"])
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="pinned-sess", title="Pinned One"), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="other-sess", title="Regular One"), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "Pinned One" in resp.text
    assert "Regular One" in resp.text
    # Pinned session has pin indicator
    assert "pinned-indicator" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_tag_filter(mock_paginated, mock_config, client, tmp_path):
    """Tag filter returns only sessions from matching workspaces."""
    from power_atlas.config import Config
    ws_tagged = str(tmp_path / "tagged-proj")
    ws_other = str(tmp_path / "other-proj")
    (tmp_path / "tagged-proj").mkdir()
    (tmp_path / "other-proj").mkdir()
    mock_config.return_value = Config(
        workspace_settings={ws_tagged: {"tags": ["frontend"], "color": ""}}
    )
    mock_paginated.return_value = (
        [
            (_make_session(cwd=ws_tagged, session_id="s1", title="Tagged Session"), "kiro-cli"),
            (_make_session(cwd=ws_other, session_id="s2", title="Other Session"), "kiro-cli"),
        ],
        True,
    )
    resp = client.get("/partials/all-sessions?tag=frontend")
    assert resp.status_code == 200
    assert "Tagged Session" in resp.text
    assert "Other Session" not in resp.text
    assert "load-more-btn" not in resp.text  # tag filter disables pagination


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_hidden_excluded_by_default(mock_paginated, mock_config, client, tmp_path):
    """Sessions from hidden workspaces are excluded by default."""
    from power_atlas.config import Config
    ws_hidden = str(tmp_path / "hidden-proj")
    ws_normal = str(tmp_path / "normal-proj")
    (tmp_path / "hidden-proj").mkdir()
    (tmp_path / "normal-proj").mkdir()
    mock_config.return_value = Config(
        workspace_settings={ws_hidden: {"tags": ["hidden"], "color": ""}}
    )
    mock_paginated.return_value = (
        [
            (_make_session(cwd=ws_hidden, session_id="s1", title="Hidden Session"), "kiro-cli"),
            (_make_session(cwd=ws_normal, session_id="s2", title="Normal Session"), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "Hidden Session" not in resp.text
    assert "Normal Session" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_time_filter(mock_paginated, mock_config, client, tmp_path):
    """Time filter returns only sessions from matching time bucket."""
    from power_atlas.config import Config
    from datetime import datetime, timedelta
    workspace = str(tmp_path)
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    old_iso = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="s1", title="Today Session", updated_at=today_iso), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="s2", title="Old Session", updated_at=old_iso), "kiro-cli"),
        ],
        True,
    )
    resp = client.get("/partials/all-sessions?time_filter=today")
    assert resp.status_code == 200
    assert "Today Session" in resp.text
    assert "Old Session" not in resp.text
    assert "load-more-btn" not in resp.text  # time filter disables pagination


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_time_grouped(mock_paginated, mock_config, client, tmp_path):
    """Sessions panel renders time-group headings."""
    from power_atlas.config import Config
    from datetime import datetime, timedelta
    workspace = str(tmp_path)
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    old_iso = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    mock_config.return_value = Config()
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="s1", title="Today Session", updated_at=today_iso), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="s2", title="Old Session", updated_at=old_iso), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    assert "group-heading" in resp.text
    assert "Today" in resp.text
    assert "Older" in resp.text
    # Empty group headings not rendered
    assert "Yesterday" not in resp.text
    assert "This week" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_pinned_above_time_groups(mock_paginated, mock_config, client, tmp_path):
    """Pinned sessions render above time-group headings with separator."""
    from power_atlas.config import Config
    from datetime import datetime
    workspace = str(tmp_path)
    today_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    mock_config.return_value = Config(pinned_sessions=["pinned-sess"])
    mock_paginated.return_value = (
        [
            (_make_session(cwd=workspace, session_id="pinned-sess", title="Pinned One", updated_at=today_iso), "kiro-cli"),
            (_make_session(cwd=workspace, session_id="other-sess", title="Regular One", updated_at=today_iso), "kiro-cli"),
        ],
        False,
    )
    resp = client.get("/partials/all-sessions")
    assert resp.status_code == 200
    # Pinned appears before time group headings
    pinned_pos = resp.text.index("Pinned One")
    sep_pos = resp.text.index("pinned-separator")
    heading_pos = resp.text.index("group-heading")
    regular_pos = resp.text.index("Regular One")
    assert pinned_pos < sep_pos < heading_pos < regular_pos


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_tag_empty_state(mock_paginated, mock_config, client, tmp_path):
    """Empty state shows tag-specific message."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    resp = client.get("/partials/all-sessions?tag=frontend")
    assert resp.status_code == 200
    assert "No sessions in workspaces tagged" in resp.text
    assert "frontend" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_partials_all_sessions_time_filter_empty_state(mock_paginated, mock_config, client, tmp_path):
    """Empty state shows time-specific message."""
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    resp = client.get("/partials/all-sessions?time_filter=today")
    assert resp.status_code == 200
    assert "No sessions active" in resp.text
    assert "today" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspaces_includes_pinned_at_top(mock_discover, mock_config, client, tmp_path):
    """Unified workspaces endpoint shows pinned workspaces at top, non-pinned below."""
    from power_atlas.config import Config
    ws_pinned = str(tmp_path / "pinned-proj")
    ws_other = str(tmp_path / "other-proj")
    mock_config.return_value = Config(pinned_folders=[ws_pinned])
    mock_discover.return_value = [
        (ws_pinned, 3, "2026-01-01T00:00:00Z", "kiro-cli"),
        (ws_other, 2, "2026-01-02T00:00:00Z", "kiro-cli"),
    ]
    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Both workspaces appear
    assert "pinned-proj" in resp.text
    assert "other-proj" in resp.text
    # Pinned workspace appears before non-pinned
    assert resp.text.index("pinned-proj") < resp.text.index("other-proj")


# --- Phase 5: open-folder, launch-terminal, terminal tile ---


@patch("power_atlas.web.sys")
@patch("power_atlas.web.os.startfile", create=True)
def test_open_folder_valid_directory(mock_startfile, mock_sys, client, tmp_path):
    """POST /api/open-folder with valid dir returns success toast."""
    mock_sys.platform = "win32"
    folder = str(tmp_path)
    resp = client.post("/api/open-folder", json={"folder": folder})
    assert resp.status_code == 200
    assert "Opened" in resp.text
    assert tmp_path.name in resp.text
    assert "success" in resp.text
    mock_startfile.assert_called_once_with(folder)


def test_open_folder_nonexistent_path(client):
    """POST /api/open-folder with non-existent path returns error toast."""
    resp = client.post("/api/open-folder", json={"folder": "C:\\nonexistent\\xyz\\bogus"})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower() or "error" in resp.text.lower()


@patch("power_atlas.web.launcher.launch_terminal")
@patch("power_atlas.web.load_config")
def test_launch_terminal_success(mock_config, mock_launch, client, tmp_path):
    """POST /api/launch-terminal with valid workspace returns success toast."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    cwd = str(tmp_path)
    mock_config.return_value = Config()
    mock_launch.return_value = LaunchResult(True, None, cwd)
    resp = client.post("/api/launch-terminal", json={"workspace": cwd})
    assert resp.status_code == 200
    assert "Terminal opened" in resp.text
    assert tmp_path.name in resp.text
    assert "success" in resp.text
    mock_launch.assert_called_once()


@patch("power_atlas.web.load_config")
def test_launch_terminal_no_workspace_no_default(mock_config, client):
    """POST /api/launch-terminal with no workspace and no default_directory falls back to home dir."""
    from power_atlas.config import Config
    mock_config.return_value = Config(default_directory="")
    with patch("power_atlas.web.launcher.launch_terminal") as mock_launch:
        mock_launch.return_value = launcher.LaunchResult(True, None, str(Path.home()))
        resp = client.post("/api/launch-terminal", json={})
        assert resp.status_code == 200
        assert "success" in resp.text
        # Verify it was called with the home directory
        mock_launch.assert_called_once()
        assert mock_launch.call_args[0][0] == str(Path.home())


@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.load_config")
def test_partials_launchers_includes_terminal_tile(mock_load, mock_providers, client):
    """Launcher grid includes the builtin terminal tile."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    mock_providers.return_value = ["kiro-cli"]
    resp = client.get("/partials/launchers")
    assert resp.status_code == 200
    assert 'data-id="builtin--terminal"' in resp.text


@patch("power_atlas.web.sys")
@patch("power_atlas.web.os.startfile", create=True)
def test_open_folder_oserror(mock_startfile, mock_sys, client, tmp_path):
    """POST /api/open-folder returns error toast when os.startfile raises OSError."""
    mock_sys.platform = "win32"
    mock_startfile.side_effect = OSError("Permission denied")
    folder = str(tmp_path)
    resp = client.post("/api/open-folder", json={"folder": folder})
    assert resp.status_code == 200
    assert "Could not open folder" in resp.text
    assert "error" in resp.text


def test_open_folder_empty_path(client):
    """POST /api/open-folder with empty folder returns error toast."""
    resp = client.post("/api/open-folder", json={"folder": ""})
    assert resp.status_code == 200
    assert "not found" in resp.text.lower() or "error" in resp.text.lower()


@patch("power_atlas.web.launcher.launch_terminal")
@patch("power_atlas.web.load_config")
def test_launch_terminal_failure_result(mock_config, mock_launch, client, tmp_path):
    """POST /api/launch-terminal returns error toast when launcher returns failure."""
    from power_atlas.config import Config
    from power_atlas.launcher import LaunchResult
    cwd = str(tmp_path)
    mock_config.return_value = Config()
    mock_launch.return_value = LaunchResult(False, None, cwd, error="No terminal found")
    resp = client.post("/api/launch-terminal", json={"workspace": cwd})
    assert resp.status_code == 200
    assert "No terminal found" in resp.text
    assert "error" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_separator_present_when_both_groups(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Separator div appears between pinned and non-pinned workspace cards."""
    from power_atlas.config import Config
    pinned_ws = str(tmp_path / "pinned-proj")
    other_ws = str(tmp_path / "other-proj")
    mock_config.return_value = Config(pinned_folders=[pinned_ws])
    mock_discover.return_value = [
        (pinned_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (other_ws, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_separator_absent_when_only_pinned(mock_discover, mock_providers, mock_config, client, tmp_path):
    """No separator when all workspaces are pinned (no non-pinned group)."""
    from power_atlas.config import Config
    pinned_ws = str(tmp_path / "pinned-proj")
    mock_config.return_value = Config(pinned_folders=[pinned_ws])
    mock_discover.return_value = [
        (pinned_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_pinned_separator_absent_when_no_pinned(mock_discover, mock_providers, mock_config, client, tmp_path):
    """No separator when no workspaces are pinned."""
    from power_atlas.config import Config
    other_ws = str(tmp_path / "other-proj")
    mock_config.return_value = Config(pinned_folders=[])
    mock_discover.return_value = [
        (other_ws, 2, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' not in resp.text



@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_session_pinned_separator_present(mock_paginated, mock_config, client, tmp_path):
    """Separator div appears between pinned and non-pinned sessions on page 1."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    pinned_session = _make_session(session_id="pinned-1", title="Pinned", cwd=workspace)
    other_session = _make_session(session_id="other-1", title="Other", cwd=workspace)
    mock_config.return_value = Config(pinned_sessions=["pinned-1"])
    mock_paginated.return_value = (
        [(pinned_session, "kiro-cli"), (other_session, "kiro-cli")],
        False,
    )
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.get_all_sessions_paginated")
def test_session_pinned_separator_absent_no_pinned(mock_paginated, mock_config, client, tmp_path):
    """No separator when no sessions are pinned."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    session = _make_session(session_id="s1", title="Regular", cwd=workspace)
    mock_config.return_value = Config(pinned_sessions=[])
    mock_paginated.return_value = (
        [(session, "kiro-cli")],
        False,
    )
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert 'class="pinned-separator"' not in resp.text



# --- Phase 2 (Workspace Tags): Workspace settings API ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_workspace_settings_save_roundtrip(mock_load, mock_save, client, tmp_path):
    """POST /api/workspace-settings/save persists tags and color to config."""
    from power_atlas.config import Config
    cwd = str(tmp_path)
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": cwd,
        "tags": ["frontend", "active"],
        "color": "#3b82f6",
    })
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert saved.workspace_settings[cwd]["tags"] == ["frontend", "active"]
    assert saved.workspace_settings[cwd]["color"] == "#3b82f6"


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_empty_cwd_rejected(mock_load, client):
    """Empty cwd is rejected with error toast."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": "",
        "tags": ["test"],
        "color": "",
    })
    assert resp.status_code == 200
    assert "invalid" in resp.text.lower() or "error" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_too_many_tags(mock_load, client, tmp_path):
    """More than 10 tags is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": str(tmp_path),
        "tags": ["t" + str(i) for i in range(11)],
        "color": "",
    })
    assert resp.status_code == 200
    assert "max 10" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_tag_too_long(mock_load, client, tmp_path):
    """Tag over 64 chars is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": str(tmp_path),
        "tags": ["x" * 65],
        "color": "",
    })
    assert resp.status_code == 200
    assert "invalid tag" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_save_tag_control_chars(mock_load, client, tmp_path):
    """Tag with control characters is rejected."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": str(tmp_path),
        "tags": ["bad\x01tag"],
        "color": "",
    })
    assert resp.status_code == 200
    assert "invalid tag" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_workspace_settings_get_returns_defaults(mock_load, client, tmp_path):
    """GET /api/workspace-settings returns empty defaults for unknown workspace."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.get("/api/workspace-settings", params={"cwd": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["tags"] == []
    assert body["settings"]["color"] == ""
    assert body["all_tags"] == []


@patch("power_atlas.web.load_config")
def test_workspace_settings_get_returns_saved_data(mock_load, client, tmp_path):
    """GET /api/workspace-settings returns previously saved tags and color."""
    from power_atlas.config import Config
    cwd = str(tmp_path)
    mock_load.return_value = Config(
        workspace_settings={cwd: {"tags": ["dev", "active"], "color": "#ef4444"}},
        tag_settings={"archived": {"color": "#64748b"}},
    )
    resp = client.get("/api/workspace-settings", params={"cwd": cwd})
    assert resp.status_code == 200
    body = resp.json()
    assert body["settings"]["tags"] == ["dev", "active"]
    assert body["settings"]["color"] == "#ef4444"
    # all_tags includes both workspace tags and tag_settings keys
    assert "dev" in body["all_tags"]
    assert "active" in body["all_tags"]
    assert "archived" in body["all_tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_workspace_settings_save_deduplicates_normalized_path(mock_load, mock_save, client, tmp_path):
    """Saving settings deduplicates paths that normalize to the same value."""
    from power_atlas.config import Config
    cwd_lower = str(tmp_path).lower()
    cwd_upper = str(tmp_path).upper()
    mock_load.return_value = Config(
        workspace_settings={cwd_lower: {"tags": ["old"], "color": ""}},
    )
    resp = client.post("/api/workspace-settings/save", json={
        "cwd": cwd_upper,
        "tags": ["new"],
        "color": "#22c55e",
    })
    assert resp.status_code == 200
    assert "saved" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    # Old key should be removed, new key used
    assert cwd_lower not in saved.workspace_settings
    assert saved.workspace_settings[cwd_upper]["tags"] == ["new"]
    assert saved.workspace_settings[cwd_upper]["color"] == "#22c55e"



# --- Phase 3 (Workspace Tags): Color precedence ---


class TestResolveWorkspaceColor:
    """Test _resolve_workspace_color precedence: explicit > tag color > empty."""

    def test_explicit_workspace_color_wins(self):
        """Workspace with explicit color returns that color regardless of tags."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["active"], "color": "#ff0000"}},
            tag_settings={"active": {"color": "#00ff00"}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == "#ff0000"

    def test_tag_color_used_when_no_explicit_color(self):
        """Workspace with no explicit color but tagged with colored tag uses tag's color."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["frontend", "active"], "color": ""}},
            tag_settings={"frontend": {"color": "#3b82f6"}, "active": {"color": "#22c55e"}},
        )
        # First tag's color wins
        assert _resolve_workspace_color("C:\\proj", config) == "#3b82f6"

    def test_empty_when_no_color_and_no_colored_tags(self):
        """Workspace with no color and uncolored tags returns empty (provider gradient)."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["uncolored"], "color": ""}},
            tag_settings={"uncolored": {"color": ""}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == ""

    def test_empty_when_no_workspace_settings(self):
        """Unknown workspace returns empty (provider gradient)."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config()
        assert _resolve_workspace_color("C:\\unknown", config) == ""

    def test_skips_uncolored_tags_uses_first_colored(self):
        """Skips tags without color, uses first tag that has a color."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["nocolor", "hascolor"], "color": ""}},
            tag_settings={"nocolor": {"color": ""}, "hascolor": {"color": "#ef4444"}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == "#ef4444"

    def test_tag_not_in_tag_settings_skipped(self):
        """Tags not defined in tag_settings are skipped gracefully."""
        from power_atlas.config import Config
        from power_atlas.web import _resolve_workspace_color
        config = Config(
            workspace_settings={"C:\\proj": {"tags": ["undefined-tag", "defined"], "color": ""}},
            tag_settings={"defined": {"color": "#abc123"}},
        )
        assert _resolve_workspace_color("C:\\proj", config) == "#abc123"


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_uses_workspace_color_over_provider(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspace with explicit color renders that color instead of provider gradient."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(
        workspace_settings={workspace: {"tags": [], "color": "#e11d48"}},
    )
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Workspace color is used instead of provider default
    assert "#e11d48" in resp.text
    assert "provider-gradient" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_uses_tag_color_when_no_explicit(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspace with no explicit color but colored tag uses tag's color."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(
        workspace_settings={workspace: {"tags": ["frontend"], "color": ""}},
        tag_settings={"frontend": {"color": "#3b82f6"}},
    )
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "#3b82f6" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspace_card_falls_through_to_provider_gradient(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspace with no color and no colored tags shows provider gradient."""
    from power_atlas.config import Config
    workspace = str(tmp_path)
    mock_config.return_value = Config(
        workspace_settings={workspace: {"tags": ["plain"], "color": ""}},
        tag_settings={"plain": {"color": ""}},
    )
    mock_discover.return_value = [(workspace, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    # Provider color is used (kiro-cli default)
    assert "#7138cc" in resp.text



# --- Phase 5 (Workspace Tags): Filters, time bucketing, group-by, /api/tags ---


class TestTimeBucket:
    """Unit tests for the _time_bucket helper."""

    def test_empty_string_returns_before(self):
        from power_atlas.web import _time_bucket
        assert _time_bucket("") == "before"

    def test_invalid_iso_returns_before(self):
        from power_atlas.web import _time_bucket
        assert _time_bucket("not-a-date") == "before"

    def test_today_timestamp(self):
        from datetime import datetime, timezone
        from power_atlas.web import _time_bucket
        now = datetime.now(timezone.utc).isoformat()
        assert _time_bucket(now) == "today"

    def test_yesterday_timestamp(self):
        from datetime import datetime, timezone, timedelta
        from power_atlas.web import _time_bucket
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        assert _time_bucket(yesterday) == "yesterday"

    def test_this_week_timestamp(self):
        from datetime import datetime, timezone, timedelta, date
        from power_atlas.web import _time_bucket
        today = date.today()
        # Monday of this week
        monday = today - timedelta(days=today.weekday())
        if monday == today or monday == today - timedelta(days=1):
            # If today is Monday or Tuesday, skip — Monday/Tuesday map to today/yesterday
            pytest.skip("Can't test this_week on Mon/Tue without hitting today/yesterday")
        monday_dt = datetime(monday.year, monday.month, monday.day, 12, 0, 0, tzinfo=timezone.utc)
        assert _time_bucket(monday_dt.isoformat()) == "this_week"

    def test_old_timestamp_returns_before(self):
        from power_atlas.web import _time_bucket
        assert _time_bucket("2020-01-01T00:00:00Z") == "before"

    def test_z_suffix_handled(self):
        from datetime import datetime, timezone
        from power_atlas.web import _time_bucket
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _time_bucket(now) == "today"


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_hidden_tag_excluded_by_default(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Workspaces tagged 'hidden' are excluded from default view."""
    from power_atlas.config import Config
    visible_ws = str(tmp_path / "visible-proj")
    hidden_ws = str(tmp_path / "hidden-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (visible_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "visible-proj" in resp.text
    assert "hidden-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_hidden_tag_filter_reveals_hidden(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Selecting 'hidden' tag filter reveals only hidden workspaces."""
    from power_atlas.config import Config
    visible_ws = str(tmp_path / "visible-proj")
    hidden_ws = str(tmp_path / "hidden-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (visible_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?tag=hidden")
    assert resp.status_code == 200
    assert "hidden-proj" in resp.text
    assert "visible-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tag_filter_shows_matching_workspaces(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Tag filter shows only workspaces that have the selected tag."""
    from power_atlas.config import Config
    frontend_ws = str(tmp_path / "frontend-proj")
    backend_ws = str(tmp_path / "backend-proj")
    mock_config.return_value = Config(
        workspace_settings={
            frontend_ws: {"tags": ["frontend"], "color": ""},
            backend_ws: {"tags": ["backend"], "color": ""},
        },
    )
    mock_discover.return_value = [
        (frontend_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (backend_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?tag=frontend")
    assert resp.status_code == 200
    assert "frontend-proj" in resp.text
    assert "backend-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_time_filter_today(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Time filter 'today' shows only workspaces updated today."""
    from datetime import datetime, timezone
    from power_atlas.config import Config
    today_ws = str(tmp_path / "today-proj")
    old_ws = str(tmp_path / "old-proj")
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (today_ws, 1, now_iso, "kiro-cli"),
        (old_ws, 1, "2020-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?time_filter=today")
    assert resp.status_code == 200
    assert "today-proj" in resp.text
    assert "old-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_tag_filter_empty_state(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Tag filter with no matches shows appropriate empty state."""
    from power_atlas.config import Config
    ws = str(tmp_path / "proj")
    mock_config.return_value = Config(
        workspace_settings={ws: {"tags": ["backend"], "color": ""}},
    )
    mock_discover.return_value = [(ws, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?tag=nonexistent")
    assert resp.status_code == 200
    assert "No workspaces with tag" in resp.text
    assert "nonexistent" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_time_filter_empty_state(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Time filter with no matches shows appropriate empty state."""
    from power_atlas.config import Config
    ws = str(tmp_path / "proj")
    mock_config.return_value = Config()
    mock_discover.return_value = [(ws, 1, "2020-01-01T00:00:00Z", "kiro-cli")]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces?time_filter=today")
    assert resp.status_code == 200
    assert "No workspaces active" in resp.text
    assert "today" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_default_time_grouping_renders_headings(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Default rendering always time-groups with Today/Yesterday/This week/Older headings."""
    from datetime import datetime, timezone, timedelta
    from power_atlas.config import Config
    today_ws = str(tmp_path / "today-proj")
    old_ws = str(tmp_path / "old-proj")
    now_iso = datetime.now(timezone.utc).isoformat()
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (today_ws, 1, now_iso, "kiro-cli"),
        (old_ws, 1, "2020-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert 'class="group-heading"' in resp.text
    assert "Today" in resp.text
    assert "Older" in resp.text
    assert "today-proj" in resp.text
    assert "old-proj" in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_filters_compose_with_provider(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Tag filter composes with provider filter (AND logic)."""
    from power_atlas.config import Config
    kiro_ws = str(tmp_path / "kiro-proj")
    claude_ws = str(tmp_path / "claude-proj")
    mock_config.return_value = Config(
        workspace_settings={
            kiro_ws: {"tags": ["active"], "color": ""},
            claude_ws: {"tags": ["active"], "color": ""},
        },
    )
    mock_discover.return_value = [
        (kiro_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (claude_ws, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_providers.return_value = ["kiro-cli", "claude-code"]

    # Tag=active AND provider=kiro-cli — only kiro workspace
    resp = client.get("/partials/workspaces?tag=active&provider=kiro-cli")
    assert resp.status_code == 200
    assert "kiro-proj" in resp.text
    assert "claude-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_hidden_filter_applies_to_pinned(mock_discover, mock_providers, mock_config, client, tmp_path):
    """Pinned workspaces tagged 'hidden' are also excluded from default view."""
    from power_atlas.config import Config
    hidden_pinned_ws = str(tmp_path / "hidden-pinned")
    visible_ws = str(tmp_path / "visible-proj")
    mock_config.return_value = Config(
        pinned_folders=[hidden_pinned_ws],
        workspace_settings={hidden_pinned_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (hidden_pinned_ws, 1, "2026-01-02T00:00:00Z", "kiro-cli"),
        (visible_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_providers.return_value = ["kiro-cli"]

    resp = client.get("/partials/workspaces")
    assert resp.status_code == 200
    assert "hidden-pinned" not in resp.text
    assert "visible-proj" in resp.text


# --- /api/tags endpoint ---


@patch("power_atlas.web.load_config")
def test_api_tags_returns_tag_list(mock_load, client, tmp_path):
    """GET /api/tags returns all tags with colors and counts."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["frontend", "active"], "color": ""},
            ws2: {"tags": ["frontend", "backend"], "color": ""},
        },
        tag_settings={
            "frontend": {"color": "#3b82f6"},
            "archived": {"color": "#64748b"},
        },
    )
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    tags = resp.json()
    tag_map = {t["name"]: t for t in tags}
    assert "frontend" in tag_map
    assert tag_map["frontend"]["count"] == 2
    assert tag_map["frontend"]["color"] == "#3b82f6"
    assert "active" in tag_map
    assert tag_map["active"]["count"] == 1
    assert tag_map["active"]["color"] == ""
    assert "backend" in tag_map
    assert tag_map["backend"]["count"] == 1
    # tag_settings tag with 0 workspaces still appears
    assert "archived" in tag_map
    assert tag_map["archived"]["count"] == 0
    assert tag_map["archived"]["color"] == "#64748b"


@patch("power_atlas.web.load_config")
def test_api_tags_empty_when_no_tags(mock_load, client):
    """GET /api/tags returns empty list when no tags configured."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    assert resp.json() == []


# --- Search endpoint with filters ---


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_excludes_hidden_by_default(mock_discover, mock_config, client, tmp_path):
    """Search results exclude hidden workspaces by default."""
    from power_atlas.config import Config
    hidden_ws = str(tmp_path / "hidden-proj")
    visible_ws = str(tmp_path / "visible-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [
        (hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (visible_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    # Search for "proj" which matches both
    resp = client.get(f"/search?q=proj")
    assert resp.status_code == 200
    assert "visible-proj" in resp.text
    assert "hidden-proj" not in resp.text


@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_with_tag_filter(mock_discover, mock_config, client, tmp_path):
    """Search results respect tag filter."""
    from power_atlas.config import Config
    frontend_ws = str(tmp_path / "frontend-proj")
    backend_ws = str(tmp_path / "backend-proj")
    mock_config.return_value = Config(
        workspace_settings={
            frontend_ws: {"tags": ["frontend"], "color": ""},
            backend_ws: {"tags": ["backend"], "color": ""},
        },
    )
    mock_discover.return_value = [
        (frontend_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (backend_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    resp = client.get("/search?q=proj&tag=frontend")
    assert resp.status_code == 200
    assert "frontend-proj" in resp.text
    assert "backend-proj" not in resp.text


def _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path):
    """Three same-named workspaces with different liveness and provider shapes.

    ``mixed-proj`` is registered under both providers but only claude-code is
    running in it, which is what separates a provider-scoped status query from
    an unscoped one.
    """
    from power_atlas.config import Config
    live_ws = str(tmp_path / "live-proj")
    dead_ws = str(tmp_path / "dead-proj")
    mixed_ws = str(tmp_path / "mixed-proj")
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (live_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (dead_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (mixed_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli"),
        (mixed_ws, 1, "2026-01-01T00:00:00Z", "claude-code"),
    ]
    mock_snap.return_value = _snapshot(live_cwds={
        ("kiro-cli", _normalize_path(live_ws)),
        ("claude-code", _normalize_path(mixed_ws)),
    })
    mock_sessions.return_value = []
    return live_ws, dead_ws, mixed_ws


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_with_status_filter(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """Search results respect status filter (regression: surplus arg 500'd the endpoint)."""
    _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path)

    resp = client.get("/search?q=proj&status=working")
    assert resp.status_code == 200
    assert "live-proj" in resp.text
    assert "mixed-proj" in resp.text
    assert "dead-proj" not in resp.text
    assert mock_snap.call_count == 1

    # status=live goes through the _LIVE_STATUSES branch of _status_matches.
    resp = client.get("/search?q=proj&status=live")
    assert resp.status_code == 200
    assert "live-proj" in resp.text
    assert "mixed-proj" in resp.text
    assert "dead-proj" not in resp.text
    assert mock_snap.call_count == 2

    # Provider-scoped: mixed-proj is a kiro-cli workspace whose only running
    # process is claude-code, so it survives this filter exactly when
    # _workspace_status is asked about every provider instead of kiro-cli.
    resp = client.get("/search?q=proj&provider=kiro-cli&status=working")
    assert resp.status_code == 200
    assert "live-proj" in resp.text
    assert "mixed-proj" not in resp.text
    assert "dead-proj" not in resp.text
    assert mock_snap.call_count == 3

    # status=all and the no-status path skip the status *filter*, but still
    # take one snapshot each: the per-card hover actions need workspace_status
    # on every card, so get_snapshot moved out of the status guard to an
    # unconditional call (web.py:1464). Two requests, two snapshots, 3 -> 5.
    for url in ("/search?q=proj&status=all", "/search?q=proj"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert "live-proj" in resp.text
        assert "dead-proj" in resp.text
    assert mock_snap.call_count == 5


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_names_the_filter(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """A filter that removes every match says which filter, as /partials/workspaces does."""
    _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path)

    resp = client.get("/search?q=proj&status=errored")
    assert resp.status_code == 200
    assert "No errored workspaces right now." in resp.text
    assert "No results for" not in resp.text

    resp = client.get("/search?q=proj&tag=nope")
    assert resp.status_code == 200
    assert "No workspaces with tag" in resp.text and "nope" in resp.text

    resp = client.get("/search?q=proj&time_filter=yesterday")
    assert resp.status_code == 200
    assert "No workspaces active yesterday" in resp.text

    # A query that matches nothing at all still reports the query, not a
    # filter — including when a filter is active, since no filter is at fault.
    for url in ("/search?q=nothingmatchesthis",
                "/search?q=nothingmatchesthis&status=working",
                "/search?q=nothingmatchesthis&tag=nope"):
        resp = client.get(url)
        assert resp.status_code == 200
        assert "No results for" in resp.text, url
        assert "workspaces right now" not in resp.text, url


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_when_hidden_tag_removes_every_match(
        mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """The default hidden-tag filter can empty a non-empty match set.

    That is the only route to the cascade's last branch: the query matched,
    but no explicit filter was named, so the wording falls back to the query.
    """
    from power_atlas.config import Config
    hidden_ws = str(tmp_path / "hidden-proj")
    mock_config.return_value = Config(
        workspace_settings={hidden_ws: {"tags": ["hidden"], "color": ""}},
    )
    mock_discover.return_value = [(hidden_ws, 1, "2026-01-01T00:00:00Z", "kiro-cli")]
    mock_snap.return_value = _snapshot()
    mock_sessions.return_value = []

    resp = client.get("/search?q=proj")
    assert resp.status_code == 200
    assert "No results for" in resp.text and "proj" in resp.text
    assert "hidden-proj" not in resp.text
    assert "workspaces right now" not in resp.text
    # Asking for the hidden tag explicitly shows the same workspace.
    resp = client.get("/search?q=proj&tag=hidden")
    assert resp.status_code == 200
    assert "hidden-proj" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_names_the_provider(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """A query that only matches other providers' workspaces says so.

    /partials/workspaces answers "no sessions found — start one with claude",
    which would be a lie here: the provider may well have sessions, just none
    matching the query.
    """
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_discover.return_value = [
        (str(tmp_path / "kiro-only-proj"), 1, "2026-01-01T00:00:00Z", "kiro-cli"),
    ]
    mock_snap.return_value = _snapshot()
    mock_sessions.return_value = []

    resp = client.get("/search?q=proj&provider=claude-code")
    assert resp.status_code == 200
    assert "No Claude Code results for" in resp.text
    assert "proj" in resp.text
    assert "start one with" not in resp.text

    # An unknown provider degrades to its raw name rather than erroring.
    resp = client.get("/search?q=proj&provider=bogus")
    assert resp.status_code == 200
    assert "No bogus results for" in resp.text


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.load_config")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_search_empty_state_escapes_input(mock_discover, mock_config, mock_snap, mock_sessions, client, tmp_path):
    """Filter values are reflected in the empty state, so they must be escaped."""
    _search_status_fixture(mock_discover, mock_config, mock_snap, mock_sessions, tmp_path)

    resp = client.get("/search", params={"q": "proj", "status": "<b>x</b>"})
    assert resp.status_code == 200
    assert "<b>x</b>" not in resp.text
    assert "&lt;b&gt;x" in resp.text


# --- Phase 6 (Tag Color Management): /api/tag/save ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_save_persists_color(mock_load, mock_save, client, tmp_path):
    """POST /api/tag/save persists color to tag_settings in config."""
    from power_atlas.config import Config
    config = Config(tag_settings={})
    mock_load.return_value = config

    resp = client.post("/api/tag/save", json={"tag": "frontend", "color": "#3b82f6"})
    assert resp.status_code == 200
    assert "Tag color saved" in resp.text

    saved = mock_save.call_args[0][0]
    assert saved.tag_settings["frontend"] == {"color": "#3b82f6"}


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_save_empty_color_clears(mock_load, mock_save, client):
    """POST /api/tag/save with empty color clears the tag's color."""
    from power_atlas.config import Config
    config = Config(tag_settings={"frontend": {"color": "#3b82f6"}})
    mock_load.return_value = config

    resp = client.post("/api/tag/save", json={"tag": "frontend", "color": ""})
    assert resp.status_code == 200
    assert "Tag color saved" in resp.text

    saved = mock_save.call_args[0][0]
    assert saved.tag_settings["frontend"] == {"color": ""}


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_empty_name(mock_load, client):
    """POST /api/tag/save rejects empty tag name."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "", "color": "#ff0000"})
    assert resp.status_code == 200
    assert "Invalid tag name" in resp.text


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_long_name(mock_load, client):
    """POST /api/tag/save rejects tag name > 64 chars."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "a" * 65, "color": "#ff0000"})
    assert resp.status_code == 200
    assert "Invalid tag name" in resp.text


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_control_chars_in_name(mock_load, client):
    """POST /api/tag/save rejects tag name with control characters."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "bad\x01tag", "color": "#ff0000"})
    assert resp.status_code == 200
    assert "Invalid tag name" in resp.text


@patch("power_atlas.web.load_config")
def test_tag_save_rejects_invalid_color(mock_load, client):
    """POST /api/tag/save rejects color with control characters."""
    from power_atlas.config import Config
    mock_load.return_value = Config()

    resp = client.post("/api/tag/save", json={"tag": "frontend", "color": "\x01red"})
    assert resp.status_code == 200
    assert "Invalid color value" in resp.text


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_save_appears_in_api_tags(mock_load, mock_save, client, tmp_path):
    """Saved tag color is reflected in subsequent /api/tags response."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    config = Config(
        workspace_settings={ws1: {"tags": ["frontend"], "color": ""}},
        tag_settings={"frontend": {"color": "#3b82f6"}},
    )
    mock_load.return_value = config

    resp = client.get("/api/tags")
    assert resp.status_code == 200
    tags = resp.json()
    tag_map = {t["name"]: t for t in tags}
    assert tag_map["frontend"]["color"] == "#3b82f6"


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_delete_removes_globally(mock_load, mock_save, client, tmp_path):
    """POST /api/tag/delete removes tag from tag_settings and all workspace assignments."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    config = Config(
        tag_settings={"frontend": {"color": "#3b82f6"}, "backend": {"color": "#10b981"}},
        workspace_settings={
            ws1: {"tags": ["frontend", "backend"], "color": ""},
            ws2: {"tags": ["frontend"], "color": ""},
        },
    )
    mock_load.return_value = config

    resp = client.post("/api/tag/delete", json={"tag": "frontend"})
    assert resp.status_code == 200
    assert "deleted" in resp.text
    assert "2 workspace" in resp.text

    saved = mock_save.call_args[0][0]
    assert "frontend" not in saved.tag_settings
    assert "backend" in saved.tag_settings
    assert "frontend" not in saved.workspace_settings[ws1]["tags"]
    assert "backend" in saved.workspace_settings[ws1]["tags"]
    assert "frontend" not in saved.workspace_settings[ws2]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_delete_hidden_protected(mock_load, mock_save, client):
    """POST /api/tag/delete rejects deletion of the 'hidden' tag."""
    from power_atlas.config import Config
    mock_load.return_value = Config(tag_settings={"hidden": {"color": ""}})

    resp = client.post("/api/tag/delete", json={"tag": "hidden"})
    assert resp.status_code == 200
    assert "Cannot delete" in resp.text
    mock_save.assert_not_called()


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_tag_delete_nonexistent_succeeds(mock_load, mock_save, client):
    """POST /api/tag/delete succeeds gracefully for a tag that doesn't exist."""
    from power_atlas.config import Config
    config = Config(tag_settings={"existing": {"color": "#fff"}})
    mock_load.return_value = config

    resp = client.post("/api/tag/delete", json={"tag": "nonexistent"})
    assert resp.status_code == 200
    assert "not found" in resp.text
    mock_save.assert_not_called()


# --- Bulk workspace settings tests ---


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_adds_tags_to_multiple(mock_load, mock_save, client, tmp_path):
    """POST /api/workspace-settings/save-bulk adds a tag to multiple workspaces."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "project1")
    ws2 = str(tmp_path / "project2")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["existing"], "color": ""},
            ws2: {"tags": [], "color": "#aaa"},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws1, ws2],
        "tags_add": ["new-tag"],
        "tags_remove": [],
    })
    assert resp.status_code == 200
    assert "updated 2 workspace" in resp.text.lower()
    saved = mock_save.call_args[0][0]
    assert "new-tag" in saved.workspace_settings[ws1]["tags"]
    assert "existing" in saved.workspace_settings[ws1]["tags"]
    assert "new-tag" in saved.workspace_settings[ws2]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_partial_success_10_tag_limit(mock_load, mock_save, client, tmp_path):
    """Bulk add returns warning when a workspace hits the 10-tag limit."""
    from power_atlas.config import Config
    ws_full = str(tmp_path / "full")
    ws_empty = str(tmp_path / "empty")
    mock_load.return_value = Config(
        workspace_settings={
            ws_full: {"tags": [f"t{i}" for i in range(10)], "color": ""},
            ws_empty: {"tags": [], "color": ""},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws_full, ws_empty],
        "tags_add": ["overflow"],
        "tags_remove": [],
    })
    assert resp.status_code == 200
    assert "warning" in resp.text.lower() or "10-tag limit" in resp.text.lower()
    assert "1 hit 10-tag limit" in resp.text
    saved = mock_save.call_args[0][0]
    # Full workspace should NOT have the new tag
    assert "overflow" not in saved.workspace_settings[ws_full]["tags"]
    # Empty workspace should have it
    assert "overflow" in saved.workspace_settings[ws_empty]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_removes_tags(mock_load, mock_save, client, tmp_path):
    """Bulk remove strips a tag from multiple workspaces."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "a")
    ws2 = str(tmp_path / "b")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["old", "keep"], "color": ""},
            ws2: {"tags": ["old"], "color": ""},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws1, ws2],
        "tags_add": [],
        "tags_remove": ["old"],
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert "old" not in saved.workspace_settings[ws1]["tags"]
    assert "keep" in saved.workspace_settings[ws1]["tags"]
    assert "old" not in saved.workspace_settings[ws2]["tags"]


@patch("power_atlas.web.save_config")
@patch("power_atlas.web.load_config")
def test_bulk_save_color_applies_to_all(mock_load, mock_save, client, tmp_path):
    """Bulk save sets color on all specified workspaces."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "x")
    ws2 = str(tmp_path / "y")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": [], "color": ""},
            ws2: {"tags": [], "color": "#old"},
        },
    )
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [ws1, ws2],
        "tags_add": [],
        "tags_remove": [],
        "color": "#new123",
    })
    assert resp.status_code == 200
    saved = mock_save.call_args[0][0]
    assert saved.workspace_settings[ws1]["color"] == "#new123"
    assert saved.workspace_settings[ws2]["color"] == "#new123"


@patch("power_atlas.web.load_config")
def test_bulk_save_add_remove_overlap_rejected(mock_load, client, tmp_path):
    """Overlapping tags_add and tags_remove returns error toast."""
    from power_atlas.config import Config
    mock_load.return_value = Config()
    resp = client.post("/api/workspace-settings/save-bulk", json={
        "cwds": [str(tmp_path)],
        "tags_add": ["conflict"],
        "tags_remove": ["conflict"],
    })
    assert resp.status_code == 200
    assert "must not overlap" in resp.text.lower()


@patch("power_atlas.web.load_config")
def test_bulk_get_returns_multiple(mock_load, client, tmp_path):
    """POST /api/workspace-settings-bulk returns settings for multiple cwds."""
    from power_atlas.config import Config
    ws1 = str(tmp_path / "proj1")
    ws2 = str(tmp_path / "proj2")
    mock_load.return_value = Config(
        workspace_settings={
            ws1: {"tags": ["frontend"], "color": "#111"},
            ws2: {"tags": ["backend"], "color": "#222"},
        },
        tag_settings={"archived": {"color": "#999"}},
    )
    resp = client.post("/api/workspace-settings-bulk", json={
        "cwds": [ws1, ws2],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspaces"][ws1]["tags"] == ["frontend"]
    assert body["workspaces"][ws1]["color"] == "#111"
    assert body["workspaces"][ws2]["tags"] == ["backend"]
    assert body["workspaces"][ws2]["color"] == "#222"
    # all_tags includes workspace tags + tag_settings keys
    assert "frontend" in body["all_tags"]
    assert "backend" in body["all_tags"]
    assert "archived" in body["all_tags"]


# --- Live-session status: helpers, filtering, dots ---

from datetime import datetime, timezone, timedelta
from power_atlas import presence
from power_atlas.web import _session_status, _workspace_status, _status_matches
from power_atlas.data import _normalize_path


def _recent_iso(secs=5):
    return (datetime.now(timezone.utc) - timedelta(seconds=secs)).isoformat()


def _old_iso(mins=10):
    return (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()


def _snapshot(live_sids=(), live_cwds=(), sid_to_cwd=None, sid_status=None):
    return presence.Snapshot(set(live_sids), set(live_cwds), sid_to_cwd or {},
                             sid_status or {})


def test_status_matches_semantics():
    assert _status_matches("", "closed") is True          # no filter
    assert _status_matches("all", "waiting") is True
    assert _status_matches("live", "working") is True
    assert _status_matches("live", "waiting") is True
    assert _status_matches("live", "errored") is True
    assert _status_matches("live", "closed") is False
    assert _status_matches("working", "working") is True
    assert _status_matches("working", "waiting") is False


@patch("power_atlas.web.get_semantic_status")
def test_session_status_semantic_and_fallback(mock_semantic):
    """Semantic status is returned when available; waiting fallback when None."""
    from power_atlas.status_classifier import SemanticStatus
    live = _snapshot(live_sids={("claude-code", "s1")})
    recent_s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    closed_s = _make_session(session_id="s2", cwd="/w", updated_at=_recent_iso())

    # Semantic path: returns classifier result
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(live, recent_s, "claude-code") == "waiting"
    mock_semantic.return_value = SemanticStatus.ERRORED
    assert _session_status(live, recent_s, "claude-code") == "errored"

    # Fallback path: classifier returns None -> working (process running, can't classify)
    mock_semantic.return_value = None
    assert _session_status(live, recent_s, "claude-code") == "working"

    # Closed: not live at all
    assert _session_status(live, closed_s, "claude-code") == "closed"


@patch("power_atlas.status_classifier._resolve_jsonl_path")
@patch("power_atlas.web.get_semantic_status")
def test_session_status_cwd_based_no_resume_id(mock_semantic, mock_resolve):
    """CWD-based detection: a process in the cwd is enough to classify as live."""
    from power_atlas.status_classifier import SemanticStatus
    import tempfile, os
    # Create a temp file with recent mtime to pass the recency gate
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    mock_resolve.return_value = tmp.name
    try:
        # Snapshot: process running in /w but no session id matched (no --resume-id)
        snap = _snapshot(live_cwds={("claude-code", _normalize_path("/w"))})
        s = _make_session(session_id="sess1", cwd="/w", updated_at=_recent_iso())

        # Semantic classifier returns WORKING
        mock_semantic.return_value = SemanticStatus.WORKING
        assert _session_status(snap, s, "claude-code") == "working"

        # Semantic classifier returns WAITING
        mock_semantic.return_value = SemanticStatus.WAITING
        assert _session_status(snap, s, "claude-code") == "waiting"

        # Semantic classifier returns None + live process → fallback to "working"
        mock_semantic.return_value = None
        assert _session_status(snap, s, "claude-code") == "working"
    finally:
        os.unlink(tmp.name)

    # Different provider → no process in cwd → closed
    snap_kiro = _snapshot(live_cwds={("kiro-cli", _normalize_path("/w"))})
    assert _session_status(snap_kiro, s, "claude-code") == "closed"


def test_workspace_status_cwd_and_provider_scoped():
    snap = _snapshot(live_cwds={("claude-code", _normalize_path("/w"))})
    assert _workspace_status(snap, "/w", {"claude-code"}) == "working"
    # provider filter excludes the live claude process
    assert _workspace_status(snap, "/w", {"kiro-cli"}) == "closed"
    # different folder is closed
    assert _workspace_status(snap, "/other", None) == "closed"


def _tracked_snapshot(reported):
    """Snapshot with one tracked claude-code session in /w reporting `reported`."""
    norm = _normalize_path("/w")
    return _snapshot(
        live_sids={("claude-code", "s1")},
        live_cwds={("claude-code", norm)},
        sid_to_cwd={("claude-code", "s1"): norm},
        sid_status={("claude-code", "s1"): reported},
    )


@patch("power_atlas.web.get_semantic_status")
def test_session_status_report_outranks_classifier(mock_semantic):
    """A non-empty report is first-hand and current, so it beats the tail read.

    Asserted here rather than on the card, where "busy"/"shell" map onto the
    same "working" the aggregate already seeds itself with and so could not
    tell a folded-in report from an ignored one.
    """
    from power_atlas.status_classifier import SemanticStatus
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.ERRORED

    # busy/shell mean a turn is running, so even an errored tail loses.
    assert _session_status(_tracked_snapshot("busy"), s, "claude-code") == "working"
    assert _session_status(_tracked_snapshot("shell"), s, "claude-code") == "working"
    # "idle" carries no verdict, and no report at all carries none either:
    # both leave the classifier in charge.
    assert _session_status(_tracked_snapshot("idle"), s, "claude-code") == "errored"
    assert _session_status(_tracked_snapshot(""), s, "claude-code") == "errored"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_reads_provider_report(mock_semantic, mock_sessions):
    """The card dot honours the provider's own report, like per-session status."""
    mock_sessions.return_value = []
    mock_semantic.return_value = None

    # Sole signal is the report: an unclassifiable tail still shows waiting.
    assert _workspace_status(_tracked_snapshot("waiting"), "/w",
                             {"claude-code"}) == "waiting"
    # "idle" is ambiguous (finished/errored/never-started) — never waiting.
    assert _workspace_status(_tracked_snapshot("idle"), "/w",
                             {"claude-code"}) == "working"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_settles_sessions_before_aggregating(
        mock_semantic, mock_sessions):
    """The card settles each session the way its row does, then aggregates.

    A tail that lags an in-flight turn must not outrank the provider's own
    "busy" — that is what painted a card "waiting" above a row the very same
    signals had settled as "working". "Errored" is the carve-out: the classifier
    is its only source, so it survives a "busy", and the row honours it too.
    """
    from power_atlas.status_classifier import SemanticStatus
    mock_sessions.return_value = []

    mock_semantic.return_value = SemanticStatus.ERRORED
    assert _workspace_status(_tracked_snapshot("busy"), "/w",
                             {"claude-code"}) == "errored"
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _workspace_status(_tracked_snapshot("busy"), "/w",
                             {"claude-code"}) == "working"
    # ...and a report still settles a session the classifier reads as working.
    mock_semantic.return_value = SemanticStatus.WORKING
    assert _workspace_status(_tracked_snapshot("waiting"), "/w",
                             {"claude-code"}) == "waiting"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_aggregates_across_sessions(mock_semantic, mock_sessions):
    """Settling per session does not flatten the card onto one session's answer."""
    from power_atlas.status_classifier import SemanticStatus
    mock_sessions.return_value = []
    norm = _normalize_path("/w")
    snap = _snapshot(
        live_sids={("claude-code", "s1"), ("claude-code", "s2")},
        live_cwds={("claude-code", norm)},
        sid_to_cwd={("claude-code", "s1"): norm, ("claude-code", "s2"): norm},
        sid_status={("claude-code", "s1"): "busy"},
    )
    # s1 is mid-turn and says so first-hand; s2 has finished and needs the user.
    mock_semantic.side_effect = lambda sid, prov, c: (
        SemanticStatus.WAITING if sid == "s2" else SemanticStatus.WORKING)
    assert _workspace_status(snap, "/w", {"claude-code"}) == "waiting"


@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.status_classifier._resolve_jsonl_path")
@patch("power_atlas.web.get_semantic_status")
def test_workspace_status_fallback_reads_provider_report(
        mock_semantic, mock_resolve, mock_sessions):
    """The no-resume-id fallback reads the report too (same live session ids)."""
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
    tmp.close()
    mock_resolve.return_value = tmp.name
    norm = _normalize_path("/w")
    mock_sessions.return_value = [
        _make_session(session_id="s9", cwd="/w", updated_at=_recent_iso())]
    mock_semantic.return_value = None
    try:
        # No sid_to_cwd entry, so only the fallback scan can see this session.
        waiting = _snapshot(
            live_cwds={("claude-code", norm)},
            sid_status={("claude-code", "s9"): "waiting"})
        assert _workspace_status(waiting, "/w", {"claude-code"}) == "waiting"

        idle = _snapshot(
            live_cwds={("claude-code", norm)},
            sid_status={("claude-code", "s9"): "idle"})
        assert _workspace_status(idle, "/w", {"claude-code"}) == "working"
    finally:
        os.unlink(tmp.name)


def test_classify_kiro_v3_working():
    """v3 classifier returns WORKING for tool_call and user messages."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    # tool_call → working
    lines_tool = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_call","toolName":"read_file","args":{},"status":"completed","kind":"read"}}',
    ]
    assert classify_kiro_v3(lines_tool) == SemanticStatus.WORKING

    # user message → working
    lines_user = [
        '{"id":"2","timestamp":"2026-07-20T10:01:00Z","payload":{"type":"user","content":"fix the bug"}}',
    ]
    assert classify_kiro_v3(lines_user) == SemanticStatus.WORKING

    # tool_result (skipped) then tool_call → working
    lines_mixed = [
        '{"id":"3","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_result","content":"file contents","success":true}}',
        '{"id":"4","timestamp":"2026-07-20T10:01:00Z","payload":{"type":"tool_call","toolName":"fs_write","args":{},"status":"completed","kind":"edit"}}',
    ]
    assert classify_kiro_v3(lines_mixed) == SemanticStatus.WORKING


def test_classify_kiro_v3_waiting():
    """v3 classifier returns WAITING for assistant messages."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    # assistant → waiting
    lines = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_result","content":"ok","success":true}}',
        '{"id":"2","timestamp":"2026-07-20T10:01:00Z","payload":{"type":"assistant","content":"Done. The file has been updated."}}',
    ]
    assert classify_kiro_v3(lines) == SemanticStatus.WAITING


def test_classify_kiro_v3_errored():
    """v3 classifier returns ERRORED when multiple recent tool_results fail."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    # Multiple failed tool_results → errored
    lines = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"tool_result","content":"error","success":false}}',
        '{"id":"2","timestamp":"2026-07-20T10:00:01Z","payload":{"type":"tool_result","content":"error","success":false}}',
        '{"id":"3","timestamp":"2026-07-20T10:00:02Z","payload":{"type":"assistant","content":"Failed."}}',
    ]
    assert classify_kiro_v3(lines) == SemanticStatus.ERRORED


def test_classify_kiro_v3_skips_noise():
    """v3 classifier skips non-meaningful message types."""
    from power_atlas.status_classifier import classify_kiro_v3, SemanticStatus
    lines = [
        '{"id":"1","timestamp":"2026-07-20T10:00:00Z","payload":{"type":"user","content":"hello"}}',
        '{"id":"2","timestamp":"2026-07-20T10:00:01Z","payload":{"type":"usage_summary","elapsedTime":5,"status":"ok"}}',
        '{"id":"3","timestamp":"2026-07-20T10:00:02Z","payload":{"type":"session_metadata","key":"ctx","value":"100"}}',
    ]
    # Should skip usage_summary and session_metadata, find user → working
    assert classify_kiro_v3(lines) == SemanticStatus.WORKING


@patch("power_atlas.web.get_semantic_status")
@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_all_sessions_paginated")
@patch("power_atlas.web.load_config")
def test_all_sessions_dot_and_status_filter(mock_config, mock_paginated, mock_snap, mock_semantic, client, tmp_path):
    from power_atlas.config import Config
    from power_atlas.status_classifier import SemanticStatus
    mock_config.return_value = Config()
    ws = str(tmp_path)
    live_s = _make_session(session_id="live1", cwd=ws, updated_at=_recent_iso())
    dead_s = _make_session(session_id="dead1", cwd=ws, updated_at=_recent_iso())
    mock_paginated.return_value = ([(live_s, "claude-code"), (dead_s, "claude-code")], False)
    mock_snap.return_value = _snapshot(live_sids={("claude-code", "live1")})
    mock_semantic.return_value = SemanticStatus.WORKING

    # No filter: both rows render; dot rendering depends on template (Phase 4).
    resp = client.get("/partials/all-sessions?page=1")
    assert resp.status_code == 200
    assert 'data-sid="live1"' in resp.text and 'data-sid="dead1"' in resp.text

    # status=live keeps only the live row.
    resp2 = client.get("/partials/all-sessions?page=1&status=live")
    assert 'data-sid="live1"' in resp2.text
    assert 'data-sid="dead1"' not in resp2.text

    # status=closed keeps only the non-live row.
    resp3 = client.get("/partials/all-sessions?page=1&status=closed")
    assert 'data-sid="dead1"' in resp3.text
    assert 'data-sid="live1"' not in resp3.text


@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspaces_status_filter_hides_dead_folders(mock_discover, mock_providers, mock_sessions, mock_snap, client, tmp_path):
    live_dir = tmp_path / "liveproj"; live_dir.mkdir()
    dead_dir = tmp_path / "deadproj"; dead_dir.mkdir()
    recent = _recent_iso()
    mock_discover.return_value = [
        (str(live_dir), 1, recent, "claude-code"),
        (str(dead_dir), 1, recent, "claude-code"),
    ]
    mock_providers.return_value = ["claude-code"]
    mock_sessions.return_value = [_make_session(cwd=str(live_dir))]
    mock_snap.return_value = _snapshot(live_cwds={("claude-code", _normalize_path(str(live_dir)))})

    resp = client.get("/partials/workspaces?status=live")
    assert resp.status_code == 200
    assert "liveproj" in resp.text
    assert "deadproj" not in resp.text


@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_all_sessions_paginated")
@patch("power_atlas.web.load_config")
def test_all_sessions_status_empty_state_escapes_input(mock_config, mock_paginated, mock_snap, client):
    from power_atlas.config import Config
    mock_config.return_value = Config()
    mock_paginated.return_value = ([], False)
    mock_snap.return_value = _snapshot()
    resp = client.get("/partials/all-sessions", params={"page": 1, "status": "<img src=x onerror=alert(1)>"})
    assert resp.status_code == 200
    assert "<img src=x" not in resp.text          # raw tag must not be reflected
    assert "&lt;img" in resp.text                  # escaped instead


@patch("power_atlas.web.presence.get_snapshot")
@patch("power_atlas.web.data.get_sessions")
@patch("power_atlas.web.data.available_providers")
@patch("power_atlas.web.data.discover_workspaces_with_counts")
def test_workspaces_status_empty_state_escapes_input(mock_discover, mock_providers, mock_sessions, mock_snap, client, tmp_path):
    d = tmp_path / "proj"; d.mkdir()
    mock_discover.return_value = [(str(d), 1, _recent_iso(), "claude-code")]
    mock_providers.return_value = ["claude-code"]
    mock_sessions.return_value = [_make_session(cwd=str(d))]
    mock_snap.return_value = _snapshot()  # nothing live -> status=working filters all out
    resp = client.get("/partials/workspaces", params={"status": "<b>x</b>"})
    assert resp.status_code == 200
    assert "<b>x</b>" not in resp.text
    assert "&lt;b&gt;x" in resp.text


# --- Status classifier tests ---

import json as _json
import time as _time
from unittest.mock import patch as _patch, MagicMock

from power_atlas.status_classifier import (
    SemanticStatus,
    _read_tail_lines,
    _resolve_jsonl_path,
    _status_cache,
    classify_claude,
    classify_kiro_v2,
    classify_kiro_v3,
    get_semantic_status,
)


class TestSemanticStatusEnum:
    def test_enum_has_four_values(self):
        assert len(SemanticStatus) == 4
        assert SemanticStatus.WORKING == "working"
        assert SemanticStatus.WAITING == "waiting"
        assert SemanticStatus.ERRORED == "errored"
        assert SemanticStatus.CLOSED == "closed"


class TestResolveJsonlPath:
    def test_kiro_v2_returns_none_for_nonexistent(self, tmp_path):
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path / "v3_empty"):
            result = _resolve_jsonl_path("nonexistent-id", "kiro-cli", "C:\\proj")
            assert result is None

    def test_kiro_v2_returns_path_when_exists(self, tmp_path):
        session_file = tmp_path / "my-session.jsonl"
        session_file.write_text("")
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path):
            result = _resolve_jsonl_path("my-session", "kiro-cli", "C:\\proj")
            assert result == session_file

    def test_claude_code_returns_none_when_no_project_folder(self):
        with _patch("power_atlas.status_classifier._get_project_folder", return_value=None):
            result = _resolve_jsonl_path("sess-1", "claude-code", "C:\\proj")
            assert result is None

    def test_claude_code_returns_path_when_exists(self, tmp_path):
        session_file = tmp_path / "sess-1.jsonl"
        session_file.write_text("")
        with _patch("power_atlas.status_classifier._get_project_folder", return_value=tmp_path):
            result = _resolve_jsonl_path("sess-1", "claude-code", "C:\\proj")
            assert result == session_file

    def test_unknown_provider_returns_none(self):
        result = _resolve_jsonl_path("sess-1", "unknown-provider", "C:\\proj")
        assert result is None

    def test_kiro_v3_returns_path_when_exists(self, tmp_path):
        """v3 session found under sessions/<hash>/sess_<id>/messages.jsonl."""
        ws_hash_dir = tmp_path / "abc123"
        ws_hash_dir.mkdir()
        sess_dir = ws_hash_dir / "sess_my-session"
        sess_dir.mkdir()
        messages_file = sess_dir / "messages.jsonl"
        messages_file.write_text("")
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            result = _resolve_jsonl_path("my-session", "kiro-cli", "C:\\proj")
            assert result == messages_file

    def test_kiro_v3_with_sess_prefix(self, tmp_path):
        """v3 session_id already has sess_ prefix."""
        ws_hash_dir = tmp_path / "abc123"
        ws_hash_dir.mkdir()
        sess_dir = ws_hash_dir / "sess_uuid-here"
        sess_dir.mkdir()
        messages_file = sess_dir / "messages.jsonl"
        messages_file.write_text("")
        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            result = _resolve_jsonl_path("sess_uuid-here", "kiro-cli", "C:\\proj")
            assert result == messages_file


class TestReadTailLines:
    def test_small_file_returns_all_lines(self, tmp_path):
        f = tmp_path / "small.jsonl"
        f.write_text("line1\nline2\nline3\n")
        lines = _read_tail_lines(f, max_bytes=4096)
        assert lines == ["line1", "line2", "line3"]

    def test_large_file_discards_first_partial_line(self, tmp_path):
        f = tmp_path / "large.jsonl"
        # Write content larger than max_bytes so seek happens
        padding = "A" * 100 + "\n"  # 101 bytes per line
        # Write 50 lines = 5050 bytes (> 4096)
        content = padding * 50 + "last_complete_line\n"
        f.write_text(content)
        lines = _read_tail_lines(f, max_bytes=200)
        # First line should NOT be a partial 'AAA...' line
        # It should start with a complete line
        assert lines[0].startswith("A" * 100) or lines[0] == "last_complete_line"
        # The key invariant: we don't get a partial line at the start
        # (the seek lands mid-line, that partial is discarded)
        for line in lines:
            # Every line is either full padding or the last line
            assert line == "A" * 100 or line == "last_complete_line"

    def test_exact_boundary_no_discard(self, tmp_path):
        """When file is exactly max_bytes, no seek past start → no discard."""
        f = tmp_path / "exact.jsonl"
        content = "line1\nline2\n"
        f.write_bytes(content.encode("utf-8"))
        lines = _read_tail_lines(f, max_bytes=len(content.encode("utf-8")) + 1)
        assert "line1" in lines
        assert "line2" in lines


class TestClassifyKiroV2:
    def _make_line(self, kind, data=None):
        return _json.dumps({"version": "v1", "kind": kind, "data": data or {}})

    def test_prompt_returns_active(self):
        lines = [self._make_line("Prompt", {"content": "hello"})]
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING

    def test_tool_results_returns_active(self):
        lines = [self._make_line("ToolResults", {"results": []})]
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING

    def test_assistant_with_tool_use_returns_active(self):
        data = {"content": [{"kind": "toolUse", "data": {"name": "fs_read"}}]}
        lines = [self._make_line("AssistantMessage", data)]
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING

    def test_assistant_without_tool_use_returns_idle(self):
        data = {"content": [{"kind": "text", "data": {"text": "Done."}}]}
        lines = [self._make_line("AssistantMessage", data)]
        assert classify_kiro_v2(lines) == SemanticStatus.WAITING

    def test_last_message_wins_reverse_order(self):
        """When multiple messages exist, the last (most recent) one determines status."""
        lines = [
            self._make_line("Prompt", {"content": "hello"}),
            self._make_line("AssistantMessage", {"content": [{"kind": "text", "data": {}}]}),
        ]
        # Last line is AssistantMessage without toolUse → IDLE
        assert classify_kiro_v2(lines) == SemanticStatus.WAITING

    def test_empty_lines_returns_none(self):
        assert classify_kiro_v2([]) is None

    def test_invalid_json_returns_none(self):
        assert classify_kiro_v2(["not json at all", "{broken"]) is None

    def test_skips_unparseable_finds_valid(self):
        """Skips garbage lines and classifies from the last valid one."""
        lines = [
            self._make_line("Prompt", {"content": "hello"}),
            "garbage line",
        ]
        # Reverse walk: skip garbage, find Prompt → ACTIVE
        assert classify_kiro_v2(lines) == SemanticStatus.WORKING


class TestClassifyClaude:
    """Live transcripts nest the payload under ``message`` and carry the API
    response's ``stop_reason``; the flat shapes are the legacy envelope."""

    @staticmethod
    def _assistant(blocks, stop_reason):
        return _json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": blocks,
                        "stop_reason": stop_reason},
        })

    @staticmethod
    def _tool_result(is_error=False):
        return _json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error},
            ]},
        })

    # --- live envelope: stop_reason decides --------------------------------

    def test_text_block_mid_turn_returns_working(self):
        """Narration before a tool call, not a finished turn — the shape that
        painted the workspace card orange under a green session row."""
        lines = [self._assistant([{"type": "text", "text": "Now I'll check."}],
                                 "tool_use")]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_tool_use_block_returns_working(self):
        lines = [self._assistant([{"type": "tool_use", "name": "Bash"}], "tool_use")]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_thinking_block_follows_the_turn_not_the_block(self):
        """A turn is split across lines with stop_reason copied onto each, so a
        trailing thinking block is classified by the turn it belongs to."""
        lines = [self._assistant([{"type": "thinking", "thinking": "..."}], "tool_use")]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_end_turn_returns_waiting(self):
        lines = [self._assistant([{"type": "text", "text": "Done."}], "end_turn")]
        assert classify_claude(lines) == SemanticStatus.WAITING

    def test_tool_result_returns_working(self):
        lines = [self._assistant([{"type": "tool_use", "name": "Bash"}], "tool_use"),
                 self._tool_result()]
        assert classify_claude(lines) == SemanticStatus.WORKING

    # --- errored: two failures and a session that stopped -------------------

    def test_two_failures_then_stop_returns_errored(self):
        lines = [self._tool_result(is_error=True), self._tool_result(is_error=True),
                 self._assistant([{"type": "text", "text": "I'm stuck."}], "end_turn")]
        assert classify_claude(lines) == SemanticStatus.ERRORED

    def test_single_failure_is_not_errored(self):
        """One failure is routine — a grep that matches nothing exits non-zero."""
        lines = [self._tool_result(is_error=True),
                 self._assistant([{"type": "text", "text": "No matches."}], "end_turn")]
        assert classify_claude(lines) == SemanticStatus.WAITING

    def test_failures_followed_by_recovery_are_not_errored(self):
        lines = [self._tool_result(is_error=True), self._tool_result(is_error=True),
                 self._assistant([{"type": "tool_use", "name": "Bash"}], "tool_use")]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_bookkeeping_lines_do_not_consume_the_error_window(self):
        """mode/attachment/ai-title lines are noise; were they counted, the
        failures below would fall out of the window and go unreported."""
        noise = [_json.dumps({"type": t}) for t in
                 ("mode", "permission-mode", "attachment", "ai-title", "bridge-session")]
        lines = ([self._tool_result(is_error=True), self._tool_result(is_error=True)]
                 + noise
                 + [self._assistant([{"type": "text", "text": "stuck"}], "end_turn")])
        assert classify_claude(lines) == SemanticStatus.ERRORED

    # --- legacy flat envelope ----------------------------------------------

    def test_user_message_returns_active(self):
        lines = [_json.dumps({"type": "user", "content": "do something"})]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_human_role_returns_active(self):
        """Legacy role-based format with 'human' role."""
        lines = [_json.dumps({"role": "human", "content": "hello"})]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_assistant_without_error_returns_idle(self):
        lines = [_json.dumps({"type": "assistant", "content": "Done."})]
        assert classify_claude(lines) == SemanticStatus.WAITING

    def test_legacy_tool_use_block_without_stop_reason_returns_working(self):
        lines = [_json.dumps({
            "type": "assistant",
            "content": [{"type": "tool_use", "name": "Bash"}],
        })]
        assert classify_claude(lines) == SemanticStatus.WORKING

    def test_assistant_with_is_error_block_returns_errored(self):
        lines = [_json.dumps({
            "type": "assistant",
            "content": [{"type": "text", "text": "failed", "is_error": True}],
        })]
        assert classify_claude(lines) == SemanticStatus.ERRORED

    def test_assistant_with_top_level_is_error_returns_errored(self):
        lines = [_json.dumps({
            "type": "assistant",
            "content": "something failed",
            "is_error": True,
        })]
        assert classify_claude(lines) == SemanticStatus.ERRORED

    def test_empty_lines_returns_none(self):
        assert classify_claude([]) is None

    def test_invalid_json_returns_none(self):
        assert classify_claude(["not json", "{{"]) is None

    def test_last_message_wins(self):
        lines = [
            _json.dumps({"type": "user", "content": "hi"}),
            _json.dumps({"type": "assistant", "content": "Done."}),
        ]
        # Last line is assistant without error → IDLE
        assert classify_claude(lines) == SemanticStatus.WAITING


class TestClassifyKiroV3:
    def test_user_returns_working(self):
        lines = [_json.dumps({"id": "x", "timestamp": "2026-01-01", "payload": {"type": "user", "content": "hello"}})]
        assert classify_kiro_v3(lines) == SemanticStatus.WORKING

    def test_tool_call_returns_working(self):
        lines = [_json.dumps({"id": "x", "timestamp": "2026-01-01", "payload": {"type": "tool_call", "toolName": "read_file", "args": {}, "status": "completed", "kind": "read"}})]
        assert classify_kiro_v3(lines) == SemanticStatus.WORKING

    def test_assistant_returns_waiting(self):
        lines = [_json.dumps({"id": "x", "timestamp": "2026-01-01", "payload": {"type": "assistant", "content": "Done."}})]
        assert classify_kiro_v3(lines) == SemanticStatus.WAITING

    def test_empty_returns_none(self):
        assert classify_kiro_v3([]) is None

    def test_skips_tool_result_finds_assistant(self):
        lines = [
            _json.dumps({"id": "1", "timestamp": "2026-01-01", "payload": {"type": "assistant", "content": "ok"}}),
            _json.dumps({"id": "2", "timestamp": "2026-01-01", "payload": {"type": "tool_result", "content": "output", "success": True}}),
        ]
        # tool_result is skipped; last meaningful is assistant → waiting
        assert classify_kiro_v3(lines) == SemanticStatus.WAITING


class TestGetSemanticStatus:
    def setup_method(self):
        """Clear cache between tests."""
        _status_cache.clear()

    def test_returns_none_for_missing_file(self):
        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=None):
            result = get_semantic_status("sess-1", "kiro-cli", "C:\\proj")
            assert result is None

    def test_caches_result_and_returns_on_same_mtime(self, tmp_path):
        session_file = tmp_path / "sess-1.jsonl"
        line = _json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "hi"}})
        session_file.write_text(line + "\n")

        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=session_file):
            # First call — classifies
            result1 = get_semantic_status("sess-1", "kiro-cli", "C:\\proj")
            assert result1 == SemanticStatus.WORKING

            # Second call — hits cache (same mtime)
            result2 = get_semantic_status("sess-1", "kiro-cli", "C:\\proj")
            assert result2 == SemanticStatus.WORKING

            # Verify cache was populated
            assert ("kiro-cli", "sess-1") in _status_cache

    def test_cache_invalidated_on_mtime_change(self, tmp_path):
        session_file = tmp_path / "sess-2.jsonl"
        # Write initial content -> ACTIVE
        line_active = _json.dumps({"version": "v1", "kind": "Prompt", "data": {"content": "hi"}})
        session_file.write_text(line_active + "\n")

        # Use a controlled monotonic clock so we can expire the TTL
        call_count = [0]
        base_time = 1000.0

        def fake_monotonic():
            # First few calls: time=1000 (within TTL)
            # After we bump: time=1010 (beyond 5s TTL)
            return base_time + (10.0 if call_count[0] > 2 else 0.0)

        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=session_file), \
             _patch("power_atlas.status_classifier.time.monotonic", side_effect=fake_monotonic):
            result1 = get_semantic_status("sess-2", "kiro-cli", "C:\\proj")
            assert result1 == SemanticStatus.WORKING

            # Write new content (IDLE) and force a different mtime
            line_idle = _json.dumps({
                "version": "v1", "kind": "AssistantMessage",
                "data": {"content": [{"kind": "text", "data": {"text": "Done."}}]},
            })
            session_file.write_text(line_idle + "\n")
            import os as _os
            stat = _os.stat(session_file)
            _os.utime(session_file, (stat.st_atime, stat.st_mtime + 10))

            # Advance the clock beyond TTL
            call_count[0] = 10

            # Next call: TTL expired + mtime changed -> reclassifies
            result2 = get_semantic_status("sess-2", "kiro-cli", "C:\\proj")
            assert result2 == SemanticStatus.WAITING

    def test_returns_none_on_stat_failure(self, tmp_path):
        fake_path = tmp_path / "gone.jsonl"
        # File doesn't exist, but _resolve_jsonl_path returns it
        # (simulates race condition)
        with _patch("power_atlas.status_classifier._resolve_jsonl_path", return_value=fake_path):
            result = get_semantic_status("sess-3", "kiro-cli", "C:\\proj")
            assert result is None

    def test_never_raises(self):
        """get_semantic_status swallows all exceptions."""
        with _patch("power_atlas.status_classifier._resolve_jsonl_path", side_effect=RuntimeError("boom")):
            result = get_semantic_status("sess-4", "kiro-cli", "C:\\proj")
            assert result is None


# --- Toast notification tests ---


class TestNotifications:
    """Tests for the notifications module (session status transition toasts)."""

    def setup_method(self):
        """Reset module state between tests."""
        from power_atlas import notifications
        notifications._session_states.clear()
        notifications._initialized = False

    def test_notification_transition_fires(self):
        """Working→Waiting triggers a toast notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        # Establish working state
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        # Transition to waiting should fire
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once_with("My Session", "waiting")

    def test_notification_working_to_waiting_fires(self):
        """Working→Waiting triggers a toast notification (explicit)."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once_with("My Session", "waiting")

    def test_notification_working_to_errored_fires(self):
        """Working→Errored triggers a toast notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "errored", True)
            mock_fire.assert_called_once_with("My Session", "errored")

    def test_notification_non_active_transition_does_not_fire(self):
        """Waiting→Closed does NOT trigger a notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "waiting", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "closed", True)
            mock_fire.assert_not_called()

    def test_notification_working_to_closed_does_not_fire(self):
        """Working→Closed does NOT trigger a notification."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-2", "Another Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-2", "Another Session", "closed", True)
            mock_fire.assert_not_called()

    def test_notification_cooldown(self):
        """Second transition within 60s is suppressed."""
        from power_atlas import notifications
        notifications.mark_initialized()
        # First: working → waiting (fires)
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once()
        # Go back to working then waiting again — within cooldown
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_not_called()  # suppressed by cooldown

    def test_notification_cooldown_expires(self):
        """After cooldown expires, notification fires again."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast"):
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
        # Manually expire the cooldown
        state = notifications._session_states["sess-1"]
        state.last_notified_at -= 61.0
        # Go back to working then waiting
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once()

    def test_notification_disabled(self):
        """enabled=False prevents all notifications."""
        from power_atlas import notifications
        notifications.mark_initialized()
        notifications.check_and_notify("sess-1", "My Session", "working", False)
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", False)
            mock_fire.assert_not_called()

    def test_notification_startup_no_fire(self):
        """Before mark_initialized(), no notifications fire."""
        from power_atlas import notifications
        # Do NOT call mark_initialized
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "working", True)
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_not_called()

    def test_notification_startup_tracks_state(self):
        """Before mark_initialized(), state IS tracked (for baseline)."""
        from power_atlas import notifications
        # Establish state before init
        notifications.check_and_notify("sess-1", "My Session", "working", True)
        # Now initialize
        notifications.mark_initialized()
        # Transition should work since state was already working
        with patch("power_atlas.notifications._fire_toast") as mock_fire:
            notifications.check_and_notify("sess-1", "My Session", "waiting", True)
            mock_fire.assert_called_once_with("My Session", "waiting")

    def test_notification_state_bounded(self):
        """>100 entries triggers LRU eviction of oldest."""
        from power_atlas import notifications
        notifications.mark_initialized()
        # Add 100 entries
        for i in range(100):
            notifications.check_and_notify(f"sess-{i}", f"Session {i}", "working", True)
        assert len(notifications._session_states) == 100
        assert "sess-0" in notifications._session_states
        # Add one more — should evict sess-0
        notifications.check_and_notify("sess-100", "Session 100", "working", True)
        assert len(notifications._session_states) == 100
        assert "sess-0" not in notifications._session_states
        assert "sess-100" in notifications._session_states


# --- POST /api/session-status (lightweight status polling) ---


class TestApiSessionStatus:
    """Tests for the lightweight POST /api/session-status endpoint."""

    @patch("power_atlas.web.presence.get_snapshot")
    def test_empty_cwds_returns_empty_maps(self, mock_snap, client):
        """Empty cwds list returns empty response immediately."""
        resp = client.post(
            "/api/session-status", json={"cwds": []},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"sessions": {}, "workspaces": {}, "active_cwds": []}
        # Snapshot never called for empty input
        mock_snap.assert_not_called()

    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_inactive_cwd_short_circuits(self, mock_snap, mock_cache, client):
        """CWDs without a live process return 'closed' without iterating sessions."""
        from power_atlas.presence import Snapshot
        # Snapshot with no live processes
        mock_snap.return_value = Snapshot(set(), set(), {})

        resp = client.post(
            "/api/session-status",
            json={"cwds": ["C:\\projects\\myapp", "C:\\other"]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspaces"]["C:\\projects\\myapp"] == "closed"
        assert body["workspaces"]["C:\\other"] == "closed"
        assert body["active_cwds"] == []
        assert body["sessions"] == {}
        # session_cache.get should NOT be called (short-circuit)
        mock_cache.get.assert_not_called()

    @patch("power_atlas.web.notifications.check_and_notify")
    @patch("power_atlas.web.get_semantic_status")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_active_cwd_returns_session_status_no_notifications(
        self, mock_snap, mock_cache, mock_semantic, mock_notify, client
    ):
        """Active CWDs return per-session status; notifications are NOT triggered."""
        from power_atlas.presence import Snapshot
        from power_atlas.data import _normalize_path

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)

        # Snapshot shows kiro-cli running in this cwd
        mock_snap.return_value = Snapshot(
            live_sids={("kiro-cli", "sess-1")},
            live_cwds={("kiro-cli", norm_cwd)},
            sid_to_cwd={("kiro-cli", "sess-1"): norm_cwd},
        )

        # Session cache returns one session
        sess = _make_session(session_id="sess-1", cwd=cwd)
        mock_cache.get.return_value = [sess]

        # Semantic status returns WAITING
        from power_atlas.status_classifier import SemanticStatus
        mock_semantic.return_value = SemanticStatus.WAITING

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()

        # Session status returned
        assert body["sessions"]["sess-1"] == "waiting"
        # Workspace-level status
        assert body["workspaces"][cwd] == "waiting"
        # Active cwds includes this one
        assert cwd in body["active_cwds"]
        # Notifications NOT triggered
        mock_notify.assert_not_called()

    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_skips_kiro_ide_provider(self, mock_snap, mock_cache, client):
        """kiro-ide sessions are never processed by this endpoint."""
        from power_atlas.presence import Snapshot
        from power_atlas.data import _normalize_path

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)

        # Only kiro-ide is running in this cwd — endpoint should see no active process
        mock_snap.return_value = Snapshot(
            live_sids=set(),
            live_cwds={("kiro-ide", norm_cwd)},
            sid_to_cwd={},
        )

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()

        # CWD treated as inactive (kiro-ide excluded from poll_providers)
        assert body["workspaces"][cwd] == "closed"
        assert body["active_cwds"] == []
        # session_cache never accessed
        mock_cache.get.assert_not_called()

    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_mixed_active_inactive_cwds(self, mock_snap, mock_cache, client):
        """Mix of active and inactive cwds: only active are iterated."""
        from power_atlas.presence import Snapshot
        from power_atlas.data import _normalize_path

        active_cwd = "C:\\projects\\active"
        inactive_cwd = "C:\\projects\\idle"
        norm_active = _normalize_path(active_cwd)

        mock_snap.return_value = Snapshot(
            live_sids=set(),
            live_cwds={("claude-code", norm_active)},
            sid_to_cwd={},
        )
        # Cache returns None for both (no cached sessions)
        mock_cache.get.return_value = None

        resp = client.post(
            "/api/session-status",
            json={"cwds": [active_cwd, inactive_cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()

        assert body["workspaces"][inactive_cwd] == "closed"
        assert active_cwd in body["active_cwds"]
        assert inactive_cwd not in body["active_cwds"]

    @patch("power_atlas.web.get_semantic_status")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_workspace_dot_comes_from_workspace_status(
        self, mock_snap, mock_cache, mock_semantic, client
    ):
        """Card dot is _workspace_status, so a poll cannot contradict a render.

        Divergence case: nothing is cached for the cwd, so the old per-session
        aggregation could only ever answer "working"; _workspace_status reads
        the tracked session id and reports the classifier's verdict instead.
        """
        from power_atlas.status_classifier import SemanticStatus

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)
        snap = _snapshot(
            live_sids={("kiro-cli", "sess-9")},
            live_cwds={("kiro-cli", norm_cwd)},
            sid_to_cwd={("kiro-cli", "sess-9"): norm_cwd},
        )
        mock_snap.return_value = snap
        mock_cache.get.return_value = None
        mock_semantic.return_value = SemanticStatus.ERRORED

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspaces"][cwd] == "errored"
        assert body["sessions"] == {}   # nothing cached -> no per-row dots
        # Same answer the render path would have produced for this cwd. An
        # unfiltered page renders with providers=None, so that is what this
        # compares against — not the {kiro-cli, claude-code} set the poll
        # happens to build for "all".
        assert body["workspaces"][cwd] == _workspace_status(snap, cwd, None)

    @patch("power_atlas.status_classifier._resolve_jsonl_path")
    @patch("power_atlas.web.get_semantic_status")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_card_may_outrank_a_cached_untracked_row(
        self, mock_snap, mock_cache, mock_semantic, mock_resolve, client, tmp_path
    ):
        """The card answers for the session presence proved live, not for every row.

        A workspace holding a tracked session (working) plus a cached,
        untracked, recently-written one (waiting) shows a working card over a
        waiting row. That is deliberate: the untracked session's process is not
        the live one, and this is the answer a full render has always given —
        the poll now matches it rather than aggregating the rows itself.
        """
        from power_atlas.status_classifier import SemanticStatus

        jsonl = tmp_path / "s2.jsonl"
        jsonl.write_text("")
        mock_resolve.return_value = str(jsonl)

        cwd = "C:\\projects\\myapp"
        norm_cwd = _normalize_path(cwd)
        snap = _snapshot(
            live_sids={("claude-code", "s1")},
            live_cwds={("claude-code", norm_cwd)},
            sid_to_cwd={("claude-code", "s1"): norm_cwd},
        )
        mock_snap.return_value = snap
        s2 = _make_session(session_id="s2", cwd=cwd, updated_at=_recent_iso())
        mock_cache.get.side_effect = lambda c, p: [s2] if p == "claude-code" else None
        mock_semantic.side_effect = lambda sid, prov, c: (
            SemanticStatus.WORKING if sid == "s1" else SemanticStatus.WAITING)

        resp = client.post(
            "/api/session-status",
            json={"cwds": [cwd]},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["workspaces"][cwd] == "working"
        assert body["sessions"]["s2"] == "waiting"
        # No render/poll divergence: the render path says the same thing, and
        # it asks with providers=None on an unfiltered page.
        assert body["workspaces"][cwd] == _workspace_status(snap, cwd, None)

    @patch("power_atlas.web.data.get_sessions")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_provider_filter_travels_with_the_poll(
        self, mock_snap, mock_cache, mock_sessions, client
    ):
        """A provider-filtered page polls about that provider only."""
        cwd = "C:\\projects\\myapp"
        mock_snap.return_value = _snapshot(
            live_cwds={("claude-code", _normalize_path(cwd))})
        mock_cache.get.return_value = None
        mock_sessions.return_value = []

        def poll(payload):
            resp = client.post("/api/session-status", json=payload,
                               headers={"Origin": "http://127.0.0.1"})
            assert resp.status_code == 200
            return resp.json()["workspaces"][cwd]

        # Only claude-code is running, so a kiro-cli-filtered page must not
        # light this card up — which is what the render path shows it.
        assert poll({"cwds": [cwd], "provider": "kiro-cli"}) == "closed"
        assert poll({"cwds": [cwd], "provider": "claude-code"}) == "working"
        # "all" and a client that sends no provider both see every CLI provider.
        assert poll({"cwds": [cwd], "provider": "all"}) == "working"
        assert poll({"cwds": [cwd]}) == "working"

    @patch("power_atlas.web.data.get_sessions")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_response_keeps_request_order(
        self, mock_snap, mock_cache, mock_sessions, client
    ):
        """Both maps echo the order the client asked in, live cwds included."""
        cwds = ["C:\\projects\\ccc", "C:\\projects\\aaa", "C:\\projects\\bbb"]
        mock_snap.return_value = _snapshot(live_cwds={
            ("claude-code", _normalize_path(c)) for c in cwds})
        mock_cache.get.return_value = None
        mock_sessions.return_value = []

        resp = client.post("/api/session-status", json={"cwds": cwds},
                           headers={"Origin": "http://127.0.0.1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_cwds"] == cwds
        assert list(body["workspaces"]) == cwds

    @patch("power_atlas.web.data.get_sessions")
    @patch("power_atlas.web.data.session_cache")
    @patch("power_atlas.web.presence.get_snapshot")
    def test_non_string_provider_does_not_500(
        self, mock_snap, mock_cache, mock_sessions, client
    ):
        """A provider of the wrong JSON shape degrades to "all", not a 500.

        The value reaches a set literal, so an array or object would raise
        TypeError: unhashable type outside the per-cwd guard and take the
        whole endpoint down.
        """
        cwd = "C:\\projects\\myapp"
        mock_snap.return_value = _snapshot(
            live_cwds={("claude-code", _normalize_path(cwd))})
        mock_cache.get.return_value = None
        mock_sessions.return_value = []

        for bogus in ([], {}, ["kiro-cli"], {"name": "kiro-cli"}, 7, True, None):
            resp = client.post("/api/session-status",
                               json={"cwds": [cwd], "provider": bogus},
                               headers={"Origin": "http://127.0.0.1"})
            assert resp.status_code == 200, bogus
            assert resp.json()["workspaces"][cwd] == "working", bogus

    def test_missing_cwds_key_returns_empty(self, client):
        """Body without 'cwds' key returns empty response without error."""
        resp = client.post(
            "/api/session-status",
            json={},
            headers={"Origin": "http://127.0.0.1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"sessions": {}, "workspaces": {}, "active_cwds": []}


# --- Path/status caching (260725_PARSE_AND_POLL_PERFORMANCE) ----------------


class TestResolveJsonlPathCaching:
    """The kiro-cli v3 fallback scans every workspace dir, so it is memoized."""

    def setup_method(self):
        from power_atlas import status_classifier
        status_classifier._path_cache.clear()

    def test_repeat_lookup_avoids_rescan(self, tmp_path):
        """A second lookup revalidates the cached path instead of walking dirs."""
        ws = tmp_path / "abc123" / "sess_cached-id"
        ws.mkdir(parents=True)
        (ws / "messages.jsonl").write_text("")

        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            first = _resolve_jsonl_path("cached-id", "kiro-cli", "C:\\proj")
            assert first == ws / "messages.jsonl"

            with _patch("power_atlas.status_classifier._resolve_jsonl_path_uncached",
                        side_effect=AssertionError("should not rescan")):
                second = _resolve_jsonl_path("cached-id", "kiro-cli", "C:\\proj")
            assert second == first

    def test_deleted_file_reresolves(self, tmp_path):
        """A cached path whose file disappeared must not be returned."""
        ws = tmp_path / "abc123" / "sess_gone-id"
        ws.mkdir(parents=True)
        target = ws / "messages.jsonl"
        target.write_text("")

        with _patch("power_atlas.status_classifier.SESSION_DIR", tmp_path / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", tmp_path):
            assert _resolve_jsonl_path("gone-id", "kiro-cli", "C:\\proj") == target
            target.unlink()
            assert _resolve_jsonl_path("gone-id", "kiro-cli", "C:\\proj") is None

    def test_different_roots_do_not_collide(self, tmp_path):
        """The cache key carries the roots, so rebinding them re-resolves."""
        root_a = tmp_path / "a"
        (root_a / "h" / "sess_same-id").mkdir(parents=True)
        (root_a / "h" / "sess_same-id" / "messages.jsonl").write_text("")
        root_b = tmp_path / "b"
        (root_b / "h2" / "sess_same-id").mkdir(parents=True)
        (root_b / "h2" / "sess_same-id" / "messages.jsonl").write_text("")

        with _patch("power_atlas.status_classifier.SESSION_DIR", root_a / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", root_a):
            a = _resolve_jsonl_path("same-id", "kiro-cli", "C:\\proj")
        with _patch("power_atlas.status_classifier.SESSION_DIR", root_b / "cli"), \
             _patch("power_atlas.status_classifier._V3_SESSIONS_ROOT", root_b):
            b = _resolve_jsonl_path("same-id", "kiro-cli", "C:\\proj")

        assert a == root_a / "h" / "sess_same-id" / "messages.jsonl"
        assert b == root_b / "h2" / "sess_same-id" / "messages.jsonl"

    def test_claude_code_is_not_cached(self, tmp_path):
        """claude-code resolution is two syscalls — caching it would only add staleness."""
        from power_atlas import status_classifier
        session_file = tmp_path / "cc-id.jsonl"
        session_file.write_text("")
        with _patch("power_atlas.status_classifier._get_project_folder", return_value=tmp_path):
            assert _resolve_jsonl_path("cc-id", "claude-code", "C:\\proj") == session_file
        assert len(status_classifier._path_cache) == 0


class TestStatusCacheLRU:
    """Status cache evicts least-recently-used in O(1) and stays bounded."""

    def test_stays_within_bound(self):
        from power_atlas import status_classifier as sc
        from power_atlas.status_classifier import SemanticStatus
        sc._status_cache.clear()
        for i in range(sc._MAX_CACHE_ENTRIES + 50):
            sc._status_cache[("kiro-cli", f"s{i}")] = (float(i), 0.0, SemanticStatus.WORKING)
            sc._evict_oldest()
        assert len(sc._status_cache) == sc._MAX_CACHE_ENTRIES

    def test_evicts_oldest_first(self):
        from power_atlas import status_classifier as sc
        from power_atlas.status_classifier import SemanticStatus
        sc._status_cache.clear()
        for i in range(sc._MAX_CACHE_ENTRIES):
            sc._status_cache[("kiro-cli", f"s{i}")] = (float(i), 0.0, SemanticStatus.WORKING)
        # Touch the oldest so it is no longer the eviction candidate
        sc._status_cache.move_to_end(("kiro-cli", "s0"))
        sc._status_cache[("kiro-cli", "new")] = (999.0, 0.0, SemanticStatus.WAITING)
        sc._evict_oldest()
        assert ("kiro-cli", "s0") in sc._status_cache
        assert ("kiro-cli", "s1") not in sc._status_cache

    def test_every_cache_operation_holds_the_lock(self, tmp_path, monkeypatch):
        """The status poll reaches this cache from a worker thread while a
        render reaches it from the event loop, so an unguarded
        get/move_to_end/popitem interleaving can raise KeyError — which
        get_semantic_status' blanket except turns into a silently wrong dot.
        Recorded rather than raised, for the same reason.
        """
        from collections import OrderedDict
        from power_atlas import status_classifier as sc

        jsonl = tmp_path / "sess.jsonl"
        jsonl.write_text('{"version":"v1","kind":"Prompt","data":{}}\n')

        class _LockChecked(OrderedDict):
            def __init__(self):
                super().__init__()
                self.unlocked_ops = []

            def _chk(self, op):
                if not sc._status_cache_lock.locked():
                    self.unlocked_ops.append(op)

            def get(self, key, default=None):
                self._chk("get")
                return super().get(key, default)

            def __setitem__(self, key, value):
                self._chk("setitem")
                super().__setitem__(key, value)

            def move_to_end(self, key, last=True):
                self._chk("move_to_end")
                super().move_to_end(key, last=last)

            def popitem(self, last=True):
                self._chk("popitem")
                return super().popitem(last=last)

        cache = _LockChecked()
        monkeypatch.setattr(sc, "_status_cache", cache)
        monkeypatch.setattr(sc, "_MAX_CACHE_ENTRIES", 1)
        monkeypatch.setattr(sc, "_resolve_jsonl_path", lambda sid, prov, cwd: jsonl)

        assert sc.get_semantic_status("s1", "kiro-cli", "/w") is not None  # miss: write
        assert sc.get_semantic_status("s1", "kiro-cli", "/w") is not None  # hit: read
        sc.get_semantic_status("s2", "kiro-cli", "/w")                     # forces eviction
        assert cache.unlocked_ops == []


@patch("power_atlas.web.get_semantic_status")
def test_session_status_prefers_provider_report_for_working(mock_semantic):
    """claude-code declaring a running turn beats an inferred verdict.

    The classifier reads the transcript tail, which lags an in-flight turn
    that has not flushed yet.
    """
    from power_atlas.status_classifier import SemanticStatus
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    for reported in ("busy", "shell"):
        snap = _snapshot(
            live_sids={("claude-code", "s1")},
            sid_status={("claude-code", "s1"): reported},
        )
        mock_semantic.return_value = SemanticStatus.WAITING
        assert _session_status(snap, s, "claude-code") == "working", reported
        # The classifier must not even be consulted for an unambiguous report.
        mock_semantic.return_value = None
        assert _session_status(snap, s, "claude-code") == "working", reported


@patch("power_atlas.web.get_semantic_status")
def test_session_status_reports_waiting_from_provider(mock_semantic):
    """"waiting" means a dialog is open and needs the human."""
    snap = _snapshot(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "waiting"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = None
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_idle_does_not_erase_errored(mock_semantic):
    """"idle" only rules out working — it must never overwrite a richer verdict.

    idle covers finished, errored and never-started alike, and the classifier
    is the only source of "errored".
    """
    from power_atlas.status_classifier import SemanticStatus
    snap = _snapshot(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "idle"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.ERRORED
    assert _session_status(snap, s, "claude-code") == "errored"
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_unknown_report_falls_through(mock_semantic):
    """An unrecognised value must degrade to today's behaviour, not break it.

    The field is undocumented internal state in a frequently-updated binary.
    """
    from power_atlas.status_classifier import SemanticStatus
    snap = _snapshot(
        live_sids={("claude-code", "s1")},
        sid_status={("claude-code", "s1"): "some-future-value"},
    )
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = SemanticStatus.WAITING
    assert _session_status(snap, s, "claude-code") == "waiting"


@patch("power_atlas.web.get_semantic_status")
def test_session_status_report_never_revives_a_closed_session(mock_semantic):
    """A stale report must not make a dead session look alive."""
    snap = _snapshot(sid_status={("claude-code", "s9"): "busy"})
    s = _make_session(session_id="s9", cwd="/w", updated_at=_recent_iso())
    mock_semantic.return_value = None
    assert _session_status(snap, s, "claude-code") == "closed"


# --- Waiting reason: blocked-on-approval vs asked-a-question ---

def _snap_waiting(reason, status="waiting"):
    return presence.Snapshot(
        set(), set(), {},
        {("claude-code", "s1"): status},
        {("claude-code", "s1"): reason},
    )


def test_waiting_detail_separates_approval_from_question():
    from power_atlas.web import _waiting_detail
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    approval = ["permission prompt", "sandbox request", "worker request"]
    for r in approval:
        cat, phrase = _waiting_detail(_snap_waiting(r), s, "claude-code", "waiting")
        assert cat == "approval", r
        assert "approval" in phrase, r
    cat, phrase = _waiting_detail(_snap_waiting("input needed"), s, "claude-code", "waiting")
    assert (cat, phrase) == ("question", "asked you a question")


def test_waiting_detail_passes_through_an_unmapped_reason():
    """A new provider value must surface, not vanish."""
    from power_atlas.web import _waiting_detail
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    assert _waiting_detail(_snap_waiting("some new thing"), s, "claude-code", "waiting") \
        == ("other", "some new thing")


def test_waiting_detail_only_applies_to_waiting_sessions():
    from power_atlas.web import _waiting_detail
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    snap = _snap_waiting("permission prompt", status="busy")
    assert _waiting_detail(snap, s, "claude-code", "working") == ("", "")
    # kiro-cli reports no reason at all; must not raise or invent one.
    bare = presence.Snapshot(set(), set(), {}, {}, {})
    assert _waiting_detail(bare, s, "kiro-cli", "waiting") == ("", "")


def test_session_row_renders_the_waiting_reason():
    from power_atlas.web import templates
    s = _make_session(session_id="s1", cwd="/w", updated_at=_recent_iso())
    tpl = templates.get_template("partials/session_row.html")

    html = tpl.render(request=None, session=s, cwd="/w", stale=False,
                      pinned_sessions=[], provider_name="claude-code",
                      provider_color="", status="waiting",
                      waiting_detail=("approval", "needs your approval"))
    assert 'title="Waiting — needs your approval"' in html
    assert 'data-waiting="approval"' in html

    # Absent detail (kiro-cli, or an older snapshot) keeps the original text.
    plain = tpl.render(request=None, session=s, cwd="/w", stale=False,
                       pinned_sessions=[], provider_name="kiro-cli",
                       provider_color="", status="waiting")
    assert 'title="Waiting — needs your input"' in plain
    assert "data-waiting" not in plain


# --- ACP session lifecycle: activity stamps, inactivity ceiling, sweeper ---


@pytest.fixture
def acp_fast(acp_store):
    """The lifecycle constants rebound small, restored afterwards.

    Every timing behaviour in this section is measured in the real constants'
    units — 900 s of silence, a 4 h ceiling, a 30 min TTL, a 60 s sweep — so a
    test that honoured them would burn 600-900 s of wall clock to assert one
    branch. They are module-level rebindable names for exactly this reason, and
    the ratios between them are preserved: tick << silence << absolute max.
    """
    acp_mod, store = acp_store
    saved = {name: getattr(acp_mod, name) for name in (
        "PROMPT_TICK_SECONDS", "PROMPT_SILENCE_SECONDS",
        "PROMPT_ABSOLUTE_MAX_SECONDS", "CANCEL_GRACE_SECONDS",
        "ACP_IDLE_TTL_SECONDS", "SWEEP_INTERVAL_SECONDS", "MAX_SESSIONS")}
    acp_mod.PROMPT_TICK_SECONDS = 0.01
    acp_mod.PROMPT_SILENCE_SECONDS = 0.08
    acp_mod.PROMPT_ABSOLUTE_MAX_SECONDS = 30.0
    acp_mod.CANCEL_GRACE_SECONDS = 0.05
    acp_mod.ACP_IDLE_TTL_SECONDS = 0.05
    acp_mod.SWEEP_INTERVAL_SECONDS = 0.01
    try:
        yield acp_mod, store
    finally:
        for name, value in saved.items():
            setattr(acp_mod, name, value)


def _live_session(acp_mod, sid="lifecycle-01", cwd=r"C:\scratch"):
    acp_mod._supervisor.sessions[sid] = acp_mod._new_session_record(cwd)
    acp_mod._supervisor.history[sid] = acp_mod._History()
    return sid


def _notify(acp_mod, method, params):
    acp_mod._supervisor._on_notification({"method": method, "params": params})


def _run_bounded(factory, seconds=15.0):
    """``asyncio.run`` under a wall-clock bound enforced off the event loop.

    The sweeper-loop regressions this bound exists for do not fail their tests,
    they **hang** them, and a hung test is a stuck CI job rather than a red
    build. Moving the loop's sleep after its work starves the event loop
    outright — with no sessions the guard `continue`s without ever yielding —
    and widening either `except Exception` to `BaseException` swallows the
    `CancelledError` that is the only thing that ends the task. Both were
    measured running past a 90 s cap with no verdict.

    An `asyncio.wait_for` inside the coroutine bounds neither: the first never
    lets a timer callback run at all, and the second is the very cancellation
    being suppressed. So the bound lives on a separate OS thread, where nothing
    the loop does can starve it. `pytest-timeout` is not a dependency of this
    project and this does not merit adding one.

    The thread is a daemon: if it really is wedged it cannot be joined, and
    leaving it spinning behind a failed assertion is the lesser evil against a
    suite that never returns.
    """
    box = {}

    def target():
        try:
            box["value"] = asyncio.run(factory())
        except BaseException as exc:      # re-raised on the calling thread
            box["error"] = exc

    thread = threading.Thread(target=target, name="bounded-asyncio-run",
                              daemon=True)
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise AssertionError(
            f"the sweeper loop did not finish within {seconds:.0f}s — a "
            "starved event loop or a swallowed CancelledError, which without "
            "this bound reads as a stuck job rather than a failing test")
    if "error" in box:
        raise box["error"]
    return box.get("value")


class TestAcpActivityStamp:
    """``last_activity`` and ``last_used`` answer opposed questions.

    "Is the agent still working?" is asked by the inactivity ceiling and must
    count *any* sign of life. "Has nobody used this session?" is asked by the
    sweeper and must ignore agent-generated noise entirely. One field serving
    both would let a chatty agent keep its own sessions permanently
    unsweepable, with no error anywhere to say so.
    """

    def test_a_new_session_carries_both_stamps_before_any_notification(
            self, acp_store):
        """The sweeper reads ``last_used`` on every tick, so a session created
        and never prompted — the "socket went away during session/new" case,
        which also has no subscriber — must not be the one entry missing it."""
        acp_mod, store = acp_store

        async def answers(self, method, params, timeout=None):
            return {"sessionId": "never-prompted-1"}

        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", answers):
            asyncio.run(acp_mod._supervisor.new_session(str(store)))
        meta = acp_mod._supervisor.sessions["never-prompted-1"]
        assert isinstance(meta["last_used"], float)
        assert isinstance(meta["last_activity"], float)

    def test_a_loaded_session_carries_both_stamps(self, acp_store):
        acp_mod, store = acp_store

        async def answers(self, method, params, timeout=None):
            return {}

        with patch.object(acp_mod._Supervisor, "ensure_started", _no_spawn), \
                patch.object(acp_mod._Supervisor, "_request", answers):
            asyncio.run(acp_mod._supervisor.load_session("loaded-1", str(store)))
        meta = acp_mod._supervisor.sessions["loaded-1"]
        assert isinstance(meta["last_used"], float)
        assert isinstance(meta["last_activity"], float)

    @pytest.mark.parametrize("method, update", [
        # The three kinds the dispatch below the stamp actually branches on.
        ("session/update", {"sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "x"}}),
        # No `status` key on purpose: kiro-cli 2.16.0 does not send one on a
        # `tool_call` frame, so anything deciding "a tool is running" from
        # `update["status"]` reads None every time.
        ("session/update", {"sessionUpdate": "tool_call", "toolCallId": "t1",
                            "title": "Running: ping", "kind": "execute"}),
        # The fall-through: at least six update kinds exist and three have
        # branches, so a turn emitting only these would be judged silent.
        ("session/update", {"sessionUpdate": "agent_thought_chunk"}),
        ("session/update", {"sessionUpdate": "plan"}),
        ("session/update", {"sessionUpdate": "current_mode_update"}),
        # Measured on kiro-cli 2.16.0: a method *distinct* from
        # `session/update`, carrying a sessionId and a `tool_call_chunk`, which
        # falls through the dispatch entirely. A method allowlist would miss
        # it and could cancel a working turn.
        ("_kiro.dev/session/update", {"sessionUpdate": "tool_call_chunk"}),
    ])
    def test_any_notification_carrying_a_session_id_advances_last_activity(
            self, acp_store, method, update):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        meta = acp_mod._supervisor.sessions[sid]
        meta["last_activity"] = 0.0
        _notify(acp_mod, method, {"sessionId": sid, "update": update})
        assert meta["last_activity"] > 0.0

    def test_the_metadata_method_advances_activity_but_not_use(self, acp_store):
        """The whole point of two fields. `_kiro.dev/metadata` is agent
        bookkeeping; counting it as *use* would make an idle session with a
        talkative agent unsweepable forever."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        meta = acp_mod._supervisor.sessions[sid]
        meta["last_activity"] = 0.0
        meta["last_used"] = 0.0
        _notify(acp_mod, acp_mod.METADATA_METHOD,
                {"sessionId": sid, acp_mod.CONTEXT_PERCENT_KEY: 12.5})
        assert meta["last_activity"] > 0.0
        assert meta["last_used"] == 0.0

    def test_a_notification_with_no_session_id_is_a_real_null_path(
            self, acp_store):
        """`_kiro.dev/subagent/list_update` is the observed case: the one
        `_kiro.dev/*` notification measured without a sessionId at all."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.sessions[sid]["last_activity"] = 0.0
        _notify(acp_mod, "_kiro.dev/subagent/list_update", {"subagents": []})
        _notify(acp_mod, "session/update",
                {"update": {"sessionUpdate": "agent_thought_chunk"}})
        assert acp_mod._supervisor.sessions[sid]["last_activity"] == 0.0

    def test_a_notification_after_the_record_is_gone_does_not_recreate_it(
            self, acp_store):
        """A resurrected record is counted against MAX_SESSIONS forever and
        re-terminated by the sweeper every minute."""
        acp_mod, _ = acp_store
        _notify(acp_mod, "session/update",
                {"sessionId": "already-closed",
                 "update": {"sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "late"}}})
        acp_mod._supervisor.touch_used("already-closed")
        assert "already-closed" not in acp_mod._supervisor.sessions

    def test_attaching_and_detaching_a_socket_counts_as_use(self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        meta = acp_mod._supervisor.sessions[sid]
        conn = _acp_conn(acp_mod)
        meta["last_used"] = 0.0
        acp_mod._registry.attach(conn, sid)
        assert meta["last_used"] > 0.0
        meta["last_used"] = 0.0
        acp_mod._registry.detach(conn)
        assert meta["last_used"] > 0.0

    @staticmethod
    def _prompt(acp_mod, sid, during):
        """Run one ``prompt()`` with ``during`` standing in for the round-trip.

        ``prompt`` is the only caller that stamps *both* ends of a turn, and
        every other test in this file that reaches a prompt goes through
        ``_handle_prompt`` with ``_request`` replaced wholesale — so the two
        ``touch_used`` calls inside it were reachable by nothing. This drives
        the supervisor method directly and hands the body of the turn to the
        caller, which is where both stamps can be observed from.
        """
        async def answers(self, method, params, timeout=None):
            return await during()

        with patch.object(acp_mod._Supervisor, "_request", answers), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            return asyncio.run(acp_mod._supervisor.prompt(sid, "hello"))

    def test_a_prompt_stamps_use_before_the_turn_starts(self, acp_store):
        """The stamp the Change 1 table mandates: a prompt *is* use, counted
        the moment it is sent rather than only when it comes back. Without it
        a session sitting on a turn that has been running since before the TTL
        is indistinguishable from one nobody has touched."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        meta = acp_mod._supervisor.sessions[sid]
        meta["last_used"] = 0.0
        seen = {}

        async def during():
            seen["at_turn_start"] = meta["last_used"]
            return {"stopReason": "end_turn"}

        assert self._prompt(acp_mod, sid, during) == {"stopReason": "end_turn"}
        assert seen["at_turn_start"] > 0.0

    def test_a_long_turn_is_not_swept_the_instant_it_finishes(self, acp_store):
        """The turn-*end* stamp, and the reason it exists. A turn may
        legitimately run for longer than the idle TTL — that is the whole point
        of replacing the wall-clock ceiling — and with only the turn-start
        stamp the sweeper would reclaim the session on the first tick after the
        answer arrived, destroying it in front of the person who came back to
        read it."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        meta = acp_mod._supervisor.sessions[sid]

        async def during():
            # A turn that outlasts the TTL: by the time it answers, the stamp
            # taken when it was sent is already older than the sweeper's limit.
            meta["last_used"] = (time.monotonic()
                                 - acp_mod.ACP_IDLE_TTL_SECONDS - 1)
            return {}

        self._prompt(acp_mod, sid, during)
        assert acp_mod._sweepable(sid, meta, time.monotonic()) is False
        # Positive control. Put the record back the way deleting the turn-end
        # stamp would leave it and the same session really is swept, so the
        # assertion above is about the stamp rather than about some other
        # condition quietly holding this session back.
        meta["last_used"] = time.monotonic() - acp_mod.ACP_IDLE_TTL_SECONDS - 1
        assert acp_mod._sweepable(sid, meta, time.monotonic()) is True

    def test_agent_chatter_inside_a_turn_moves_activity_and_not_use(
            self, acp_store):
        """Both fields in motion at once, which is the only place the split
        can actually be observed. The agent talking mid-turn is liveness — the
        ceiling must see it — and it is not *use*: if it advanced `last_used`
        a talkative agent could hold a session past the sweeper indefinitely
        without a person ever touching it."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        meta = acp_mod._supervisor.sessions[sid]
        seen = {}

        async def during():
            # Zeroed *after* the turn-start stamp landed, so anything the agent
            # says next has to move each field visibly or not at all.
            meta["last_used"] = 0.0
            meta["last_activity"] = 0.0
            _notify(acp_mod, "session/update",
                    {"sessionId": sid,
                     "update": {"sessionUpdate": "agent_thought_chunk"}})
            _notify(acp_mod, acp_mod.METADATA_METHOD,
                    {"sessionId": sid, acp_mod.CONTEXT_PERCENT_KEY: 12.5})
            seen["activity"] = meta["last_activity"]
            seen["used"] = meta["last_used"]
            return {}

        self._prompt(acp_mod, sid, during)
        assert seen["activity"] > 0.0
        assert seen["used"] == 0.0
        # And the turn ending is use again, whatever the agent did in between.
        assert meta["last_used"] > 0.0


# --- ACP sub-agent visibility: crew parsing, activity, read-only access ----
#
# Wire shapes measured 2026-08-11 against a real kiro-cli 2.16.2 subprocess,
# spawned and driven directly outside PowerAtlas — see acp.py's
# SUBAGENT_LIST_METHOD/SUBAGENT_ACTIVITY_METHOD/_SUBAGENT_ACTIVE_STATUSES
# comments for the captured vocabulary and what it corrected. Some entry-level
# details (an initially-nameless slot, a second sub-agent registering after
# the first) remain corroborated only against kirodotdev/kirocrew's tested ACP
# client rather than measured here — flagged per-test below where that is the
# case. These tests pin the parsing/attribution/exemption rules this file's
# own code chose given that shape, not the shape itself.


class TestAcpSubagentListAttribution:
    """`_kiro.dev/subagent/list_update` carries no sessionId of its own, so
    the crew it describes has to be attributed to whichever session is
    running the fan-out — and only when that is unambiguous."""

    def test_a_crew_is_attributed_to_the_sole_inflight_session(self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "working"}},
        ]})
        crew = acp_mod._supervisor.crews[sid]
        assert crew["sub-1"]["role"] == "explorer"
        assert crew["sub-1"]["done"] is False
        assert acp_mod._supervisor.subagent_sessions["sub-1"] == {"parent": sid}
        assert "sub-1" in acp_mod._supervisor.subagent_history

    def test_zero_inflight_sessions_drops_the_update(self, acp_store):
        acp_mod, _ = acp_store
        _live_session(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "working"}},
        ]})
        assert acp_mod._supervisor.crews == {}
        assert "sub-1" not in acp_mod._supervisor.subagent_sessions

    def test_two_inflight_sessions_is_ambiguous_and_drops_the_update(
            self, acp_store):
        """Never guess which of two running turns a crew belongs to — the
        same rule METADATA_METHOD's neighbouring comment states for a
        different notification."""
        acp_mod, _ = acp_store
        sid_a = _live_session(acp_mod, sid="crew-a")
        sid_b = _live_session(acp_mod, sid="crew-b")
        acp_mod._supervisor.inflight.add(sid_a)
        acp_mod._supervisor.inflight.add(sid_b)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "working"}},
        ]})
        assert acp_mod._supervisor.crews == {}


class TestAcpSubagentListParsing:
    """Field-level parsing of one ``subagents`` entry."""

    def _seed(self, acp_mod):
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        return sid

    def test_a_finished_entry_never_reopens(self, acp_store):
        """Q&A, 2026-08-11: "stay, marked done" — a stale or reordered update
        repeating an earlier status must not un-finish a card."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "done"}},
        ]})
        assert acp_mod._supervisor.crews[sid]["sub-1"]["done"] is True
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        assert acp_mod._supervisor.crews[sid]["sub-1"]["done"] is True

    def test_a_failed_entry_captures_its_error_message(self, acp_store):
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "failed", "message": "network unreachable"}},
        ]})
        entry = acp_mod._supervisor.crews[sid]["sub-1"]
        assert entry["done"] is True
        assert entry["error"] == "network unreachable"

    def test_an_empty_slot_is_skipped_until_named(self, acp_store):
        """kiro-cli sometimes announces a slot before it has anything to say
        about it — corroborated by kirocrew's own `_native_subagent_sync`,
        which skips exactly this case rather than showing an empty card. Not
        directly observed in the 2026-08-11 capture: every entry in both
        captured runs had `role`/`task` populated from its first appearance,
        so this remains kirocrew-only evidence."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "status": {"type": "pending"}},
        ]})
        assert acp_mod._supervisor.crews.get(sid, {}) == {}
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        assert "sub-1" in acp_mod._supervisor.crews[sid]

    def test_role_and_task_fall_back_to_the_alternate_field_names(self, acp_store):
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "agentName": "reviewer",
             "sessionName": "review the PR", "status": {"type": "working"}},
        ]})
        entry = acp_mod._supervisor.crews[sid]["sub-1"]
        assert entry["role"] == "reviewer"
        assert entry["task"] == "review the PR"

    def test_an_oversized_task_is_clipped(self, acp_store):
        """Measured 2026-08-11: `initialQuery` — the fallback `task` almost
        always resolves to, since it was populated on every entry in both
        captured runs — is the sub-agent's full task prompt, not a short
        label, and rode the `subagents` wire frame on every broadcast with no
        bound at all until MAX_SUBAGENT_TASK_CHARS."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        long_query = "x" * (acp_mod.MAX_SUBAGENT_TASK_CHARS * 2)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "initialQuery": long_query, "status": {"type": "working"}},
        ]})
        entry = acp_mod._supervisor.crews[sid]["sub-1"]
        assert entry["task"] == long_query[:acp_mod.MAX_SUBAGENT_TASK_CHARS]

    def test_a_non_list_subagents_field_is_ignored_rather_than_raising(
            self, acp_store):
        acp_mod, _ = acp_store
        self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": "nope"})
        assert acp_mod._supervisor.crews == {}

    def test_started_at_is_set_when_an_entry_is_first_created(self, acp_store):
        """startedAt is a unix timestamp float set when a child_id first appears
        in the crew — used by the frontend to compute elapsed time."""
        import time
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        before = time.time()
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "working"}},
        ]})
        after = time.time()
        entry = acp_mod._supervisor.crews[sid]["sub-1"]
        assert isinstance(entry["startedAt"], float)
        assert before <= entry["startedAt"] <= after

    def test_started_at_is_preserved_on_subsequent_updates(self, acp_store):
        """startedAt must not change when the crew entry is updated — it records
        when the sub-agent first appeared, not when it was last seen."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "working"}},
        ]})
        original_started_at = acp_mod._supervisor.crews[sid]["sub-1"]["startedAt"]
        # Second update: status change
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "terminated"}},
        ]})
        assert acp_mod._supervisor.crews[sid]["sub-1"]["startedAt"] == original_started_at


class TestAcpSubagentActivity:
    """`_kiro.dev/session/update` — SUBAGENT_ACTIVITY_METHOD — a sub-agent's
    own tool calls and text, tagged with its own sessionId."""

    def test_tool_call_chunk_updates_the_crew_cards_action(self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        conn = _acp_conn(acp_mod)
        acp_mod._registry.attach(conn, sid)
        _queued(conn)
        _notify(acp_mod, acp_mod.SUBAGENT_ACTIVITY_METHOD, {
            "sessionId": "sub-1",
            "update": {"sessionUpdate": "tool_call_chunk",
                       "toolCallId": "tc-1", "title": "read"},
        })
        assert acp_mod._supervisor.crews[sid]["sub-1"]["action"] == "read"
        frames = _queued(conn)
        subagents = [f for f in frames if f["type"] == "subagents"]
        assert subagents[-1]["payload"]["subagents"][0]["action"] == "read"

    def test_tool_call_chunk_for_an_unregistered_child_is_a_noop(self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_ACTIVITY_METHOD, {
            "sessionId": "unregistered-sub",
            "update": {"sessionUpdate": "tool_call_chunk",
                       "toolCallId": "tc-1", "title": "read"},
        })
        assert acp_mod._supervisor.crews == {}

    def test_agent_message_chunk_for_a_registered_child_is_recorded(
            self, acp_store):
        """No dedicated branch for the routing itself — the existing
        agent_message_chunk dispatch already keys purely off `sessionId`, so
        registering the child's history buffer is most of the fix. The flat
        `text` field here (rather than nested `content.text`) exercises
        `_content_text`'s fallback branch, kept as defensive coverage — not a
        shape kiro-cli was captured sending. Measured 2026-08-11 against a
        real kiro-cli 2.16.2 subprocess: SUBAGENT_ACTIVITY_METHOD never
        carried `agent_message_chunk` at all in either captured run (only
        `tool_call_chunk`), and every `agent_message_chunk` seen on any
        channel used the nested `content.text` shape. This combination — flat
        text, on this method — is untested against real traffic on both
        axes."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        _notify(acp_mod, acp_mod.SUBAGENT_ACTIVITY_METHOD, {
            "sessionId": "sub-1",
            "update": {"sessionUpdate": "agent_message_chunk",
                       "text": "hello from sub"},
        })
        events = acp_mod._supervisor.subagent_history["sub-1"].events()
        assert any(e["type"] == "chunk" and e["payload"]["text"] == "hello from sub"
                   for e in events)


class TestAcpSubagentsFrameDelivery:

    def test_a_subagents_frame_is_broadcast_but_not_recorded_into_history(
            self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        conn = _acp_conn(acp_mod)
        acp_mod._registry.attach(conn, sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        frames = _queued(conn)
        assert frames and frames[-1]["type"] == "subagents"
        assert frames[-1]["payload"]["subagents"][0]["sessionId"] == "sub-1"
        assert all(e["type"] != "subagents"
                   for e in acp_mod._supervisor.history[sid].events())

    def test_subscribing_after_the_fact_gets_a_crew_snapshot(self, acp_store):
        """A reload's only source for the bar — see `_emit_subagents_frame`'s
        docstring for why it is deliberately not in the replay buffer."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        conn = _acp_conn(acp_mod)
        acp_mod._handle_subscribe(conn, sid)
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["session", "history", "subagents"]

    def test_started_at_is_included_in_the_wire_payload(self, acp_store):
        """_subagents_payload must include startedAt so the frontend can compute
        elapsed time without a second round-trip."""
        import time
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        conn = _acp_conn(acp_mod)
        acp_mod._registry.attach(conn, sid)
        _queued(conn)  # drain attach noise
        before = time.time()
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "status": {"type": "working"}},
        ]})
        after = time.time()
        frames = _queued(conn)
        subagents_frame = next(f for f in frames if f["type"] == "subagents")
        entry = subagents_frame["payload"]["subagents"][0]
        assert "startedAt" in entry
        assert isinstance(entry["startedAt"], float)
        assert before <= entry["startedAt"] <= after


class TestAcpSubagentReadOnlyAccess:
    """A sub-agent's own session id is subscribable like a real one, and
    refused everywhere a real one would be driven."""

    def _seed_crew(self, acp_mod):
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer",
             "initialQuery": "look around", "status": {"type": "working"}},
        ]})
        return sid

    def test_subscribing_to_a_registered_child_serves_a_readonly_session(
            self, acp_store):
        acp_mod, _ = acp_store
        sid = self._seed_crew(acp_mod)
        conn = _acp_conn(acp_mod)
        acp_mod._handle_subscribe(conn, "sub-1")
        frames = _queued(conn)
        assert [f["type"] for f in frames] == ["session", "history"]
        payload = frames[0]["payload"]
        assert payload["readOnly"] is True
        assert payload["parentSessionId"] == sid
        assert payload["role"] == "explorer"
        assert payload["task"] == "look around"
        assert conn.session_id == "sub-1"

    def test_a_prompt_against_a_subagent_session_is_refused_read_only(
            self, acp_store):
        acp_mod, _ = acp_store
        self._seed_crew(acp_mod)
        conn = _acp_conn(acp_mod)
        asyncio.run(acp_mod._handle_prompt(conn, "sub-1", {"prompt": "hi"}))
        frames = _queued(conn)
        assert frames[0]["payload"]["code"] == "read_only_session"

    def test_a_close_against_a_subagent_session_is_refused_read_only(
            self, acp_store):
        acp_mod, _ = acp_store
        self._seed_crew(acp_mod)
        conn = _acp_conn(acp_mod)
        asyncio.run(acp_mod._handle_close(conn, "sub-1"))
        frames = _queued(conn)
        assert frames[0]["payload"]["code"] == "read_only_session"

    def test_a_cancel_against_a_subagent_session_is_refused_read_only(
            self, acp_store):
        acp_mod, _ = acp_store
        self._seed_crew(acp_mod)
        conn = _acp_conn(acp_mod)
        asyncio.run(acp_mod._handle_cancel(conn, "sub-1"))
        frames = _queued(conn)
        assert frames[0]["payload"]["code"] == "read_only_session"

    def test_subagent_sessions_are_exempt_from_the_session_cap(self, acp_store):
        """Q&A, 2026-08-11: "exempt them" — a fan-out must not crowd out room
        for a real session the user asked for."""
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        with patch.object(acp_mod, "MAX_SESSIONS", 1):
            assert acp_mod._supervisor.at_capacity() is True
            _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
                {"sessionId": "sub-1", "role": "a", "status": {"type": "working"}},
                {"sessionId": "sub-2", "role": "b", "status": {"type": "working"}},
            ]})
            # Still exactly as at capacity as before — two more live sessions
            # would trip it further, two sub-agents must not.
            assert acp_mod._supervisor.at_capacity() is True
            assert len(acp_mod._supervisor.sessions) == 1


class TestAcpSubagentCleanup:

    def test_closing_the_parent_tears_down_its_crew(self, acp_session):
        acp_mod, sid = acp_session
        acp_mod._supervisor.inflight.add(sid)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-1", "role": "explorer", "status": {"type": "working"}},
        ]})
        acp_mod._supervisor.inflight.discard(sid)
        watcher = _acp_conn(acp_mod)
        acp_mod._handle_subscribe(watcher, "sub-1")
        _queued(watcher)
        parent_conn = _acp_conn(acp_mod)
        acp_mod._registry.attach(parent_conn, sid)
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._handle_close(parent_conn, sid))
        assert sid not in acp_mod._supervisor.crews
        assert "sub-1" not in acp_mod._supervisor.subagent_sessions
        assert "sub-1" not in acp_mod._supervisor.subagent_history
        frames = _queued(watcher)
        assert frames[0]["type"] == "session_closed"


class TestAcpSubagentEviction:

    def test_the_oldest_finished_entries_are_evicted_over_the_cap(self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        with patch.object(acp_mod, "MAX_SUBAGENTS_PER_SESSION", 2):
            _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
                {"sessionId": "sub-1", "role": "a", "status": {"type": "done"}},
            ]})
            _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
                {"sessionId": "sub-2", "role": "b", "status": {"type": "done"}},
            ]})
            _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
                {"sessionId": "sub-3", "role": "c", "status": {"type": "working"}},
            ]})
        crew = acp_mod._supervisor.crews[sid]
        assert set(crew) == {"sub-2", "sub-3"}
        assert "sub-1" not in acp_mod._supervisor.subagent_sessions
        assert "sub-1" not in acp_mod._supervisor.subagent_history

    def test_a_still_running_entry_is_never_evicted(self, acp_store):
        acp_mod, _ = acp_store
        sid = _live_session(acp_mod)
        acp_mod._supervisor.inflight.add(sid)
        with patch.object(acp_mod, "MAX_SUBAGENTS_PER_SESSION", 1):
            _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
                {"sessionId": "sub-1", "role": "a", "status": {"type": "working"}},
            ]})
            _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
                {"sessionId": "sub-2", "role": "b", "status": {"type": "working"}},
            ]})
        crew = acp_mod._supervisor.crews[sid]
        # Both survive: neither is `done`, and eviction only ever removes a
        # finished entry — a bound this small is simply exceeded rather than
        # violated by dropping a sub-agent that is still running.
        assert set(crew) == {"sub-1", "sub-2"}


class TestAcpInactivityCeiling:
    """The turn ceiling, driven directly.

    It lives inside ``_request``, and ~30 doubles in this file replace that
    function wholesale — so nothing reaching ``_handle_prompt`` exercises a
    line of it. Every test here drives ``_request`` itself with a fake
    ``_write`` and a hand-driven pending future.
    """

    def _drive(self, acp_mod, sid, scenario):
        """Run one ``_request`` in inactivity mode alongside ``scenario``.

        Returns ``(outcome, written)`` where ``outcome`` is the result or the
        exception the request raised, and ``written`` is every JSON-RPC object
        the supervisor put on the wire — the cancel notification included.
        """
        written = []

        def fake_write(self, obj):
            written.append(obj)

        async def run():
            sup = acp_mod._supervisor
            sup._loop = asyncio.get_running_loop()
            try:
                with patch.object(acp_mod._Supervisor, "_write", fake_write):
                    task = asyncio.ensure_future(sup._request(
                        "session/prompt", {"sessionId": sid},
                        timeout=acp_mod._INACTIVITY))
                    # Let the request register its pending future and reach the
                    # wait; `scenario` reads `_pending` on its first line.
                    await asyncio.sleep(0)
                    try:
                        await scenario(sup, task, written)
                    except BaseException:
                        task.cancel()
                        raise
                    try:
                        return await asyncio.wait_for(task, 10)
                    except Exception as exc:
                        return exc
            finally:
                sup._loop = None
                sup._pending.clear()

        return asyncio.run(run()), written

    @staticmethod
    def _pending(sup):
        return next(iter(sup._pending.values()))

    def test_a_turn_that_keeps_streaming_outlives_the_old_wall_clock(
            self, acp_fast):
        """The behaviour SC-8 exists for. The old bound was 600 s of wall
        clock whatever the agent was doing; this turn runs for many multiples
        of its silence window and completes, because something kept arriving.
        """
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)

        async def scenario(sup, task, written):
            for _ in range(30):
                await asyncio.sleep(acp_mod.PROMPT_TICK_SECONDS / 2)
                _notify(acp_mod, "session/update",
                        {"sessionId": sid,
                         "update": {"sessionUpdate": "agent_thought_chunk"}})
            assert not task.done()
            self._pending(sup).set_result({"stopReason": "end_turn"})

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert outcome == {"stopReason": "end_turn"}
        assert [o.get("method") for o in written] == ["session/prompt"]

    def test_only_unhandled_update_kinds_still_count_as_life(self, acp_fast):
        """`agent_thought_chunk`, `plan` and `current_mode_update` reach no
        branch in `_on_notification`, and `_kiro.dev/session/update` reaches
        none either. Stamping above the dispatch on the *presence of a session
        id* is what stops a turn emitting nothing else being cancelled."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)

        async def scenario(sup, task, written):
            for kind in ("plan", "current_mode_update", "agent_thought_chunk",
                         "plan", "current_mode_update", "tool_call_chunk"):
                await asyncio.sleep(acp_mod.PROMPT_SILENCE_SECONDS / 3)
                _notify(acp_mod, "_kiro.dev/session/update",
                        {"sessionId": sid, "update": {"sessionUpdate": kind}})
            assert not task.done()
            self._pending(sup).set_result({"stopReason": "end_turn"})

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert outcome == {"stopReason": "end_turn"}
        assert "session/cancel" not in [o.get("method") for o in written]

    def test_silence_past_the_window_cancels_agent_side_and_raises(
            self, acp_fast):
        """The other half of SC-8. The old path popped the future and raised
        without telling the agent anything, so it kept working while the
        session read idle."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)

        async def scenario(sup, task, written):
            return

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert isinstance(outcome, acp_mod.AgentTimeout)
        assert "silent" in str(outcome)
        cancels = [o for o in written if o.get("method") == "session/cancel"]
        assert cancels and cancels[0]["params"] == {"sessionId": sid}
        # A notification, not a request: a cancel that itself awaited an answer
        # would be a Stop button that hangs.
        assert "id" not in cancels[0]

    def test_a_cancel_honoured_inside_the_grace_returns_the_real_answer(
            self, acp_fast):
        """Measured at 9 ms on kiro-cli 2.16.0, and matched on the pending
        future rather than dropped — which is why `CANCEL_GRACE_SECONDS` is
        seconds rather than the 30 s an unmeasured worst case bought."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)

        async def scenario(sup, task, written):
            fut = self._pending(sup)
            for _ in range(400):
                if any(o.get("method") == "session/cancel" for o in written):
                    break
                await asyncio.sleep(acp_mod.PROMPT_TICK_SECONDS / 4)
            fut.set_result({"stopReason": "cancelled"})

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert outcome == {"stopReason": "cancelled"}
        assert [o.get("method") for o in written] == [
            "session/prompt", "session/cancel"]

    def test_a_prompt_on_a_long_idle_session_gets_a_full_window(self, acp_fast):
        """The deadline is seeded locally at send time. Reading the shared
        `last_activity` as the baseline would kill the first prompt on any
        session idle longer than the silence window — a session idle 20 minutes
        with a tab attached is unswept and already "silent"."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        acp_mod._supervisor.sessions[sid]["last_activity"] = (
            time.monotonic() - 10 * acp_mod.PROMPT_SILENCE_SECONDS)

        async def scenario(sup, task, written):
            # Several ticks, and comfortably inside one silence window: the
            # naive version dies on the very first tick.
            await asyncio.sleep(acp_mod.PROMPT_SILENCE_SECONDS / 3)
            assert not task.done(), "cancelled before the agent could answer"
            self._pending(sup).set_result({"stopReason": "end_turn"})

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert outcome == {"stopReason": "end_turn"}
        assert "session/cancel" not in [o.get("method") for o in written]

    def test_a_turn_past_the_absolute_ceiling_is_cancelled_even_while_talking(
            self, acp_fast):
        """Without this a turn emitting one chunk just under the silence
        window runs forever, and `inflight` makes that session simultaneously
        un-closable and un-sweepable for the app's lifetime."""
        acp_mod, _ = acp_fast
        acp_mod.PROMPT_SILENCE_SECONDS = 30.0
        acp_mod.PROMPT_ABSOLUTE_MAX_SECONDS = 0.05
        sid = _live_session(acp_mod)

        async def scenario(sup, task, written):
            for _ in range(60):
                if task.done():
                    return
                await asyncio.sleep(acp_mod.PROMPT_TICK_SECONDS / 2)
                _notify(acp_mod, "session/update",
                        {"sessionId": sid,
                         "update": {"sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": "."}}})

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert isinstance(outcome, acp_mod.AgentTimeout)
        assert "ceiling" in str(outcome)
        assert "session/cancel" in [o.get("method") for o in written]

    def test_agent_death_during_a_turn_surfaces_the_typed_error(self, acp_fast):
        """`_detach` clears `sessions` and fails every pending future. The
        ceiling must fall through to the future rather than inventing a
        timeout, or the page gets an `internal_error` where a typed
        `agent_died` belongs."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)

        async def scenario(sup, task, written):
            fut = self._pending(sup)
            sup.sessions.pop(sid)
            await asyncio.sleep(acp_mod.PROMPT_TICK_SECONDS * 4)
            fut.set_exception(acp_mod.AgentDied("the channel closed"))

        outcome, written = self._drive(acp_mod, sid, scenario)
        assert isinstance(outcome, acp_mod.AgentDied)
        assert "session/cancel" not in [o.get("method") for o in written]

    def test_the_request_signature_is_unchanged(self):
        """19 fixed-signature stubs in this file replace `_request` wholesale.
        A new parameter would raise `TypeError` in every one of them, which is
        why the inactivity mode travels in the existing `timeout` slot."""
        import inspect
        from power_atlas import acp as acp_mod
        params = list(inspect.signature(
            acp_mod._Supervisor._request).parameters)
        assert params == ["self", "method", "params", "timeout"]

    def test_a_wall_clock_request_still_reports_its_ceiling_in_seconds(
            self, acp_fast):
        """The sentinel is read with `is` above the try for exactly this
        reason: the wall-clock arm formats `{timeout:.0f}` into its message and
        a sentinel would blow up there."""
        acp_mod, _ = acp_fast

        def fake_write(self, obj):
            pass

        async def run():
            sup = acp_mod._supervisor
            sup._loop = asyncio.get_running_loop()
            try:
                with patch.object(acp_mod._Supervisor, "_write", fake_write):
                    await sup._request("session/new", {}, timeout=0.01)
            finally:
                sup._loop = None
                sup._pending.clear()

        with pytest.raises(acp_mod.AgentTimeout) as exc:
            asyncio.run(run())
        assert "within 0s" in str(exc.value)


class TestAcpIdleSweeper:
    """Six conditions, one synchronous claim, and a failure mode that must
    never take the task out."""

    def _idle(self, acp_mod, sid):
        acp_mod._supervisor.sessions[sid]["last_used"] = (
            time.monotonic() - acp_mod.ACP_IDLE_TTL_SECONDS - 1)

    def _sweep(self, acp_mod, written):
        with patch.object(acp_mod._Supervisor, "_write",
                          _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

    def test_an_idle_unattended_session_is_terminated(self, acp_fast):
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        self._idle(acp_mod, sid)
        written = []
        self._sweep(acp_mod, written)
        assert [o["method"] for o in written] == [acp_mod.CLOSE_METHOD]
        assert written[0]["params"] == {"sessionId": sid}
        assert sid not in acp_mod._supervisor.sessions
        assert sid not in acp_mod._supervisor.history

    def test_a_session_inside_the_ttl_is_left_alone(self, acp_fast):
        acp_mod, _ = acp_fast
        _live_session(acp_mod)
        written = []
        self._sweep(acp_mod, written)
        assert written == []

    @pytest.mark.parametrize("blocker", [
        "subscriber", "inflight", "closing", "loading"])
    def test_each_remaining_condition_protects_the_session(self, acp_fast,
                                                           blocker):
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        if blocker == "subscriber":
            # An attached tab means leave it alone regardless of age. Attaching
            # stamps `last_used`, so the ageing comes after it.
            acp_mod._registry.attach(_acp_conn(acp_mod), sid)
        elif blocker == "inflight":
            acp_mod._supervisor.inflight.add(sid)
        elif blocker == "closing":
            acp_mod._supervisor.closing.add(sid)
        else:
            # The condition the original four missed: a session mid-load has
            # zero subscribers *by construction*, so it satisfied every other
            # condition and would have been terminated mid-load.
            acp_mod._registry.loading[sid] = []
        self._idle(acp_mod, sid)
        written = []
        self._sweep(acp_mod, written)
        assert written == []
        assert sid in acp_mod._supervisor.sessions

    def test_a_session_closed_between_snapshot_and_sweep_is_skipped(
            self, acp_fast, caplog):
        """The tuple is snapshotted once but `close_session` awaits inside the
        loop, so a user close can pop session *n* before it is reached —
        `close_session` would then raise and log a WARNING every pass.

        The wire trace alone cannot see the missing re-check: without it
        `close_session` is still *called* for the stolen session, raises
        `AgentRejected` before writing anything, and `_sweep_once` swallows it,
        so the bytes on the pipe are identical either way. What differs is the
        call count and the WARNING — the recurring log line the condition
        exists to prevent — so both are asserted here.
        """
        acp_mod, _ = acp_fast
        first = _live_session(acp_mod, "sweep-a")
        second = _live_session(acp_mod, "sweep-b")
        self._idle(acp_mod, first)
        self._idle(acp_mod, second)
        written = []
        attempted = []
        real_close = acp_mod._Supervisor.close_session

        async def close_and_steal(self, session_id):
            attempted.append(session_id)
            # Releasing the first session takes the second one with it, the
            # way a user close landing mid-pass would.
            acp_mod._supervisor.sessions.pop(second, None)
            return await real_close(self, session_id)

        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"), \
                patch.object(acp_mod._Supervisor, "close_session",
                             close_and_steal), \
                patch.object(acp_mod._Supervisor, "_write",
                             _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

        assert [o["params"]["sessionId"] for o in written] == [first]
        assert attempted == [first]
        assert [r.getMessage() for r in caplog.records
                if r.levelno >= logging.WARNING] == []

    def test_a_prompt_during_the_terminate_round_trip_is_refused(self, acp_fast):
        """The claim on `closing` is taken in the synchronous prefix. Without
        it a prompt arriving mid-terminate passes every guard and starts a turn
        on a session being released."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        self._idle(acp_mod, sid)
        conn = _acp_conn(acp_mod)
        # Attached from the socket's point of view but not registered as a
        # subscriber — otherwise condition 3 would keep the session off this
        # path entirely and there would be nothing to test.
        conn.session_id = sid
        seen = []

        async def slow_terminate(self, method, params, timeout=None):
            await asyncio.sleep(0)
            # Mid-round-trip: the session is still registered, and the claim is
            # the only thing standing between it and a new turn.
            assert params["sessionId"] in self.sessions
            await acp_mod._handle_prompt(conn, sid, {"prompt": "hello"})
            seen.extend(f["payload"].get("code") for f in _queued(conn))
            return {}

        with patch.object(acp_mod._Supervisor, "_request", slow_terminate), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

        assert seen == ["close_in_progress"]
        assert sid not in acp_mod._supervisor.sessions

    def test_a_load_during_the_terminate_round_trip_is_refused(self, acp_fast):
        """C2-32. Unreachable before the sweeper existed: a close needed a
        subscribed socket pressing Close, and that socket is by definition not
        the one arriving here asking to adopt the session."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod, "aaaabbbb-cccc-dddd-eeee-ffff00001111")
        self._idle(acp_mod, sid)
        conn = _acp_conn(acp_mod)
        seen = []

        async def slow_terminate(self, method, params, timeout=None):
            await asyncio.sleep(0)
            await acp_mod._handle_load(conn, sid)
            seen.extend(f["payload"].get("code") for f in _queued(conn))
            return {}

        with patch.object(acp_mod._Supervisor, "_request", slow_terminate), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

        assert seen == ["close_in_progress"]

    def test_a_subscribe_during_the_terminate_round_trip_is_refused(
            self, acp_fast):
        """The mirror of the `load` guard, and the reason the asymmetry closed.

        Attaching mid-terminate does self-heal — the broadcast below reaches
        whoever is attached at that instant — but not before the attach has
        stamped `last_used` on a record about to be popped and handed the socket
        a full replay that unwinds ~0.26 s later. Both halves are asserted from
        outside the sweep: `_sweep_once` swallows every exception raised inside
        `close_session`, so an assert in the patched round-trip would degrade to
        a log line rather than a failure.
        """
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        acp_mod._supervisor.record(sid, acp_mod.envelope(
            "chunk", {"role": "agent", "text": "hello"}, sid))
        self._idle(acp_mod, sid)
        stamped = acp_mod._supervisor.sessions[sid]["last_used"]
        conn = _acp_conn(acp_mod)
        during = []
        attached = []
        stamps = []

        async def slow_terminate(self, method, params, timeout=None):
            await asyncio.sleep(0)
            acp_mod._handle_subscribe(conn, sid)
            during.extend(_queued(conn))
            attached.append(conn.session_id)
            stamps.append(self.sessions[sid]["last_used"])
            return {}

        with patch.object(acp_mod._Supervisor, "_request", slow_terminate), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

        assert [f["type"] for f in during] == ["error"]
        assert [f["payload"]["code"] for f in during] == ["close_in_progress"]
        # Refused, so nothing to attach and nothing to tear down: no replay was
        # built, the idle clock was not restarted on a doomed record, and the
        # broadcast has no socket to tell.
        assert attached == [None]
        assert stamps == [stamped]
        assert _queued(conn) == []
        assert sid not in acp_mod._supervisor.sessions

    def test_a_swept_session_tells_any_watcher_it_is_gone(self, acp_fast):
        """`_handle_close`'s notification half, reproduced rather than reached
        by relaxing its `not_subscribed` guard — the sweeper has no socket, and
        that guard protects a real case."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        self._idle(acp_mod, sid)
        conn = _acp_conn(acp_mod)

        async def attach_mid_flight(self, method, params, timeout=None):
            await asyncio.sleep(0)
            acp_mod._registry.attach(conn, sid)
            return {}

        with patch.object(acp_mod._Supervisor, "_request", attach_mid_flight), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

        assert [f["type"] for f in _queued(conn)] == ["session_closed"]
        assert conn.session_id is None

    def test_a_failing_terminate_is_a_warning_not_a_dead_task(self, acp_fast,
                                                              caplog):
        """If a kiro-cli build drops the private terminate method the sweeper
        must degrade to memory growth, never to a crashed task."""
        acp_mod, _ = acp_fast
        first = _live_session(acp_mod, "sweep-boom")
        second = _live_session(acp_mod, "sweep-ok")
        self._idle(acp_mod, first)
        self._idle(acp_mod, second)

        async def refuse_one(self, method, params, timeout=None):
            if params["sessionId"] == first:
                raise acp_mod.AgentRejected("Method not found (code -32601)")
            return {}

        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"), \
                patch.object(acp_mod._Supervisor, "_request", refuse_one), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod, lambda: acp_mod._sweep_once())

        assert first in acp_mod._supervisor.sessions
        assert second not in acp_mod._supervisor.sessions
        # Claimed and released even on the failure path, or the session would
        # be permanently unpromptable as well as unswept.
        assert first not in acp_mod._supervisor.closing
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def _always_refuse(self, acp_mod):
        async def refuse(self, method, params, timeout=None):
            raise acp_mod.AgentRejected("Method not found (code -32601)")
        return refuse

    def _failing_sweeps(self, acp_mod, sid, passes, caplog):
        """Run `passes` sweeps against a session whose terminate always fails."""
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"), \
                patch.object(acp_mod._Supervisor, "_request",
                             self._always_refuse(acp_mod)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            self._idle(acp_mod, sid)
            for _ in range(passes):
                _run_bound(acp_mod, lambda: acp_mod._sweep_once())
        return [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_a_stuck_session_logs_one_traceback_not_one_a_minute(
            self, acp_fast, caplog):
        """Measured against kiro-cli with `_kiro.dev/session/terminate`
        removed: one session failed 23 times in 120 s at a 5 s interval, each
        with a full traceback. At the shipped 60 s interval that is one
        multi-line traceback a minute, per stuck session, for the application's
        lifetime, into a log with no rotation on this path. The failure still
        has to be visible on every tick — it is just not worth a stack trace
        after the first, and the count is what tells "still stuck" apart from
        "stuck again"."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod, "sweep-stuck")
        records = self._failing_sweeps(acp_mod, sid, 3, caplog)

        assert len(records) == 3
        assert [r.exc_info is not None for r in records] == [True, False, False]
        assert "2 consecutive" in records[1].getMessage()
        assert "3 consecutive" in records[2].getMessage()
        assert sid in records[1].getMessage()
        # Still there and still promptable, which is the degradation the
        # WARNING path exists to keep.
        assert sid in acp_mod._supervisor.sessions
        assert sid not in acp_mod._supervisor.closing

    def test_a_session_that_closes_forgets_its_earlier_failures(
            self, acp_fast, caplog):
        """The counter is per session and consecutive: a close that finally
        succeeds means the next failure is a new fault and deserves its
        traceback. Reachable because a session id outlives a close —
        `session/load` re-registers the same id from the agent's own store."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod, "sweep-recovers")
        self._failing_sweeps(acp_mod, sid, 2, caplog)
        assert acp_mod._sweep_failures[sid] == 2

        written = []
        self._sweep(acp_mod, written)
        assert sid not in acp_mod._supervisor.sessions
        assert sid not in acp_mod._sweep_failures

        # The same id comes back — a load of the session from the agent's store
        # — and fails to close again. A stale count would swallow the traceback.
        caplog.clear()
        _live_session(acp_mod, sid)
        records = self._failing_sweeps(acp_mod, sid, 1, caplog)
        assert len(records) == 1
        assert records[0].exc_info is not None

    def test_the_failure_counts_cannot_outlive_the_sessions(self, acp_fast,
                                                            caplog):
        """The trap in the fix: a per-session counter that is never pruned
        trades unbounded log growth for unbounded memory growth. A session can
        leave `sessions` without its close ever succeeding — swept by another
        path, closed by a user, or dropped by `_detach` when the agent dies."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod, "sweep-vanishes")
        self._failing_sweeps(acp_mod, sid, 1, caplog)
        assert sid in acp_mod._sweep_failures

        # Gone by some other path, with its close still never having succeeded.
        acp_mod._supervisor.sessions.pop(sid, None)
        acp_mod._supervisor.sessions["sweep-other"] = (
            acp_mod._new_session_record(r"C:\scratch"))
        self._sweep(acp_mod, [])
        assert acp_mod._sweep_failures == {}

    def test_the_loop_sleeps_before_it_works(self, acp_fast):
        """A `continue` placed before the sleep never yields, and this task
        shares its loop with every websocket and every render — a tight loop
        here takes the whole application down."""
        acp_mod, _ = acp_fast
        acp_mod.SWEEP_INTERVAL_SECONDS = 5.0
        _live_session(acp_mod)
        passes = []

        async def record():
            passes.append(1)

        async def run():
            with patch.object(acp_mod, "_sweep_once", record):
                task = acp_mod.start_sweeper()
                await asyncio.sleep(0.05)
                still_running = not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                return still_running

        assert _run_bounded(run) is True
        assert passes == []

    def test_a_tick_with_no_sessions_costs_nothing_and_still_yields(
            self, acp_fast):
        acp_mod, _ = acp_fast
        passes = []

        async def record():
            passes.append(1)

        async def run():
            with patch.object(acp_mod, "_sweep_once", record):
                task = acp_mod.start_sweeper()
                # Many intervals' worth of wall clock, and no sessions. Getting
                # here at all is the yield half of the assertion.
                await asyncio.sleep(acp_mod.SWEEP_INTERVAL_SECONDS * 20)
                assert not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        _run_bounded(run)
        assert passes == []

    def test_a_pass_that_raises_does_not_kill_the_loop(self, acp_fast, caplog):
        acp_mod, _ = acp_fast
        _live_session(acp_mod)
        calls = []

        async def boom():
            calls.append(1)
            raise RuntimeError("something the sweeper did not expect")

        async def run():
            with caplog.at_level(logging.WARNING, logger="power_atlas.acp"), \
                    patch.object(acp_mod, "_sweep_once", boom):
                task = acp_mod.start_sweeper()
                await asyncio.sleep(acp_mod.SWEEP_INTERVAL_SECONDS * 20)
                still_running = not task.done()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                return still_running

        assert _run_bounded(run) is True
        assert len(calls) > 1
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_cancelling_the_sweeper_mid_close_returns_at_once(self, acp_fast):
        """`CancelledError` is a BaseException and is deliberately not caught,
        and the close is deliberately not shielded — either would hold teardown
        for up to REQUEST_TIMEOUT_SECONDS against `__main__.py`'s 5 s join."""
        acp_mod, _ = acp_fast
        sid = _live_session(acp_mod)
        self._idle(acp_mod, sid)

        async def never_answers(self, method, params, timeout=None):
            await asyncio.sleep(30)

        async def run():
            acp_mod._supervisor._loop = asyncio.get_running_loop()
            try:
                with patch.object(acp_mod._Supervisor, "_request",
                                  never_answers), \
                        patch.object(acp_mod._Supervisor, "alive",
                                     lambda self: True):
                    task = acp_mod.start_sweeper()
                    await asyncio.sleep(acp_mod.SWEEP_INTERVAL_SECONDS * 5)
                    started = time.monotonic()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                    return time.monotonic() - started
            finally:
                acp_mod._supervisor._loop = None
                acp_mod._supervisor._pending.clear()

        assert _run_bounded(run) < 1.0


class TestAcpConfiguredLimits:
    """`MAX_SESSIONS` and the two new timings come from configuration, read
    once at startup and rebound as module-level names."""

    def test_the_shipped_default_is_eight(self):
        from power_atlas import acp as acp_mod
        from power_atlas.config import Config
        assert acp_mod.MAX_SESSIONS == 8
        assert Config().acp_max_sessions == 8
        assert Config().acp_idle_ttl_seconds == 1800
        assert Config().acp_prompt_silence_seconds == 900

    def test_it_stays_a_module_attribute(self):
        """Nine sites in this file read `acp.MAX_SESSIONS`; moving it onto
        `_Supervisor` would break every one with `AttributeError`."""
        from power_atlas import acp as acp_mod
        assert "MAX_SESSIONS" in vars(acp_mod)
        assert not hasattr(acp_mod._supervisor, "MAX_SESSIONS")

    def test_apply_config_rebinds_all_three(self, acp_fast):
        acp_mod, _ = acp_fast
        from power_atlas.config import Config
        acp_mod.apply_config(Config(acp_max_sessions=5,
                                    acp_idle_ttl_seconds=600,
                                    acp_prompt_silence_seconds=120))
        assert acp_mod.MAX_SESSIONS == 5
        assert acp_mod.ACP_IDLE_TTL_SECONDS == 600.0
        assert acp_mod.PROMPT_SILENCE_SECONDS == 120.0
        # The refusal the operator reads has to quote the cap in force.
        assert "5 sessions" in acp_mod._session_limit_message()

    @pytest.mark.parametrize("field, attr, bad", [
        ("acp_max_sessions", "MAX_SESSIONS", 0),
        ("acp_max_sessions", "MAX_SESSIONS", 17),
        ("acp_max_sessions", "MAX_SESSIONS", True),
        ("acp_idle_ttl_seconds", "ACP_IDLE_TTL_SECONDS", 299),
        ("acp_idle_ttl_seconds", "ACP_IDLE_TTL_SECONDS", 86401),
        ("acp_prompt_silence_seconds", "PROMPT_SILENCE_SECONDS", 59),
        ("acp_prompt_silence_seconds", "PROMPT_SILENCE_SECONDS", 7201),
    ])
    def test_an_out_of_range_value_is_named_and_the_value_in_force_kept(
            self, acp_fast, caplog, field, attr, bad):
        """Named and kept rather than raised on: this runs inside startup, and
        refusing to boot over a hand-edited number would trade a wrong session
        cap for no application at all."""
        acp_mod, _ = acp_fast
        from power_atlas.config import Config
        before = getattr(acp_mod, attr)
        with caplog.at_level(logging.WARNING, logger="power_atlas.acp"):
            acp_mod.apply_config(Config(**{field: bad}))
        assert getattr(acp_mod, attr) == before
        assert any(field in r.getMessage() for r in caplog.records)

    def test_the_cap_check_touches_no_disk(self):
        """`at_capacity()` runs on the event loop and `load_config()` is an
        uncached whole-file TOML parse — reading it there reproduces the exact
        stall `_handle_new` already threads out to avoid."""
        from power_atlas import acp as acp_mod
        from power_atlas import config as config_mod

        def explode():
            raise AssertionError("at_capacity must not read config from disk")

        with patch.object(config_mod, "load_config", explode):
            acp_mod._supervisor.at_capacity()
            acp_mod._session_limit_message()


class TestAcpLifespanWiring:
    """`web.py` owns the lifecycle hook; `acp.py` owns the policy."""

    def test_the_sweeper_is_started_and_awaited_before_the_teardown(self):
        """`sweeper` must be a *member* of the gather, not merely cancelled.

        A fake that finished on the first loop turn would read `done=True`
        under ordinary scheduling whether or not the gather ever named it, so
        this one suspends on its way out — once with wall clock and again with
        a run of bare yields. Nothing short of the gather actually awaiting
        this task gets it to "sweeper finished" before `shutdown()` runs.
        """
        import types
        from power_atlas import web as web_mod

        order = []
        state = {}

        async def forever():
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                order.append("sweeper cancelled")
                # Teardown work with real suspension points in it — the shape
                # of a sweeper cancelled mid-`close_session`.
                await asyncio.sleep(0.05)
                for _ in range(5):
                    await asyncio.sleep(0)
                order.append("sweeper finished")
                raise

        def start_sweeper():
            state["task"] = asyncio.ensure_future(forever())
            order.append("sweeper started")
            return state["task"]

        def shutdown():
            # The sweeper must be finished before the agent is killed: one
            # still parked inside `close_session` would be racing its own
            # teardown, and `acp.shutdown()` is synchronous so it cannot wait.
            order.append("shutdown, sweeper done=%s" % state["task"].done())

        # `set_sessions_changed_hook` is part of what lifespan requires of the
        # acp module, so the stub carries it: the wiring that closes D32 runs
        # here, before the sweeper starts, and a stub missing it would fail for
        # a reason unrelated to the ordering this test is about.
        hooked = []
        fake = types.SimpleNamespace(start_sweeper=start_sweeper,
                                     shutdown=shutdown,
                                     set_sessions_changed_hook=hooked.append)

        async def run():
            async with web_mod.lifespan(None):
                order.append("serving")
                await asyncio.sleep(0.01)

        with patch.object(web_mod, "acp", fake):
            asyncio.run(run())

        assert order == ["sweeper started", "serving", "sweeper cancelled",
                         "sweeper finished", "shutdown, sweeper done=True"]

    def test_an_acp_import_failure_still_yields_a_running_app(self):
        """`web.py` degrades an ACP import failure to "/acp disabled". An
        unguarded sweeper start would promote it to "will not start"."""
        from power_atlas import web as web_mod

        async def run():
            async with web_mod.lifespan(None):
                return "started"

        with patch.object(web_mod, "acp", None):
            assert asyncio.run(run()) == "started"


# --- Phase 3: the remote surface is the whole authorization boundary -------
#
# D3 designed two independent layers — a NetBird access-control policy and this
# device cookie. Phase 0 measured that the policy layer does not exist: all 17
# peers on the account sit in this host's network map. D33 records the decision
# to ship anyway, so every check below is the only thing between a peer and
# `kiro-cli acp -a`. Each test here is written to fail when its one control is
# removed, because a bypassable check with a passing test beside it is the
# failure mode this phase cannot afford.

_REMOTE_IP = "100.78.26.204"
_LOCAL_BIND_IP = "100.78.142.124"
_TEST_SECRET = "T" * 43


@pytest.fixture
def remote_enabled():
    """The process state `__main__` reaches after a successful remote bind."""
    from power_atlas import web as web_mod
    web_mod.set_remote_host(_LOCAL_BIND_IP)
    web_mod.set_remote_secret(_TEST_SECRET)
    web_mod._exchange_failures.clear()
    try:
        yield web_mod
    finally:
        web_mod.set_remote_host("")
        web_mod.set_remote_secret("")
        web_mod._exchange_failures.clear()


def _peer_http(path, headers=(), *, client=(_REMOTE_IP, 33333), method="GET",
               asgi_app=None, body=b""):
    """Drive an HTTP scope with an explicit peer address, byte-for-byte headers.

    No client within reach can set `scope["client"]`, and that value is the one
    thing `RemoteAccessGuard` classifies on — so the whole remote surface is
    only reachable by building the scope uvicorn would build.
    """
    raw = list(headers)
    if not any(k.lower() == b"host" for k, _ in raw):
        raw.insert(0, (b"host", f"{_LOCAL_BIND_IP}:4915".encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": raw, "client": client,
        "server": (_LOCAL_BIND_IP, 4915), "app": app, "state": {},
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run((asgi_app or app)(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    payload = b"".join(m.get("body", b"") for m in sent
                       if m["type"] == "http.response.body")
    return status, payload, sent


def _peer_ws(path, headers=(), *, client=(_REMOTE_IP, 33333), asgi_app=None):
    raw = list(headers)
    if not any(k.lower() == b"host" for k, _ in raw):
        raw.insert(0, (b"host", f"{_LOCAL_BIND_IP}:4915".encode()))
    scope = {
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "scheme": "ws", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": raw, "client": client,
        "server": (_LOCAL_BIND_IP, 4915), "subprotocols": [], "app": app,
        "state": {},
    }
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    asyncio.run((asgi_app or app)(scope, receive, send))
    return sent


class _Reached(Exception):
    """Raised by the sentinel inner app, so "the guard let it through" is a
    distinguishable outcome rather than an indistinguishable 403."""


def _guard_over_sentinel():
    """`RemoteAccessGuard` wrapping an app that announces being reached.

    Every refusal test below has a matching pass test through this, because a
    403 from the real app could equally mean the route does not exist.
    """
    from power_atlas.web import RemoteAccessGuard

    async def _sentinel(scope, receive, send):
        raise _Reached(scope["path"])

    return RemoteAccessGuard(_sentinel)


def _cookie_header(device_id="phone", issued_at=None, secret=None):
    """Mint a cookie the way the exchange route does, for the tests that need
    a valid one — computed, never hardcoded, so a change to the derivation
    cannot leave these passing against a stale literal."""
    from power_atlas import web as web_mod
    import hashlib as _h, hmac as _hm, time as _t
    stamp = str(int(_t.time()) if issued_at is None else issued_at)
    key = (secret if secret is not None else _TEST_SECRET).encode("utf-8")
    sig = _hm.new(key, f"{device_id}.{stamp}".encode("utf-8"), _h.sha256).hexdigest()
    return (b"cookie",
            f"{web_mod._DEVICE_COOKIE_NAME}={device_id}.{stamp}.{sig}".encode())


class TestRemotenessComesFromTheTransport:
    """D26. `Host` is attacker-controlled and answers "what name did the
    browser use"; `scope["client"]` answers "where did this connection come
    from". Deriving remoteness from the header lets a NetBird peer send
    `Host: 127.0.0.1` and skip both the allowlist and the cookie."""

    @pytest.mark.parametrize("peer", ["127.0.0.1", "127.0.0.5", "::1"])
    def test_loopback_peers_are_local(self, peer):
        from power_atlas.web import _is_remote_peer
        assert _is_remote_peer(peer) is False

    @pytest.mark.parametrize("peer", [
        None, "", "testclient", "not-an-ip", "100.78.26.204", "fd00::1",
        "::ffff:100.78.26.204",  # a v4-mapped NetBird peer is still a peer
        "127.0.0.1 ", "0x7f000001",
    ])
    def test_everything_else_is_remote(self, peer):
        from power_atlas.web import _is_remote_peer
        assert _is_remote_peer(peer) is True

    def test_a_v4_mapped_loopback_is_safe_either_way(self):
        """`IPv6Address.is_loopback` delegates to the embedded v4 address from
        Python 3.13 and did not before, so a dual-stack loopback connection
        reads local on a new interpreter and remote on an old one. Both
        verdicts are safe: the old one only forces a genuine loopback client
        through the cookie, and no remote peer can make the kernel report a
        mapped-loopback peername. What must never flip is the line below."""
        from power_atlas.web import _is_remote_peer
        assert _is_remote_peer("::ffff:100.78.26.204") is True

    @pytest.mark.parametrize("path", ["/", "/api/launchers", "/api/settings"])
    def test_loopback_host_header_does_not_make_a_peer_local(self, path, remote_enabled):
        """The bypass D26 exists to close, exercised end to end."""
        status, body, _ = _peer_http(path, [(b"host", b"127.0.0.1:4915"),
                                            _cookie_header()])
        assert status == 403, f"{path} served a remote peer claiming to be loopback"
        assert b"Forbidden" in body

    def test_absent_client_is_treated_as_remote(self, remote_enabled):
        status, _, _ = _peer_http("/api/settings", client=None)
        assert status == 403


class TestRemotePathAllowlistIsDefaultDeny:
    """D6. A denylist over ~40 routes leaks by default on the next route
    added; this makes a new route loopback-only until someone lists it."""

    @pytest.mark.parametrize("path", [
        "/", "/api/launchers", "/api/settings", "/api/remote-access",
        "/api/remote-access/rotate",
        "/api/save-setting", "/partials/workspaces", "/staticfoo",
        "/staticfoo/style.css", "/acp/", "/ws/acp/x", "/remote-authx",
        "/api/save-launcher", "/api/run-launcher", "/partials/all-sessions",
    ])
    def test_non_allowlisted_paths_are_refused(self, path, remote_enabled):
        status, body, _ = _peer_http(path, [_cookie_header()])
        assert status == 403, f"{path} is reachable from the NetBird address"
        assert b"Forbidden" in body

    @pytest.mark.parametrize("path", ["/", "/api/launchers", "/api/settings"])
    def test_sc4_routes_need_loopback(self, path, remote_enabled):
        """SC-4 by name: these three carry `custom_launchers[].env`, the
        settings surface and the dashboard, and must need loopback."""
        with pytest.raises(_Reached):
            _peer_http(path, [_cookie_header()], client=("127.0.0.1", 1),
                       asgi_app=_guard_over_sentinel())
        status, _, _ = _peer_http(path, [_cookie_header()],
                                  asgi_app=_guard_over_sentinel())
        assert status == 403

    @pytest.mark.parametrize("path", [
        "/acp", "/remote-auth", "/static/style.css",
        "/static/deep/nested.js",
    ])
    def test_allowlisted_paths_pass_the_path_gate(self, path, remote_enabled):
        with pytest.raises(_Reached):
            _peer_http(path, [_cookie_header()], asgi_app=_guard_over_sentinel())

    def test_the_static_mount_is_matched_as_a_directory(self, remote_enabled):
        """A bare `startswith("/static")` also admits `/staticfoo`."""
        from power_atlas.web import _remote_path_allowed
        assert _remote_path_allowed("/static", "http") is True
        assert _remote_path_allowed("/static/style.css", "http") is True
        assert _remote_path_allowed("/staticfoo", "http") is False
        assert _remote_path_allowed("/staticfoo/style.css", "http") is False

    def test_the_allowlist_is_scope_typed(self, remote_enabled):
        """Each entry admits the protocol it was written for and no other.

        `/ws/acp` is the only websocket entry. A path-only allowlist let a
        websocket upgrade to `/static/...` through on the cookie alone, and
        `StaticFiles.__call__` opens with `assert scope["type"] == "http"` —
        so the mount entry turned an upgrade into an unhandled `AssertionError`
        instead of a refusal. Post-authentication, so noise rather than a
        boundary failure, but `/static` + websocket is the stated reason this
        guard exists.
        """
        from power_atlas.web import _remote_path_allowed
        assert _remote_path_allowed("/ws/acp", "websocket") is True
        assert _remote_path_allowed("/ws/acp", "http") is False
        for path in ("/acp", "/remote-auth", "/static", "/static/style.css"):
            assert _remote_path_allowed(path, "http") is True, path
            assert _remote_path_allowed(path, "websocket") is False, path

    def test_a_websocket_upgrade_to_the_static_mount_is_refused(self, remote_enabled):
        """End-to-end through the guard, because the unit test above cannot
        show that the guard passes the scope type at all."""
        sent = _peer_ws("/static/style.css", [_cookie_header()],
                        asgi_app=_guard_over_sentinel())
        assert sent == [{"type": "websocket.close", "code": 1008}]

    def test_the_websocket_route_still_passes_the_gate(self, remote_enabled):
        """The refusal above must not have been bought by closing `/ws/acp`."""
        with pytest.raises(_Reached):
            _peer_ws("/ws/acp", [_cookie_header()],
                     asgi_app=_guard_over_sentinel())


def _stop_switch_reset():
    """Restore the surface, whatever a test did to the variable holding it."""
    from power_atlas import web as web_mod
    web_mod.set_remote_stopped(False)


class TestTheRuntimeStopSwitchFailsClosed:
    """Disabling remote control without restarting PowerAtlas.

    The user chose *refuse every remote request immediately* over *close the
    socket*, understanding that the port stays bound until the process
    restarts. So what is pinned here is refusal, never a closed listener.

    **The failure direction is the whole design**, which is why most of these
    checks are not "stopping stops" but "every way this can break refuses".
    Written the obvious way — `if _remote_stopped: refuse` — that flag becomes
    the only thing between a remote peer and the app, so an unset, inverted,
    shadowed or half-applied flag leaves remote access live while the user
    believes it is off. Fail-open, silently, on the one control that exists for
    the moment the user wants the machine off the network.

    The implementation instead installs the empty map into the allowlist
    `_remote_path_allowed` already reads, and that lookup is default-deny (D6).
    A broken switch is therefore a surface that admits nothing: a bug disables
    remote access, not the guard.
    """

    # Every path a remote peer can reach when the surface is serving. Listed
    # rather than derived from `_REMOTE_ALLOWED_PATHS`, because deriving it
    # from the same map the switch empties would make the stopped half of each
    # check iterate over nothing and pass vacuously.
    _SERVED = ["/acp", "/remote-auth", "/api/acp/sessions",
               "/static", "/static/style.css", "/static/deep/nested.js"]

    @pytest.fixture(autouse=True)
    def _reset(self):
        _stop_switch_reset()
        yield
        _stop_switch_reset()

    def test_every_path_the_guard_admitted_is_refused_once_stopped(
            self, remote_enabled):
        """The pass half first, so the refusal half cannot be bought by a path
        that was never reachable. Inverting the stop check, or never folding it
        into the guard's condition at all, fails here."""
        from power_atlas import web as web_mod
        for path in self._SERVED:
            with pytest.raises(_Reached):
                _peer_http(path, [_cookie_header()],
                           asgi_app=_guard_over_sentinel())
        web_mod.set_remote_stopped(True)
        for path in self._SERVED:
            status, body, _ = _peer_http(path, [_cookie_header()],
                                         asgi_app=_guard_over_sentinel())
            assert status == 403, f"{path} still served a remote peer while stopped"
            assert body == web_mod._FORBIDDEN_BODY

    def test_the_refusal_is_the_guards_own_and_not_a_second_one(
            self, remote_enabled):
        """21 bytes of `{"error":"Forbidden"}` with a matching Content-Length.
        A stopped surface has to be byte-indistinguishable from a path that was
        never on the allowlist, or the switch is itself a probe telling an
        unauthenticated peer which state the machine is in."""
        from power_atlas import web as web_mod
        web_mod.set_remote_stopped(True)
        status, body, sent = _peer_http("/acp", [_cookie_header()])
        assert status == 403
        assert body == b'{"error":"Forbidden"}'
        assert len(body) == 21
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert (b"content-length", b"21") in start["headers"]
        assert _peer_http("/api/settings", [_cookie_header()])[1] == body, \
            "a stopped path answers differently from an unlisted one"

    def test_a_stopped_websocket_gets_the_scope_typed_close(self, remote_enabled):
        """Emitting `http.response.start` into a `websocket` scope is an ASGI
        protocol violation that surfaces as a uvicorn exception rather than a
        refusal — and `/ws/acp` is the path this switch matters most on, since
        it is the one that drives the agent."""
        from power_atlas import web as web_mod
        with pytest.raises(_Reached):
            _peer_ws("/ws/acp", [_cookie_header()],
                     asgi_app=_guard_over_sentinel())
        web_mod.set_remote_stopped(True)
        assert _peer_ws("/ws/acp", [_cookie_header()],
                        asgi_app=_guard_over_sentinel()) == [
            {"type": "websocket.close", "code": 1008}]

    def test_loopback_is_untouched_by_the_stop(self, remote_enabled):
        """The switch disables *remote* access. A dashboard that stopped
        working when the user pressed it would be a different feature — and the
        panel holding the button is itself served over loopback, so a switch
        that took loopback with it could not be undone from the UI."""
        from power_atlas import web as web_mod
        web_mod.set_remote_stopped(True)
        for path in ("/", "/api/settings", "/acp", "/api/remote-access"):
            with pytest.raises(_Reached):
                _peer_http(path, [_cookie_header()], client=("127.0.0.1", 1),
                           asgi_app=_guard_over_sentinel())

    def test_resuming_restores_the_surface_and_widens_nothing(self, remote_enabled):
        from power_atlas import web as web_mod
        web_mod.set_remote_stopped(True)
        web_mod.set_remote_stopped(False)
        for path in self._SERVED:
            with pytest.raises(_Reached):
                _peer_http(path, [_cookie_header()],
                           asgi_app=_guard_over_sentinel())
        # Resume must restore the surface, not invent a wider one: default-deny
        # still holds afterwards, including over the switch's own route.
        for path in ("/", "/api/settings", "/staticfoo",
                     "/api/remote-access", "/api/remote-access/stop"):
            assert _peer_http(path, [_cookie_header()],
                              asgi_app=_guard_over_sentinel())[0] == 403, path

    @pytest.mark.parametrize("value", [
        None, 0, "", "false", "no", 1, True, [], {}, ("no",),
    ])
    def test_only_an_exact_false_resumes(self, value, remote_enabled):
        """The mutation this exists to catch: a switch whose *default* — an
        absent field, an unparseable body, any falsy value — reads as "not
        stopped". That is the fail-open direction, and it is reachable from the
        HTTP route by sending `{}`, so it is not hypothetical.
        """
        from power_atlas import web as web_mod
        web_mod.set_remote_stopped(value)
        assert web_mod.remote_stopped() is True, \
            f"{value!r} resumed the remote surface"
        assert _peer_http("/acp", [_cookie_header()],
                          asgi_app=_guard_over_sentinel())[0] == 403

    @pytest.mark.parametrize("corrupt", [None, "", "/acp", 0, 123, ()])
    def test_a_surface_that_is_not_a_mapping_refuses_rather_than_raising(
            self, corrupt, remote_enabled, monkeypatch):
        """Every way a bad edit can leave this variable — never assigned,
        cleared, rebound to something that is not a mapping — has to land on
        refusal. A raise would be as bad as a pass: `_remote_path_allowed` runs
        on the `websocket` scope, where an exception is a broken handshake
        rather than a 500, and the `except` that would have to catch it is a
        second place to accidentally let traffic through.
        """
        from power_atlas import web as web_mod
        monkeypatch.setattr(web_mod, "_remote_surface", corrupt)
        assert web_mod._remote_path_allowed("/acp", "http") is False
        assert web_mod.remote_stopped() is True
        status, body, _ = _peer_http("/acp", [_cookie_header()])
        assert status == 403
        assert body == web_mod._FORBIDDEN_BODY
        assert _peer_ws("/ws/acp", [_cookie_header()],
                        asgi_app=_guard_over_sentinel()) == [
            {"type": "websocket.close", "code": 1008}]

    def test_the_reported_state_is_read_out_of_the_guards_own_surface(
            self, remote_enabled, monkeypatch):
        """There is no second variable, and this is what says so. The mutation
        it catches is tracking "stopped" in a boolean beside the surface, which
        can then disagree with what the guard enforces — a panel reading
        STOPPED over a surface that is still serving is exactly the failure the
        switch exists to prevent."""
        from power_atlas import web as web_mod
        monkeypatch.setattr(web_mod, "_remote_surface", {})
        assert web_mod.remote_stopped() is True
        assert _peer_http("/acp", [_cookie_header()],
                          asgi_app=_guard_over_sentinel())[0] == 403
        monkeypatch.setattr(web_mod, "_remote_surface",
                            web_mod._REMOTE_ALLOWED_PATHS)
        assert web_mod.remote_stopped() is False
        with pytest.raises(_Reached):
            _peer_http("/acp", [_cookie_header()],
                       asgi_app=_guard_over_sentinel())

    def test_the_static_mount_goes_with_the_surface(self, remote_enabled):
        """The mount is an entry in the map rather than a literal inside the
        matcher, and that placement is what makes an empty map an empty
        surface. A `startswith("/static")` branch outside the map keeps serving
        the stylesheet and every page asset to a remote peer after the switch
        was pressed — and the page they belong to would look half-alive."""
        from power_atlas import web as web_mod
        assert web_mod._REMOTE_ALLOWED_PATHS[web_mod._REMOTE_STATIC_MOUNT] == "http"
        web_mod.set_remote_stopped(True)
        for path in ("/static", "/static/style.css", "/static/deep/nested.js"):
            assert web_mod._remote_path_allowed(path, "http") is False, path
            assert _peer_http(path, [_cookie_header()],
                              asgi_app=_guard_over_sentinel())[0] == 403, path


class TestTheStopRoute:
    """`POST /api/remote-access/stop`, the loopback-only control for the switch
    above. Loopback-only by the same default-deny allowlist `/api/remote-access`
    and `/api/remote-access/rotate` rely on, not a second mechanism."""

    _PATH = "/api/remote-access/stop"

    @pytest.fixture(autouse=True)
    def _reset(self):
        _stop_switch_reset()
        yield
        _stop_switch_reset()

    def test_the_route_is_loopback_only(self, remote_enabled):
        """Both directions matter. A remote peer must not resume a surface its
        owner stopped — and must not be able to *stop* it either, which would
        be a denial of service against the owner's own phone, driven from a
        peer that never authenticated. Adding this path to
        `_REMOTE_ALLOWED_PATHS` makes this fail."""
        from power_atlas import web as web_mod
        assert self._PATH not in web_mod._REMOTE_ALLOWED_PATHS
        status, body, _ = _peer_http(
            self._PATH,
            [_cookie_header(),
             (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode()),
             (b"content-type", b"application/json")],
            method="POST", body=b'{"stopped": true}')
        assert status == 403
        assert b"Forbidden" in body
        assert web_mod.remote_stopped() is False, \
            "a remote peer reached the switch"

    def test_the_route_stops_and_resumes_the_running_process(
            self, client, remote_enabled):
        from power_atlas import web as web_mod
        body = client.post(self._PATH, json={"stopped": True}).json()
        assert body["ok"] is True and body["stopped"] is True
        assert web_mod.remote_stopped() is True
        assert _peer_http("/acp", [_cookie_header()],
                          asgi_app=_guard_over_sentinel())[0] == 403
        body = client.post(self._PATH, json={"stopped": False}).json()
        assert body["stopped"] is False
        with pytest.raises(_Reached):
            _peer_http("/acp", [_cookie_header()],
                       asgi_app=_guard_over_sentinel())

    @pytest.mark.parametrize("payload", [
        {}, {"stopped": None}, {"stopped": "false"}, {"stopped": 0},
        {"stoped": False}, {"stopped": []}, [], "false", 0,
    ])
    def test_anything_but_an_explicit_false_stops(
            self, client, payload, remote_enabled):
        """The route's fail-closed direction, driven from a real request. A
        typo'd field name, a string `"false"` from a hand-rolled client, a body
        that is not an object — none of them may re-open the boundary."""
        from power_atlas import web as web_mod
        body = client.post(self._PATH, json=payload).json()
        assert body["stopped"] is True, f"{payload!r} resumed remote access"
        assert web_mod.remote_stopped() is True
        assert _peer_http("/acp", [_cookie_header()],
                          asgi_app=_guard_over_sentinel())[0] == 403

    @pytest.mark.parametrize("raw", [b"{not json", b"", b"null"])
    def test_a_body_that_is_not_an_object_stops(self, client, raw, remote_enabled):
        """An unparseable body is not an error path here: it is precisely the
        case that must not resume, so it falls into the stopping direction
        rather than into a 400 the caller might retry differently."""
        from power_atlas import web as web_mod
        resp = client.post(self._PATH, content=raw,
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        assert resp.json()["stopped"] is True
        assert web_mod.remote_stopped() is True

    def test_the_reply_reports_what_is_in_force_not_what_was_asked(
            self, client, remote_enabled):
        """`stopped` is read back out of the guard's surface rather than echoed
        from the request, so a caller that sent nonsense is told what actually
        happened."""
        body = client.post(self._PATH, json={"stopped": "nope"}).json()
        assert body["stopped"] is True
        assert body["persisted"] is False
        assert body["socket_closed"] is False, \
            "the reply claims a closed socket; the port stays bound until restart"
        assert "restart" in body["message"] and "config.toml" in body["message"]

    def test_the_switch_never_writes_config_toml(
            self, client, isolated_config, remote_enabled):
        """A runtime switch, by the user's explicit choice.
        `remote_bind_address` is what a restart reads and this route
        deliberately leaves it alone — a kill switch that rewrites the
        configuration is one the user has to undo twice, and a restart would
        then come back up refusing everything with nothing on screen saying
        why."""
        from power_atlas import config as config_mod
        config_mod.save_config(config_mod.load_config())
        path = isolated_config / "config.toml"
        before = path.read_bytes()
        assert client.post(self._PATH, json={"stopped": True}).json()["stopped"] is True
        assert client.post(self._PATH, json={"stopped": False}).json()["stopped"] is False
        assert path.read_bytes() == before, \
            "the runtime switch wrote to config.toml"

    def test_the_secret_route_reports_the_state_in_force(
            self, client, remote_enabled):
        """The panel renders off this payload, so the field has to be here or
        the button cannot know which of Stop and Resume to draw."""
        assert client.get("/api/remote-access").json()["stopped"] is False
        client.post(self._PATH, json={"stopped": True})
        assert client.get("/api/remote-access").json()["stopped"] is True

    def test_the_route_needs_a_post(self, client):
        """POST is what puts it under `same_origin_guard`'s Origin/Referer
        check; a GET would be reachable by a cross-origin `<img src>`."""
        assert client.get(self._PATH).status_code == 405

    def test_the_route_is_csrf_protected(self, raw_client, remote_enabled):
        """A page on another origin must not be able to stop — or resume — the
        surface. The resume direction is the one that matters most."""
        from power_atlas import web as web_mod
        web_mod.set_remote_stopped(True)
        resp = raw_client.post(self._PATH, json={"stopped": False},
                               headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403
        assert web_mod.remote_stopped() is True, \
            "a cross-origin POST resumed the surface anyway"

    def test_the_route_is_not_cacheable(self, client):
        resp = client.post(self._PATH, json={"stopped": True})
        assert resp.status_code == 200
        assert "stopped" in resp.json(), "the assertion below would be vacuous"
        assert resp.headers["cache-control"] == "no-store"


class TestRemoteRequestsNeedTheCookie:
    """SC-5. With the NetBird policy layer absent (D33), this is the control."""

    @pytest.mark.parametrize("path", ["/acp", "/static/style.css"])
    def test_http_without_a_cookie_is_refused(self, path, remote_enabled):
        status, body, _ = _peer_http(path)
        assert status == 403
        assert b"Forbidden" in body

    @pytest.mark.parametrize("path", ["/acp", "/static/style.css"])
    def test_http_with_a_valid_cookie_is_served(self, path, remote_enabled):
        status, _, _ = _peer_http(path, [_cookie_header()])
        assert status == 200, f"{path} refused a device holding a valid cookie"

    def test_the_ws_acp_upgrade_without_a_cookie_is_closed(self, remote_enabled):
        sent = _peer_ws("/ws/acp")
        assert sent and sent[0]["type"] == "websocket.close"
        assert sent[0]["code"] == 1008
        assert not any(m["type"] == "http.response.start" for m in sent)

    def test_ws_static_is_refused_and_never_reaches_the_mount(self, remote_enabled):
        """D7's second finding: `/static` is a `Mount` whose `matches` admits
        websocket scopes, so `ws://<ip>/static/x` reached `StaticFiles` having
        passed neither `same_origin_guard` nor `ws_acp`."""
        sent = _peer_ws("/static/style.css")
        assert sent and sent[0]["type"] == "websocket.close"
        assert sent[0]["code"] == 1008

    def test_a_websocket_refusal_is_never_an_http_response(self, remote_enabled):
        """Emitting `http.response.start` into a websocket scope is an ASGI
        protocol violation: it surfaces as a uvicorn exception rather than a
        refusal, on the one path this guard exists to protect."""
        for path in ("/ws/acp", "/static/style.css", "/api/settings"):
            sent = _peer_ws(path)
            assert all(m["type"].startswith("websocket.") for m in sent), path

    def test_the_ws_upgrade_with_a_cookie_passes_the_guard(self, remote_enabled):
        from power_atlas.web import RemoteAccessGuard
        reached = []

        async def _sentinel(scope, receive, send):
            reached.append(scope["path"])

        _peer_ws("/ws/acp", [_cookie_header()],
                 asgi_app=RemoteAccessGuard(_sentinel))
        assert reached == ["/ws/acp"]

    def test_loopback_needs_no_cookie(self, remote_enabled):
        """The laptop's own dashboard is untouched (SC-1's second half)."""
        status, _, _ = _peer_http("/api/last-refresh", client=("127.0.0.1", 5),
                                  headers=[(b"host", b"127.0.0.1:4915")])
        assert status == 200


class TestRemoteNavigationRule:
    """Cookies are host-scoped and **port-agnostic**, so another service on any
    port of the NetBird address is "same-site" for `SameSite=Strict`. This
    applies `_acp_navigation_ok`'s `Sec-Fetch-Site` rule to remote `http` GETs
    to close the browser half of that — and to those only."""

    # `/static/*` is the path that discriminates this rule. `same_origin_guard`
    # already applies `_acp_navigation_ok` to `GET /acp`, so a test written
    # against `/acp` answers 403 whether this rule exists or not — it was, and
    # the mutation that deleted the rule left it green. Everything else on the
    # remote allowlist has no inner navigation check at all.
    @pytest.mark.parametrize("path", ["/static/style.css", "/acp"])
    @pytest.mark.parametrize("site", [b"cross-site", b"same-site"])
    def test_a_cross_site_remote_get_is_refused(self, site, path, remote_enabled):
        status, _, _ = _peer_http(
            path, [_cookie_header(), (b"sec-fetch-site", site)])
        assert status == 403

    @pytest.mark.parametrize("path", ["/static/style.css", "/acp"])
    @pytest.mark.parametrize("site", [b"same-origin", b"none"])
    def test_a_same_origin_or_typed_remote_get_is_served(self, site, path, remote_enabled):
        status, _, _ = _peer_http(
            path, [_cookie_header(), (b"sec-fetch-site", site)])
        assert status == 200

    def test_the_ws_upgrade_is_exempt(self, remote_enabled):
        """`/ws/acp` is a GET at the HTTP layer but a `websocket` ASGI scope,
        and browsers do not attach `Sec-Fetch-Site` to a handshake — the
        literal reading would break the phone client outright."""
        from power_atlas.web import RemoteAccessGuard
        reached = []

        async def _sentinel(scope, receive, send):
            reached.append(scope["path"])

        _peer_ws("/ws/acp", [_cookie_header()],
                 asgi_app=RemoteAccessGuard(_sentinel))
        assert reached == ["/ws/acp"]

    def test_a_non_browser_client_is_not_blocked_by_it(self, remote_enabled):
        """The rule falls back to the Origin/Referer check with
        `allow_missing=True`, so it constrains browsers only. **The cookie, not
        this rule, is the control against a non-browser remote client** — which
        is why the cookie tests above, not this one, carry SC-5."""
        status, _, _ = _peer_http("/acp", [_cookie_header()])
        assert status == 200
        assert _peer_http("/acp")[0] == 403


class TestDeviceCookieVerification:
    @pytest.mark.parametrize("mutate", [
        lambda d, t, s: (d, t, "0" * 64),                      # forged signature
        lambda d, t, s: (d, t, s[:-1] + ("0" if s[-1] != "0" else "1")),
        lambda d, t, s: ("other", t, s),                       # id swapped
        lambda d, t, s: (d, str(int(t) - 1), s),               # stamp swapped
        lambda d, t, s: (d, t, ""),                            # empty signature
        lambda d, t, s: (d, "", s),                            # empty stamp
        lambda d, t, s: ("", t, s),                            # empty id
        lambda d, t, s: ("a;b", t, s),                         # id outside charset
        lambda d, t, s: (d, "٤٩١٥", s),    # non-ASCII digits
        lambda d, t, s: (d, t, "é" * 64),                 # non-ASCII signature
    ])
    def test_a_mutated_cookie_is_refused(self, mutate, remote_enabled):
        from power_atlas import web as web_mod
        device, stamp = "phone", str(int(time.time()))
        sig = web_mod._device_cookie_sig(_TEST_SECRET, device, stamp)
        d, t, s = mutate(device, stamp, sig)
        scope = {"headers": [(b"cookie",
                              f"{web_mod._DEVICE_COOKIE_NAME}={d}.{t}.{s}".encode(
                                  "utf-8", "replace"))]}
        assert web_mod._cookie_ok(scope) is False

    def test_the_unmutated_cookie_is_accepted(self, remote_enabled):
        """Positive control: the rejections above are not a broken helper."""
        from power_atlas import web as web_mod
        scope = {"headers": [_cookie_header()]}
        assert web_mod._cookie_ok(scope) is True

    def test_an_expired_cookie_is_refused(self, remote_enabled):
        from power_atlas import web as web_mod
        old = int(time.time()) - web_mod.REMOTE_COOKIE_MAX_AGE_SECONDS - 1
        scope = {"headers": [_cookie_header(issued_at=old)]}
        assert web_mod._cookie_ok(scope) is False

    def test_a_cookie_just_inside_the_age_limit_is_accepted(self, remote_enabled):
        from power_atlas import web as web_mod
        fresh = int(time.time()) - web_mod.REMOTE_COOKIE_MAX_AGE_SECONDS + 60
        scope = {"headers": [_cookie_header(issued_at=fresh)]}
        assert web_mod._cookie_ok(scope) is True

    def test_a_far_future_cookie_is_refused(self, remote_enabled):
        """Without a future bound, a forged-clock stamp is an unbounded life."""
        from power_atlas import web as web_mod
        ahead = int(time.time()) + web_mod._COOKIE_FUTURE_SKEW_SECONDS + 60
        scope = {"headers": [_cookie_header(issued_at=ahead)]}
        assert web_mod._cookie_ok(scope) is False

    def test_a_cookie_signed_with_another_secret_is_refused(self, remote_enabled):
        from power_atlas import web as web_mod
        scope = {"headers": [_cookie_header(secret="X" * 43)]}
        assert web_mod._cookie_ok(scope) is False

    def test_a_short_secret_refuses_every_cookie(self, remote_enabled):
        """`_cookie_ok` re-checks the length floor `set_remote_secret` already
        applied. Reducing it to `if not secret:` left the whole suite green,
        because the only test that claimed to justify it set the secret to `""`
        — which exercises the *emptiness* branch and says nothing about the
        floor.

        The floor is what makes a truncated secret file fail closed rather than
        becoming a short, brute-forceable HMAC key. Set a non-empty
        under-length secret directly, and mint the cookie with that same
        secret, so the refusal cannot be attributed to a signature mismatch.
        """
        from power_atlas import web as web_mod
        short = "short"
        assert 0 < len(short) < web_mod.REMOTE_SECRET_MIN_LEN
        header = _cookie_header(secret=short)
        web_mod._REMOTE_SECRET = short
        try:
            assert web_mod._cookie_ok({"headers": [header]}) is False
        finally:
            web_mod.set_remote_secret(_TEST_SECRET)

    def test_a_secret_at_the_floor_is_accepted(self, remote_enabled):
        """Positive control: the refusal above is the length check, not an
        unrelated failure in the mint helper."""
        from power_atlas import web as web_mod
        exact = "Y" * web_mod.REMOTE_SECRET_MIN_LEN
        header = _cookie_header(secret=exact)
        web_mod._REMOTE_SECRET = exact
        try:
            assert web_mod._cookie_ok({"headers": [header]}) is True
        finally:
            web_mod.set_remote_secret(_TEST_SECRET)

    def test_the_cookie_survives_a_restart(self, remote_enabled):
        """D24's whole premise: verifiable with no server-side store, so the
        phone does not re-enter the secret after every launch."""
        from power_atlas import web as web_mod
        header = _cookie_header()
        web_mod.set_remote_secret("")          # process exits
        assert web_mod._cookie_ok({"headers": [header]}) is False
        web_mod.set_remote_secret(_TEST_SECRET)  # and comes back up
        assert web_mod._cookie_ok({"headers": [header]}) is True

    def test_a_cookie_among_others_is_found(self, remote_enabled):
        from power_atlas import web as web_mod
        name, value = _cookie_header()[1].decode().split("=", 1)
        scope = {"headers": [(b"cookie",
                              f"other=1; {name}={value}; third=x".encode())]}
        assert web_mod._cookie_ok(scope) is True

    @pytest.mark.parametrize("raw", [
        b"", b"=", b";;;", b"pa_device", b"pa_device=", b"pa_device=..",
        b"pa_device=" + b"A" * 4000, b"\xff\xfe", b"pa_device=a.b",
    ])
    def test_a_malformed_cookie_header_never_raises(self, raw, remote_enabled):
        from power_atlas import web as web_mod
        assert web_mod._cookie_ok({"headers": [(b"cookie", raw)]}) is False

    @pytest.mark.parametrize("device_id", ["a b", "a,b", "a/b", "a@b", "x" * 65])
    def test_a_correctly_signed_out_of_charset_id_is_still_refused(
            self, device_id, remote_enabled):
        """The charset bound is enforced at **both** ends, not only where the
        cookie is minted. A cookie produced by any other minting path — an
        older build, a future one, a hand-rolled script holding the secret —
        must not be able to carry a cookie-attribute or log-injection
        character through the verifier just because its signature is right."""
        from power_atlas import web as web_mod
        stamp = str(int(time.time()))
        sig = web_mod._device_cookie_sig(_TEST_SECRET, device_id, stamp)
        scope = {"headers": [
            (b"cookie",
             f"pa_device={device_id}.{stamp}.{sig}".encode("utf-8", "replace"))]}
        assert web_mod._cookie_ok(scope) is False

    def test_make_device_cookie_refuses_the_same_ids(self, remote_enabled):
        from power_atlas.web import make_device_cookie
        assert make_device_cookie("a b") == ""
        assert make_device_cookie("x" * 65) == ""
        assert make_device_cookie("phone") != ""


class TestSecretFailsClosed:
    """A missing or empty secret compared with `compare_digest` would match an
    empty signature and silently remove authentication while the bind stayed
    open. Every unusable form has to collapse to "refuse everything"."""

    @pytest.mark.parametrize("secret", ["", "   ", "short", "T" * 42])
    def test_an_unusable_secret_refuses_every_remote_request(self, secret, caplog):
        from power_atlas import web as web_mod
        web_mod.set_remote_host(_LOCAL_BIND_IP)
        try:
            with caplog.at_level(logging.ERROR, logger="power_atlas.web"):
                web_mod.set_remote_secret(secret)
            assert web_mod._REMOTE_SECRET == ""
            for path in ("/acp", "/static/style.css", "/remote-auth"):
                status, _, _ = _peer_http(path, [_cookie_header(secret=secret or "x")])
                assert status in (403, 503), path
            assert _peer_ws("/ws/acp")[0]["type"] == "websocket.close"
        finally:
            web_mod.set_remote_host("")
            web_mod.set_remote_secret("")

    def test_a_short_secret_logs_at_error(self, caplog):
        from power_atlas import web as web_mod
        try:
            with caplog.at_level(logging.ERROR, logger="power_atlas.web"):
                web_mod.set_remote_secret("T" * 42)
            assert any(r.levelno >= logging.ERROR for r in caplog.records)
        finally:
            web_mod.set_remote_secret("")

    def test_an_empty_secret_does_not_match_an_empty_signature(self):
        from power_atlas import web as web_mod
        web_mod.set_remote_secret("")
        scope = {"headers": [(b"cookie", b"pa_device=phone.1.")]}
        assert web_mod._cookie_ok(scope) is False

    def test_an_empty_secret_cannot_be_signed_around(self):
        """The `compare_digest` trap in full, and the reason the length floor
        is repeated inside `_cookie_ok` rather than trusted from the setter.

        With no secret, HMAC keyed by the empty string is computable by anyone
        on the network — so the signature check alone would happily verify a
        cookie signed under nothing. It is the "no usable secret" refusal, not
        the comparison, that closes this."""
        import hashlib as _h, hmac as _hm
        from power_atlas import web as web_mod
        web_mod.set_remote_secret("")
        stamp = str(int(time.time()))
        sig = _hm.new(b"", f"phone.{stamp}".encode(), _h.sha256).hexdigest()
        scope = {"headers": [
            (b"cookie", f"pa_device=phone.{stamp}.{sig}".encode())]}
        assert web_mod._cookie_ok(scope) is False

    def test_load_remote_secret_collapses_every_unusable_form(self, tmp_path, monkeypatch):
        from power_atlas import config as config_mod
        path = tmp_path / "remote-secret"
        monkeypatch.setattr(config_mod, "REMOTE_SECRET_PATH", path)
        assert config_mod.load_remote_secret() == ""       # absent
        path.write_text("")
        assert config_mod.load_remote_secret() == ""       # empty
        path.write_text("   \n")
        assert config_mod.load_remote_secret() == ""       # whitespace
        path.write_text("T" * 42)
        assert config_mod.load_remote_secret() == ""       # one short
        path.write_text("T" * 43 + "\n")
        assert config_mod.load_remote_secret() == "T" * 43

    def test_ensure_remote_secret_does_not_rotate_an_existing_one(self, tmp_path, monkeypatch):
        """Re-issuing here would revoke every device that already holds a
        cookie, on a route the user thinks only sets an address."""
        from power_atlas import config as config_mod
        path = tmp_path / "remote-secret"
        monkeypatch.setattr(config_mod, "REMOTE_SECRET_PATH", path)
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        first = config_mod.ensure_remote_secret()
        assert len(first) >= config_mod.REMOTE_SECRET_MIN_LEN
        assert config_mod.ensure_remote_secret() == first

    def test_the_secret_never_reaches_a_log_record(self, tmp_path, monkeypatch, caplog):
        """`orchestrator.log` is a plain file next to config.toml."""
        from power_atlas import config as config_mod, web as web_mod
        path = tmp_path / "remote-secret"
        monkeypatch.setattr(config_mod, "REMOTE_SECRET_PATH", path)
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        with caplog.at_level(logging.DEBUG):
            secret = config_mod.ensure_remote_secret()
            web_mod.set_remote_secret(secret)
            try:
                _peer_http("/remote-auth", method="POST",
                           body=f"device_id=phone&secret={secret}".encode(),
                           headers=[(b"content-type",
                                     b"application/x-www-form-urlencoded"),
                                    (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode())])
            finally:
                web_mod.set_remote_secret("")
        assert secret not in caplog.text


class TestSecretExchange:
    def test_the_form_is_reachable_without_a_cookie(self, remote_enabled):
        status, body, _ = _peer_http("/remote-auth")
        assert status == 200
        assert b"<form" in body

    def test_the_exchange_is_the_only_cookie_exempt_path(self):
        from power_atlas.web import _COOKIE_EXEMPT, _REMOTE_AUTH_PATH
        assert _COOKIE_EXEMPT == frozenset({_REMOTE_AUTH_PATH})

    def _post(self, secret, device_id="phone", peer=_REMOTE_IP):
        return _peer_http(
            "/remote-auth", method="POST", client=(peer, 4444),
            body=f"device_id={device_id}&secret={secret}".encode(),
            headers=[(b"content-type", b"application/x-www-form-urlencoded"),
                     (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode())])

    def test_the_right_secret_sets_a_usable_cookie(self, remote_enabled):
        from power_atlas import web as web_mod
        status, _, sent = self._post(_TEST_SECRET)
        assert status == 200
        headers = dict(next(m for m in sent if m["type"] == "http.response.start")["headers"])
        raw = headers[b"set-cookie"].decode()
        assert "HttpOnly" in raw
        assert "samesite=strict" in raw.lower()
        assert "secure" not in raw.lower()   # D5: no TLS, WireGuard carries it
        assert f"Max-Age={web_mod.REMOTE_COOKIE_MAX_AGE_SECONDS}" in raw
        value = raw.split(";")[0].split("=", 1)[1]
        assert web_mod._cookie_ok(
            {"headers": [(b"cookie", f"pa_device={value}".encode())]}) is True

    def test_a_wrong_secret_is_refused_and_logged_with_the_peer(self, remote_enabled, caplog):
        with caplog.at_level(logging.WARNING, logger="power_atlas.web"):
            status, _, _ = self._post("W" * 43)
        assert status == 403
        assert any(_REMOTE_IP in r.getMessage() for r in caplog.records
                   if r.levelno == logging.WARNING)

    @pytest.mark.parametrize("device_id", [
        "", "a;b", "a,b", "a b", "a.b", "x" * 65, "phone%0d%0a",
    ])
    def test_an_out_of_charset_device_id_is_refused(self, device_id, remote_enabled):
        status, _, _ = self._post(_TEST_SECRET, device_id=device_id)
        assert status == 400, f"device id {device_id!r} was accepted"

    def test_repeated_failures_are_backed_off(self, remote_enabled):
        peer = "100.78.9.9"
        assert self._post("W" * 43, peer=peer)[0] == 403
        assert self._post("W" * 43, peer=peer)[0] == 429
        # The backoff throttles before the secret is compared, so even the
        # right one is refused while the lockout stands.
        assert self._post(_TEST_SECRET, peer=peer)[0] == 429

    def test_a_throttled_peer_is_logged_once_per_lockout_window(self, remote_enabled, caplog):
        """`/remote-auth` is reachable by an unauthenticated remote peer by
        construction, and the 429 costs that peer nothing to retry — so a WARNING
        on every refused attempt is a remote write-amplification primitive
        against `orchestrator.log`: one line per request, at whatever rate the
        peer can issue them, for as long as it cares to.

        The bound is per lockout *window*, not a blanket suppression: the first
        refusal in each window is still recorded, because that is the rate at
        which the line carries new information. Removing `_claim_throttle_warning`
        and logging unconditionally puts all 20 lines back.
        """
        from power_atlas import web as web_mod
        peer = "100.78.9.11"
        assert self._post("W" * 43, peer=peer)[0] == 403      # opens the window
        with caplog.at_level(logging.WARNING, logger="power_atlas.web"):
            for _ in range(20):
                assert self._post("W" * 43, peer=peer)[0] == 429
        throttle_lines = [r for r in caplog.records
                          if "throttled for peer" in r.getMessage()]
        assert len(throttle_lines) == 1, \
            f"20 refused attempts wrote {len(throttle_lines)} log lines"
        assert peer in throttle_lines[0].getMessage()

    def test_a_new_lockout_window_is_logged_again(self, remote_enabled, caplog):
        """The suppression must not be permanent — a peer that comes back after
        its lockout expires, fails again and is re-locked is new information, and
        `warned=True` sticking across windows would hide a sustained attack after
        its first minute."""
        from power_atlas import web as web_mod
        peer = "100.78.9.12"
        assert self._post("W" * 43, peer=peer)[0] == 403
        with caplog.at_level(logging.WARNING, logger="power_atlas.web"):
            assert self._post("W" * 43, peer=peer)[0] == 429   # logs
            assert self._post("W" * 43, peer=peer)[0] == 429   # silent
            # Expire the lockout the way time would, then let the peer fail
            # again — which opens a fresh window via `_record_exchange_failure`.
            count, _, _ = web_mod._exchange_failures[peer]
            web_mod._exchange_failures[peer] = (count, 0.0, True)
            assert self._post("W" * 43, peer=peer)[0] == 403   # not throttled
            assert self._post("W" * 43, peer=peer)[0] == 429   # logs again
        throttle_lines = [r for r in caplog.records
                          if "throttled for peer" in r.getMessage()]
        assert len(throttle_lines) == 2, \
            "the second lockout window must be logged as well as the first"

    def test_a_success_clears_the_backoff(self, remote_enabled):
        peer = "100.78.9.10"
        assert self._post("W" * 43, peer=peer)[0] == 403
        from power_atlas import web as web_mod
        web_mod._exchange_failures.pop(peer, None)
        assert self._post(_TEST_SECRET, peer=peer)[0] == 200
        assert peer not in web_mod._exchange_failures

    def test_the_failure_table_is_bounded(self, remote_enabled):
        """`_exchange_failures` is keyed by peer address, and `/remote-auth` is
        reachable unauthenticated, so an attacker cycling source addresses
        grows it once per address. Deleting `_EXCHANGE_MAX_TRACKED_PEERS`
        entirely left the suite green — the eviction was documented and
        unpinned.

        600 distinct peers against a 512 ceiling: the table must not have
        grown past the ceiling, and must still be tracking (not cleared).
        """
        from power_atlas import web as web_mod
        web_mod._exchange_failures.clear()
        for i in range(600):
            web_mod._record_exchange_failure(f"100.64.{i // 256}.{i % 256}")
        assert len(web_mod._exchange_failures) <= web_mod._EXCHANGE_MAX_TRACKED_PEERS
        assert len(web_mod._exchange_failures) == web_mod._EXCHANGE_MAX_TRACKED_PEERS
        web_mod._exchange_failures.clear()

    def test_eviction_is_oldest_first_and_keeps_the_recent_peer(self, remote_enabled):
        """Evicting the *newest* entry would let an attacker who overflows the
        table clear their own backoff, which is the failure mode the bound is
        supposed to close rather than open."""
        from power_atlas import web as web_mod
        web_mod._exchange_failures.clear()
        first = "100.64.255.254"
        web_mod._record_exchange_failure(first)
        for i in range(web_mod._EXCHANGE_MAX_TRACKED_PEERS + 10):
            web_mod._record_exchange_failure(f"100.65.{i // 256}.{i % 256}")
        assert first not in web_mod._exchange_failures, "oldest should be evicted"
        assert "100.65.2.9" in web_mod._exchange_failures, "newest must survive"
        web_mod._exchange_failures.clear()

    def test_an_oversized_declared_body_is_refused_before_it_is_read(self, remote_enabled):
        """`/remote-auth` is the ONE path an unauthenticated remote peer can
        reach, so an unbounded `await request.body()` there is a remote
        resource-exhaustion primitive. Measured unbounded: 64 MiB drove
        268.7 MB of peak RSS.

        The per-peer backoff does not bound it — it is read at request entry,
        so concurrent oversized POSTs from an already-throttled peer are all
        buffered before any records a failure.
        """
        from power_atlas import web as web_mod
        oversized = web_mod._REMOTE_AUTH_MAX_BODY + 1
        status, _, _ = _peer_http(
            "/remote-auth", method="POST", client=("100.64.200.1", 4444),
            body=b"x" * oversized,
            headers=[(b"content-type", b"application/x-www-form-urlencoded"),
                     (b"content-length", str(oversized).encode()),
                     (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode())])
        assert status == 413
        web_mod._exchange_failures.clear()

    def test_a_lying_content_length_is_caught_while_streaming(self, remote_enabled):
        """`Content-Length` is attacker-controlled, and a chunked request
        carries none at all, so the declared-length check cannot be the only
        one. `_peer_http` sends the body regardless of the header."""
        from power_atlas import web as web_mod
        status, _, _ = _peer_http(
            "/remote-auth", method="POST", client=("100.64.200.2", 4444),
            body=b"x" * (web_mod._REMOTE_AUTH_MAX_BODY + 1),
            headers=[(b"content-type", b"application/x-www-form-urlencoded"),
                     (b"content-length", b"12"),
                     (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode())])
        assert status == 413
        web_mod._exchange_failures.clear()

    def test_a_field_flood_is_refused_rather_than_parsed(self, remote_enabled):
        """`parse_qsl` builds every pair before anything inspects them: a body
        of 1,000,000 fields cost 1.03 s of **synchronous** CPU on the event
        loop, stalling every websocket and the dashboard. `max_num_fields`
        reports the overflow by raising, which must surface as a refusal and
        not as a 500 an unauthenticated peer can drive.
        """
        from power_atlas import web as web_mod
        flood = b"&".join(b"a=1" for _ in range(web_mod._REMOTE_AUTH_MAX_FIELDS + 5))
        assert len(flood) <= web_mod._REMOTE_AUTH_MAX_BODY, \
            "must overflow on field count, not on bytes"
        status, _, _ = _peer_http(
            "/remote-auth", method="POST", client=("100.64.200.3", 4444),
            body=flood,
            headers=[(b"content-type", b"application/x-www-form-urlencoded"),
                     (b"content-length", str(len(flood)).encode()),
                     (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode())])
        assert status == 413
        web_mod._exchange_failures.clear()

    def test_a_normal_sized_body_still_authenticates(self, remote_enabled):
        """Positive control: the caps above are not simply refusing everything.
        The real form posts well under 200 bytes."""
        from power_atlas import web as web_mod
        body = f"device_id=phone&secret={_TEST_SECRET}".encode()
        assert len(body) < 200
        status, _, _ = self._post(_TEST_SECRET, peer="100.64.200.4")
        assert status == 200
        web_mod._exchange_failures.clear()

    def test_the_exchange_post_still_needs_a_same_origin(self, remote_enabled):
        """`_COOKIE_EXEMPT` exempts the cookie, not `same_origin_guard`."""
        status, _, _ = _peer_http(
            "/remote-auth", method="POST",
            body=f"device_id=phone&secret={_TEST_SECRET}".encode(),
            headers=[(b"content-type", b"application/x-www-form-urlencoded"),
                     (b"origin", b"http://evil.com")])
        assert status == 403

    def test_other_methods_are_not_exempt_by_path(self, remote_enabled):
        status, _, _ = _peer_http("/remote-auth", method="DELETE")
        assert status == 405


class TestRemoteBindDoesNotWidenTheHostAllowlist:
    @pytest.mark.parametrize("host", _HOSTILE_HOSTS)
    def test_hostile_hosts_still_refused_with_the_bind_on(self, host, remote_enabled):
        """The fixture docstring's rule holds with the remote bind enabled:
        the set gains exactly one IP, not a relaxation."""
        status, body, _ = _peer_http(
            "/acp", [(b"host", host.encode()), _cookie_header()])
        assert status == 403, f"Host: {host} was served with the remote bind on"
        assert _ACP_TOKEN.encode() not in body

    def test_the_configured_ip_is_admitted(self, remote_enabled):
        from power_atlas.web import _host_allowed
        assert _host_allowed(f"{_LOCAL_BIND_IP}:4915") is True
        assert _host_allowed(_LOCAL_BIND_IP) is True

    def test_it_is_loopback_only_by_default(self):
        from power_atlas import web as web_mod
        assert web_mod._ALLOWED_HOSTS == web_mod._LOOPBACK_HOSTS
        assert web_mod._host_allowed(f"{_LOCAL_BIND_IP}:4915") is False

    def test_unsetting_restores_loopback_only(self):
        from power_atlas import web as web_mod
        web_mod.set_remote_host(_LOCAL_BIND_IP)
        web_mod.set_remote_host("")
        assert web_mod._ALLOWED_HOSTS == web_mod._LOOPBACK_HOSTS

    @pytest.mark.parametrize("address", [
        "0.0.0.0", "::", "127.0.0.1", "myhost", "[fd00::1]", "fe80::1%eth0",
        "FD00::1", "not an ip",
    ])
    def test_an_invalid_address_never_widens_the_set(self, address):
        from power_atlas import web as web_mod
        try:
            web_mod.set_remote_host(address)
            assert web_mod._ALLOWED_HOSTS == web_mod._LOOPBACK_HOSTS, address
        finally:
            web_mod.set_remote_host("")

    def test_the_allowlist_is_never_read_from_config_per_request(self):
        """D15's rule applied here: `load_config` is an uncached whole-file
        TOML parse and this runs on every request and every upgrade."""
        from power_atlas import web as web_mod

        def _explode():
            raise AssertionError("load_config on the Host-check path")

        with patch.object(web_mod, "load_config", _explode):
            assert web_mod._host_allowed("127.0.0.1:4915") is True
            web_mod.set_remote_host(_LOCAL_BIND_IP)
            try:
                assert web_mod._host_allowed(f"{_LOCAL_BIND_IP}:4915") is True
                web_mod.set_remote_secret(_TEST_SECRET)
                assert _peer_http("/static/style.css",
                                  [_cookie_header()])[0] == 200
            finally:
                web_mod.set_remote_host("")
                web_mod.set_remote_secret("")


class TestRemoteBindAddressValidation:
    @pytest.mark.parametrize("address", [
        "0.0.0.0", "::", "::0", "0000::", "::ffff:0.0.0.0", "0:0:0:0:0:0:0:0",
        "127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1",
        "224.0.0.1", "ff02::1",
        "powerlaptop", "powerlaptop.netbird.cloud", "not an ip",
        "[fd00::1]", "fd00::1%eth0", "FD00::1", "100.078.142.124",
        " 100.78.142.124 x", "1" * 60,
    ])
    def test_rejected(self, address):
        from power_atlas.config import validate_remote_bind_address
        assert validate_remote_bind_address(address, 4915) != "", address

    def test_empty_means_loopback_only_and_is_valid(self):
        from power_atlas.config import validate_remote_bind_address
        assert validate_remote_bind_address("", 4915) == ""
        assert validate_remote_bind_address("   ", 4915) == ""

    @pytest.mark.parametrize("address", ["100.78.142.124", "fd00::1", "10.0.0.5"])
    def test_accepted(self, address):
        from power_atlas.config import validate_remote_bind_address
        assert validate_remote_bind_address(address, 4915) == ""

    def test_a_zero_port_is_rejected_by_name(self):
        """SC-3b. With `port = 0` the OS assigns per bind call, so the two
        sockets would land on different numbers and the laptop and phone URLs
        would permanently disagree."""
        from power_atlas.config import validate_remote_bind_address
        reason = validate_remote_bind_address("100.78.142.124", 0)
        assert "port" in reason.lower()

    def test_load_config_sanitises_rather_than_raising(self, tmp_path, monkeypatch, caplog):
        """`load_config` is documented as never raising and ~16 routes call it
        on the event loop; a raising validator turns one config.toml typo into
        a 500 on every route plus a startup crash."""
        from power_atlas import config as config_mod
        path = tmp_path / "config.toml"
        path.write_text('port = 4915\nremote_bind_address = "0.0.0.0"\n')
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        with caplog.at_level(logging.ERROR, logger=config_mod.__name__):
            cfg = config_mod.load_config()
        assert cfg.remote_bind_address == ""
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_load_config_drops_the_address_when_the_port_is_zero(self, tmp_path, monkeypatch):
        from power_atlas import config as config_mod
        path = tmp_path / "config.toml"
        path.write_text('port = 0\nremote_bind_address = "100.78.142.124"\n')
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        assert config_mod.load_config().remote_bind_address == ""

    def test_load_config_keeps_a_valid_address(self, tmp_path, monkeypatch):
        from power_atlas import config as config_mod
        path = tmp_path / "config.toml"
        path.write_text('port = 4915\nremote_bind_address = "100.78.142.124"\n')
        monkeypatch.setattr(config_mod, "CONFIG_PATH", path)
        assert config_mod.load_config().remote_bind_address == "100.78.142.124"


class TestEnablingRemoteAccessIsOneStep:
    """`/api/save-setting` is the whole of enabling remote access.

    The dashboard's bind-address control posts here and nowhere else, and the
    reason it can be one control rather than a procedure is that this route
    sets the address **and** issues the device secret in the same request. The
    panel's behaviour is covered in ``tests/acp_page.test.mjs``; what is pinned
    here is the contract that behaviour rests on. Split the two halves apart
    again and the UI goes on looking correct while the surface it configures
    comes up unauthenticable — which is precisely the state the hand-edited
    config.toml path used to leave the user in.

    ``isolated_config`` has already redirected ``CONFIG_PATH``, ``CONFIG_DIR``
    and ``REMOTE_SECRET_PATH`` into ``tmp_path``, so nothing here can create a
    ``remote-secret`` beside the developer's real config.
    """

    def _configured(self, tmp_path, address=""):
        (tmp_path / "config.toml").write_text(
            f'port = 4915\nremote_bind_address = "{address}"\n')

    def test_saving_an_address_issues_the_secret_in_the_same_request(
            self, client, isolated_config):
        from power_atlas import config as config_mod
        self._configured(isolated_config)
        assert not (isolated_config / "remote-secret").exists()
        resp = client.post("/api/save-setting",
                           json={"key": "remote_bind_address",
                                 "value": "100.78.142.124"})
        assert resp.json() == {"ok": True, "restart_required": True}
        assert config_mod.load_config().remote_bind_address == "100.78.142.124"
        # The half that makes it one step. Without it the address binds a socket
        # at the next launch that no device can authenticate to — or, as the
        # startup path actually behaves, does not bind at all.
        assert config_mod.load_remote_secret() != ""

    def test_saving_an_address_does_not_rotate_an_existing_secret(
            self, client, isolated_config):
        from power_atlas import config as config_mod
        self._configured(isolated_config)
        before = config_mod.ensure_remote_secret()
        assert before
        client.post("/api/save-setting",
                    json={"key": "remote_bind_address", "value": "fd00::1"})
        assert config_mod.load_remote_secret() == before, (
            "setting an address reissued the secret, signing out every device "
            "already holding a cookie on a route the user thinks only sets an "
            "address")

    def test_a_refused_address_is_reported_by_name_and_writes_nothing(
            self, client, isolated_config):
        from power_atlas import config as config_mod
        self._configured(isolated_config)
        resp = client.post("/api/save-setting",
                           json={"key": "remote_bind_address", "value": "0.0.0.0"})
        body = resp.json()
        assert body["ok"] is False
        # The panel renders this string verbatim; it is the only thing on screen
        # telling the user what to type instead.
        assert "wildcard" in body["error"]
        assert config_mod.load_config().remote_bind_address == ""
        assert not (isolated_config / "remote-secret").exists(), (
            "a refused address still created a device secret")

    def test_clearing_the_address_turns_it_off_and_keeps_the_secret(
            self, client, isolated_config):
        from power_atlas import config as config_mod
        self._configured(isolated_config, "100.78.142.124")
        before = config_mod.ensure_remote_secret()
        resp = client.post("/api/save-setting",
                           json={"key": "remote_bind_address", "value": ""})
        assert resp.json()["ok"] is True
        assert config_mod.load_config().remote_bind_address == ""
        # What the panel's hint promises: devices already enrolled work again if
        # remote access is turned back on.
        assert config_mod.load_remote_secret() == before


class TestSettingsSurface:
    # Config I/O is redirected by the module-level `isolated_config` autouse
    # fixture, which covers this class and every other test in the file.

    def test_the_acp_keys_are_writable_within_bounds(self, client):
        resp = client.post("/api/save-setting",
                           json={"key": "acp_max_sessions", "value": 8})
        assert resp.json() == {"ok": True, "restart_required": True}

    @pytest.mark.parametrize("key,value", [
        ("acp_max_sessions", 0), ("acp_max_sessions", 17),
        ("acp_idle_ttl_seconds", 299), ("acp_idle_ttl_seconds", 86401),
        ("acp_prompt_silence_seconds", 59), ("acp_prompt_silence_seconds", 7201),
    ])
    def test_out_of_range_values_are_refused(self, key, value, client):
        body = client.post("/api/save-setting",
                           json={"key": key, "value": value}).json()
        assert body["ok"] is False
        assert "between" in body["error"]

    def test_every_bounded_key_is_declared(self):
        """Adding a key to `_SETTING_TYPES` without a bound is what would turn
        the fail-closed `Unknown setting` refusal into an unbounded write.

        The assertion that matters runs in that direction: **every int key in
        `_SETTING_TYPES` must carry a bound**. The two set-equality checks
        below only restate that the declared bounds are declared, which no
        realistic regression violates — adding `"acp_future_knob": int` to
        `_SETTING_TYPES` and nothing to `_SETTING_BOUNDS` left them both green,
        which is exactly the regression this docstring warns about.

        `port` is excluded because it carries its own range check in
        `save_setting` (0 for random, else 1024-65535), which a single
        inclusive `(lo, hi)` pair cannot express.
        """
        from power_atlas.web import _SETTING_BOUNDS, _SETTING_TYPES
        unbounded = {k for k, t in _SETTING_TYPES.items() if t is int} - {"port"}
        assert unbounded == set(_SETTING_BOUNDS), (
            "int settings without a bound: "
            f"{sorted(unbounded - set(_SETTING_BOUNDS))}")
        assert set(_SETTING_BOUNDS) == {
            "acp_max_sessions", "acp_idle_ttl_seconds", "acp_prompt_silence_seconds"}
        assert set(_SETTING_BOUNDS) <= set(_SETTING_TYPES)

    def test_the_startup_only_keys_say_restart_to_apply(self, client):
        from power_atlas.web import _RESTART_TO_APPLY
        assert {"acp_max_sessions", "acp_idle_ttl_seconds",
                "acp_prompt_silence_seconds", "remote_bind_address",
                "port"} <= _RESTART_TO_APPLY
        body = client.get("/api/settings").json()
        assert set(body["restart_to_apply"]) == set(_RESTART_TO_APPLY)

    def test_without_a_startup_snapshot_every_restart_key_reads_pending(self, client):
        """No snapshot is not the same as nothing pending, and must not read
        as it.

        A test importing the app, or an entry point that never calls
        `set_startup_config`, leaves `_STARTUP_VALUES` at None. Reporting an
        empty `restart_pending` there would tell the user every value is live
        when nothing knows whether it is — the same positively-wrong-field
        failure `peek_hotkey` already cost once. Over-warning is the safe
        direction.
        """
        import power_atlas.web as web_mod
        saved = web_mod._STARTUP_VALUES
        web_mod._STARTUP_VALUES = None
        try:
            body = client.get("/api/settings").json()
        finally:
            web_mod._STARTUP_VALUES = saved
        assert body["in_force"] == {}
        assert set(body["restart_pending"]) == set(web_mod._RESTART_TO_APPLY)

    @patch("power_atlas.web.autostart.is_enabled", return_value=False)
    @patch("power_atlas.web.load_config")
    def test_only_the_keys_the_process_is_not_running_read_pending(
            self, mock_load, _mock_autostart, client):
        """The badge's whole reason for existing: stored != in force.

        Before the snapshot the endpoint could not tell the two apart, so the
        page badged every restart-only key unconditionally and no restart ever
        cleared it.
        """
        import power_atlas.web as web_mod
        from power_atlas.config import Config
        started = Config()
        mock_load.return_value = started
        saved = web_mod._STARTUP_VALUES
        try:
            web_mod.set_startup_config(started)
            # Nothing touched yet: the process is running what is stored.
            body = client.get("/api/settings").json()
            assert body["restart_pending"] == []
            assert body["in_force"]["acp_max_sessions"] == started.acp_max_sessions

            # One value edited on disk. Only that key is now unapplied, and
            # `in_force` keeps reporting what the process actually runs.
            changed = Config()
            changed.acp_max_sessions = started.acp_max_sessions + 4
            mock_load.return_value = changed
            body = client.get("/api/settings").json()
            assert body["restart_pending"] == ["acp_max_sessions"]
            assert body["acp_max_sessions"] == started.acp_max_sessions + 4
            assert body["in_force"]["acp_max_sessions"] == started.acp_max_sessions
        finally:
            web_mod._STARTUP_VALUES = saved

    def test_the_startup_snapshot_copies_values_rather_than_holding_the_config(self):
        """`save_config` rewrites the config in place on any settings change.

        A snapshot holding the object would track those edits and report every
        value as in force — the exact bug it exists to prevent, and invisible
        because the endpoint would look like it was working.
        """
        import power_atlas.web as web_mod
        from power_atlas.config import Config
        config = Config()
        saved = web_mod._STARTUP_VALUES
        try:
            web_mod.set_startup_config(config)
            config.acp_max_sessions += 4
            assert web_mod._STARTUP_VALUES["acp_max_sessions"] != config.acp_max_sessions
            assert web_mod._restart_pending(config) == ["acp_max_sessions"]
        finally:
            web_mod._STARTUP_VALUES = saved

    def test_peek_hotkey_says_restart_to_apply(self, client):
        """`peek_hotkey` is read once, at startup:
        `create_peek(server_url, config.peek_hotkey)` hands it to
        `PeekWindow.__init__`, which parses it into `self._trigger_keys`.
        Nothing re-reads the config or re-registers the listener afterwards,
        while `index.html` offers a live input for it.

        Omitting it from `_RESTART_TO_APPLY` did not make the endpoint silent —
        it made it answer `restart_required: False`, which is a positively wrong
        answer the user acts on. A field that lies is worse than an absent one.
        """
        from power_atlas.web import _RESTART_TO_APPLY
        assert "peek_hotkey" in _RESTART_TO_APPLY
        body = client.post("/api/save-setting",
                           json={"key": "peek_hotkey", "value": "ctrl+shift+p"}).json()
        assert body == {"ok": True, "restart_required": True}

    def test_an_invalid_remote_bind_address_is_refused_by_name(self, client):
        body = client.post("/api/save-setting",
                           json={"key": "remote_bind_address", "value": "0.0.0.0"}).json()
        assert body["ok"] is False
        assert "wildcard" in body["error"]

    def test_a_whitespace_only_bind_address_is_persisted_stripped(self, client, tmp_path):
        """`value = value.strip()` sat *inside* `if value.strip():`, so it never
        ran for a whitespace-only value and `"   "` was written to config.toml
        verbatim. `load_config` strips it again on read, so the effect was
        cosmetic — but the stored value disagreed with the effective one, and a
        later reader resolves that gap in whichever direction they happen to
        look.
        """
        from power_atlas import config as config_mod
        (tmp_path / "config.toml").write_text("port = 4915\n")
        body = client.post("/api/save-setting",
                           json={"key": "remote_bind_address", "value": "   "}).json()
        assert body["ok"] is True
        assert config_mod.CONFIG_PATH.read_text(encoding="utf-8").count('"   "') == 0
        assert config_mod.load_config().remote_bind_address == ""
        # The stored text itself, not just what `load_config` makes of it.
        assert 'remote_bind_address = ""' in \
            config_mod.CONFIG_PATH.read_text(encoding="utf-8")
        # Disabling must not have created a secret as a side effect.
        assert not (tmp_path / "remote-secret").exists()

    def test_unknown_settings_are_still_default_denied(self, client):
        body = client.post("/api/save-setting",
                           json={"key": "acp_nonsense", "value": 1}).json()
        assert body == {"ok": False, "error": "Unknown setting: acp_nonsense"}

    def test_enabling_the_bind_creates_the_secret(self, client, tmp_path):
        from power_atlas import config as config_mod
        (tmp_path / "config.toml").write_text("port = 4915\n")
        body = client.post("/api/save-setting",
                           json={"key": "remote_bind_address",
                                 "value": "100.78.142.124"}).json()
        assert body["ok"] is True
        assert len((tmp_path / "remote-secret").read_text()) >= config_mod.REMOTE_SECRET_MIN_LEN

    def test_the_secret_route_is_loopback_only(self, remote_enabled):
        assert _peer_http("/api/remote-access", [_cookie_header()])[0] == 403

    def test_the_rotate_route_is_loopback_only(self, remote_enabled):
        """A remote peer must not be able to lock the owner out of every device,
        and one holding a stolen cookie must not be able to re-key the surface
        around it. Loopback-only by the same default-deny allowlist
        `/api/remote-access` relies on — adding the path to
        `_REMOTE_ALLOWED_PATHS` makes this fail."""
        status, body, _ = _peer_http("/api/remote-access/rotate",
                                     [_cookie_header(),
                                      (b"origin", f"http://{_LOCAL_BIND_IP}:4915".encode())],
                                     method="POST")
        assert status == 403
        assert b"Forbidden" in body

    def test_rotating_replaces_the_stored_secret(self, client, tmp_path):
        from power_atlas import config as config_mod
        first = config_mod.ensure_remote_secret()
        assert len(first) >= config_mod.REMOTE_SECRET_MIN_LEN
        body = client.post("/api/remote-access/rotate").json()
        assert body["ok"] is True
        second = body["secret"]
        assert second != first, "the secret was not actually rotated"
        assert len(second) >= config_mod.REMOTE_SECRET_MIN_LEN
        assert config_mod.load_remote_secret() == second
        # Rotation is destructive and irreversible, and the payload has to say
        # so — a caller that renders `ok` and nothing else must still have been
        # told what it just did.
        assert body["devices_revoked"] is True
        assert "re-enter" in body["message"] or "restart" in body["message"]

    def test_rotating_invalidates_a_cookie_minted_under_the_old_secret(
            self, client, remote_enabled, tmp_path):
        """The point of the route (D24): rotation is the *only* revocation
        mechanism, so a cookie signed by the previous secret must stop working.
        Every device is revoked at once — that is the intended semantic.
        """
        from power_atlas import web as web_mod
        old_cookie = _cookie_header()
        # Before: the old cookie reaches the app.
        with pytest.raises(_Reached):
            _peer_http("/acp", [old_cookie], asgi_app=_guard_over_sentinel())
        body = client.post("/api/remote-access/rotate").json()
        assert body["ok"] is True
        assert body["applied"] is True, \
            "the running process still authenticates the old secret"
        assert web_mod._REMOTE_SECRET == body["secret"]
        # After: the same cookie is refused, and one minted under the new secret
        # works — so this is revocation, not a blanket lockout.
        status, _, _ = _peer_http("/acp", [old_cookie],
                                  asgi_app=_guard_over_sentinel())
        assert status == 403, "the old device cookie still authenticates"
        with pytest.raises(_Reached):
            _peer_http("/acp", [_cookie_header(secret=body["secret"])],
                       asgi_app=_guard_over_sentinel())

    def test_rotating_needs_a_post(self, client):
        """Irreversible, so it must not be reachable by a cross-origin
        `<img src>` — and POST is what puts it under `same_origin_guard`'s
        Origin/Referer check."""
        assert client.get("/api/remote-access/rotate").status_code == 405

    def test_rotating_is_csrf_protected(self, raw_client, tmp_path):
        """The same CSRF protection every other mutating route here gets. The
        secret on disk must be untouched by the refused call."""
        from power_atlas import config as config_mod
        before = config_mod.ensure_remote_secret()
        resp = raw_client.post("/api/remote-access/rotate",
                               headers={"Origin": "http://evil.example"})
        assert resp.status_code == 403
        assert config_mod.load_remote_secret() == before, \
            "a cross-origin POST rotated the secret anyway"

    def test_rotating_is_not_cacheable(self, client):
        resp = client.post("/api/remote-access/rotate")
        assert resp.status_code == 200
        assert "secret" in resp.json(), "the assertion below would be vacuous"
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["pragma"] == "no-cache"

    def test_a_failed_write_leaves_the_previous_secret_in_effect(
            self, client, remote_enabled, monkeypatch):
        """Ordering: the file is written first, the in-process secret second.
        When the write fails nothing changes at all — the alternative ordering
        revokes every device now and then resurrects them all on restart, when
        the process reloads the old secret from disk."""
        from power_atlas import web as web_mod
        monkeypatch.setattr(web_mod, "rotate_remote_secret", lambda: "")
        body = client.post("/api/remote-access/rotate").json()
        assert body["ok"] is False
        assert "still in effect" in body["error"]
        assert web_mod._REMOTE_SECRET == _TEST_SECRET, \
            "the in-process secret changed despite the write failing"
        with pytest.raises(_Reached):
            _peer_http("/acp", [_cookie_header()],
                       asgi_app=_guard_over_sentinel())

    def test_rotating_without_a_live_remote_surface_says_restart(self, client, tmp_path):
        """`set_remote_secret` documents that a process with no remote listener
        holds no secret in memory. Rotating must not quietly give a
        loopback-only process a live authentication path — it persists the new
        secret and says the change lands on restart, rather than implying
        otherwise."""
        from power_atlas import config as config_mod, web as web_mod
        assert web_mod._REMOTE_SECRET == "", "no remote surface in this fixture"
        body = client.post("/api/remote-access/rotate").json()
        assert body["ok"] is True
        assert body["applied"] is False
        assert body["restart_required"] is True
        assert "restart" in body["message"]
        assert web_mod._REMOTE_SECRET == "", \
            "a loopback-only process now authenticates remote cookies"
        assert config_mod.load_remote_secret() == body["secret"]

    def test_the_secret_route_is_not_cacheable(self, client):
        """This body carries the **permanent** device secret, where `/acp`
        carries the strictly weaker per-launch rotating `_ACP_TOKEN` — and
        `/acp` already sets these headers. Nothing fetches this route yet,
        which is why the header goes on before a consumer exists to cache it.
        """
        resp = client.get("/api/remote-access")
        assert resp.status_code == 200
        assert "secret" in resp.json(), "the assertion below would be vacuous"
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["pragma"] == "no-cache"


class TestBindSockets:
    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def test_loopback_socket_is_exclusive_and_not_inheritable(self):
        """A second local process binding the identical 127.0.0.1:<port> would
        hijack a surface serving `_ACP_TOKEN` and fronting `kiro-cli acp -a`.
        `uvicorn.Config.bind_socket` sets `SO_REUSEADDR`, which permits it."""
        import socket as socket_mod
        import sys as sys_mod
        s = self._main()._bind("127.0.0.1", 0)
        try:
            assert s.getsockopt(socket_mod.SOL_SOCKET, socket_mod.SO_REUSEADDR) == 0
            assert s.get_inheritable() is False
            if sys_mod.platform == "win32":
                assert s.getsockopt(socket_mod.SOL_SOCKET,
                                    socket_mod.SO_EXCLUSIVEADDRUSE) != 0
        finally:
            s.close()

    def test_a_second_bind_to_the_same_address_fails(self):
        s = self._main()._bind("127.0.0.1", 0)
        try:
            port = s.getsockname()[1]
            with pytest.raises(OSError):
                self._main()._bind("127.0.0.1", port).close()
        finally:
            s.close()

    def test_proxy_headers_is_disabled_at_the_config_site(self):
        """`ProxyHeadersMiddleware` overwrites `scope["client"]` from
        `X-Forwarded-For` for peers in `forwarded_allow_ips`, and
        `proxy_headers` defaults to True — so `FORWARDED_ALLOW_IPS=*` in the
        environment would make D26's basis an environment variable."""
        import ast
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        calls = [n for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "Config"]
        assert calls, "the uvicorn.Config call site went"
        for call in calls:
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            assert "proxy_headers" in kwargs, \
                f"uvicorn.Config at line {call.lineno} leaves proxy_headers defaulted"
            assert kwargs["proxy_headers"].value is False

    def test_run_is_handed_both_sockets(self):
        """One `Server`, not two: two would run lifespan twice — two refresh
        loops, two sweepers racing the same sessions, `acp.shutdown()` twice."""
        import ast
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        assert 'kwargs={"sockets": socks}' in source
        servers = [n for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "Server"]
        assert len(servers) == 1

    def test_the_thread_and_ready_event_scaffolding_survives(self):
        """`run()` blocks, so removing the thread or the patched-startup event
        means neither the tray nor peek ever starts — and the socket change
        rewrote the block they live in."""
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        for fragment in ("_make_patched_startup(server, ready_event)",
                         "ready_event.wait(timeout=10)",
                         "create_peek(server_url",
                         "run_tray, args=(server_url, config)"):
            assert fragment in source, fragment

    def test_server_url_comes_from_the_loopback_socket(self):
        """With two sockets, `server.servers[0].sockets[0]` is merely whichever
        bound first. It feeds `create_peek` and `run_tray`."""
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        assert "loopback_sock = socks[0]" in source
        assert "port = loopback_sock.getsockname()[1]" in source
        assert 'server_url = f"http://127.0.0.1:{port}"' in source
        assert "server.servers[0].sockets[0]" not in source

    def test_both_bound_addresses_are_logged(self):
        """`Server.startup` skips its "Uvicorn running on…" banner entirely
        when `sockets=` is passed, so this line is the only operator-visible
        record of which addresses are live — precisely when there are two."""
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        assert 'log.info("Listening on %s",' in source
        assert "[s.getsockname()[:2] for s in socks]" in source

    def test_the_loopback_bind_keeps_its_random_port_fallback(self):
        """The app must never come up remote-only with no loopback listener."""
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        assert 'socks = [_bind("127.0.0.1", 0)]' in source
        assert source.index('socks = [_bind("127.0.0.1", desired_port)]') < \
            source.index("_bind_remote_socket(log, config, socks, port)")


class TestRemoteSocketBinding:
    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def _cfg(self, **kw):
        from power_atlas.config import Config
        return Config(**kw)

    def test_no_address_means_no_second_socket(self):
        socks = []
        assert self._main()._bind_remote_socket(
            logging.getLogger("t"), self._cfg(), socks, 4915) is False
        assert socks == []

    def test_an_unusable_secret_means_the_socket_is_never_created(self, monkeypatch, caplog):
        """Otherwise the remote surface is bound and accepting while
        authentication is structurally impossible."""
        main_mod = self._main()
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: "")
        called = []
        monkeypatch.setattr(main_mod, "_bind",
                            lambda h, p: called.append((h, p)))
        socks = []
        with caplog.at_level(logging.ERROR):
            ok = main_mod._bind_remote_socket(
                logging.getLogger("t"),
                self._cfg(port=4915, remote_bind_address="100.78.142.124"),
                socks, 4915)
        assert ok is False
        assert called == [] and socks == []
        assert any(r.levelno == logging.ERROR for r in caplog.records)

    def test_a_bind_failure_degrades_rather_than_exiting(self, monkeypatch, caplog):
        """D27: NetBird's interface may not be up at login, raising
        `WinError 10049`. Unhandled that makes the app exit 1."""
        main_mod = self._main()
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)

        def _boom(host, port):
            raise OSError(10049, "cannot assign requested address")

        monkeypatch.setattr(main_mod, "_bind", _boom)
        sentinel = object()
        socks = [sentinel]
        with caplog.at_level(logging.ERROR):
            ok = main_mod._bind_remote_socket(
                logging.getLogger("t"),
                self._cfg(port=4915, remote_bind_address="100.78.142.124"),
                socks, 4915)
        assert ok is False
        assert socks == [sentinel], "the loopback listener must survive"
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        from power_atlas import web as web_mod
        assert web_mod._ALLOWED_HOSTS == web_mod._LOOPBACK_HOSTS
        assert web_mod._REMOTE_SECRET == ""

    def test_the_remote_socket_is_appended_after_the_loopback_one(self, monkeypatch):
        """`server_url` — the URL tray and peek open — is read from `socks[0]`,
        so the loopback listener has to stay at index 0. Reading
        `server.servers[0].sockets[0]` instead would give whichever bound
        first, which with two sockets is not a property anything guarantees."""
        main_mod = self._main()
        from power_atlas import web as web_mod
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)
        made = object()
        # Capture the arguments rather than discarding them. With
        # `lambda h, p: made` the stub could not tell `_bind(address, port)`
        # from `_bind(address, port + 1)`, so "two sockets, on the same port
        # number" — criterion 2 — was earned by reading the source, not by any
        # assertion. The unusable-secret test above already captures this way.
        calls = []
        monkeypatch.setattr(main_mod, "_bind",
                            lambda h, p: (calls.append((h, p)), made)[1])
        loopback = object()
        socks = [loopback]
        try:
            main_mod._bind_remote_socket(
                logging.getLogger("t"),
                self._cfg(port=4915, remote_bind_address="100.78.142.124"),
                socks, 4915)
            assert socks == [loopback, made]
            assert calls == [("100.78.142.124", 4915)]
        finally:
            web_mod.set_remote_host("")
            web_mod.set_remote_secret("")

    def test_success_wires_both_startup_setters(self, monkeypatch):
        main_mod = self._main()
        from power_atlas import web as web_mod
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)
        made = object()
        calls = []
        monkeypatch.setattr(main_mod, "_bind",
                            lambda h, p: (calls.append((h, p)), made)[1])
        socks = []
        try:
            ok = main_mod._bind_remote_socket(
                logging.getLogger("t"),
                self._cfg(port=4915, remote_bind_address="100.78.142.124"),
                socks, 4915)
            assert ok is True and socks == [made]
            # The remote listener must be on the loopback listener's port, not
            # a neighbour of it: the phone's bookmarked URL and the laptop's
            # `server_url` name the same number by construction.
            assert calls == [("100.78.142.124", 4915)]
            assert web_mod._host_allowed("100.78.142.124:4915") is True
            assert web_mod._REMOTE_SECRET == _TEST_SECRET
        finally:
            web_mod.set_remote_host("")
            web_mod.set_remote_secret("")

    def test_the_remote_bind_uses_the_actual_loopback_port_after_a_fallback(
            self, monkeypatch):
        """When the configured port is taken, loopback lands on an OS-assigned
        one — and the remote socket must follow it there. Binding the
        *configured* port instead would split the two listeners across
        different ports with nothing failing."""
        main_mod = self._main()
        from power_atlas import web as web_mod
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)
        calls = []
        monkeypatch.setattr(main_mod, "_bind",
                            lambda h, p: (calls.append((h, p)), object())[1])
        socks = [object()]
        try:
            main_mod._bind_remote_socket(
                logging.getLogger("t"),
                self._cfg(port=4915, remote_bind_address="100.78.142.124"),
                socks, 51234)
            assert calls == [("100.78.142.124", 51234)]
        finally:
            web_mod.set_remote_host("")
            web_mod.set_remote_secret("")


class TestChooseSocketsReturnsTheLoopbackPort:
    """`server_url` is the URL tray and peek open, and it is built from the port
    `_choose_sockets` returns. Every existing assertion about that was a
    substring check against `__main__.py`'s source text, which a
    behaviour-preserving edit walks straight past: appending
    `port = socks[-1].getsockname()[1]` after the remote bind left every literal
    those tests grep for intact, made `server_url` name the **remote** listener
    — nothing listens on `127.0.0.1:<remote-port>`, so tray and peek open a
    dead URL — and kept the suite green.

    Only a test holding real bound sockets can see it, so these bind them.
    Loopback only: `127.0.0.1` twice on two OS-assigned ports is enough to make
    `socks[0]` and `socks[-1]` differ, and no non-loopback interface is touched.
    """

    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def _cfg(self, **kw):
        from power_atlas.config import Config
        return Config(**kw)

    def test_the_returned_port_is_the_first_sockets_port(self, monkeypatch):
        main_mod = self._main()
        from power_atlas import web as web_mod
        import socket as _socket
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)
        extra = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        extra.bind(("127.0.0.1", 0))
        extra.listen()
        # Stand in for the remote listener with a second *loopback* socket on a
        # different OS-assigned port, so socks[0] and socks[-1] disagree.
        monkeypatch.setattr(main_mod, "_bind_remote_socket",
                            lambda log, config, socks, port: socks.append(extra) or True)
        socks = []
        try:
            socks, port = main_mod._choose_sockets(
                logging.getLogger("t"), self._cfg(port=0), 0)
            assert len(socks) == 2, "the stand-in remote socket was appended"
            assert socks[0].getsockname()[1] != socks[-1].getsockname()[1], \
                "the two ports must differ or this test cannot discriminate"
            assert port == socks[0].getsockname()[1]
            assert port != socks[-1].getsockname()[1]
        finally:
            for s in socks:
                s.close()
            extra.close()
            web_mod.set_remote_host("")
            web_mod.set_remote_secret("")

    def test_the_returned_port_is_where_loopback_actually_listens(self, monkeypatch):
        """The port is not merely `socks[0]`'s by identity — it is the one a
        client can connect to on 127.0.0.1, which is what `server_url` promises.
        """
        main_mod = self._main()
        import socket as _socket
        socks = []
        try:
            socks, port = main_mod._choose_sockets(
                logging.getLogger("t"), self._cfg(port=0), 0)
            probe = _socket.create_connection(("127.0.0.1", port), timeout=2)
            probe.close()
        finally:
            for s in socks:
                s.close()

    def test_a_taken_port_falls_back_and_reports_the_fallback(self, monkeypatch):
        """The random-port fallback must be reflected in the returned port, or
        `server_url` names the port that was already taken by something else."""
        main_mod = self._main()
        import socket as _socket
        squatter = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        taken = squatter.getsockname()[1]
        socks = []
        try:
            socks, port = main_mod._choose_sockets(
                logging.getLogger("t"), self._cfg(port=taken), taken)
            assert port != taken, "the fallback port must not be the taken one"
            assert port == socks[0].getsockname()[1]
        finally:
            for s in socks:
                s.close()
            squatter.close()


class TestChooseSocketsNamesTheBindsItTried:
    """A bind failure has to name the attempts that were actually made.

    Extracting `_choose_sockets` left the caller in `_run_foreground` logging
    "failed on port %d and on the random fallback" for *every* OSError out of
    it — but on the `port = 0` path no fallback is attempted at all, because
    the OS has already been asked for any free port and had none to give.
    Which binds ran is visible only inside this function, so this is where it
    is said; the caller logs the consequence and nothing about the attempts.

    No socket is bound here: `_bind` is replaced by one that refuses, which is
    also what records the attempts.
    """

    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def _cfg(self, **kw):
        from power_atlas.config import Config
        return Config(**kw)

    def _refusing_bind(self, attempts):
        def _refuse(host, port):
            attempts.append((host, port))
            raise OSError("no port available")
        return _refuse

    def test_the_zero_port_failure_does_not_claim_a_fallback(
            self, monkeypatch, caplog):
        main_mod = self._main()
        attempts = []
        monkeypatch.setattr(main_mod, "_bind", self._refusing_bind(attempts))
        with caplog.at_level(logging.DEBUG, logger="t"):
            with pytest.raises(OSError):
                main_mod._choose_sockets(
                    logging.getLogger("t"), self._cfg(port=0), 0)
        assert attempts == [("127.0.0.1", 0)], \
            "the zero-port path has no second attempt to make"
        assert "OS-assigned port failed" in caplog.text
        assert "fallback" not in caplog.text, \
            "no fallback ran, so no log line may report one"

    def test_a_failed_fallback_reports_both_attempts(
            self, monkeypatch, caplog):
        """The message the caller used to emit unconditionally is true here
        and only here — both binds were tried and both failed."""
        main_mod = self._main()
        attempts = []
        monkeypatch.setattr(main_mod, "_bind", self._refusing_bind(attempts))
        with caplog.at_level(logging.DEBUG, logger="t"):
            with pytest.raises(OSError):
                main_mod._choose_sockets(
                    logging.getLogger("t"), self._cfg(port=51999), 51999)
        assert attempts == [("127.0.0.1", 51999), ("127.0.0.1", 0)], \
            "the static attempt and then the random fallback"
        assert "on the random fallback" in caplog.text


class TestTheLogFileIsBounded:
    """`orchestrator.log` had no size ceiling, and two sources write to it with
    no natural end: Phase 2's idle sweeper emits a line per tick per stuck
    session for as long as the session stays stuck, and an unauthenticated
    remote peer drives a WARNING per refused `/remote-auth` attempt. The
    developer's own file reached ~10 MB before either existed.

    These bind a real file and drive a real rollover. A source-text assertion
    that the handler is named `RotatingFileHandler` passes just as happily
    against one constructed without `maxBytes`, which never rolls over at all.
    """

    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def test_the_handler_actually_rolls_over(self, tmp_path, monkeypatch):
        main_mod = self._main()
        monkeypatch.setattr(main_mod, "_LOG_MAX_BYTES", 512)
        monkeypatch.setattr(main_mod, "_LOG_BACKUP_COUNT", 2)
        log_path = tmp_path / "orchestrator.log"
        handler = main_mod._build_log_handler(log_path)
        handler.setFormatter(logging.Formatter("%(message)s"))
        try:
            for i in range(200):
                handler.emit(logging.LogRecord(
                    "t", logging.INFO, __file__, i, "x" * 80, None, None))
        finally:
            handler.close()
        assert log_path.exists(), "the live file must survive rotation"
        assert (tmp_path / "orchestrator.log.1").exists(), \
            "nothing rotated: the log still grows without bound"
        # The bound is what the change is for: current + backupCount, each
        # capped, and no unbounded tail of old segments.
        segments = sorted(p.name for p in tmp_path.glob("orchestrator.log*"))
        assert segments == ["orchestrator.log", "orchestrator.log.1",
                            "orchestrator.log.2"], segments
        total = sum(p.stat().st_size for p in tmp_path.glob("orchestrator.log*"))
        assert total <= 512 * 3 + 200, \
            f"200 records of 80 bytes left {total} bytes on disk"

    def test_the_live_path_is_always_the_newest_segment(self, tmp_path, monkeypatch):
        """`tray.py` opens `CONFIG_DIR / "orchestrator.log"` with `os.startfile`
        and nothing else in `src/` reads or tails it, so rotation is only safe
        if that fixed path keeps naming the file being written *now*. A handler
        that renamed forward — writing to `.1` and leaving the base path stale —
        would satisfy "it rotates" and silently break the tray's Open Log."""
        main_mod = self._main()
        monkeypatch.setattr(main_mod, "_LOG_MAX_BYTES", 256)
        monkeypatch.setattr(main_mod, "_LOG_BACKUP_COUNT", 2)
        log_path = tmp_path / "orchestrator.log"
        handler = main_mod._build_log_handler(log_path)
        handler.setFormatter(logging.Formatter("%(message)s"))
        try:
            for i in range(60):
                handler.emit(logging.LogRecord(
                    "t", logging.INFO, __file__, i, f"line-{i:03d}", None, None))
        finally:
            handler.close()
        assert (tmp_path / "orchestrator.log.1").exists(), \
            "the test cannot discriminate unless a rollover happened"
        assert "line-059" in log_path.read_text(encoding="utf-8")

    def test_the_crash_log_is_not_rotated(self):
        """`faulthandler` writes to a raw descriptor held for the process
        lifetime, so renaming `crash.log` underneath it would send the next dump
        to an unlinked inode. It must stay a plain append-mode file."""
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        assert 'open(CONFIG_DIR / "crash.log", "a", encoding="utf-8")' in source
        assert "RotatingFileHandler" not in source.split(
            "def _enable_crash_handler")[1].split("def _run_foreground")[0]


class TestUvicornProtocolErrorFloodIsBounded:
    """The size bound above caps the file; it does not cap the rate.

    Measured 2026-07-31 against a real NetBird peer and re-measured per logger
    on loopback 2026-08-01: `Content-Length: 10` followed by 200,000 unread
    bytes leaves h11 in MUST_CLOSE, and the layer beneath the application
    writes ~35 lines / ~2,970 bytes per request. 1,872 of those bytes over 12
    requests come from `uvicorn.error`; 33,732 — the whole `LocalProtocolError`
    traceback — come from `asyncio`. Both are beneath the app, so
    `web._claim_throttle_warning` bounds neither, and both were confirmed still
    firing on iterations that returned 429. Against the rotation that is an
    anti-forensics primitive: ~14,000 such requests roll every backup off the
    end, erasing the 403s and 429s that record the attacker's own run.

    These drive real `logging` records through the real filter on a real
    logger. Asserting the filter class exists would pass just as happily
    against one that suppressed everything, including the evidence.
    """

    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    def _rig(self, name, clock=None, **kw):
        """A private logger carrying the real filter, plus a capturing handler."""
        main_mod = self._main()
        logger = logging.getLogger(f"pa.repeat.{name}")
        logger.handlers.clear()
        logger.filters.clear()
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        cap = self._Capture()
        logger.addHandler(cap)
        filt = main_mod._RepeatedRecordFilter(
            clock=clock or (lambda: 0.0), **kw)
        logger.addFilter(filt)
        return logger, cap, filt

    @staticmethod
    def _flood(logger, n, exc=None):
        """`n` copies of uvicorn's actual protocol-error call."""
        for _ in range(n):
            logger.warning("Invalid HTTP request received.",
                           exc_info=exc or RuntimeError("h11 MUST_CLOSE"))

    @staticmethod
    def _kept(cap):
        return [r for r in cap.records
                if not getattr(r, "_pa_repeat_summary", False)]

    @staticmethod
    def _summaries(cap):
        return [r.getMessage() for r in cap.records
                if getattr(r, "_pa_repeat_summary", False)]

    def test_identical_records_collapse_to_one_per_window(self):
        """The whole point: 500 requests must not cost 500 tracebacks."""
        logger, cap, _ = self._rig("collapse")
        self._flood(logger, 500)
        assert len(self._kept(cap)) == 1, \
            f"{len(self._kept(cap))} of 500 identical records were written"
        assert self._summaries(cap) == [], \
            "no window has closed yet, so nothing should be summarised"

    def test_the_suppressed_count_is_written_when_the_window_closes(self):
        """A silent drop would trade one unreliable log for another. The count
        is the evidence that survives when the traceback does not."""
        now = [0.0]
        logger, cap, main_filt = self._rig("count", clock=lambda: now[0])
        self._flood(logger, 1 + 42)
        now[0] = main_filt._window + 1
        self._flood(logger, 1)
        summaries = self._summaries(cap)
        assert len(summaries) == 1, summaries
        assert "42 further identical WARNING record(s) suppressed" in summaries[0], \
            summaries[0]
        assert "Invalid HTTP request received." in summaries[0], summaries[0]
        assert "RuntimeError" in summaries[0], \
            f"the summary must name what was suppressed; got {summaries[0]!r}"

    def test_a_different_error_is_not_suppressed(self):
        """Same logger, same level, same uvicorn template — a different
        exception underneath it is a different failure and must be visible.
        Keying on the template alone would bury it."""
        logger, cap, _ = self._rig("distinct")
        self._flood(logger, 200, exc=RuntimeError("h11 MUST_CLOSE"))
        self._flood(logger, 200, exc=ValueError("something else entirely"))
        kept = self._kept(cap)
        assert len(kept) == 2, f"expected both failures, got {len(kept)}"
        types = {r.exc_info[0].__name__ for r in kept}
        assert types == {"RuntimeError", "ValueError"}, types

    def test_the_same_error_is_logged_again_in_the_next_window(self):
        """Suppression must be a rate limit, not a permanent mute — an attack
        resuming an hour later has to reappear in the log."""
        now = [0.0]
        logger, cap, main_filt = self._rig("rearm", clock=lambda: now[0])
        self._flood(logger, 50)
        assert len(self._kept(cap)) == 1
        now[0] = main_filt._window + 0.001
        self._flood(logger, 50)
        assert len(self._kept(cap)) == 2, \
            "the error stayed muted after its window expired"

    def test_the_window_table_stays_bounded_under_many_distinct_keys(self):
        """Replacing unbounded log growth with unbounded memory growth would be
        no fix at all — the trap `acp._sweep_failures` names and avoids."""
        main_mod = self._main()
        logger, _cap, main_filt = self._rig("bounded")
        for i in range(main_mod._PROTOCOL_ERROR_MAX_KEYS * 20):
            logger.warning("distinct template %d" % i)
        assert len(main_filt._windows) <= main_mod._PROTOCOL_ERROR_MAX_KEYS, \
            (f"{len(main_filt._windows)} keys retained against a cap of "
             f"{main_mod._PROTOCOL_ERROR_MAX_KEYS}")

    def test_eviction_does_not_silently_drop_a_count(self):
        """Overflow is the one path that discards a window early. It must still
        write what it discarded, or the cap becomes a way to erase the count."""
        main_mod = self._main()
        logger, cap, _ = self._rig("evict")
        self._flood(logger, 1 + 17)
        for i in range(main_mod._PROTOCOL_ERROR_MAX_KEYS + 5):
            logger.warning("filler template %d" % i)
        summaries = self._summaries(cap)
        assert any("17 further identical" in s for s in summaries), \
            f"the evicted window's count was lost; summaries={summaries}"

    def test_startup_and_shutdown_lines_are_never_suppressed(self):
        """`uvicorn.error` is also where uvicorn's lifecycle lines go. A filter
        that swallowed them would hide the server starting and stopping."""
        logger, cap, main_filt = self._rig("lifecycle")
        for _ in range(20):
            logger.info("Application startup complete.")
        for _ in range(20):
            logger.info("Shutting down")
        assert len(self._kept(cap)) == 40, \
            f"only {len(self._kept(cap))} of 40 lifecycle lines survived"
        assert main_filt._windows == {}, \
            "lifecycle lines must not even occupy a window slot"

    def test_a_message_carrying_a_memory_address_still_collapses(self):
        """The measured flood's expensive record is `asyncio`'s, and its `msg`
        is composed from the transport, protocol and handle *reprs* — each
        carrying a distinct memory address. Keying on the message would mint a
        key per request and collapse exactly nothing, which is why the key
        drops the message whenever an exception is attached and stands the
        exception's type and raise site in its place."""
        logger, cap, _ = self._rig("addresses")
        try:
            raise RuntimeError("can't handle event type Response ...")
        except RuntimeError as exc:
            raised = exc
        for i in range(300):
            logger.error(
                "Exception in callback _ProactorReadPipeTransport"
                f"<_ProactorSocketTransport fd=%d at 0x{0x1F0A0000 + i * 64:x}>"
                f"\nhandle: <Handle at 0x{0x2B0C0000 + i * 32:x}>", i,
                exc_info=raised)
        assert len(self._kept(cap)) == 1, \
            (f"{len(self._kept(cap))} of 300 address-bearing records were "
             "written: the key is varying with the address")

    def test_a_different_raise_site_is_not_suppressed(self):
        """Same exception type from a different line is a different bug. The
        raise site is half of what makes the normalised key discriminating."""
        logger, cap, _ = self._rig("raisesite")

        def a():
            raise ValueError("one")

        def b():
            raise ValueError("two")

        for fn in (a, b, a, b):
            for _ in range(50):
                try:
                    fn()
                except ValueError as exc:
                    logger.error("failed", exc_info=exc)
        assert len(self._kept(cap)) == 2, \
            f"expected one record per raise site, got {len(self._kept(cap))}"

    def test_the_filter_covers_the_logger_that_carries_the_traceback(self):
        """Attribution over 12 loopback requests, 2026-08-01: `uvicorn.error`
        1,872 bytes, `asyncio` 33,732 bytes. The `LocalProtocolError` escapes
        uvicorn's own handler and surfaces through asyncio's default exception
        handler, so installing on `uvicorn.error` alone would bound 5% of the
        amplification while looking like a fix."""
        main_mod = self._main()
        assert "asyncio" in main_mod._PROTOCOL_ERROR_LOGGERS, \
            "the logger carrying 95% of the flood is not covered"
        saved = {n: list(logging.getLogger(n).filters)
                 for n in main_mod._PROTOCOL_ERROR_LOGGERS}
        try:
            filt = main_mod._install_repeat_filter()
            for name in main_mod._PROTOCOL_ERROR_LOGGERS:
                assert filt in logging.getLogger(name).filters, name
        finally:
            for name, pre in saved.items():
                logging.getLogger(name).filters = pre

    def test_the_filter_survives_uvicorns_own_dictconfig(self):
        """`uvicorn.Config.__init__` runs `dictConfig` over uvicorn's
        LOGGING_CONFIG, which replaces this logger's handlers. If it cleared
        filters too, installing at `basicConfig` time would be a no-op in
        production while every test above still passed."""
        import logging.config
        import uvicorn.config
        main_mod = self._main()
        saved = {n: list(logging.getLogger(n).filters)
                 for n in main_mod._PROTOCOL_ERROR_LOGGERS}
        try:
            filt = main_mod._install_repeat_filter()
            assert main_mod._install_repeat_filter() is filt, \
                "a second install must not stack a second filter"
            for name in main_mod._PROTOCOL_ERROR_LOGGERS:
                assert len([f for f in logging.getLogger(name).filters
                            if isinstance(f, main_mod._RepeatedRecordFilter)]) == 1
            logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
            assert filt in logging.getLogger("uvicorn.error").filters, \
                "uvicorn's logging config dropped the filter"
        finally:
            for name, pre in saved.items():
                logging.getLogger(name).filters = pre

    def test_a_pending_count_is_flushed_on_the_shutdown_path(self):
        """`log_level="warning"` leaves `uvicorn.error` silent once a flood
        stops, so the sweep on the next record never arrives and the last
        window's count would never be written."""
        logger, cap, main_filt = self._rig("flush")
        self._flood(logger, 1 + 9)
        assert self._summaries(cap) == [], "nothing should have closed yet"
        main_filt.flush()
        assert any("9 further identical" in s for s in self._summaries(cap)), \
            self._summaries(cap)
        assert main_filt._windows == {}, "flush must also clear the table"
        source = Path(self._main().__file__).read_text(encoding="utf-8")
        tail = source.split("def _run_foreground")[1]
        assert tail.index("repeat_filter.flush()") < tail.index("logging.shutdown()"), \
            "flushing after logging.shutdown() writes the count to a closed handler"


class TestALoopbackFallbackSkipsTheRemoteBind:
    """D25's premise is a bookmarked `http://<address>:<port>/…` on a phone. A
    loopback fallback assigns a *new* port every restart, so binding the remote
    socket on it produces a listener in front of `kiro-cli acp -a` that no
    bookmark can reach — exposure with no corresponding reachability.

    The loopback fallback itself must stay: loopback is mandatory and its
    fallback is what keeps the app running through a port conflict.
    """

    def _main(self):
        from power_atlas import __main__ as main_mod
        return main_mod

    def _cfg(self, **kw):
        from power_atlas.config import Config
        return Config(**kw)

    def test_no_remote_socket_is_bound_after_a_fallback(self, monkeypatch, caplog):
        main_mod = self._main()
        import socket as _socket
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)
        attempted = []
        monkeypatch.setattr(main_mod, "_bind_remote_socket",
                            lambda log, config, socks, port: attempted.append(port))
        squatter = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        taken = squatter.getsockname()[1]
        socks = []
        try:
            with caplog.at_level(logging.WARNING):
                socks, port = main_mod._choose_sockets(
                    logging.getLogger("t"),
                    self._cfg(port=taken, remote_bind_address=_LOCAL_BIND_IP),
                    taken)
            assert port != taken, "the loopback fallback itself must still happen"
            assert len(socks) == 1, "only the loopback listener may exist"
            assert attempted == [], \
                "the remote socket was bound on a port that changes every restart"
            messages = " ".join(r.getMessage() for r in caplog.records
                                if r.levelno >= logging.WARNING)
            assert "Remote bind" in messages and "skipped" in messages, \
                f"the skip must be visible at WARNING; got {messages!r}"
        finally:
            for s in socks:
                s.close()
            squatter.close()

    def test_the_remote_bind_still_happens_without_a_fallback(self, monkeypatch):
        """The skip must be conditional on the fallback, not on the remote
        address being set — otherwise it disables remote access outright."""
        main_mod = self._main()
        monkeypatch.setattr(main_mod, "load_remote_secret", lambda: _TEST_SECRET)
        attempted = []
        monkeypatch.setattr(main_mod, "_bind_remote_socket",
                            lambda log, config, socks, port: attempted.append(port))
        socks = []
        try:
            socks, port = main_mod._choose_sockets(
                logging.getLogger("t"),
                self._cfg(port=0, remote_bind_address=_LOCAL_BIND_IP), 0)
            assert attempted == [port]
        finally:
            for s in socks:
                s.close()

    def test_a_fallback_without_a_remote_address_is_silent_about_remote(self, monkeypatch, caplog):
        """A user who never enabled remote access must not be told a remote bind
        was skipped — the port-conflict warning is the whole story for them."""
        main_mod = self._main()
        import socket as _socket
        squatter = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        squatter.bind(("127.0.0.1", 0))
        squatter.listen()
        taken = squatter.getsockname()[1]
        socks = []
        try:
            with caplog.at_level(logging.WARNING):
                socks, port = main_mod._choose_sockets(
                    logging.getLogger("t"), self._cfg(port=taken), taken)
            assert "Remote bind" not in " ".join(
                r.getMessage() for r in caplog.records)
        finally:
            for s in socks:
                s.close()
            squatter.close()


class TestGuardOrdering:
    def test_the_remote_guard_is_outermost(self):
        """`add_middleware` inserts at index 0 and the stack is built over
        `reversed(middleware)`, so the last registered wraps the rest. A deny
        survives either order; the refusal body does not."""
        from power_atlas.web import RemoteAccessGuard
        assert app.user_middleware[0].cls is RemoteAccessGuard

    def test_it_rejects_before_the_host_guard_does(self, remote_enabled):
        """The refusal body is the raw-ASGI one, not `same_origin_guard`'s."""
        status, body, sent = _peer_http("/api/settings", [(b"host", b"evil.com")])
        assert status == 403
        start = next(m for m in sent if m["type"] == "http.response.start")
        assert dict(start["headers"])[b"content-type"] == b"application/json"
        assert body == b'{"error":"Forbidden"}'


def _acp_row(sid, *, title="", first="", updated="2026-07-31T12:00:00Z",
             cwd="C:\\dev\\ws"):
    """A `data.Session` with only the fields the listing endpoint reads."""
    return Session(session_id=sid, title=title, cwd=cwd,
                   created_at="2026-07-01T00:00:00Z", updated_at=updated,
                   first_prompt=first, last_prompt="", last_reply_tail="")


class _LoopBoundSessions(dict):
    """A `_supervisor.sessions` stand-in that records *where* it was iterated.

    `asyncio.get_running_loop()` succeeds only on the thread running the loop,
    so the check is exact rather than a proxy: a snapshot taken in the route
    body registers zero off-loop reads, and the same snapshot moved inside
    `asyncio.to_thread` registers one. Both `__iter__` and `keys` record,
    because `frozenset(d)` and `frozenset(d.keys())` are the same intent
    written two ways and only one of them would otherwise be caught.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reads = 0
        self.off_loop_reads = 0

    def _record(self):
        self.reads += 1
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.off_loop_reads += 1

    def __iter__(self):
        self._record()
        return super().__iter__()

    def keys(self):
        self._record()
        return super().keys()


@pytest.fixture
def acp_listing_store(monkeypatch):
    """A synthetic kiro-cli store behind the listing endpoint.

    Patches the two data-layer entry points the route uses and `_lock_holder`,
    recording every lock read so "availability is computed only for returned
    rows" is a **count** rather than a shape assertion — a response-shape check
    passes whether the endpoint walked 30 rows or all 1,207.
    """
    from power_atlas import acp as acp_mod
    from power_atlas import data as data_mod

    state = {
        "workspaces": [],
        "sessions": {},
        "lock_calls": [],
        "locked": set(),
    }

    def _discover(provider=None):
        assert provider == "kiro-cli", (
            f"the browser is ACP's, and ACP is v2 kiro-cli only; got {provider!r}")
        return list(state["workspaces"])

    def _get_sessions(cwd, provider="kiro-cli"):
        return list(state["sessions"].get(cwd, []))

    def _holder(sid):
        state["lock_calls"].append(sid)
        return 4242 if sid in state["locked"] else None

    def _add(cwd, sessions, updated="2026-07-31T00:00:00Z"):
        state["workspaces"].append((cwd, len(sessions), updated, "kiro-cli"))
        state["sessions"][cwd] = sessions

    state["add"] = _add
    monkeypatch.setattr(data_mod, "discover_workspaces_with_counts", _discover)
    monkeypatch.setattr(data_mod, "get_sessions", _get_sessions)
    monkeypatch.setattr(acp_mod, "_lock_holder", _holder)
    monkeypatch.setattr(acp_mod._supervisor, "sessions", {})
    return state


class TestAcpListingEndpoint:
    """Phase 4. A purpose-built read-only listing (D18) feeding the session
    browser, paginated independently at both levels (D19), with D17's
    three-state availability computed only for the rows it returns."""

    _PATH = "/api/acp/sessions"

    def test_the_payload_carries_exactly_the_documented_fields(self, client, acp_listing_store):
        """The payload **is** the audit surface: what is not in this key set
        cannot leak from this route. Asserted as equality, not containment, so
        a field added later fails here rather than reaching a phone."""
        acp_listing_store["add"]("C:\\dev\\PowerAtlas", [_acp_row("s1", title="a title")])
        body = client.get(self._PATH).json()
        # `capacity` joined in 2026-08-03 (F-14). It is the only field here that
        # describes this PowerAtlas rather than the store, and it is deliberate:
        # the rail could reach MAX_SESSIONS in eight taps while showing neither
        # the count nor a way to free a slot. It carries no session content.
        assert set(body) == {"groups", "group_page", "group_total", "has_more",
                             "capacity"}
        assert set(body["capacity"]) == {"held", "max"}
        group = body["groups"][0]
        # `exists` joined the group in Phase 5b. It is the one thing in this
        # payload a browser cannot derive for itself, which is why it is served
        # rather than computed in the rail — see `_acp_cwd_exists`.
        assert set(group) == {"cwd", "name", "total", "session_page",
                              "has_more", "sessions", "exists"}
        assert group["cwd"] == "C:\\dev\\PowerAtlas"
        assert group["name"] == "PowerAtlas"
        # `status` joined the row in 2026-08-03. It is the dashboard's semantic
        # verdict, carried so the rail can draw the same dot for a session both
        # surfaces are showing — and it is `""` for every row this ACP does not
        # hold, because that is the only row the rail draws a dot for.
        assert set(group["sessions"][0]) == {"id", "title", "updated_at",
                                             "availability", "status"}

    def _held_rows(self, client, acp_listing_store, monkeypatch, semantic):
        """One workspace holding a free, a held and a foreign-locked session,
        with the classifier replaced by *semantic*. Returns `(rows_by_id,
        classified_ids)`."""
        from power_atlas import acp as acp_mod
        from power_atlas import presence as presence_mod
        from power_atlas import web as web_mod

        acp_listing_store["add"]("C:\\dev\\ws", [
            _acp_row("free"), _acp_row("mine"), _acp_row("theirs")])
        acp_listing_store["locked"].add("theirs")
        acp_mod._supervisor.sessions["mine"] = {"cwd": "C:\\dev\\ws"}

        asked = []

        def _classify(session_id, provider, cwd):
            asked.append(session_id)
            return semantic(session_id)

        monkeypatch.setattr(web_mod, "get_semantic_status", _classify)
        # No process scan in a unit test, and none needed: kiro-cli reports no
        # status of its own, so an empty snapshot is what the real one carries
        # for this provider anyway.
        monkeypatch.setattr(presence_mod, "get_snapshot",
                            lambda *a, **k: presence_mod.Snapshot(set(), set()))
        try:
            rows = client.get(self._PATH).json()["groups"][0]["sessions"]
        finally:
            acp_mod._supervisor.sessions.pop("mine", None)
        return {r["id"]: r for r in rows}, asked

    def test_status_is_settled_only_for_the_rows_this_acp_holds(
            self, client, acp_listing_store, monkeypatch):
        """A dot means "this ACP is driving it", so a verdict is spent on
        exactly those rows and no others.

        The count is the assertion rather than the shape. Classifying a whole
        page to render at most `MAX_SESSIONS` dots is the cost this narrowing
        exists to avoid, and a response-shape check passes either way.
        """
        from power_atlas.status_classifier import SemanticStatus
        rows, asked = self._held_rows(
            client, acp_listing_store, monkeypatch,
            lambda _sid: SemanticStatus.WAITING)

        assert rows["mine"]["status"] == "waiting"
        # `""` and not a verdict: a locked session is live in a process this
        # cannot ask, and an available one has no live process at all.
        assert rows["free"]["status"] == ""
        assert rows["theirs"]["status"] == ""
        assert asked == ["mine"], (
            "a transcript was read for a row that draws no dot; the tail parse "
            f"is the cost being avoided here — classified {asked}")

    @pytest.mark.parametrize("semantic", [
        pytest.param(lambda _sid: None, id="nothing-classifiable"),
        pytest.param(
            lambda _sid: (_ for _ in ()).throw(RuntimeError("tail unreadable")),
            id="classifier-raised"),
    ])
    def test_a_held_row_nothing_can_classify_still_reads_as_working(
            self, client, acp_listing_store, monkeypatch, semantic):
        """The direction this fails in is the load-bearing part.

        A session this process holds is running by definition — we *are* the
        process — so an unreadable transcript is a failure to classify and never
        evidence of quiet. Both arms land on `working`: the one
        `_resolved_session_status` takes for the dashboard's own row, and the
        one the `except` here takes when the classifier raises outright.
        """
        rows, _asked = self._held_rows(
            client, acp_listing_store, monkeypatch, semantic)
        assert rows["mine"]["status"] == "working"

    def test_capacity_counts_held_sessions_and_the_cap(self, client,
                                                       acp_listing_store):
        """What the rail renders as `N/8 sessions open`."""
        from power_atlas import acp as acp_mod
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        acp_mod._supervisor.sessions["a"] = {"cwd": "C:\\dev\\ws"}
        acp_mod._supervisor.sessions["b"] = {"cwd": "C:\\dev\\ws"}
        try:
            body = client.get(self._PATH).json()
        finally:
            acp_mod._supervisor.sessions.pop("a", None)
            acp_mod._supervisor.sessions.pop("b", None)
        assert body["capacity"] == {"held": 2, "max": acp_mod.MAX_SESSIONS}

    def test_capacity_counts_a_creation_still_in_flight(self, client,
                                                        acp_listing_store):
        """`_reserved`, not just `sessions` — and the difference is the whole
        race the number exists to cover.

        `at_capacity()` is `len(sessions) + _reserved >= MAX_SESSIONS`, so a
        `session/new` that is still resolving already holds a slot the rail
        cannot see in `sessions`. Counting only `sessions` would report a free
        slot for the ~0.5-1.1 s a creation takes — which is exactly the window
        in which a second tap arrives, and exactly the tap this number exists
        to stop.
        """
        from power_atlas import acp as acp_mod
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        acp_mod._supervisor.sessions["a"] = {"cwd": "C:\\dev\\ws"}
        acp_mod._supervisor._reserved += 1
        try:
            body = client.get(self._PATH).json()
        finally:
            acp_mod._supervisor.sessions.pop("a", None)
            acp_mod._supervisor._reserved -= 1
        assert body["capacity"]["held"] == 2

    def test_capacity_agrees_with_at_capacity_at_the_boundary(self, client,
                                                             acp_listing_store):
        """The rail's `held >= max` and the server's `at_capacity()` must be the
        same predicate, or the page refuses a tap the server would have served
        (or the reverse, which loses a transcript).
        """
        from power_atlas import acp as acp_mod
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        for i in range(acp_mod.MAX_SESSIONS):
            acp_mod._supervisor.sessions["s%d" % i] = {"cwd": "C:\\dev\\ws"}
        try:
            assert acp_mod._supervisor.at_capacity()
            cap = client.get(self._PATH).json()["capacity"]
            assert cap["held"] >= cap["max"]
            acp_mod._supervisor.sessions.pop("s0")
            assert not acp_mod._supervisor.at_capacity()
            cap = client.get(self._PATH).json()["capacity"]
            assert cap["held"] < cap["max"]
        finally:
            for i in range(acp_mod.MAX_SESSIONS):
                acp_mod._supervisor.sessions.pop("s%d" % i, None)

    def test_the_documented_extent_is_the_whole_store_not_the_first_page(self):
        """The disclosure this route makes, stated at its true size.

        The user chose documentation over capping, which makes the wording the
        entire control — so it is pinned here like any other control. Both
        surfaces have to carry it: the route docstring, for whoever is reading
        the code, and the README's remote-access section, for whoever is
        deciding whether to switch remote access on.

        What the final QA measured over the real remote surface: `group_total`
        is 61 with `has_more: true`, so an authorized peer that keeps paging
        reaches **every** workspace path and **every** session title on the
        machine — not the 10x3 the rail happens to draw first. The 22.1%
        fallback rate reads as a bounded sample and is not one; it says how
        often `title` is raw prompt text, not how much of the store is
        reachable. This test fails if either surface loses the extent
        statement, or if the percentage is left standing as the only figure.
        """
        from power_atlas.web import api_acp_sessions

        def flatten(text):
            # Both surfaces wrap at ~79 columns and both emphasise with `**`,
            # so a raw `in` check is really asking "did the sentence happen to
            # break in the same place today". Neither is what is being pinned.
            return " ".join(text.replace("*", "").lower().split())

        doc = flatten(api_acp_sessions.__doc__ or "")
        readme = flatten((Path(__file__).resolve().parents[1] / "README.md")
                         .read_text(encoding="utf-8"))
        for name, text in (("route docstring", doc), ("README", readme)):
            assert "every workspace path and every session title" in text, (
                f"the {name} no longer states the full extent of the listing")
            assert "group_total" in text and "61" in text, (
                f"the {name} dropped the measurement the extent rests on")
            assert "not a bound" in text or "not the 10" in text, (
                f"the {name} no longer separates the page size from the extent, "
                "which is the exact misreading — 10x3 as a ceiling — that this "
                "wording exists to prevent")
            # The percentage may stay — it is true about the *fallback* — but
            # only alongside something that stops it being read as a ceiling,
            # and that retraction has to be *next to it*. Scanning the whole
            # document instead was measured passing against a copy whose
            # retraction had been deleted, because an unrelated "not a bound"
            # elsewhere answered for it — a check that cannot fail is worse
            # than no check, since it reads as coverage.
            if "22.1%" in text:
                near = text[text.index("22.1%"):][:260]
                assert "not a bound" in near or "not bound" in near, (
                    f"the {name} quotes 22.1% with nothing beside it to stop "
                    f"the figure reading as the size of the exposure: {near!r}")

    def test_no_env_launcher_or_action_field_appears_anywhere(self, client, acp_listing_store):
        """D18's reason for a new route rather than reusing
        `/partials/all-sessions`: that partial renders the hover-driven launch
        cluster. Substring-matched over the serialized body, so a nested
        structure cannot hide one.

        The second row is untitled **on purpose**. `_acp_row_title` falls back
        to the first prompt, and 267 of the real store's 1,210 sessions (22.1%)
        take that fallback — so a body scanned with every row titled never sees
        the payload's largest free-text field. `first_prompt` is deliberately
        not in the banned list: the *key* is absent but the *value* ships under
        `title` by design (see the route docstring), so banning the substring
        read as protection against a disclosure this route makes on purpose."""
        acp_listing_store["add"]("C:\\dev\\PowerAtlas", [
            _acp_row("s1", title="a title", first="a prompt"),
            _acp_row("s2", title="", first="fix the sweeper"),
        ])
        response = client.get(self._PATH)
        titles = [r["title"] for r in response.json()["groups"][0]["sessions"]]
        assert titles == ["a title", "fix the sweeper"], (
            "the prompt fallback never fired, so the scan below inspected a "
            "body without a prompt-derived title in it")
        raw = response.text.lower()
        for banned in ("env", "launcher", "custom_launchers", "args", "command",
                       "cmd", "token", "secret", "onclick", "href", "action",
                       "last_prompt", "last_reply_tail"):
            assert banned not in raw, f"{banned!r} reached the remote-facing payload"

    def test_availability_is_computed_only_for_returned_rows(self, client, acp_listing_store):
        """D17: ~30 rows, not 1,207. The discriminating property is *which* ids
        were checked, not the order they were checked in: an exact ordered list
        also fails on a per-group call, an sid dedup or any reordering that
        changes nothing observable, and it silently assumes the `held`
        short-circuit never fires — one held session in the fixture would break
        it for a reason unrelated to what it measures. The expectation is
        derived from the response instead: every returned row that is not
        already `held` is checked exactly once, and nothing else is."""
        for i in range(6):
            acp_listing_store["add"](f"C:\\dev\\w{i}",
                             [_acp_row(f"w{i}s{j}") for j in range(40)])
        body = client.get(self._PATH).json()
        returned = [row["id"] for g in body["groups"] for row in g["sessions"]]
        assert len(returned) == 18, "6 groups x 3 sessions is the default window"
        needs_a_check = {row["id"] for g in body["groups"] for row in g["sessions"]
                         if row["availability"] != "held"}
        calls = acp_listing_store["lock_calls"]
        assert set(calls) == needs_a_check, (
            f"{len(set(calls))} distinct ids checked for {len(needs_a_check)} "
            f"returned rows needing a check; the store holds 240")
        assert len(calls) == len(set(calls)), (
            f"{len(calls)} lock reads for {len(set(calls))} distinct ids")

    def test_the_supervisor_snapshot_happens_on_the_loop(self, client, acp_listing_store,
                                                         monkeypatch):
        """D9. `_supervisor.sessions` is loop-owned and unlocked, so a worker
        thread iterating it races every mutation the loop makes — a torn read,
        or `RuntimeError: dictionary changed size during iteration`. Asserting
        the right output does not discriminate: the output is identical whether
        the read was safe or not."""
        from power_atlas import acp as acp_mod
        live = _LoopBoundSessions({"held-1": {"cwd": "C:\\dev\\ws"}})
        monkeypatch.setattr(acp_mod._supervisor, "sessions", live)
        acp_listing_store["locked"].add("locked-1")
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("held-1"), _acp_row("locked-1"),
                                         _acp_row("free-1")])
        body = client.get(self._PATH).json()
        rows = {r["id"]: r["availability"] for r in body["groups"][0]["sessions"]}
        assert rows == {"held-1": "held", "locked-1": "locked",
                        "free-1": "available"}, "the snapshot was never consulted"
        assert live.reads >= 1, "the supervisor was not read at all"
        assert live.off_loop_reads == 0, (
            f"{live.off_loop_reads} of {live.reads} reads of loop-owned state "
            f"happened on a worker thread")

    def test_the_lock_checks_run_off_the_event_loop(self, client, acp_listing_store,
                                                    monkeypatch):
        """D17 again: a bounded file read plus a `psutil` query per row, ~30
        times. On the loop that is a stall on every other request the app is
        serving, and no output assertion can see the difference."""
        from power_atlas import web as web_mod
        real = web_mod._acp_availability
        where = []

        def _spy(session_ids, held):
            try:
                asyncio.get_running_loop()
                where.append("loop")
            except RuntimeError:
                where.append("thread")
            return real(session_ids, held)

        monkeypatch.setattr(web_mod, "_acp_availability", _spy)
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        assert client.get(self._PATH).status_code == 200
        assert where == ["thread"], f"availability was resolved on the {where}"

    def test_it_fails_open_to_available_when_the_lock_check_raises(
            self, client, acp_listing_store, monkeypatch):
        """D17's fail-open. A wrongly-greyed session is unreachable from the UI
        with no way for the user to find out why; a wrongly-available one costs
        one click and gets the agent's own typed in-use refusal at load."""
        from power_atlas import acp as acp_mod

        def _boom(sid):
            raise OSError("psutil is having a day")

        monkeypatch.setattr(acp_mod, "_lock_holder", _boom)
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1"), _acp_row("s2")])
        response = client.get(self._PATH)
        assert response.status_code == 200
        rows = response.json()["groups"][0]["sessions"]
        assert [r["availability"] for r in rows] == ["available", "available"]

    def test_a_208_session_workspace_pages_to_the_end(self, client, acp_listing_store):
        """D19. Every existing listing filter sets `has_more = False`, i.e.
        filters the loaded page and then declares nothing follows it. Walking
        to the end is what distinguishes real paging from a truncation that
        happens to render."""
        sessions = [_acp_row(f"s{i:03d}") for i in range(208)]
        acp_listing_store["add"]("C:\\dev\\Big", sessions)
        seen, page = [], 1
        while True:
            body = client.get(self._PATH, params={
                "cwd": "C:\\dev\\Big", "session_page": page, "session_size": 3,
            }).json()
            group = body["groups"][0]
            assert group["total"] == 208
            assert group["session_page"] == page
            seen.extend(r["id"] for r in group["sessions"])
            if not group["has_more"]:
                break
            page += 1
            assert page <= 70, "paging did not terminate"
        assert page == 70, f"208 rows at 3 a page is 70 pages, not {page}"
        assert seen == [s.session_id for s in sessions], (
            f"{len(seen)} of 208 rows survived paging, "
            f"{len(set(seen))} of them distinct")

    def test_group_paging_and_session_paging_are_independent(self, client, acp_listing_store):
        """"Independently at both levels" is the whole of D19: moving one axis
        must not move or reset the other."""
        for i in range(25):
            acp_listing_store["add"](f"C:\\dev\\w{i:02d}",
                             [_acp_row(f"w{i:02d}s{j}") for j in range(9)])
        first = client.get(self._PATH, params={"group_page": 1}).json()
        second = client.get(self._PATH, params={"group_page": 2}).json()
        assert first["group_total"] == second["group_total"] == 25
        assert first["has_more"] is True and second["has_more"] is True
        assert [g["cwd"] for g in first["groups"]] == [f"C:\\dev\\w{i:02d}" for i in range(10)]
        assert [g["cwd"] for g in second["groups"]] == [f"C:\\dev\\w{i:02d}" for i in range(10, 20)]
        for group in first["groups"] + second["groups"]:
            assert group["session_page"] == 1
            assert group["has_more"] is True
            assert len(group["sessions"]) == 3

        # Paging deep inside one group leaves the group axis where it was.
        deep = client.get(self._PATH, params={
            "cwd": "C:\\dev\\w00", "session_page": 3}).json()
        assert [r["id"] for r in deep["groups"][0]["sessions"]] == [
            "w00s6", "w00s7", "w00s8"]
        assert deep["groups"][0]["has_more"] is False
        assert deep["group_total"] == 1 and deep["has_more"] is False

        again = client.get(self._PATH, params={"group_page": 2}).json()
        assert [g["cwd"] for g in again["groups"]] == [g["cwd"] for g in second["groups"]]
        assert all(g["session_page"] == 1 for g in again["groups"])

        last = client.get(self._PATH, params={"group_page": 3}).json()
        assert len(last["groups"]) == 5 and last["has_more"] is False

    def test_page_sizes_are_clamped_and_pages_are_floored(self, client, acp_listing_store):
        """The cost of a row is a file read plus a `psutil` query, so a
        caller-supplied page size is an amplification lever, not a preference."""
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row(f"s{i:03d}") for i in range(200)])
        body = client.get(self._PATH, params={
            "group_page": 0, "session_page": -3,
            "group_size": 9999, "session_size": 9999}).json()
        assert body["group_page"] == 1
        group = body["groups"][0]
        assert group["session_page"] == 1
        assert len(group["sessions"]) == 50, "session_size must clamp to 50"
        assert len(acp_listing_store["lock_calls"]) == 50

    def test_the_group_axis_clamps_at_twenty(self, client, acp_listing_store):
        """The clamp above cannot see the group axis: its fixture holds one
        workspace, so `group_size=9999` and `group_size=1` return the same
        thing. Raising `_ACP_MAX_GROUPS_PER_PAGE` to `10**9` left the entire
        file green.

        The group axis is the expensive one — a group costs a full session load
        for its `total` on top of a lock read per returned row — so this asserts
        a **literal**, not the constant. A test that reads the constant it is
        meant to pin passes for whatever the constant is mutated to."""
        for i in range(25):
            acp_listing_store["add"](f"C:\\dev\\w{i:02d}", [_acp_row(f"w{i:02d}s0")])
        body = client.get(self._PATH, params={"group_size": 9999}).json()
        assert len(body["groups"]) == 20, (
            f"group_size must clamp to 20; {len(body['groups'])} groups came back")
        assert body["group_total"] == 25
        assert body["has_more"] is True, "5 workspaces are still unreturned"
        assert len(acp_listing_store["lock_calls"]) == 20

    def test_a_hidden_workspace_is_excluded_and_unreachable_by_cwd(
            self, client, acp_listing_store, monkeypatch):
        """`hidden` is the user saying "not on my dashboard", and a phone is not
        an exemption — `/partials/all-sessions` honours the tag and so does this.

        The exclusion was first skipped as "needs `load_config()`, which D15
        forbids on the loop". D15 forbids it **on the event loop**; `_acp_listing`
        runs entirely inside `asyncio.to_thread` and `load_config` is
        `threading.Lock`-guarded, so this is the one listing route where the
        config read is free. Requesting the hidden workspace by `cwd` is checked
        too: a filter applied only to the group axis would leave the exact path
        the rail already knows as a way around it."""
        from power_atlas import web as web_mod
        from power_atlas.config import Config

        acp_listing_store["add"]("C:\\dev\\Visible", [_acp_row("v1")])
        acp_listing_store["add"]("C:\\dev\\Hidden", [_acp_row("h1")])
        monkeypatch.setattr(web_mod, "load_config", lambda: Config(
            workspace_settings={"C:\\dev\\Hidden": {"tags": ["hidden"], "color": ""}}))

        body = client.get(self._PATH).json()
        assert [g["cwd"] for g in body["groups"]] == ["C:\\dev\\Visible"]
        assert body["group_total"] == 1, "a hidden workspace still counted"
        assert acp_listing_store["lock_calls"] == ["v1"], (
            "a hidden workspace's sessions were loaded and lock-checked anyway")

        direct = client.get(self._PATH, params={"cwd": "C:\\dev\\Hidden"}).json()
        assert direct["groups"] == [] and direct["group_total"] == 0, (
            "a hidden workspace was reachable by naming its path")

    def test_a_disabled_kiro_cli_lists_nothing(self, client, acp_listing_store,
                                               monkeypatch):
        """The same `_enabled(config, "kiro-cli")` flag every dashboard listing
        honours. A provider the user switched off should not be listable, least
        of all from the surface intended to leave loopback. The empty payload is
        asserted alongside an untouched lock-call recorder, so an implementation
        that walks the store and then filters the rendered rows fails here."""
        from power_atlas import web as web_mod
        from power_atlas.config import Config

        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        monkeypatch.setattr(web_mod, "load_config", lambda: Config(
            provider_settings={"kiro-cli": {"enabled": False}}))

        body = client.get(self._PATH).json()
        assert body["groups"] == [] and body["group_total"] == 0
        assert body["has_more"] is False
        assert acp_listing_store["lock_calls"] == []

    def test_sub_agent_sessions_are_absent(self, client, tmp_path, monkeypatch):
        """4,734 of the store's 5,941 files carry `parent_session_id`. The
        route inherits `data_kiro.load_sessions`'s filter rather than
        re-deriving it, so this runs against a real on-disk store — a mocked
        data layer would assert the mock, not the filter."""
        from power_atlas import data as data_mod
        from power_atlas import data_kiro

        store = tmp_path / "cli"
        store.mkdir()
        workspace = tmp_path / "ws"
        workspace.mkdir()

        def _write(sid, extra=None):
            record = {"session_id": sid, "title": f"t-{sid}", "cwd": str(workspace),
                      "created_at": "2026-07-31T00:00:00Z",
                      "updated_at": "2026-07-31T00:00:00Z"}
            record.update(extra or {})
            (store / f"{sid}.json").write_text(json.dumps(record), encoding="utf-8")

        _write("parent-1")
        _write("child-1", {"parent_session_id": "parent-1"})
        _write("child-2", {"parent_session_id": "parent-1"})

        monkeypatch.setattr(data_kiro, "SESSION_DIR", store)
        monkeypatch.setattr(data_kiro, "SQLITE_PATH", tmp_path / "absent.db")
        monkeypatch.setattr(data_kiro, "_meta_cache", {})
        monkeypatch.setattr(data_kiro, "_cwd_index", {})
        monkeypatch.setattr(data_kiro, "_cwd_index_mtime", None)
        monkeypatch.setattr(data_mod, "_cache", {})
        monkeypatch.setattr(data_mod, "session_cache", data_mod.SessionCache())

        body = client.get(self._PATH).json()
        ids = [r["id"] for g in body["groups"] for r in g["sessions"]]
        assert ids == ["parent-1"], f"a sub-agent session reached the rail: {ids}"
        assert [g["total"] for g in body["groups"]] == [1]

    def test_the_listing_is_on_the_remote_allowlist_behind_the_cookie(
            self, remote_enabled, acp_listing_store):
        """Phase 5b's integration step, and the pair SC-3 specifies for it.

        Phase 3 held this path off `_REMOTE_ALLOWED_PATHS` deliberately —
        registering a path before the route existed would have made it remotely
        reachable the moment it was written, inverting default-deny (D6). The
        rail is now the reason a phone opens `/acp` at all, so the path is
        registered; the predecessor of this test asserted its absence and was
        replaced here rather than deleted.

        Both arms matter and neither implies the other. Without the allowlist
        entry a cookie-bearing device is refused and the rail is dead on the
        phone; without the cookie the entry alone would serve every workspace
        path and session title on the account's 17-peer network to anyone who
        can reach the port.

        The HTTP scope is the whole point: `_peer_http` is the only way to set
        `scope["client"]`, which is the one value `RemoteAccessGuard`
        classifies on (D26)."""
        from power_atlas.web import _ACP_LISTING_PATH, _REMOTE_ALLOWED_PATHS
        assert _REMOTE_ALLOWED_PATHS[_ACP_LISTING_PATH] == "http"
        acp_listing_store["add"]("C:\\dev\\PowerAtlas", [_acp_row("s1")])

        status, body, _ = _peer_http(_ACP_LISTING_PATH, [_cookie_header()])
        assert status == 200, (
            "a device holding a valid cookie cannot reach the session rail, "
            "which is the only reason the page is on a phone")
        assert b'"groups"' in body

        bare, refused, _ = _peer_http(_ACP_LISTING_PATH)
        assert bare == 403, "the listing is served to any peer that reaches the port"
        assert refused == b'{"error":"Forbidden"}'

    def test_the_listing_is_not_reachable_as_a_websocket(self, remote_enabled):
        """The allowlist is scope-typed, not merely path-keyed. `/static` taught
        this the hard way: a path-only entry admitted `ws://<ip>/static/x` on
        the cookie alone. This route is `http` and must stay that way.

        Through `_guard_over_sentinel` and not the real app, for a reason the
        first draft of this test got wrong: Starlette answers a websocket scope
        with no matching route by sending `websocket.close` too, so against the
        real app the assertion passed whether the guard refused the upgrade or
        merely failed to route it. The sentinel makes "the guard let it through"
        a distinguishable outcome — it raises."""
        from power_atlas.web import _ACP_LISTING_PATH
        sent = _peer_ws(_ACP_LISTING_PATH, [_cookie_header()],
                        asgi_app=_guard_over_sentinel())
        assert sent == [{"type": "websocket.close", "code": 1008}]

    def test_the_listing_still_passes_the_gate_over_http(self, remote_enabled):
        """The refusal above must not have been bought by closing the path
        outright: the same guard, the same cookie, the http scope."""
        with pytest.raises(_Reached):
            from power_atlas.web import _ACP_LISTING_PATH
            _peer_http(_ACP_LISTING_PATH, [_cookie_header()],
                       asgi_app=_guard_over_sentinel())

    def test_a_vanished_workspace_directory_is_reported_as_such(
            self, client, acp_listing_store, tmp_path):
        """Phase 5b. Measured on the real store 2026-08-01: **14 of 65
        workspaces** name a directory that no longer exists, including the
        208-session `nrf_tool` worktree. Every one of their sessions reports
        `available`, correctly — D17 measures lock liveness, and nothing holds
        a lock on a session in a deleted tree — so without this field the rail
        offers 208 rows that fail the moment one is tapped.

        It has to be a *server* field: a browser cannot stat a filesystem.

        The two workspaces are asserted in one call so the discriminating
        property is the **difference** between them; a single vanished
        workspace also passes against an endpoint hardcoding `False`."""
        here = tmp_path / "still-here"
        here.mkdir()
        acp_listing_store["add"](str(here), [_acp_row("s1")])
        acp_listing_store["add"](str(tmp_path / "deleted"), [_acp_row("s2")])
        groups = client.get(self._PATH).json()["groups"]
        assert [g["exists"] for g in groups] == [True, False]
        # And it changed nothing about availability, which measures a different
        # thing and is what a naive fix would have rewritten.
        assert [s["availability"] for g in groups for s in g["sessions"]] == [
            "available", "available"]

    def test_an_unstattable_directory_is_not_reported_as_vanished(
            self, client, acp_listing_store, monkeypatch):
        """Fails to `True`. A permission error or a temporarily-unmounted
        network drive raising `OSError` must not badge a live workspace as
        gone: a false "folder missing" tells the user to stop trusting rows
        that are fine, which is the more expensive of the two errors. The
        opposite reading is merely the behaviour that shipped before.

        Patches `os.stat` rather than `Path.exists` since Phase 5b's review:
        the function under test no longer goes through `Path.exists`, and
        patching the method it stopped calling would have left this test
        passing while measuring nothing."""
        from power_atlas import web as web_mod

        target = "C:\\dev\\unreadable"
        real = web_mod.os.stat

        def _boom(path, *args, **kwargs):
            # Only the workspace under test. `load_config` stats its own path
            # through the same function, and raising for everything would fail
            # this test somewhere that has nothing to do with what it measures.
            if path == target:
                raise OSError(1314, "A required privilege is not held by the client")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(web_mod.os, "stat", _boom)
        acp_listing_store["add"](target, [_acp_row("s1")])
        assert client.get(self._PATH).json()["groups"][0]["exists"] is True

    def test_the_response_is_not_cacheable(self, client, acp_listing_store):
        """Availability is a liveness reading with a lifetime of seconds. A
        phone rendering a cached `available` for a session another process took
        in the meantime is the wrong failure to cache."""
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        response = client.get(self._PATH)
        assert response.headers["cache-control"] == "no-store"

    def test_a_title_falls_back_to_the_first_prompt(self, client, acp_listing_store):
        """`data_kiro` stamps `"<untitled>"` when the store carries no title,
        and the `session-tab-title` steering rework is out of this plan's
        scope — so the rail shows what the user actually typed."""
        acp_listing_store["add"]("C:\\dev\\ws", [
            _acp_row("s1", title="<untitled>", first="fix the sweeper"),
            _acp_row("s2", title="", first="x" * 300),
            _acp_row("s3", title="a real title", first="ignored"),
        ])
        rows = client.get(self._PATH).json()["groups"][0]["sessions"]
        assert [r["title"] for r in rows] == [
            "fix the sweeper", "x" * 120, "a real title"]

    def test_an_unknown_workspace_is_an_empty_listing_not_an_error(self, client,
                                                                   acp_listing_store):
        from power_atlas import acp as acp_mod
        acp_listing_store["add"]("C:\\dev\\ws", [_acp_row("s1")])
        body = client.get(self._PATH, params={"cwd": "C:\\dev\\gone"}).json()
        assert body == {"groups": [], "group_page": 1, "group_total": 0,
                        "has_more": False,
                        "capacity": {"held": 0, "max": acp_mod.MAX_SESSIONS}}
        assert acp_listing_store["lock_calls"] == []


@pytest.fixture
def acp_store_dir(tmp_path, monkeypatch):
    """A real on-disk kiro-cli store, and a factory for sessions in it.

    Real files rather than mocks, because what the delete path is being tested
    on is filesystem behaviour: which paths a session actually owns, and whether
    a failure part-way leaves the store readable. A mocked `Path` would let
    every one of those pass without touching the property.

    Returns a callable that writes one session's full set — `.json`, `.jsonl`,
    `.history`, `.lock` and the `<id>/tasks/` subtree the live store carries —
    and hands back the paths it wrote.
    """
    from power_atlas import acp as acp_mod
    monkeypatch.setattr(acp_mod, "KIRO_SESSION_DIR", tmp_path)

    def _make(session_id, cwd="C:\\dev\\ws", *, lock=True, history=True):
        written = []
        meta = tmp_path / f"{session_id}.json"
        meta.write_text(json.dumps({"session_id": session_id, "cwd": cwd,
                                    "title": "a session"}), encoding="utf-8")
        written.append(meta)
        jsonl = tmp_path / f"{session_id}.jsonl"
        jsonl.write_text('{"kind":"Prompt"}\n', encoding="utf-8")
        written.append(jsonl)
        if history:
            hist = tmp_path / f"{session_id}.history"
            hist.write_text("first prompt\n", encoding="utf-8")
            written.append(hist)
        if lock:
            lk = tmp_path / f"{session_id}.lock"
            lk.write_text(json.dumps({"pid": 4242,
                                      "started_at": "2026-08-01T10:00:00Z"}),
                          encoding="utf-8")
            written.append(lk)
        # ~500 of the real store's ids carry one of these, each holding a
        # `tasks/` subdirectory — so the delete has a tree to remove, not just
        # an empty directory.
        tasks = tmp_path / session_id / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "t1.json").write_text("{}", encoding="utf-8")
        written.append(tmp_path / session_id)
        return written

    return _make


class TestAcpFlatListing:
    """`mode=recent` — the flat recency shape a day-grouped rail reads.

    Seamed at `get_all_sessions_paginated` rather than at the store, because
    what this mode adds is a *contract with* that function: which provider it
    is pinned to, which workspaces it is told to skip, and that the skipping is
    handed over rather than applied afterwards. Those are the three things that
    would leak or truncate if they regressed, and none of them is observable
    from the returned rows.
    """

    _PATH = "/api/acp/sessions"

    @pytest.fixture
    def collector(self, monkeypatch):
        """Answers the collector call and records what the route asked for."""
        from power_atlas import data as data_mod

        asked = {}

        def _paginated(page=1, page_size=20, provider=None,
                       pinned_sessions=None, enabled_providers=None,
                       exclude_cwds=None):
            asked.update(page=page, page_size=page_size, provider=provider,
                         enabled_providers=enabled_providers,
                         exclude_cwds=exclude_cwds)
            rows = [(data_mod.Session(
                session_id=f"s{i}", title=f"title {i}", cwd=rf"C:\ws\w{i}",
                created_at="", updated_at=f"2026-08-0{3 - i}T10:00:00.000000000Z",
                first_prompt="", last_prompt="", last_reply_tail=""),
                "kiro-cli") for i in range(3)]
            return rows, True

        monkeypatch.setattr(data_mod, "get_all_sessions_paginated", _paginated)
        monkeypatch.setattr(data_mod, "discover_workspaces_with_counts",
                            lambda provider=None: [])
        return asked

    def test_mode_recent_answers_a_flat_list_and_not_groups(self, client, collector):
        body = client.get(self._PATH, params={"mode": "recent"}).json()
        assert "sessions" in body and "groups" not in body
        assert [r["id"] for r in body["sessions"]] == ["s0", "s1", "s2"]
        assert body["page"] == 1 and body["has_more"] is True
        assert set(body["sessions"][0]) == {
            "id", "title", "updated_at", "availability", "status", "cwd",
            "name", "exists"}

    def test_a_row_carries_the_workspace_it_came_from(self, client, collector):
        """Grouped by day there is no workspace header, so the row is the only
        place left that can say which project a session belongs to — and the
        only thing a missing-folder warning can attach to."""
        row = client.get(self._PATH, params={"mode": "recent"}).json()["sessions"][0]
        assert row["cwd"] == r"C:\ws\w0" and row["name"] == "w0"

    def test_an_unrecognised_mode_answers_the_grouped_shape(self, client,
                                                            acp_listing_store):
        """Exact match, not truthiness. An older client, a typo or a probe must
        get the response every existing caller already expects rather than a
        shape none of them can read."""
        for mode in ("", "recent-ish", "RECENT", "1", "date"):
            body = client.get(self._PATH, params={"mode": mode}).json()
            assert "groups" in body, f"mode={mode!r} did not fall through"

    def test_the_flat_page_size_is_clamped(self, client, collector):
        """The route is remotely reachable, so a caller-supplied page size is an
        amplification lever — availability is O(rows) with no budget of its own."""
        client.get(self._PATH, params={"mode": "recent", "size": 5000})
        assert collector["page_size"] == 100
        client.get(self._PATH, params={"mode": "recent", "size": 0, "page": -4})
        assert collector["page_size"] == 1 and collector["page"] == 1

    def test_the_collector_is_pinned_to_the_acp_provider(self, client, collector):
        """`get_all_sessions_paginated` spans every registered provider by
        default. A row served here for another one would be a session the
        browser cannot resume, which is the same reason the grouped listing
        hardcodes the provider."""
        client.get(self._PATH, params={"mode": "recent"})
        assert collector["provider"] == "kiro-cli"
        assert collector["enabled_providers"] == {"kiro-cli"}

    def test_hidden_workspaces_are_handed_over_not_filtered_afterwards(
            self, client, monkeypatch, collector):
        """The exclusion has to reach the collector, because the collector is
        what early-stops. Filtering its result instead would let hidden rows
        decide where the read stopped: the page comes back short and `has_more`
        stops describing what the caller is showing."""
        from power_atlas import config as config_mod
        from power_atlas import data as data_mod

        monkeypatch.setattr(
            data_mod, "discover_workspaces_with_counts",
            lambda provider=None: [(r"C:\ws\secret", 3, "2026-08-03T10:00:00Z",
                                    "kiro-cli"),
                                   (r"C:\ws\open", 3, "2026-08-03T10:00:00Z",
                                    "kiro-cli")])
        monkeypatch.setattr(
            config_mod, "get_workspace_settings",
            lambda cfg, cwd: {"tags": ["hidden"] if "secret" in cwd else []})

        client.get(self._PATH, params={"mode": "recent"})
        assert collector["exclude_cwds"] == {r"C:\ws\secret"}

    def test_a_disabled_provider_serves_nothing(self, client, monkeypatch,
                                                 collector):
        from power_atlas import web as web_mod

        monkeypatch.setattr(web_mod, "_enabled", lambda cfg, prov: False)
        body = client.get(self._PATH, params={"mode": "recent"}).json()
        assert body["sessions"] == [] and body["has_more"] is False
        assert "page_size" not in collector, (
            "a disabled provider still reached the collector")

    def test_the_capacity_pair_rides_the_flat_shape_too(self, client, collector):
        """The rail reads the cap off whichever listing it last fetched, so a
        mode that dropped it would let the cap go stale for as long as the user
        stayed in that mode."""
        cap = client.get(self._PATH, params={"mode": "recent"}).json()["capacity"]
        assert set(cap) == {"held", "max"}


class TestAcpDeleteEndpoint:
    """Session deletion — the first thing PowerAtlas writes to kiro-cli's store.

    Irreversible, loopback-only, and refused for any session something might
    still be holding. Each test below pins one of those three.
    """

    _PATH = "/api/acp/sessions/delete"

    def _post(self, client, ids):
        return client.post(self._PATH, json={"session_ids": ids})

    def test_it_removes_every_path_the_session_owns(self, client, acp_store_dir,
                                                    monkeypatch):
        """All five, not the three `data_kiro` reads.

        A delete written from the loader's point of view would leave the
        `.lock` and the `<id>/` tree behind: invisible to PowerAtlas, still on
        disk, and — for the lock — still able to make the id read as `locked`.
        """
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        paths = acp_store_dir("sess-1")
        assert all(p.exists() for p in paths)

        body = self._post(client, ["sess-1"]).json()

        assert body == {"deleted": ["sess-1"], "failed": []}
        assert [p for p in paths if p.exists()] == []

    def test_a_held_session_is_refused_and_nothing_is_removed(self, client,
                                                              acp_store_dir,
                                                              monkeypatch):
        """PowerAtlas's own agent has it open — Close is the remedy, and the
        message has to say so, because Close and Delete are different actions
        and only one of them is reversible."""
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        paths = acp_store_dir("sess-held")
        acp_mod._supervisor.sessions["sess-held"] = {"cwd": "C:\\dev\\ws"}
        try:
            body = self._post(client, ["sess-held"]).json()
        finally:
            acp_mod._supervisor.sessions.pop("sess-held", None)

        assert body["deleted"] == []
        assert body["failed"][0]["code"] == "held"
        assert "close" in body["failed"][0]["message"].lower()
        assert all(p.exists() for p in paths)

    def test_a_locked_session_is_refused_and_names_the_holder(self, client,
                                                              acp_store_dir,
                                                              monkeypatch):
        """A foreign live kiro-cli. The pid is carried because it is the only
        thing that tells the user *where* to go and close it."""
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: 21344)
        paths = acp_store_dir("sess-locked")

        body = self._post(client, ["sess-locked"]).json()

        assert body["deleted"] == []
        assert body["failed"][0]["code"] == "locked"
        assert "21344" in body["failed"][0]["message"]
        assert all(p.exists() for p in paths)

    def test_an_unreadable_lock_does_not_grant_a_deletion(self, client,
                                                          acp_store_dir,
                                                          monkeypatch):
        """`_lock_holder` raising is "I could not tell", and the route treats it
        as no holder — the same fail-open `_acp_availability` takes, and for the
        same reason: the hint may add a refusal, never grant one. What stops
        that becoming data loss is the staging rename, not this check."""
        from power_atlas import acp as acp_mod

        def _boom(sid):
            raise OSError("the lock could not be read")

        monkeypatch.setattr(acp_mod, "_lock_holder", _boom)
        acp_store_dir("sess-hintless")
        body = self._post(client, ["sess-hintless"]).json()
        assert body["deleted"] == ["sess-hintless"]

    def test_an_id_that_could_form_a_path_is_refused_before_it_does(
            self, client, acp_store_dir, monkeypatch, tmp_path):
        """The id becomes a filename in a directory holding 5,958 other
        conversations, so it is checked against the same rule the `load` path
        applies. A traversal must be refused *and* leave the target alone."""
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        outsider = tmp_path.parent / "outside.json"
        outsider.write_text("keep me", encoding="utf-8")

        body = self._post(client, ["../outside", "a/b", "with.dot", ""]).json()

        assert body["deleted"] == []
        assert [f["code"] for f in body["failed"]] == ["bad_id"] * 4
        assert outsider.exists()

    def test_a_session_with_no_files_is_not_found_rather_than_deleted(
            self, client, acp_store_dir, monkeypatch):
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        body = self._post(client, ["never-existed"]).json()
        assert body["deleted"] == []
        assert body["failed"][0]["code"] == "not_found"

    def test_a_failure_part_way_through_leaves_the_session_whole(
            self, client, acp_store_dir, monkeypatch):
        """**The property the whole design exists for.**

        A session is up to five paths. Unlinking them in turn has no way to
        fail cleanly: on Windows an unlink is refused for a file another
        process holds open (`winerror=32`, measured 2026-08-03), so a delete
        racing a live holder would remove the `.json`, trip on the `.jsonl`,
        and leave an entry nothing in this repo can parse.

        So every path is *renamed* first — a refused rename changes nothing —
        and only once all of them have moved is anything destroyed. Simulated
        here by failing the third rename rather than by opening a real handle,
        because a held-file test would assert Windows semantics on a POSIX
        runner and pass vacuously.
        """
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        paths = acp_store_dir("sess-racy")

        real_replace = os.replace
        calls = {"n": 0}

        def _flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 3:
                err = OSError("in use")
                err.winerror = 32
                raise err
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _flaky)
        body = self._post(client, ["sess-racy"]).json()

        assert body["deleted"] == []
        assert body["failed"][0]["code"] == "in_use"
        # Every path back under its own name — not merely present under some
        # name. A rollback that left `<id>.json.pa-deleting-ab12` on disk would
        # satisfy "nothing was destroyed" while hiding the session from every
        # reader, which is the same outcome as deleting it.
        assert all(p.exists() for p in paths), (
            "a refused delete did not restore the session it had staged")

    def test_a_mixed_request_reports_each_id_separately(self, client,
                                                        acp_store_dir,
                                                        monkeypatch):
        """The list form admits partial success, which is why the outcome is
        per id and the status is 200 either way — a 4xx over a mixed result
        would leave the caller unable to tell which half happened."""
        from power_atlas import acp as acp_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        kept = acp_store_dir("sess-keep")
        acp_store_dir("sess-go")
        acp_mod._supervisor.sessions["sess-keep"] = {"cwd": "C:\\dev\\ws"}
        try:
            res = self._post(client, ["sess-keep", "sess-go"])
        finally:
            acp_mod._supervisor.sessions.pop("sess-keep", None)

        assert res.status_code == 200
        body = res.json()
        assert body["deleted"] == ["sess-go"]
        assert [f["id"] for f in body["failed"]] == ["sess-keep"]
        assert all(p.exists() for p in kept)

    def test_a_successful_delete_expires_the_caches_that_would_restore_the_row(
            self, client, acp_store_dir, monkeypatch):
        """Without this the row comes back on the next Refresh.

        `get_sessions` answers from `session_cache`, and the workspace counts
        sit behind a 30 s TTL — so the store changing underneath them is not
        something either notices in time for the request the user is about to
        make.
        """
        from power_atlas import acp as acp_mod
        from power_atlas import data as data_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: None)
        acp_store_dir("sess-cached", cwd="C:\\dev\\cached")
        forgotten = []
        counts_cleared = []
        monkeypatch.setattr(data_mod.session_cache, "forget",
                            lambda cwd, provider="kiro-cli": forgotten.append((cwd, provider)))
        monkeypatch.setattr(data_mod, "invalidate_workspace_counts",
                            lambda: counts_cleared.append(True))

        self._post(client, ["sess-cached"])

        assert forgotten == [("C:\\dev\\cached", "kiro-cli")]
        assert counts_cleared == [True]

    def test_nothing_is_invalidated_when_nothing_was_deleted(self, client,
                                                             acp_store_dir,
                                                             monkeypatch):
        """A refused request must not drop a workspace's parsed session list:
        that is a full re-parse of the store's largest workspace (208 sessions)
        bought for nothing."""
        from power_atlas import acp as acp_mod
        from power_atlas import data as data_mod
        monkeypatch.setattr(acp_mod, "_lock_holder", lambda sid: 21344)
        acp_store_dir("sess-busy")
        forgotten = []
        monkeypatch.setattr(data_mod.session_cache, "forget",
                            lambda cwd, provider="kiro-cli": forgotten.append(cwd))

        self._post(client, ["sess-busy"])

        assert forgotten == []

    def test_the_route_is_not_on_the_remote_surface(self):
        """**The whole authorization story, and it is an absence.**

        `_REMOTE_ALLOWED_PATHS` is default-deny (D6), so leaving this path out
        of it is what refuses a remote peer — no check in the route relies on
        being remembered. This test is the tripwire for the one-line edit that
        would make irreversible deletion reachable from a phone.
        """
        from power_atlas.web import _ACP_DELETE_PATH, _REMOTE_ALLOWED_PATHS
        assert _ACP_DELETE_PATH not in _REMOTE_ALLOWED_PATHS
        # And it is not reachable by prefix through the listing entry either:
        # the two paths share one, and the matcher is exact for everything but
        # the static mount.
        from power_atlas.web import _remote_path_allowed
        assert not _remote_path_allowed(_ACP_DELETE_PATH, "http")

    @pytest.mark.parametrize("payload", [
        {}, {"session_ids": []}, {"session_ids": "sess-1"},
        {"session_ids": ["a"] * 201},
    ])
    def test_a_malformed_request_is_refused_without_touching_the_store(
            self, client, acp_store_dir, payload):
        res = client.post(self._PATH, json=payload)
        assert res.status_code == 400


class TestAcpWorkspacesEndpoint:
    """The create picker's list. Paths and counts, no session content.

    Split from the listing route rather than added to it because the two answer
    different questions at very different prices: `_acp_listing` loads every
    session of every group it returns to compute `total`, which for a picker
    over 65 workspaces would be the most expensive request the app makes.
    """

    _PATH = "/api/acp/workspaces"

    def test_it_carries_paths_and_counts_and_no_session_content(
            self, client, acp_listing_store, tmp_path):
        """Asserted as equality, so a field added later fails here rather than
        reaching a phone — this route is on the remote surface."""
        ws = tmp_path / "ProjectOne"
        ws.mkdir()
        acp_listing_store["add"](str(ws), [_acp_row("s1", title="a title"),
                                           _acp_row("s2")])

        body = client.get(self._PATH).json()

        assert set(body) == {"workspaces", "missing", "capacity"}
        assert body["workspaces"] == [
            {"cwd": str(ws), "name": "ProjectOne", "sessions": 2}]
        # The whole point of the split: no id, no title, no timestamp. The
        # listing route's own docstring records that `title` can be raw prompt
        # text, and this route is reachable from the same remote surface.
        assert "s1" not in json.dumps(body)
        assert "a title" not in json.dumps(body)

    def test_a_workspace_whose_folder_is_gone_is_not_offered(
            self, client, acp_listing_store, tmp_path):
        """`_resolve_session_cwd` refuses every one of them with `BadCwd`, so
        offering them would be offering guaranteed failures — 14 of the real
        store's 65 workspaces are in this state. The count is reported because a
        list that silently shows 51 of 65 reads as broken."""
        live = tmp_path / "live"
        live.mkdir()
        acp_listing_store["add"](str(live), [_acp_row("s1")])
        acp_listing_store["add"](str(tmp_path / "deleted"), [_acp_row("s2")])

        body = client.get(self._PATH).json()

        assert [w["cwd"] for w in body["workspaces"]] == [str(live)]
        assert body["missing"] == 1

    def test_a_hidden_workspace_is_excluded_like_it_is_from_the_listing(
            self, client, acp_listing_store, tmp_path, monkeypatch):
        """A workspace hidden from the dashboard has not asked to be a create
        target either — the same exclusion `_acp_listing` applies."""
        from power_atlas import web as web_mod
        shown, hidden = tmp_path / "shown", tmp_path / "hidden"
        shown.mkdir()
        hidden.mkdir()
        acp_listing_store["add"](str(shown), [_acp_row("s1")])
        acp_listing_store["add"](str(hidden), [_acp_row("s2")])
        monkeypatch.setattr(
            web_mod, "_enabled", lambda config, provider: True)
        import power_atlas.config as config_mod
        real = config_mod.get_workspace_settings
        monkeypatch.setattr(
            config_mod, "get_workspace_settings",
            lambda config, cwd: ({"tags": ["hidden"]} if cwd == str(hidden)
                                 else real(config, cwd)))

        body = client.get(self._PATH).json()

        assert [w["cwd"] for w in body["workspaces"]] == [str(shown)]
        # Hidden is not missing: the folder is there, the user chose not to see
        # it. Conflating the two would report a phantom "folder no longer on
        # this machine" for a directory that is fine.
        assert body["missing"] == 0

    def test_it_reports_the_same_capacity_pair_the_listing_does(
            self, client, acp_listing_store, tmp_path):
        """The picker refuses at the cap before spending anything, so it needs
        the pair on the answer it already makes rather than a second request."""
        from power_atlas import acp as acp_mod
        ws = tmp_path / "ws"
        ws.mkdir()
        acp_listing_store["add"](str(ws), [_acp_row("s1")])
        acp_mod._supervisor.sessions["a"] = {"cwd": str(ws)}
        try:
            body = client.get(self._PATH).json()
        finally:
            acp_mod._supervisor.sessions.pop("a", None)
        assert body["capacity"] == {"held": 1, "max": acp_mod.MAX_SESSIONS}

    def test_it_is_on_the_remote_surface_and_deletion_is_not(self):
        """The asymmetry, pinned.

        Creating a session already works from a phone — `session/new` rides the
        allowlisted `/ws/acp` — so a picker that could not list workspaces
        remotely would remove a capability that exists today. Deletion does not
        exist yet, so keeping it local costs nothing.
        """
        from power_atlas.web import (_ACP_WORKSPACES_PATH, _ACP_DELETE_PATH,
                                     _REMOTE_ALLOWED_PATHS)
        assert _REMOTE_ALLOWED_PATHS[_ACP_WORKSPACES_PATH] == "http"
        assert _ACP_DELETE_PATH not in _REMOTE_ALLOWED_PATHS

    def test_it_reads_the_store_only_once_per_request(self, client,
                                                      acp_listing_store,
                                                      tmp_path, monkeypatch):
        """`get_sessions` is never called. That is the cost difference from the
        listing route, and it is the reason this route exists at all: the
        listing loads every session of every group it returns to compute
        `total`, measured at 975 of 1,210 sessions for a single request."""
        from power_atlas import data as data_mod
        ws = tmp_path / "ws"
        ws.mkdir()
        acp_listing_store["add"](str(ws), [_acp_row("s1")])
        calls = []
        monkeypatch.setattr(data_mod, "get_sessions",
                            lambda cwd, provider="kiro-cli": calls.append(cwd) or [])

        client.get(self._PATH)

        assert calls == []


class TestWorkspaceExistsDiscriminatesAbsenceFromSilence:
    """Phase 5b review. `_acp_cwd_exists`'s docstring promised that "a
    permission error or an unmounted network drive must not badge a live
    workspace as gone", and for the network half the implementation delivered
    the opposite: `Path.exists()` **swallows** every `OSError` on pathlib's own
    ignore list and returns `False` without raising, so the `except OSError:
    return True` beneath it was never reached for them.

    The test that guarded it could not see this, because it monkeypatched
    `Path.exists` to raise — which proves the handler works *if reached*, not
    that the real call reaches it. Everything below either calls the real
    syscall or drives the classifier directly.
    """

    # A UNC host chosen not to resolve. `os.stat` on it raises ERROR_BAD_NETPATH
    # rather than answering, which is the whole case under test.
    UNREACHABLE = r"\\pa-no-such-host-9f3c\share\proj"

    def test_a_real_unreachable_network_path_is_not_reported_as_vanished(self):
        """The review's own case, driven through a real syscall.

        Skips rather than passes when the environment does not produce the
        error, because a DNS wildcard or a captive resolver can make this name
        resolve — and a test that quietly measured nothing on those machines is
        the exact defect this class exists to correct.

        Costs one cold negative-cache miss, measured at 2.6 s on this machine
        and 0.00 s on every run after; the same probe against a *routable* dead
        host took 42 s, which is why `_ACP_EXISTS_BUDGET_SECONDS` exists.
        """
        from power_atlas.web import _acp_cwd_exists

        try:
            os.stat(self.UNREACHABLE)
        except OSError as exc:
            observed = exc
        else:
            pytest.skip(f"{self.UNREACHABLE} resolved here; no unreachable path "
                        "to test against")
        if os.name == "nt" and getattr(observed, "winerror", None) is None:
            pytest.skip(f"unexpected error shape: {observed!r}")

        # The discriminating half. `Path.exists()` is what the shipped code
        # delegated to, and it answers `False` for this path without raising —
        # so asserting only the line below would pass against the old
        # implementation too.
        assert Path(self.UNREACHABLE).exists() is False
        assert _acp_cwd_exists(self.UNREACHABLE) is True

    def test_a_real_absent_directory_is_still_reported_gone(self, tmp_path):
        """The positive control, also a real syscall. Without it every
        assertion above is satisfied by a function that returns `True`."""
        from power_atlas.web import _acp_cwd_exists

        assert _acp_cwd_exists(str(tmp_path)) is True
        assert _acp_cwd_exists(str(tmp_path / "never-created")) is False

    @pytest.mark.parametrize("winerror,absent", [
        (2, True),      # ERROR_FILE_NOT_FOUND
        (3, True),      # ERROR_PATH_NOT_FOUND — an unmapped drive letter
        (123, True),    # ERROR_INVALID_NAME — a name that can never resolve
        (21, False),    # ERROR_NOT_READY — the drive is there and did not answer
        (53, False),    # ERROR_BAD_NETPATH — the measured unreachable-UNC code
        (67, False),    # ERROR_BAD_NET_NAME
        (121, False),   # ERROR_SEM_TIMEOUT
        (1231, False),  # ERROR_NETWORK_UNREACHABLE
        (1326, False),  # ERROR_LOGON_FAILURE — an expired mapped-share credential
        (1921, False),  # ERROR_CANT_RESOLVE_FILENAME
        (1314, False),  # ERROR_PRIVILEGE_NOT_HELD
        (9999, False),  # unanticipated: unknown fails open, never to "gone"
    ])
    def test_winerror_is_read_before_errno(self, winerror, absent):
        """Every one of these carries `errno` 2 — `ENOENT` — because that is
        what Windows gives them, and `ERROR_BAD_NETPATH` really does arrive
        that way (measured 2026-08-01). A classifier that read `errno` first
        would call all twelve absent, so the parametrisation discriminates the
        ordering and not merely the membership of the two sets."""
        from power_atlas.web import _acp_stat_says_absent

        exc = OSError(errno.ENOENT, "measured shape")
        exc.winerror = winerror
        assert _acp_stat_says_absent(exc) is absent

    @pytest.mark.parametrize("code,absent", [
        (errno.ENOENT, True),
        (errno.ENOTDIR, True),
        (errno.EACCES, False),
        (errno.ETIMEDOUT, False),
        (errno.EIO, False),
    ])
    def test_errno_decides_where_there_is_no_winerror(self, code, absent):
        """The POSIX arm: `winerror` is absent and `errno` is all there is."""
        from power_atlas.web import _acp_stat_says_absent

        assert _acp_stat_says_absent(OSError(code, "posix shape")) is absent


class TestWorkspaceStatIsBoundedPerRequest:
    """Phase 5b review, Low. One blocking `stat` per returned group, serialized
    inside a single worker, up to the clamp of 20. The event loop is correctly
    not blocked; the *response* is. Measured 2026-08-01: `os.stat` on a
    routable-but-dead UNC host took 42.2 s to return, so the unbounded version
    could hold one request for over ten minutes."""

    def test_the_budget_stops_the_walk_but_never_before_the_first(self,
                                                                 monkeypatch):
        from power_atlas import web as web_mod

        seen = []

        def _slow(cwd):
            seen.append(cwd)
            time.sleep(0.02)
            return False

        monkeypatch.setattr(web_mod, "_acp_cwd_exists", _slow)
        monkeypatch.setattr(web_mod, "_ACP_EXISTS_BUDGET_SECONDS", 0.05)
        flags = web_mod._acp_exists_flags([f"C:\\ws-{i}" for i in range(20)])

        assert len(flags) == 20, "every group must still get an answer"
        assert 1 <= len(seen) < 20, (
            f"the budget neither fired nor spared the first call: {len(seen)} "
            "of 20 were stat'ed")
        # Everything actually stat'ed reported gone; everything past the
        # deadline reports present, because not having looked is not evidence
        # of absence.
        assert flags[:len(seen)] == [False] * len(seen)
        assert flags[len(seen):] == [True] * (20 - len(seen))

    def test_a_single_group_is_answered_from_disk_and_not_from_the_budget(
            self, monkeypatch):
        """A zero budget must not turn a one-group page into a fabricated
        `True`: the deadline is set from `time.monotonic()` one line above the
        first check, so the first entry always runs."""
        from power_atlas import web as web_mod

        monkeypatch.setattr(web_mod, "_ACP_EXISTS_BUDGET_SECONDS", 0.0)
        monkeypatch.setattr(web_mod, "_acp_cwd_exists", lambda cwd: False)
        assert web_mod._acp_exists_flags(["C:\\only"]) == [False]
        assert web_mod._acp_exists_flags(["C:\\a", "C:\\b"]) == [False, True]


class TestAcpSteer:
    """``steer`` client frame injects a mid-turn message via ``_session/steer``
    (a JSON-RPC *request*, not a notification — verified by live probe 2026-08-12).
    """

    def _conn(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def test_steer_frame_is_routed(self, acp_session):
        """A ``steer`` frame on a subscribed in-flight session calls
        ``_supervisor.steer`` with the correct args and queues a ``steer_ack``
        frame back to the requesting socket only (unicast, not broadcast)."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        captured = []
        _queued(conn)

        # A second subscriber on the same session — must NOT receive steer_ack.
        conn2 = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn2)
        acp_mod._registry.attach(conn2, sid)
        _queued(conn2)

        async def fake_steer(self_ignored, session_id, text):
            captured.append((session_id, text))
            return {"queued": True}

        with patch.object(acp_mod._Supervisor, "steer", fake_steer):
            asyncio.run(acp_mod._handle_steer(
                conn, sid, {"message": "focus on the login bug"}))

        assert captured == [(sid, "focus on the login bug")]
        outbound = _queued(conn)
        assert len(outbound) == 1
        assert outbound[0]["type"] == "steer_ack"
        assert outbound[0]["sessionId"] == sid
        assert outbound[0]["payload"]["queued"] is True

        # The second subscriber must not have received a steer_ack frame.
        outbound2 = _queued(conn2)
        assert not any(f["type"] == "steer_ack" for f in outbound2), \
            f"steer_ack was broadcast to a non-requesting socket: {outbound2}"

    def test_steer_refused_for_subagent_session(self, acp_session):
        """``steer`` on a sub-agent's own session id must return a
        ``read_only_session`` error — sub-agents cannot be steered directly."""
        acp_mod, sid = acp_session
        child_id = "sub-steer-01"
        acp_mod._supervisor.subagent_sessions[child_id] = {"parent": sid}
        # A connection not subscribed to the child — guard fires on subagent_sessions
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, child_id, {"message": "x"}))
            outbound = _queued(conn)
            assert outbound[0]["payload"]["code"] == "read_only_session"
        finally:
            acp_mod._supervisor.subagent_sessions.pop(child_id, None)
            acp_mod._registry.connections.discard(conn)

    def test_steer_refused_when_no_turn_in_progress(self, acp_session):
        """Steering with no active turn returns ``no_turn_in_progress`` rather
        than hanging for ``REQUEST_TIMEOUT_SECONDS``."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        # inflight is empty (default for acp_session fixture)
        _queued(conn)
        asyncio.run(acp_mod._handle_steer(conn, sid, {"message": "hurry up"}))
        outbound = _queued(conn)
        assert outbound[0]["payload"]["code"] == "no_turn_in_progress"

    def test_steer_supervisor_uses_request_not_notify(self, acp_session):
        """``_supervisor.steer`` must put an ``id`` on the wire — a notification
        (no ``id``) would hang the caller for ``REQUEST_TIMEOUT_SECONDS``."""
        acp_mod, sid = acp_session
        written = []
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, written)), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            _run_bound(acp_mod,
                       lambda: acp_mod._supervisor.steer(sid, "quick check"))

        assert len(written) == 1
        assert "id" in written[0], "steer must use _request (has id), not _notify"
        assert written[0]["method"] == "_session/steer"
        assert written[0]["params"]["sessionId"] == sid
        assert written[0]["params"]["message"] == "quick check"

    def test_steer_refused_for_unknown_session(self, acp_session):
        """``steer`` when session_id is not in ``_supervisor.sessions`` returns
        ``unknown_session``."""
        acp_mod, sid = acp_session
        unknown_sid = "not-registered-steer"
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, unknown_sid, {"message": "x"}))
            outbound = _queued(conn)
            assert outbound[0]["payload"]["code"] == "unknown_session"
        finally:
            acp_mod._registry.connections.discard(conn)

    def test_steer_refused_for_not_subscribed(self, acp_session):
        """``steer`` when the socket is not subscribed to the session returns
        ``not_subscribed``."""
        acp_mod, sid = acp_session
        acp_mod._supervisor.inflight.add(sid)
        # conn is not attached to sid
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, sid, {"message": "x"}))
            outbound = _queued(conn)
            assert outbound[0]["payload"]["code"] == "not_subscribed"
        finally:
            acp_mod._supervisor.inflight.discard(sid)
            acp_mod._registry.connections.discard(conn)

    def test_steer_refused_for_close_in_progress(self, acp_session):
        """``steer`` when the session is in ``closing`` returns
        ``close_in_progress``."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        acp_mod._supervisor.closing.add(sid)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, sid, {"message": "x"}))
            outbound = _queued(conn)
            assert outbound[0]["payload"]["code"] == "close_in_progress"
        finally:
            acp_mod._supervisor.inflight.discard(sid)
            acp_mod._supervisor.closing.discard(sid)

    def test_steer_refused_for_empty_payload(self, acp_session):
        """``steer`` with an empty string ``""`` returns ``bad_payload``."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, sid, {"message": ""}))
            outbound = _queued(conn)
            assert outbound[0]["payload"]["code"] == "bad_payload"
        finally:
            acp_mod._supervisor.inflight.discard(sid)

    def test_steer_refused_for_whitespace_only_payload(self, acp_session):
        """``steer`` with a whitespace-only string returns ``bad_payload``."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, sid, {"message": "  "}))
            outbound = _queued(conn)
            assert outbound[0]["payload"]["code"] == "bad_payload"
        finally:
            acp_mod._supervisor.inflight.discard(sid)

    def test_steer_refused_for_non_string_payload(self, acp_session):
        """``steer`` with a non-string ``message`` value (e.g. an int) returns an
        error frame rather than raising AttributeError."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        _queued(conn)
        try:
            asyncio.run(acp_mod._handle_steer(conn, sid, {"message": 123}))
            outbound = _queued(conn)
            assert outbound, "Expected an error frame, got nothing"
            assert outbound[0]["type"] == "error"
            assert outbound[0]["payload"]["code"] == "bad_payload"
        finally:
            acp_mod._supervisor.inflight.discard(sid)


class TestAcpCancelCascade:
    """After a parent cancel, crew entries that were still running are marked
    done=True locally and a ``subagents`` frame is broadcast — because kiro-cli
    never emits terminal subagent status after a parent cancel (probe-verified,
    2026-08-12: 11 post-cancel list_update frames, all children still working).
    """

    def _conn(self, acp_mod, sid):
        conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(conn)
        acp_mod._registry.attach(conn, sid)
        return conn

    def _crew_entry(self, order=0):
        return {
            "role": "worker",
            "task": "do something",
            "status": "working",
            "action": "",
            "done": False,
            "error": "",
            "order": order,
            "startedAt": time.time() - 5.0,
            "stoppedAt": None,
        }

    def test_cancel_marks_crew_done_and_emits_subagents(self, acp_session):
        """After a successful cancel, all non-done crew entries have
        ``done=True``, ``stoppedAt`` set, and a ``subagents`` broadcast was
        sent to the session's subscribers."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)

        # Seed two active crew entries
        acp_mod._supervisor.crews[sid] = {
            "child-a": self._crew_entry(0),
            "child-b": self._crew_entry(1),
        }

        # Subscriber that will receive the broadcast
        sub_conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(sub_conn)
        acp_mod._registry.attach(sub_conn, sid)
        _queued(sub_conn)

        before = time.time()
        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))
        after = time.time()

        crew = acp_mod._supervisor.crews[sid]
        assert crew["child-a"]["done"] is True
        assert crew["child-b"]["done"] is True
        assert before <= crew["child-a"]["stoppedAt"] <= after
        assert before <= crew["child-b"]["stoppedAt"] <= after

        # A subagents frame must have been broadcast
        broadcast = _queued(sub_conn)
        assert any(f["type"] == "subagents" for f in broadcast), \
            f"Expected a subagents frame in broadcast: {broadcast}"

    def test_cancel_preserves_already_set_stoppedAt(self, acp_session):
        """An undone entry that already has ``stoppedAt`` set (e.g. from a
        list_update that raced the cancel command) must keep the existing
        precise timestamp after the cascade runs — ``if not entry.get(stoppedAt)``
        must not overwrite it with a fresh ``time.time()``."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)

        precise_ts = time.time() - 1.5
        entry = self._crew_entry(0)
        # done=False so the cascade loop ENTERS the entry, but stoppedAt is
        # already set — the guard must preserve it rather than overwriting it.
        entry["done"] = False
        entry["stoppedAt"] = precise_ts
        acp_mod._supervisor.crews[sid] = {"child-a": entry}

        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))

        result_entry = acp_mod._supervisor.crews[sid]["child-a"]
        assert result_entry["done"] is True
        assert result_entry["stoppedAt"] == precise_ts, (
            f"stoppedAt was overwritten: expected {precise_ts!r}, "
            f"got {result_entry['stoppedAt']!r}"
        )

    def test_cancel_skips_cascade_when_no_crew(self, acp_session):
        """No crew for the session: cascade is a no-op — no exception raised."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)
        # crews has no entry for sid

        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))  # must not raise

    def test_cancel_cascade_skipped_on_agent_error(self, acp_session):
        """When ``_supervisor.cancel`` raises an ``AcpError`` subclass (e.g.
        ``AgentRejected``), the cancel cascade must NOT run — the crew entry
        stays ``done=False`` and no ``subagents`` frame is broadcast."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)

        # Seed an active crew entry
        acp_mod._supervisor.crews[sid] = {
            "child-a": self._crew_entry(0),
        }

        # Subscriber that would receive any broadcast
        sub_conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(sub_conn)
        acp_mod._registry.attach(sub_conn, sid)
        _queued(sub_conn)

        async def cancel_raises(self_, session_id):
            raise acp_mod.AgentRejected("nope")

        with patch.object(acp_mod._Supervisor, "cancel", cancel_raises), \
                patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))

        # Cascade must not have run: crew entry still undone
        assert acp_mod._supervisor.crews[sid]["child-a"]["done"] is False
        # No subagents frame broadcast
        broadcast = _queued(sub_conn)
        assert not any(f["type"] == "subagents" for f in broadcast), \
            f"Unexpected subagents frame after agent error: {broadcast}"

    def test_cancel_skips_cascade_when_crew_is_empty_dict(self, acp_session):
        """An empty dict crew (``crews[sid] = {}``) is a valid state — the
        cascade loop has nothing to iterate, no ``subagents`` frame is
        broadcast, and cancel proceeds normally."""
        acp_mod, sid = acp_session
        conn = self._conn(acp_mod, sid)
        acp_mod._supervisor.inflight.add(sid)

        # Seed explicitly empty crew dict (not absent, not None)
        acp_mod._supervisor.crews[sid] = {}

        # Subscriber that would receive any broadcast
        sub_conn = acp_mod._Connection(_SinkWs())
        acp_mod._registry.connections.add(sub_conn)
        acp_mod._registry.attach(sub_conn, sid)
        _queued(sub_conn)

        with patch.object(acp_mod._Supervisor, "_write", _sent(acp_mod, [])), \
                patch.object(acp_mod._Supervisor, "alive", lambda self: True):
            asyncio.run(acp_mod._handle_cancel(conn, sid))  # must not raise

        # No subagents frame for an empty crew
        broadcast = _queued(sub_conn)
        assert not any(f["type"] == "subagents" for f in broadcast), \
            f"Unexpected subagents frame for empty crew: {broadcast}"


class TestAcpStoppedAt:
    """``stoppedAt`` is stamped on a crew entry when it first transitions to
    done, and is included in the ``subagents`` wire payload."""

    def _seed(self, acp_mod, sid="stopped-01"):
        acp_mod._supervisor.sessions[sid] = acp_mod._new_session_record(r"C:\scratch")
        acp_mod._supervisor.history[sid] = acp_mod._History()
        acp_mod._supervisor.inflight.add(sid)
        return sid

    def test_stoppedAt_set_on_list_update_terminal(self, acp_store):
        """``_on_subagent_list`` sets ``stoppedAt`` when an entry transitions
        to done for the first time."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        before = time.time()
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-ts-01", "role": "worker",
             "status": {"type": "terminated"}},
        ]})
        after = time.time()

        entry = acp_mod._supervisor.crews[sid]["sub-ts-01"]
        assert entry["done"] is True
        assert entry["stoppedAt"] is not None
        assert before <= entry["stoppedAt"] <= after

    def test_stoppedAt_not_set_for_active_entry(self, acp_store):
        """A still-working entry must have ``stoppedAt`` as ``None``."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-ts-02", "role": "worker",
             "status": {"type": "working"}},
        ]})
        entry = acp_mod._supervisor.crews[sid]["sub-ts-02"]
        assert entry["done"] is False
        assert entry["stoppedAt"] is None

    def test_stoppedAt_preserved_on_subsequent_list_update(self, acp_store):
        """A stoppedAt already on a done entry must not be overwritten by a
        later (stale/reordered) list_update — terminal is sticky."""
        acp_mod, _ = acp_store
        sid = self._seed(acp_mod)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-ts-03", "role": "worker",
             "status": {"type": "terminated"}},
        ]})
        original_ts = acp_mod._supervisor.crews[sid]["sub-ts-03"]["stoppedAt"]
        assert original_ts is not None

        # A repeated update for the same child — must be ignored (terminal sticky)
        time.sleep(0.01)
        _notify(acp_mod, acp_mod.SUBAGENT_LIST_METHOD, {"subagents": [
            {"sessionId": "sub-ts-03", "role": "worker",
             "status": {"type": "terminated"}},
        ]})
        assert acp_mod._supervisor.crews[sid]["sub-ts-03"]["stoppedAt"] == original_ts

    def test_stoppedAt_in_subagents_payload(self):
        """``_subagents_payload`` includes the ``stoppedAt`` field in each
        entry dict."""
        from power_atlas import acp as acp_mod

        ts = 1_700_000_000.0
        crew = {
            "sub-pay-01": {
                "role": "worker", "task": "x", "status": "terminated",
                "action": "", "done": True, "error": "",
                "order": 0, "startedAt": ts - 10.0, "stoppedAt": ts,
            },
            "sub-pay-02": {
                "role": "helper", "task": "y", "status": "working",
                "action": "", "done": False, "error": "",
                "order": 1, "startedAt": ts - 3.0, "stoppedAt": None,
            },
        }
        payload = acp_mod._subagents_payload(crew)
        by_id = {e["sessionId"]: e for e in payload}

        assert "stoppedAt" in by_id["sub-pay-01"]
        assert by_id["sub-pay-01"]["stoppedAt"] == ts

        assert "stoppedAt" in by_id["sub-pay-02"]
        assert by_id["sub-pay-02"]["stoppedAt"] is None
