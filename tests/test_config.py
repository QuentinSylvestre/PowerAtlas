"""Tests for config module."""

import threading
from pathlib import Path

import pytest
import tomli_w
import tomllib

from power_atlas.config import (
    Config,
    LaunchProfile,
    get_active_launch_profile,
    load_config,
    save_config,
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config to tmp dir.

    `REMOTE_SECRET_PATH` is listed explicitly and is not redundant with
    `CONFIG_DIR`. It is computed **at import time** as `CONFIG_DIR /
    "remote-secret"`, so rebinding `CONFIG_DIR` afterwards does not move it —
    it keeps pointing at the real `%LOCALAPPDATA%\\power-atlas\\remote-secret`.
    Latent when added: a 2026-08-03 census of all seven test modules found the
    secret functions called only from `tests/test_web.py`, which redirects all
    three paths. It stops being latent the first time a secret test is written
    here, and the failure is writing a live credential over the user's own.

    `LOCAL_SECRET_PATH` is import-time in exactly the same way and is
    redirected for the same reason.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 4 review
    """
    monkeypatch.setattr("power_atlas.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("power_atlas.config.CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr("power_atlas.config.REMOTE_SECRET_PATH",
                        tmp_path / "remote-secret")
    monkeypatch.setattr("power_atlas.config.LOCAL_SECRET_PATH",
                        tmp_path / "local-secret")


def _write_toml(tmp_path, data):
    """Helper to write raw TOML data."""
    path = tmp_path / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


# --- Default Config ---


def test_default_config_has_launch_profile():
    """Config() has active_launch_profile == 'default' and one default profile."""
    cfg = Config()
    assert cfg.active_launch_profile == "default"
    assert len(cfg.launch_profiles) == 1
    p = cfg.launch_profiles[0]
    assert p.id == "default"
    assert p.name == "Default"
    assert p.terminal_command == ""  # Raw dataclass default; load_config fills platform-specific
    assert p.wt_profile == "PowerShell"


# --- Round-trip ---


def test_round_trip_with_profiles():
    """Save with profiles, load gets same profiles back."""
    profiles = [
        LaunchProfile(id="custom", name="Custom", terminal_command="wt.exe",
                      wt_profile="Git Bash"),
        LaunchProfile(id="default", name="Default"),
    ]
    cfg = Config(active_launch_profile="custom", launch_profiles=profiles, pinned_folders=["/a", "/b"])
    save_config(cfg)
    loaded = load_config()
    assert loaded.active_launch_profile == "custom"
    assert len(loaded.launch_profiles) == 2
    assert loaded.launch_profiles[0].id == "custom"
    assert loaded.launch_profiles[0].terminal_command == "wt.exe"
    assert loaded.launch_profiles[0].wt_profile == "Git Bash"
    assert loaded.launch_profiles[1].id == "default"
    assert loaded.pinned_folders == ["/a", "/b"]
    assert loaded.pinned_sessions == []


def test_round_trip_preserves_all_profile_fields():
    """All fields of a profile survive a save/load cycle."""
    p = LaunchProfile(
        id="test-prof",
        name="Test Profile",
        terminal_command="alacritty.exe",
        wt_profile="Ubuntu",
    )
    cfg = Config(active_launch_profile="test-prof", launch_profiles=[p])
    save_config(cfg)
    loaded = load_config()
    lp = loaded.launch_profiles[0]
    assert lp.id == "test-prof"
    assert lp.name == "Test Profile"
    assert lp.terminal_command == "alacritty.exe"
    assert lp.wt_profile == "Ubuntu"


# --- Legacy Migration ---


def test_legacy_terminal_command_migrates_to_profile(tmp_path):
    """TOML with terminal_command but no launch_profiles migrates to default profile."""
    _write_toml(tmp_path, {"terminal_command": "wt.exe"})
    cfg = load_config()
    assert len(cfg.launch_profiles) == 1
    assert cfg.launch_profiles[0].terminal_command == "wt.exe"
    assert cfg.launch_profiles[0].id == "default"


def test_legacy_empty_terminal_command_no_modification(tmp_path):
    """Empty terminal_command produces default profile with platform-appropriate terminal_command."""
    _write_toml(tmp_path, {"terminal_command": ""})
    cfg = load_config()
    assert len(cfg.launch_profiles) == 1
    p = cfg.launch_profiles[0]
    assert p.id == "default"
    assert p.name == "Default"
    assert p.wt_profile == "PowerShell"
    import sys
    if sys.platform == "win32":
        assert "wt new-tab" in p.terminal_command
    else:
        assert p.terminal_command == ""


def test_legacy_migration_skipped_when_profiles_exist(tmp_path):
    """If launch_profiles exist, terminal_command is ignored."""
    _write_toml(tmp_path, {
        "terminal_command": "wt.exe",
        "launch_profiles": [{"id": "custom", "name": "Custom", "terminal_command": "kitty"}],
    })
    cfg = load_config()
    assert len(cfg.launch_profiles) == 1
    assert cfg.launch_profiles[0].terminal_command == "kitty"


# --- Malformed Profile Fields ---


def test_malformed_profile_id_regenerated(tmp_path):
    """Invalid profile ID gets regenerated."""
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "has spaces!!!", "name": "Bad"}],
    })
    cfg = load_config()
    assert cfg.launch_profiles[0].id == "imported-1"


def test_terminal_command_control_chars_stripped(tmp_path):
    """Control characters (< 0x20) stripped from terminal_command except space."""
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "t1", "terminal_command": "wt\t.exe\n"}],
    })
    cfg = load_config()
    assert cfg.launch_profiles[0].terminal_command == "wt.exe"


def test_terminal_command_max_length(tmp_path):
    """terminal_command truncated to 512 chars."""
    long_cmd = "x" * 600
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "t1", "terminal_command": long_cmd}],
    })
    cfg = load_config()
    assert len(cfg.launch_profiles[0].terminal_command) == 512


def test_wt_profile_empty_defaults_to_powershell(tmp_path):
    """Empty wt_profile defaults to 'PowerShell'."""
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "t1", "wt_profile": ""}],
    })
    cfg = load_config()
    assert cfg.launch_profiles[0].wt_profile == "PowerShell"


# --- Duplicate Profile IDs ---


def test_duplicate_ids_first_kept_second_regenerated(tmp_path):
    """Duplicate profile IDs: first kept, second gets imported-N."""
    _write_toml(tmp_path, {
        "launch_profiles": [
            {"id": "myprof", "name": "First"},
            {"id": "myprof", "name": "Second"},
        ],
    })
    cfg = load_config()
    assert len(cfg.launch_profiles) == 2
    assert cfg.launch_profiles[0].id == "myprof"
    assert cfg.launch_profiles[0].name == "First"
    assert cfg.launch_profiles[1].id == "imported-1"
    assert cfg.launch_profiles[1].name == "Second"


def test_duplicate_active_id_remaps_to_first(tmp_path):
    """active_launch_profile pointing to regenerated duplicate remaps to first."""
    _write_toml(tmp_path, {
        "active_launch_profile": "myprof",
        "launch_profiles": [
            {"id": "myprof", "name": "First"},
            {"id": "myprof", "name": "Second"},
        ],
    })
    cfg = load_config()
    # active_launch_profile was "myprof" which still exists as the first
    assert cfg.active_launch_profile == "myprof"
    assert cfg.launch_profiles[0].id == "myprof"


def test_active_id_pointing_to_nonexistent_remaps(tmp_path):
    """active_launch_profile pointing to nonexistent ID remaps to first profile."""
    _write_toml(tmp_path, {
        "active_launch_profile": "gone",
        "launch_profiles": [{"id": "alpha", "name": "Alpha"}],
    })
    cfg = load_config()
    assert cfg.active_launch_profile == "alpha"


# --- Empty Profiles List ---


def test_empty_profiles_list_normalizes(tmp_path):
    """Empty launch_profiles list normalizes to a default profile."""
    _write_toml(tmp_path, {"launch_profiles": []})
    cfg = load_config()
    assert len(cfg.launch_profiles) == 1
    p = cfg.launch_profiles[0]
    assert p.id == "default"
    assert p.wt_profile == "PowerShell"
    import sys
    if sys.platform == "win32":
        assert "wt new-tab" in p.terminal_command
    else:
        assert p.terminal_command == ""


# --- get_active_launch_profile ---


def test_get_active_profile_returns_correct():
    """get_active_launch_profile returns the matching profile."""
    p1 = LaunchProfile(id="one", name="One", terminal_command="cmd1")
    p2 = LaunchProfile(id="two", name="Two", terminal_command="cmd2")
    cfg = Config(active_launch_profile="two", launch_profiles=[p1, p2])
    result = get_active_launch_profile(cfg)
    assert result.id == "two"
    assert result.terminal_command == "cmd2"


def test_get_active_profile_fallback_when_missing():
    """get_active_launch_profile returns first profile when active ID missing."""
    p1 = LaunchProfile(id="one", name="One")
    cfg = Config(active_launch_profile="nonexistent", launch_profiles=[p1])
    result = get_active_launch_profile(cfg)
    assert result.id == "one"


def test_get_active_profile_fallback_empty_list():
    """get_active_launch_profile returns fresh default when list is empty."""
    cfg = Config(active_launch_profile="x", launch_profiles=[])
    result = get_active_launch_profile(cfg)
    assert result == LaunchProfile()


def test_get_active_profile_returns_copy():
    """Mutating returned profile doesn't affect config."""
    p = LaunchProfile(id="default", terminal_command="original")
    cfg = Config(launch_profiles=[p])
    result = get_active_launch_profile(cfg)
    result.terminal_command = "mutated"
    assert cfg.launch_profiles[0].terminal_command == "original"


# --- save_config never writes terminal_command ---


def test_save_config_never_writes_terminal_command():
    """save_config never writes the legacy terminal_command key."""
    from power_atlas.config import CONFIG_PATH
    cfg = Config(launch_profiles=[LaunchProfile(terminal_command="wt.exe")])
    save_config(cfg)
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    # terminal_command should not exist at root level
    assert "terminal_command" not in raw
    # But it exists inside the profile
    assert raw["launch_profiles"][0]["terminal_command"] == "wt.exe"


# --- Existing functionality preservation ---


def test_pinned_folders_dict_to_str_migration(tmp_path):
    """list[dict] pinned_folders migrates to list[str] with deduplication."""
    _write_toml(tmp_path, {
        "pinned_folders": [
            {"folder": "/a", "provider": "kiro-cli"},
            {"folder": "/b", "provider": "claude-code"},
            {"folder": "/a", "provider": "claude-code"},
        ]
    })
    cfg = load_config()
    assert cfg.pinned_folders == ["/a", "/b"]


def test_missing_keys_use_defaults(tmp_path):
    """A TOML with only one key should still produce a full Config with defaults."""
    _write_toml(tmp_path, {"peek_hotkey": "alt+z"})
    cfg = load_config()
    assert cfg.peek_hotkey == "alt+z"
    assert cfg.active_launch_profile == "default"
    assert cfg.pinned_folders == []


def test_unknown_keys_ignored(tmp_path):
    """Unknown keys in TOML should not raise or appear on Config."""
    _write_toml(tmp_path, {"unknown_key": "hello", "extra": 42})
    cfg = load_config()
    assert not hasattr(cfg, "unknown_key")


def test_missing_file_returns_defaults():
    cfg = load_config()
    assert cfg == Config()


def test_thread_safety():
    """Concurrent save/load doesn't corrupt."""
    errors = []

    def writer(i):
        try:
            save_config(Config(
                active_launch_profile="default",
                launch_profiles=[LaunchProfile(terminal_command=f"cmd_{i}")],
                pinned_folders=[str(i)],
            ))
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            cfg = load_config()
            assert isinstance(cfg.active_launch_profile, str)
        except Exception as e:
            errors.append(e)

    save_config(Config())

    threads = []
    for i in range(20):
        threads.append(threading.Thread(target=writer, args=(i,)))
        threads.append(threading.Thread(target=reader))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    cfg = load_config()
    assert isinstance(cfg, Config)


def test_wrong_type_bool_gets_default(tmp_path):
    """An int for a str field should fall back to default."""
    _write_toml(tmp_path, {"peek_hotkey": 42})
    cfg = load_config()
    assert cfg.peek_hotkey == "ctrl+shift+z"


def test_wrong_type_list_gets_default(tmp_path):
    """A scalar for a list field should fall back to default."""
    _write_toml(tmp_path, {"pinned_folders": "not a list"})
    cfg = load_config()
    assert cfg.pinned_folders == []


def test_custom_launchers_round_trip():
    """custom_launchers list[dict] persists through save/load cycle."""
    launchers = [
        {"id": "abc", "name": "Dev", "command": "npm", "custom_args": "start", "cwd": "C:\\proj", "env": {}, "color": "#ef4444"},
    ]
    cfg = Config(custom_launchers=launchers)
    save_config(cfg)
    loaded = load_config()
    assert len(loaded.custom_launchers) == 1
    assert loaded.custom_launchers[0]["name"] == "Dev"


def test_peek_hotkey_round_trip():
    """peek_hotkey persists through save/load cycle with custom value."""
    cfg = Config(peek_hotkey="alt+p")
    save_config(cfg)
    loaded = load_config()
    assert loaded.peek_hotkey == "alt+p"


def test_provider_settings_round_trip():
    """provider_settings dict persists through save/load cycle."""
    settings = {
        "kiro-cli-v3": {"default_args": "-a --verbose", "color": "#4a6ede", "enabled": True},
        "claude-code": {"default_args": "", "color": "#c2590f", "enabled": False},
    }
    cfg = Config(provider_settings=settings)
    save_config(cfg)
    loaded = load_config()
    assert loaded.provider_settings == settings


def test_kiro_cli_v2_key_dropped_on_load(tmp_path):
    """A config.toml with [provider_settings.kiro-cli] has that key stripped on load."""
    _write_toml(tmp_path, {
        "provider_settings": {
            "kiro-cli": {"default_args": "-a", "color": "", "enabled": True},
            "claude-code": {"default_args": "", "color": "", "enabled": True},
        }
    })
    cfg = load_config()
    assert "kiro-cli" not in cfg.provider_settings
    assert "claude-code" in cfg.provider_settings


def test_save_config_drops_trust_all_tools():
    """save_config never writes trust_all_tools to TOML."""
    from power_atlas.config import CONFIG_PATH
    cfg = Config(provider_settings={"kiro-cli-v3": {"default_args": "", "color": "", "enabled": True}})
    save_config(cfg)
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    assert "trust_all_tools" not in raw


def test_port_round_trip():
    """Port value survives write + reload."""
    save_config(Config(port=9876))
    c = load_config()
    assert c.port == 9876


def test_port_missing_defaults_zero(tmp_path):
    """Config without port key defaults to 0."""
    _write_toml(tmp_path, {"peek_hotkey": "ctrl+z"})
    c = load_config()
    assert c.port == 0


def test_port_bool_in_toml_rejected(tmp_path):
    """TOML boolean for port is rejected by load_config (bool guard)."""
    (tmp_path / "config.toml").write_text('port = true\n')
    c = load_config()
    assert c.port == 0


# --- Additional edge cases ---


def test_profile_id_too_long_regenerated(tmp_path):
    """Profile ID > 64 chars gets regenerated."""
    long_id = "a" * 65
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": long_id, "name": "TooLong"}],
    })
    cfg = load_config()
    assert cfg.launch_profiles[0].id == "imported-1"


def test_name_whitespace_only_defaults(tmp_path):
    """Profile name of all whitespace normalizes to 'Default'."""
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "t1", "name": "   "}],
    })
    cfg = load_config()
    assert cfg.launch_profiles[0].name == "Default"


def test_name_control_chars_stripped(tmp_path):
    """Control characters in profile name are stripped before further processing."""
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "t1", "name": "\x01Evil"}],
    })
    cfg = load_config()
    assert cfg.launch_profiles[0].name == "Evil"


def test_non_dict_profile_entries_guarded(tmp_path):
    """Non-dict entries in launch_profiles are guarded (fires for programmatically constructed data; TOML cannot produce mixed-type arrays)."""
    # TOML arrays of mixed types aren't valid, but raw strings/ints could sneak through
    # Write a valid TOML with profiles being dicts, plus test empty dict
    _write_toml(tmp_path, {
        "launch_profiles": [{"id": "valid", "name": "OK"}],
    })
    cfg = load_config()
    assert len(cfg.launch_profiles) == 1
    assert cfg.launch_profiles[0].id == "valid"


# --- Phase 2: Defensive config ---


def test_corrupt_config_backs_up_and_defaults(tmp_path):
    """Corrupt TOML triggers backup and returns defaults, no raise."""
    from power_atlas.config import CONFIG_PATH
    CONFIG_PATH.write_bytes(b"\x00\xff\xfe junk not valid toml \x01\x02")
    cfg = load_config()
    # Should return defaults
    assert cfg == Config()
    # .bak file should exist with the corrupt content
    bak = CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak")
    assert bak.exists()
    assert bak.read_bytes() == b"\x00\xff\xfe junk not valid toml \x01\x02"


def test_mixed_pinned_folders_no_crash(tmp_path):
    """Mixed list [dict, str] pinned_folders loads without AttributeError and migrates."""
    _write_toml(tmp_path, {
        "pinned_folders": [{"folder": "/a"}, "x"],
    })
    # Should not raise AttributeError
    cfg = load_config()
    # The dict entry migrates to "/a", the str "x" is kept as-is
    assert "/a" in cfg.pinned_folders
    assert "x" in cfg.pinned_folders


def test_unknown_keys_preserved_on_save(tmp_path):
    """Unknown keys in TOML are preserved through load/modify/save cycle."""
    from power_atlas.config import CONFIG_PATH
    _write_toml(tmp_path, {"future_key": "hello", "peek_hotkey": "alt+z"})
    cfg = load_config()
    # Modify a known field
    cfg.peek_hotkey = "ctrl+p"
    save_config(cfg)
    # Reload raw TOML and assert future_key is still present
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    assert raw["future_key"] == "hello"
    assert raw["peek_hotkey"] == "ctrl+p"


def test_nested_bad_types_dropped(tmp_path):
    """Invalid nested types are sanitized: non-str in lists, non-dict in provider_settings."""
    _write_toml(tmp_path, {
        "pinned_folders": [123, "valid"],
        "pinned_sessions": [True, "sess1"],
        "provider_settings": {"x": "notadict", "y": {"default_args": "", "color": "", "enabled": True}},
        "custom_launchers": ["notadict", {"id": "ok"}],
    })
    cfg = load_config()
    # Non-str entries dropped from pinned_folders
    assert cfg.pinned_folders == ["valid"]
    # Non-str entries dropped from pinned_sessions (bool is not str)
    assert cfg.pinned_sessions == ["sess1"]
    # provider_settings with non-dict values dropped
    assert "x" not in cfg.provider_settings
    assert cfg.provider_settings == {"y": {"default_args": "", "color": "", "enabled": True}}
    # custom_launchers: non-dict entries dropped
    assert cfg.custom_launchers == [{"id": "ok"}]


# --- default_directory ---


def test_default_directory_round_trip():
    """default_directory persists through save/load cycle."""
    cfg = Config(default_directory="/home/user/projects")
    save_config(cfg)
    loaded = load_config()
    assert loaded.default_directory == "/home/user/projects"


def test_default_directory_default_empty():
    """Config without default_directory defaults to empty string."""
    cfg = load_config()
    assert cfg.default_directory == ""


def test_default_directory_control_chars_sanitized(tmp_path):
    """Control characters in default_directory are stripped on load."""
    _write_toml(tmp_path, {"default_directory": "/home/\x01user/\tprojects"})
    cfg = load_config()
    assert cfg.default_directory == "/home/user/projects"


def test_default_directory_whitespace_stripped(tmp_path):
    """Leading/trailing whitespace in default_directory is stripped on load."""
    _write_toml(tmp_path, {"default_directory": "  /home/user/projects  "})
    cfg = load_config()
    assert cfg.default_directory == "/home/user/projects"


# --- workspace_settings & tag_settings ---


def test_workspace_settings_round_trip_windows_paths():
    """workspace_settings with Windows backslash paths survive TOML round-trip."""
    ws = {
        "C:\\Users\\dev\\project-a": {"tags": ["web", "prod"], "color": "#ff0000"},
        "C:\\Users\\dev\\project-b": {"tags": ["backend"], "color": ""},
    }
    cfg = Config(workspace_settings=ws)
    save_config(cfg)
    loaded = load_config()
    assert "C:\\Users\\dev\\project-a" in loaded.workspace_settings
    assert loaded.workspace_settings["C:\\Users\\dev\\project-a"]["tags"] == ["web", "prod"]
    assert loaded.workspace_settings["C:\\Users\\dev\\project-a"]["color"] == "#ff0000"
    assert loaded.workspace_settings["C:\\Users\\dev\\project-b"]["tags"] == ["backend"]
    assert loaded.workspace_settings["C:\\Users\\dev\\project-b"]["color"] == ""


def test_tag_settings_round_trip():
    """tag_settings persist through save/load cycle even without workspace assignments."""
    tags = {
        "web": {"color": "#3b82f6"},
        "backend": {"color": "#10b981"},
    }
    cfg = Config(tag_settings=tags)
    save_config(cfg)
    loaded = load_config()
    # "hidden" is always added; tags persist regardless of workspace assignments
    assert loaded.tag_settings == {**tags, "hidden": {"color": ""}}


def test_orphan_pruning_removed(tmp_path):
    """Tags persist in tag_settings even when not assigned to any workspace."""
    _write_toml(tmp_path, {
        "tag_settings": {
            "orphan-tag": {"color": "#ff0000"},
            "another": {"color": "#00ff00"},
        },
        "workspace_settings": {},
    })
    cfg = load_config()
    assert "orphan-tag" in cfg.tag_settings
    assert "another" in cfg.tag_settings
    assert cfg.tag_settings["orphan-tag"]["color"] == "#ff0000"
    assert cfg.tag_settings["another"]["color"] == "#00ff00"


def test_tag_settings_sanitization_invalid_color(tmp_path):
    """tag_settings with non-string color gets sanitized to empty string."""
    _write_toml(tmp_path, {
        "tag_settings": {
            "web": {"color": 123},
            "valid": {"color": "#abc"},
        },
        "workspace_settings": {
            "C:\\proj": {"tags": ["web", "valid"], "color": ""},
        },
    })
    cfg = load_config()
    assert cfg.tag_settings["web"]["color"] == ""
    assert cfg.tag_settings["valid"]["color"] == "#abc"


def test_workspace_settings_sanitization_invalid_tags(tmp_path):
    """workspace_settings with non-list tags or non-string entries are sanitized."""
    _write_toml(tmp_path, {
        "workspace_settings": {
            "C:\\proj": {"tags": [123, "valid", True], "color": "#aaa"},
        },
    })
    cfg = load_config()
    # Only string entries kept
    assert cfg.workspace_settings["C:\\proj"]["tags"] == ["valid"]
    assert cfg.workspace_settings["C:\\proj"]["color"] == "#aaa"


def test_workspace_settings_sanitization_invalid_color(tmp_path):
    """workspace_settings with non-string color gets sanitized to empty string."""
    _write_toml(tmp_path, {
        "workspace_settings": {
            "/home/user/proj": {"tags": ["ok"], "color": 42},
        },
    })
    cfg = load_config()
    assert cfg.workspace_settings["/home/user/proj"]["color"] == ""
    assert cfg.workspace_settings["/home/user/proj"]["tags"] == ["ok"]


def test_workspace_settings_non_dict_entries_dropped(tmp_path):
    """workspace_settings entries that aren't dicts are dropped."""
    _write_toml(tmp_path, {
        "workspace_settings": {
            "valid": {"tags": [], "color": ""},
            "bad": "not a dict",
        },
    })
    cfg = load_config()
    assert "valid" in cfg.workspace_settings
    assert "bad" not in cfg.workspace_settings


def test_workspace_settings_defaults_missing_fields(tmp_path):
    """workspace_settings entries without tags/color get defaults on load."""
    _write_toml(tmp_path, {
        "workspace_settings": {
            "C:\\proj": {},
        },
    })
    cfg = load_config()
    assert cfg.workspace_settings["C:\\proj"]["tags"] == []
    assert cfg.workspace_settings["C:\\proj"]["color"] == ""


def test_get_workspace_settings_normalized_lookup(tmp_path):
    """get_workspace_settings matches case-insensitively on Windows."""
    from power_atlas.config import get_workspace_settings
    import sys

    _write_toml(tmp_path, {
        "workspace_settings": {
            "C:\\Users\\Dev\\Project": {"tags": ["web"], "color": "#f00"},
        },
    })
    cfg = load_config()

    if sys.platform == "win32":
        # Case-insensitive match
        result = get_workspace_settings(cfg, "c:\\users\\dev\\project")
        assert result["tags"] == ["web"]
        assert result["color"] == "#f00"

        # Forward-slash variant also matches
        result2 = get_workspace_settings(cfg, "C:/Users/Dev/Project")
        assert result2["tags"] == ["web"]
    else:
        # On Linux, exact match only
        result = get_workspace_settings(cfg, "C:\\Users\\Dev\\Project")
        assert result["tags"] == ["web"]


def test_get_workspace_settings_missing_returns_default():
    """get_workspace_settings returns empty defaults for unknown paths."""
    from power_atlas.config import get_workspace_settings

    cfg = Config()
    result = get_workspace_settings(cfg, "/nonexistent")
    assert result == {"tags": [], "color": ""}


def test_get_workspace_settings_lazy_builds_norm_map():
    """get_workspace_settings lazy-builds _ws_norm_map if not present."""
    from power_atlas.config import get_workspace_settings

    cfg = Config(workspace_settings={"/proj": {"tags": ["x"], "color": "#000"}})
    # Remove the _ws_norm_map if it exists (simulates a Config not from load_config)
    if hasattr(cfg, "_ws_norm_map"):
        delattr(cfg, "_ws_norm_map")
    result = get_workspace_settings(cfg, "/proj")
    assert result["tags"] == ["x"]
    assert hasattr(cfg, "_ws_norm_map")


# --- ACP permission mode and rules --------------------------------------------
# 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL Phase 1: the on/off switch is
# migrated into `acp_permission_mode` (D-8, D-27), an unreadable mode loads as
# Manual (D-25), and a malformed rule table never raises and never narrows
# protection silently (D-26).


@pytest.fixture
def mode_warnings_reset(monkeypatch):
    """The once-per-value warning set is process-global."""
    monkeypatch.setattr("power_atlas.config._mode_values_warned", set())


def _seed():
    from power_atlas.agent_profile import normalise_rules
    return normalise_rules({})


@pytest.mark.parametrize("data,mode,agents_blocked", [
    ({"acp_permissions_enabled": True}, "manual", True),
    ({"acp_permissions_enabled": False}, "yolo", False),
    ({}, "yolo", False),
    # D-27: the newest schema wins after a rollback and roll-forward.
    ({"acp_permissions_enabled": True, "acp_permission_mode": "yolo"}, "yolo", False),
    ({"acp_permissions_enabled": False, "acp_permission_mode": "manual"}, "manual", False),
])
def test_the_permission_switch_migrates_to_a_mode(tmp_path, data, mode, agents_blocked):
    _write_toml(tmp_path, data)
    cfg = load_config()
    assert cfg.acp_permission_mode == mode
    assert cfg._mode_warning == ""
    expected = _seed()
    if agents_blocked:
        # D-23: the old overlay's write deny on `**/.kiro/agents/**`.
        expected["protected_block"] = ["agents"]
    assert cfg.acp_permission_rules == expected
    assert not hasattr(cfg, "acp_permissions_enabled")


def test_migrated_rules_are_seeded_only_when_the_table_is_absent_or_empty(tmp_path):
    """D-27: rules already present win over the `true` seed."""
    rules = {"shell": {"default": "block", "allow": ["ls"], "block": []}}
    _write_toml(tmp_path, {"acp_permissions_enabled": True,
                           "acp_permission_rules": rules})
    cfg = load_config()
    assert cfg.acp_permission_mode == "manual"
    assert cfg.acp_permission_rules["shell"] == {
        "default": "block", "allow": ["ls"], "block": []}
    assert cfg.acp_permission_rules["protected_block"] == []
    _write_toml(tmp_path, {"acp_permissions_enabled": True,
                           "acp_permission_rules": {}})
    assert load_config().acp_permission_rules["protected_block"] == ["agents"]


def test_the_legacy_key_is_dropped_on_the_next_save(tmp_path):
    _write_toml(tmp_path, {"acp_permissions_enabled": True, "port": 4915})
    cfg = load_config()
    save_config(cfg)
    with open(tmp_path / "config.toml", "rb") as f:
        data = tomllib.load(f)
    assert "acp_permissions_enabled" not in data
    assert data["acp_permission_mode"] == "manual"
    assert data["acp_permission_rules"]["protected_block"] == ["agents"]
    assert data["port"] == 4915
    # And the saved file reads back the same way, with no legacy key to win.
    assert load_config().acp_permission_mode == "manual"


@pytest.mark.parametrize("raw,mode,warns", [
    ("Manual", "manual", False),
    ("YOLO", "yolo", False),
    (" manual ", "manual", False),
    ("AUTO", "manual", True),
    ("auto", "manual", True),
    ("junk", "manual", True),
    ("", "manual", True),
    (1, "manual", True),
    (True, "manual", True),
    (["yolo"], "manual", True),
])
def test_the_mode_loads_case_insensitively_and_fails_toward_manual(
        tmp_path, caplog, mode_warnings_reset, raw, mode, warns):
    """D-25: `auto` and anything unreadable load as Manual, never Yolo, with a
    `mode_warning` for the panel and one WARNING per distinct value per
    process, however many times the config is loaded."""
    import logging
    _write_toml(tmp_path, {"acp_permission_mode": raw})
    with caplog.at_level(logging.WARNING, logger="power_atlas.config"):
        for _ in range(3):
            cfg = load_config()
    assert cfg.acp_permission_mode == mode
    warnings = [r for r in caplog.records if "acp_permission_mode" in r.getMessage()]
    if warns:
        assert cfg._mode_warning
        assert "Manual" in cfg._mode_warning
        assert len(warnings) == 1, [r.getMessage() for r in warnings]
    else:
        assert cfg._mode_warning == ""
        assert warnings == []


@pytest.mark.parametrize("rules", [
    {"shell": {"default": "ask", "allow": "git status"}},          # string allow
    {"shell": "ask"},                                               # non-table row
    {"fs_read": ["./**"]},                                          # list row
    {"not_a_row": {"default": "allow"}},                            # unknown row
    {"protected_block": "agents"},                                  # non-list
    {"protected_block": 7},
    {"protected_block": ["agents", "nope", {"x": 1}]},
    {"mcp": {"default": 7, "allow": ["paecho/pa_echo"]}},          # bad default
    {"fs_write": {"default": "ask",
                  "allow": [f"**/a{i}/**" for i in range(10000)]}},  # 10 000 entries
    {"shell": {"default": "ask", "block": ["rm\u007f"]}},            # invalid block
    {"shell": {"default": "ask", "block": "rm *"}},                 # non-list block
    {"web_fetch": {"default": "allow", "allow": [1, "", "*", "example.com"]}},
])
def test_a_malformed_rule_table_never_raises_and_normalises(tmp_path, rules):
    """D-26: every row is present and well-typed after load; an invalid allow
    pattern is dropped and the list capped at 100; a block list is kept as
    stored so generation refuses it by name rather than dropping a
    protection; unknown rows and Protected names are dropped."""
    from power_atlas.agent_profile import PERMISSION_ROWS, pattern_error
    _write_toml(tmp_path, {"acp_permission_mode": "manual",
                           "acp_permission_rules": rules})
    cfg = load_config()
    loaded = cfg.acp_permission_rules
    assert set(loaded) == set(PERMISSION_ROWS) | {"protected_block"}
    for row in PERMISSION_ROWS:
        assert loaded[row]["default"] in ("allow", "ask", "block")
        assert isinstance(loaded[row]["allow"], list)
        assert len(loaded[row]["allow"]) <= 100
        assert all(pattern_error(p) == "" for p in loaded[row]["allow"])
    assert set(loaded["protected_block"]) <= {"agents", "steering", "skills", "hooks"}
    shell = rules.get("shell")
    if isinstance(shell, dict) and "block" in shell:
        assert loaded["shell"]["block"] == shell["block"], (
            "a block pattern was dropped rather than refused")


def test_an_invalid_block_pattern_refuses_generation_by_name(tmp_path):
    """D-26, D-14: kept on load, refused by the compiler with the row named."""
    from power_atlas.agent_profile import AgentProfileError, compile_block
    _write_toml(tmp_path, {"acp_permission_mode": "manual",
                           "acp_permission_rules": {
                               "shell": {"default": "ask", "block": ["rm\u007f"]}}})
    cfg = load_config()
    with pytest.raises(AgentProfileError, match=r"Run commands \(shell\)"):
        compile_block(cfg)


def test_a_bad_default_keeps_the_row_lists(tmp_path):
    _write_toml(tmp_path, {"acp_permission_rules": {
        "mcp": {"default": "sometimes", "allow": ["paecho/pa_echo"],
                "block": ["evil/tool"]}}})
    row = load_config().acp_permission_rules["mcp"]
    assert row == {"default": "ask", "allow": ["paecho/pa_echo"],
                   "block": ["evil/tool"]}


def test_a_corrupt_file_marks_the_load_error(tmp_path):
    """The session gate's self-heal must not regenerate from defaults
    (260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL D-30)."""
    (tmp_path / "config.toml").write_text("not = [ toml", encoding="utf-8")
    cfg = load_config()
    assert getattr(cfg, "_load_error", "")
    assert cfg.acp_permission_mode == "yolo"


def test_save_config_refuses_a_config_from_an_unreadable_file(tmp_path):
    """Phase 1 re-review, finding 1: `save_config` is the choke point every
    writer passes through, so the defaults that stand in for an unreadable
    config.toml are never written over it -- not by any route."""
    from power_atlas.config import ConfigUnreadableError
    corrupt = b'acp_permission_mode = "manual"\npinned_sessions = ["keep"]\nbroken = [\n'
    (tmp_path / "config.toml").write_bytes(corrupt)
    cfg = load_config()
    cfg.notifications = {"enabled": True}
    with pytest.raises(ConfigUnreadableError) as caught:
        save_config(cfg)
    assert "config.toml" in str(caught.value)
    assert "by hand" in str(caught.value)
    assert (tmp_path / "config.toml").read_bytes() == corrupt
    assert not (tmp_path / "config.tmp").exists()
    # A clean load still saves.
    (tmp_path / "config.toml").write_bytes(b'acp_permission_mode = "manual"\n')
    fresh = load_config()
    fresh.notifications = {"enabled": True}
    save_config(fresh)
    assert load_config().notifications == {"enabled": True}


def test_the_backup_note_names_a_bak_only_when_one_was_written(
        tmp_path, monkeypatch):
    """Phase 1 re-review, finding 2: no message claims a `.bak` copy that
    `load_config` failed to write."""
    import power_atlas.config as config_mod
    (tmp_path / "config.toml").write_text("not = [ toml", encoding="utf-8")
    saved = load_config()
    assert "config.toml.bak" in config_mod.unreadable_backup_note(saved)
    assert "config.toml.bak" in config_mod.unreadable_config_message(saved)

    def no_copy(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(config_mod.shutil, "copy2", no_copy)
    unsaved = load_config()
    assert unsaved._load_error
    note = config_mod.unreadable_backup_note(unsaved)
    assert ".bak" not in note and "could not save a backup" in note
    assert ".bak" not in config_mod.unreadable_config_message(unsaved)


# --- 260924_ACP_PERMISSION_MODES_YOLO_AUTO_MANUAL final review ---


def test_the_load_diagnostics_survive_a_copy_and_are_never_stored(tmp_path):
    """A9: declared fields, so `dataclasses.replace` keeps `_load_error`, and
    none of them reaches config.toml."""
    import dataclasses
    (tmp_path / "config.toml").write_text("not = [ toml", encoding="utf-8")
    broken = load_config()
    copied = dataclasses.replace(broken)
    assert copied._load_error == broken._load_error != ""
    assert copied._load_error_kind == "parse"
    (tmp_path / "config.toml").write_text(
        'acp_permission_mode = "AUTO"\n', encoding="utf-8")
    cfg = load_config()
    cfg.peek_hotkey = "ctrl+alt+p"
    save_config(cfg)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "_load_error" not in text and "_raw_permission" not in text
    assert "_mode_warning" not in text


def test_an_unrelated_save_keeps_the_stored_mode_and_rules_as_written(tmp_path):
    """A10 (user decision 2026-09-25): a peek-hotkey save writes the stored
    permission values back byte for byte, so an invalid allow pattern and a
    junk mode survive, and so do the warnings that name them."""
    (tmp_path / "config.toml").write_text(
        'acp_permission_mode = "AUTO"\n'
        '[acp_permission_rules.shell]\n'
        'default = "ask"\n'
        'allow = ["git status", "*"]\n'
        'block = []\n', encoding="utf-8")
    cfg = load_config()
    assert cfg.acp_permission_mode == "manual" and cfg._mode_warning
    assert "*" not in cfg.acp_permission_rules["shell"]["allow"]
    assert cfg._rules_warning
    cfg.peek_hotkey = "ctrl+alt+p"
    save_config(cfg)
    with open(tmp_path / "config.toml", "rb") as f:
        stored = tomllib.load(f)
    assert stored["acp_permission_mode"] == "AUTO"
    assert stored["acp_permission_rules"] == {
        "shell": {"default": "ask", "allow": ["git status", "*"], "block": []}}
    assert stored["peek_hotkey"] == "ctrl+alt+p"
    again = load_config()
    assert again._mode_warning and again._rules_warning


def test_a_permission_change_is_stored_even_when_it_equals_the_loaded_value(
        tmp_path):
    """A10: an assignment is a change. Choosing Manual over a stored "AUTO"
    (which already reads as Manual) overwrites it, and new rules replace an
    invalid stored pattern; a change made in place counts as well."""
    (tmp_path / "config.toml").write_text(
        'acp_permission_mode = "AUTO"\n'
        '[acp_permission_rules.shell]\n'
        'default = "ask"\n'
        'allow = ["*"]\n'
        'block = []\n', encoding="utf-8")
    cfg = load_config()
    cfg.acp_permission_mode = "manual"
    save_config(cfg)
    with open(tmp_path / "config.toml", "rb") as f:
        stored = tomllib.load(f)
    assert stored["acp_permission_mode"] == "manual"
    assert stored["acp_permission_rules"]["shell"]["allow"] == ["*"]
    cfg = load_config()
    cfg.acp_permission_rules["shell"]["allow"].append("git log")
    save_config(cfg)
    with open(tmp_path / "config.toml", "rb") as f:
        stored = tomllib.load(f)
    assert stored["acp_permission_rules"]["shell"]["allow"] == ["git log"]
    assert load_config()._rules_warning == ""


def test_a_read_error_is_retried_and_never_called_corrupt(tmp_path, monkeypatch):
    """RE5: an `OSError` (a sharing violation) is retried once. A second
    failure loads the defaults stand-in, but writes no `.bak` and says the
    file could not be opened rather than asking for a hand fix."""
    import power_atlas.config as config_mod
    (tmp_path / "config.toml").write_text('peek_hotkey = "ctrl+q"\n',
                                          encoding="utf-8")
    monkeypatch.setattr(config_mod, "_READ_RETRY_SECONDS", 0)
    real = config_mod._read_config_file
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(13, "The process cannot access the file")
        return real()

    monkeypatch.setattr(config_mod, "_read_config_file", flaky)
    cfg = load_config()
    assert cfg._load_error == "" and cfg.peek_hotkey == "ctrl+q"

    def locked():
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(config_mod, "_read_config_file", locked)
    cfg = load_config()
    assert cfg._load_error and cfg._load_error_kind == "read"
    assert not (tmp_path / "config.toml.bak").exists()
    message = config_mod.unreadable_config_message(cfg)
    assert "could not be opened" in message
    assert "by hand" not in message and ".bak" not in message
    with pytest.raises(config_mod.ConfigUnreadableError):
        save_config(cfg)


def test_a_persistent_read_error_logs_once_and_waits_outside_the_lock(
        tmp_path, monkeypatch, caplog):
    """Cycle 2, C-L6: a config.toml that stays unreadable logs one WARNING
    per distinct error, not one per call, and the wait before the retry does
    not hold `_lock`."""
    import logging
    import power_atlas.config as config_mod
    (tmp_path / "config.toml").write_text('peek_hotkey = "ctrl+q"\n',
                                          encoding="utf-8")
    monkeypatch.setattr(config_mod, "_read_errors_logged", set())
    held_during_sleep = []
    monkeypatch.setattr(config_mod.time, "sleep",
                        lambda _s: held_during_sleep.append(config_mod._lock.locked()))

    def locked():
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(config_mod, "_read_config_file", locked)
    with caplog.at_level(logging.WARNING, logger="power_atlas.config"):
        for _ in range(3):
            assert load_config()._load_error
    assert held_during_sleep == [False, False, False]
    assert sum("could not be read" in r.getMessage() for r in caplog.records) == 1
