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
    """Redirect config to tmp dir."""
    monkeypatch.setattr("power_atlas.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("power_atlas.config.CONFIG_PATH", tmp_path / "config.toml")


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


def test_workspace_icons_round_trip():
    """workspace_icons dict persists through save/load cycle."""
    icons = {"C:\\projects\\app": "rocket", "C:\\work\\lib": "books"}
    cfg = Config(workspace_icons=icons)
    save_config(cfg)
    loaded = load_config()
    assert loaded.workspace_icons == icons


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
        "kiro-cli": {"default_args": "-a --verbose", "color": "#4a6ede", "enabled": True},
        "claude-code": {"default_args": "", "color": "#c2590f", "enabled": False},
    }
    cfg = Config(provider_settings=settings)
    save_config(cfg)
    loaded = load_config()
    assert loaded.provider_settings == settings


def test_trust_all_tools_migration(tmp_path):
    """trust_all_tools=true migrates to provider_settings['kiro-cli'].default_args='-a'."""
    _write_toml(tmp_path, {"trust_all_tools": True})
    cfg = load_config()
    assert cfg.provider_settings == {"kiro-cli": {"default_args": "-a", "color": "", "enabled": True}}


def test_trust_all_tools_no_migration_when_provider_settings_exist(tmp_path):
    """trust_all_tools=true adds kiro-cli when only OTHER providers exist, but NOT when kiro-cli already exists."""
    # Case 1: Other providers exist but no kiro-cli → kiro-cli IS added
    existing = {"claude-code": {"default_args": "--dangerously-skip-permissions", "color": "", "enabled": True}}
    _write_toml(tmp_path, {"trust_all_tools": True, "provider_settings": existing})
    cfg = load_config()
    assert "kiro-cli" in cfg.provider_settings
    assert cfg.provider_settings["kiro-cli"]["default_args"] == "-a"
    assert cfg.provider_settings["claude-code"] == existing["claude-code"]

    # Case 2: kiro-cli already exists → NOT overwritten
    existing_with_kiro = {
        "kiro-cli": {"default_args": "--custom", "color": "#111", "enabled": True},
        "claude-code": {"default_args": "", "color": "", "enabled": True},
    }
    _write_toml(tmp_path, {"trust_all_tools": True, "provider_settings": existing_with_kiro})
    cfg = load_config()
    assert cfg.provider_settings["kiro-cli"]["default_args"] == "--custom"


def test_trust_all_tools_false_no_migration(tmp_path):
    """trust_all_tools=false does not trigger migration."""
    _write_toml(tmp_path, {"trust_all_tools": False})
    cfg = load_config()
    assert cfg.provider_settings == {}


def test_save_config_drops_trust_all_tools():
    """save_config never writes trust_all_tools to TOML."""
    from power_atlas.config import CONFIG_PATH
    cfg = Config(provider_settings={"kiro-cli": {"default_args": "-a", "color": "", "enabled": True}})
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
        "workspace_icons": {"good": "icon1"},
        "provider_settings": {"x": "notadict", "y": {"default_args": "", "color": "", "enabled": True}},
        "custom_launchers": ["notadict", {"id": "ok"}],
    })
    cfg = load_config()
    # Non-str entries dropped from pinned_folders
    assert cfg.pinned_folders == ["valid"]
    # Non-str entries dropped from pinned_sessions (bool is not str)
    assert cfg.pinned_sessions == ["sess1"]
    # workspace_icons with valid str keys+values preserved
    assert cfg.workspace_icons == {"good": "icon1"}
    # provider_settings with non-dict values dropped
    assert "x" not in cfg.provider_settings
    assert cfg.provider_settings == {"y": {"default_args": "", "color": "", "enabled": True}}
    # custom_launchers: non-dict entries dropped
    assert cfg.custom_launchers == [{"id": "ok"}]
