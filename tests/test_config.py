"""Tests for config module."""

import threading
from pathlib import Path

import pytest

from kiro_orchestrator.config import Config, load_config, save_config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect config to tmp dir."""
    monkeypatch.setattr("kiro_orchestrator.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("kiro_orchestrator.config.CONFIG_PATH", tmp_path / "config.toml")


def test_round_trip():
    cfg = Config(trust_all_tools=True, terminal_command="wt.exe", pinned_folders=["/a", "/b"])
    save_config(cfg)
    loaded = load_config()
    assert loaded.trust_all_tools is True
    assert loaded.terminal_command == "wt.exe"
    assert loaded.pinned_folders == ["/a", "/b"]
    assert loaded.pinned_sessions == []


def test_missing_keys_use_defaults():
    """A TOML with only one key should still produce a full Config with defaults."""
    import tomli_w
    from kiro_orchestrator.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"trust_all_tools": True}, f)
    cfg = load_config()
    assert cfg.trust_all_tools is True
    assert cfg.use_pywebview is True  # default
    assert cfg.pinned_folders == []  # default


def test_unknown_keys_ignored():
    """Unknown keys in TOML should not raise or appear on Config."""
    import tomli_w
    from kiro_orchestrator.config import CONFIG_PATH, CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump({"trust_all_tools": False, "unknown_key": "hello", "extra": 42}, f)
    cfg = load_config()
    assert cfg.trust_all_tools is False
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
            assert isinstance(cfg.trust_all_tools, bool)
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
