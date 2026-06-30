"""Tests for autostart module."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from power_atlas import autostart


@pytest.fixture
def tmp_shortcut(tmp_path, monkeypatch):
    """Patch the shortcut/desktop path to use tmp_path."""
    shortcut_file = tmp_path / "PowerAtlas.lnk"
    if sys.platform == "win32":
        monkeypatch.setattr(autostart, "_windows_shortcut_path", lambda: shortcut_file)
    else:
        desktop_file = tmp_path / "power-atlas.desktop"
        monkeypatch.setattr(autostart, "_linux_desktop_path", lambda: desktop_file)
        shortcut_file = desktop_file
    return shortcut_file


def test_is_enabled_false_when_missing(tmp_shortcut):
    assert autostart.is_enabled() is False


def test_disable_when_missing(tmp_shortcut):
    autostart.disable()  # should not raise
    assert autostart.is_enabled() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only COM shortcut test")
def test_enable_creates_shortcut_windows(tmp_shortcut):
    mock_shortcut = MagicMock()
    mock_shell = MagicMock()
    mock_shell.CreateShortCut.return_value = mock_shortcut

    with patch("win32com.client.Dispatch", return_value=mock_shell) as mock_dispatch:
        autostart.enable()

    mock_dispatch.assert_called_once_with("WScript.Shell")
    mock_shell.CreateShortCut.assert_called_once_with(str(tmp_shortcut))
    mock_shortcut.save.assert_called_once()
    expected_icon = str(Path(autostart.__file__).parent / "static" / "poweratlas.ico")
    assert mock_shortcut.IconLocation == f"{expected_icon},0"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only desktop file test")
def test_enable_creates_desktop_file_linux(tmp_shortcut):
    autostart.enable()
    assert tmp_shortcut.exists()
    content = tmp_shortcut.read_text()
    assert "[Desktop Entry]" in content
    assert "power_atlas" in content


def test_disable_removes_shortcut(tmp_shortcut):
    tmp_shortcut.write_text("")  # simulate existing shortcut
    assert autostart.is_enabled() is True

    autostart.disable()
    assert autostart.is_enabled() is False
