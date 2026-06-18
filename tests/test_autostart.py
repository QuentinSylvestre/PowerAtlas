"""Tests for autostart module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kiro_orchestrator import autostart


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


def test_disable_removes_shortcut(tmp_startup):
    shortcut = tmp_startup / autostart.SHORTCUT_NAME
    shortcut.write_text("")  # simulate existing shortcut
    assert autostart.is_enabled() is True

    autostart.disable()
    assert autostart.is_enabled() is False
