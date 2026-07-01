"""Tests for config module."""

import threading
from pathlib import Path

import pytest

from power_atlas.config import Config, load_config, save_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config to tmp dir."""
    monkeypatch.setattr("power_atlas.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("power_atlas.config.CONFIG_PATH", tmp_path / "config.toml")


def test_round_trip():
    cfg = Config(terminal_command="wt.exe", pinned_folders=["/a", "/b"])
    save_config(cfg)
    loaded = load_config()
    assert loaded.terminal_command == "wt.exe"
    assert loaded.pinned_folders == ["/a", "/b"]
    assert loaded.pinned_sessions == []


def test_missing_keys_use_defaults():
    """A TOML with only one key should still produce a full Config with defaults."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"terminal_command": "wt.exe"}, f)
    cfg = load_config()
    assert cfg.terminal_command == "wt.exe"
    assert cfg.peek_hotkey == "ctrl+shift+z"  # default
    assert cfg.pinned_folders == []  # default


def test_unknown_keys_ignored():
    """Unknown keys in TOML should not raise or appear on Config."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"terminal_command": "cmd", "unknown_key": "hello", "extra": 42}, f)
    cfg = load_config()
    assert cfg.terminal_command == "cmd"
    assert not hasattr(cfg, "unknown_key")


def test_missing_file_returns_defaults():
    cfg = load_config()
    assert cfg == Config()


def test_thread_safety():
    """Concurrent save/load doesn't corrupt."""
    errors = []

    def writer(i):
        try:
            save_config(Config(terminal_command=f"cmd_{i}", pinned_folders=[str(i)]))
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            cfg = load_config()
            # Should always be a valid Config
            assert isinstance(cfg.terminal_command, str)
        except Exception as e:
            errors.append(e)

    # Initial save so file exists
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
    # Final state should be valid
    cfg = load_config()
    assert isinstance(cfg, Config)



def test_wrong_type_bool_gets_default():
    """A string 'yes' or int 1 for a bool field should fall back to default."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # peek_hotkey should be str — test that int falls back to default
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"peek_hotkey": 42}, f)
    cfg = load_config()
    assert cfg.peek_hotkey == "ctrl+shift+z"  # default (int != str)


def test_wrong_type_str_gets_default():
    """An integer for a str field should fall back to default."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"peek_hotkey": 42}, f)
    cfg = load_config()
    assert cfg.peek_hotkey == "ctrl+shift+z"  # default (int != str)


def test_wrong_type_list_gets_default():
    """A scalar string for a list field should fall back to default."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"pinned_folders": "not a list", "terminal_command": 42}, f)
    cfg = load_config()
    assert cfg.pinned_folders == []  # default
    assert cfg.terminal_command == ""  # default (int for str field)


def test_workspace_icons_round_trip():
    """workspace_icons dict persists through save/load cycle."""
    icons = {"C:\\projects\\app": "🚀", "C:\\work\\lib": "📚"}
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
    assert loaded.custom_launchers[0]["id"] == "abc"


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


def test_trust_all_tools_migration():
    """trust_all_tools=true migrates to provider_settings['kiro-cli'].default_args='-a'."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"trust_all_tools": True, "terminal_command": "wt.exe"}, f)
    cfg = load_config()
    assert cfg.provider_settings == {"kiro-cli": {"default_args": "-a", "color": "", "enabled": True}}
    assert cfg.terminal_command == "wt.exe"


def test_trust_all_tools_no_migration_when_provider_settings_exist():
    """trust_all_tools=true does NOT migrate if provider_settings already exist."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = {"claude-code": {"default_args": "--dangerously-skip-permissions", "color": "", "enabled": True}}
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"trust_all_tools": True, "provider_settings": existing}, f)
    cfg = load_config()
    assert cfg.provider_settings == existing
    # No kiro-cli entry added
    assert "kiro-cli" not in cfg.provider_settings


def test_trust_all_tools_false_no_migration():
    """trust_all_tools=false does not trigger migration."""
    import tomli_w
    from power_atlas.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"trust_all_tools": False}, f)
    cfg = load_config()
    assert cfg.provider_settings == {}


def test_save_config_drops_trust_all_tools():
    """save_config never writes trust_all_tools to TOML."""
    import tomllib
    from power_atlas.config import CONFIG_PATH
    cfg = Config(provider_settings={"kiro-cli": {"default_args": "-a", "color": "", "enabled": True}})
    save_config(cfg)
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    assert "trust_all_tools" not in raw
