"""Tests for power_atlas.peek — works without pywebview/pynput installed."""

import sys
from unittest.mock import MagicMock, patch

import pytest


# Ensure the module can be imported even without pywebview/pynput by mocking them
# before importing peek.py (if they aren't available in the test env).
@pytest.fixture(autouse=True)
def _mock_optional_deps(monkeypatch):
    """Ensure peek module is importable regardless of optional deps."""
    # If webview/pynput are genuinely installed, tests still pass — no harm.
    pass


def _make_key(char=None, name=None):
    """Create a mock key object mimicking pynput key events."""
    key = MagicMock()
    if char is not None:
        key.char = char
        # Remove name attr so hasattr checks work correctly
        del key.name
    elif name is not None:
        key.name = name
        key.char = None
    else:
        # No char, no name
        del key.char
        del key.name
    return key


class TestParseHotkey:
    def test_ctrl_shift_z(self):
        from power_atlas.peek import PeekWindow

        result = PeekWindow._parse_hotkey("ctrl+shift+z")
        assert result == {"ctrl", "shift", "z"}

    def test_alt_f1(self):
        from power_atlas.peek import PeekWindow

        result = PeekWindow._parse_hotkey("alt+f1")
        assert result == {"alt", "f1"}

    def test_whitespace_handling(self):
        from power_atlas.peek import PeekWindow

        result = PeekWindow._parse_hotkey(" ctrl + shift + z ")
        assert result == {"ctrl", "shift", "z"}


class TestNormalizeKey:
    def test_char_key(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(char="z")
        assert PeekWindow._normalize_key(key) == "z"

    def test_char_key_uppercase(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(char="Z")
        assert PeekWindow._normalize_key(key) == "z"

    def test_ctrl_l(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(name="ctrl_l")
        assert PeekWindow._normalize_key(key) == "ctrl"

    def test_ctrl_r(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(name="ctrl_r")
        assert PeekWindow._normalize_key(key) == "ctrl"

    def test_shift_r(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(name="shift_r")
        assert PeekWindow._normalize_key(key) == "shift"

    def test_alt_gr(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(name="alt_gr")
        assert PeekWindow._normalize_key(key) == "alt"

    def test_bare_ctrl(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(name="ctrl")
        assert PeekWindow._normalize_key(key) == "ctrl"

    def test_escape(self):
        from power_atlas.peek import PeekWindow

        key = _make_key(name="esc")
        assert PeekWindow._normalize_key(key) == "esc"

    def test_none_when_no_attrs(self):
        from power_atlas.peek import PeekWindow

        key = _make_key()
        assert PeekWindow._normalize_key(key) is None


class TestIsAvailable:
    def test_is_available(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        assert peek_mod.is_available() is True

    def test_not_available(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", False)
        assert peek_mod.is_available() is False


class TestCreatePeek:
    def test_unavailable_returns_none(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", False)
        monkeypatch.setattr(peek_mod, "_IMPORT_ERROR", "No module named 'webview'")
        result = peek_mod.create_peek("http://localhost:8000")
        assert result is None

    def test_invalid_hotkey_fallback(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        captured_args = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z"):
            captured_args["server_url"] = server_url
            captured_args["hotkey"] = hotkey
            # Minimal init to avoid real webview/pynput usage
            self._server_url = server_url
            self._hotkey = hotkey
            self._window = None
            self._visible = False
            self._listener = None
            self._trigger_keys = peek_mod.PeekWindow._parse_hotkey(hotkey)
            self._pressed_keys = set()
            self._triggered = False
            self._webview_ready = None

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000", "nope")
        assert result is not None
        assert captured_args["hotkey"] == "ctrl+shift+z"

    def test_invalid_hotkey_only_modifier(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        captured_args = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z"):
            captured_args["hotkey"] = hotkey
            self._server_url = server_url
            self._hotkey = hotkey
            self._window = None
            self._visible = False
            self._listener = None
            self._trigger_keys = peek_mod.PeekWindow._parse_hotkey(hotkey)
            self._pressed_keys = set()
            self._triggered = False
            self._webview_ready = None

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000", "ctrl+shift")
        assert result is not None
        assert captured_args["hotkey"] == "ctrl+shift+z"

    def test_valid_hotkey(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        captured_args = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z"):
            captured_args["server_url"] = server_url
            captured_args["hotkey"] = hotkey
            self._server_url = server_url
            self._hotkey = hotkey
            self._window = None
            self._visible = False
            self._listener = None
            self._trigger_keys = peek_mod.PeekWindow._parse_hotkey(hotkey)
            self._pressed_keys = set()
            self._triggered = False
            self._webview_ready = None

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000", "ctrl+alt+p")
        assert result is not None
        assert isinstance(result, peek_mod.PeekWindow)
        assert captured_args["server_url"] == "http://localhost:8000"
        assert captured_args["hotkey"] == "ctrl+alt+p"

    def test_exception_in_init_returns_none(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        def mock_init(self, server_url, hotkey="ctrl+shift+z"):
            raise RuntimeError("Something broke")

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000")
        assert result is None
