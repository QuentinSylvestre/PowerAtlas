"""Tests for autostart module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from power_atlas import autostart


@pytest.fixture
def tmp_startup(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "STARTUP_DIR", tmp_path)
    return tmp_path


def test_is_enabled_false_when_missing(tmp_startup):
    assert autostart.is_enabled() is False


def test_disable_when_missing(tmp_startup):
    autostart.disable()  # should not raise
    assert autostart.is_enabled() is False


def test_enable_creates_shortcut(tmp_startup):
    mock_shortcut = MagicMock()
    mock_shell = MagicMock()
    mock_shell.CreateShortCut.return_value = mock_shortcut

    with patch("win32com.client.Dispatch", return_value=mock_shell) as mock_dispatch:
        autostart.enable()

    mock_dispatch.assert_called_once_with("WScript.Shell")
    mock_shell.CreateShortCut.assert_called_once_with(str(tmp_startup / autostart.SHORTCUT_NAME))
    mock_shortcut.save.assert_called_once()
    expected_icon = str(Path(autostart.__file__).parent / "static" / "poweratlas.ico")
    assert mock_shortcut.IconLocation == f"{expected_icon},0"


def test_disable_removes_shortcut(tmp_startup):
    shortcut = tmp_startup / autostart.SHORTCUT_NAME
    shortcut.write_text("")  # simulate existing shortcut
    assert autostart.is_enabled() is True

    autostart.disable()
    assert autostart.is_enabled() is False



def test_appdata_fallback_uses_home(monkeypatch):
    """When APPDATA is empty, module falls back to Path.home() / AppData / Roaming."""
    import importlib
    import os
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("APPDATA", "")
    # Re-import to trigger module-level code with empty APPDATA
    import power_atlas.autostart as _mod
    importlib.reload(_mod)
    expected = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert _mod.STARTUP_DIR == expected
