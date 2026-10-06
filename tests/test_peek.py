"""Tests for power_atlas.peek — works without pywebview/pynput installed."""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest


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

    def test_ctrl_modified_char(self):
        """When Ctrl is held, pynput reports control codes (0x01-0x1a) instead of letters."""
        from power_atlas.peek import PeekWindow

        # Ctrl+Z = 0x1a, Ctrl+A = 0x01, Ctrl+P = 0x10
        key_z = _make_key(char="\x1a")
        assert PeekWindow._normalize_key(key_z) == "z"
        key_a = _make_key(char="\x01")
        assert PeekWindow._normalize_key(key_a) == "a"
        key_p = _make_key(char="\x10")
        assert PeekWindow._normalize_key(key_p) == "p"

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



class TestHotkeyStateMachine:
    """The hook layer: it records keys and posts events, never touches the
    window. Rewritten from the `_show`/`_hide` patches, which no longer exist:
    the hook enqueues and one worker acts (D-21), and per-show navigation is
    gone (D-13). Driven through the non-Windows `_on_press` path here; the
    Windows filter has its own class below.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def _make_peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        pw = _new_peek(monkeypatch)
        pw._adapter = _FakeAdapter()
        return pw

    def test_full_combo_posts_one_press(self, monkeypatch):
        pw = self._make_peek(monkeypatch)
        pw._on_press(_make_key(name="ctrl_l"))
        pw._on_press(_make_key(name="shift_l"))
        assert _drain(pw) == []
        pw._on_press(_make_key(char="z"))
        (ev,) = _drain(pw)
        assert ev[:2] == ("press", "peek")
        assert pw._adapter.calls == [], "the hook never touches the window"

    def test_release_modifier_posts_release(self, monkeypatch):
        pw = self._make_peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z")):
            pw._on_press(k)
        _drain(pw)
        pw._on_release(_make_key(name="ctrl_l"))
        assert _drain(pw) == [("release",)]
        assert pw._triggered is False

    def test_a_modifier_release_with_no_press_posts_nothing(self, monkeypatch):
        """Every Ctrl the user types would otherwise wake the worker."""
        pw = self._make_peek(monkeypatch)
        pw._on_press(_make_key(name="ctrl_l"))
        pw._on_release(_make_key(name="ctrl_l"))
        assert _drain(pw) == []

    def test_escape_posts_esc(self, monkeypatch):
        pw = self._make_peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z")):
            pw._on_press(k)
        _drain(pw)
        pw._on_press(_make_key(name="esc"))
        assert _drain(pw) == [("esc",)]
        assert pw._triggered is False
        assert pw._pressed_keys == set()  # cleared on escape

    def test_partial_combo_does_not_trigger(self, monkeypatch):
        pw = self._make_peek(monkeypatch)
        pw._on_press(_make_key(name="ctrl_l"))
        pw._on_press(_make_key(char="z"))
        # Missing shift — should not trigger
        assert _drain(pw) == []
        assert pw._triggered is False

    def test_auto_repeat_is_not_an_event(self, monkeypatch):
        pw = self._make_peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z"), _make_key(char="z"),
                  _make_key(char="z")):
            pw._on_press(k)
        assert len(_drain(pw)) == 1

    def test_the_chord_key_up_rearms_with_modifiers_held(self, monkeypatch):
        """Off Windows too, a second tap while Ctrl+Shift stay down is a new
        press, so the modifiers-held double-tap works everywhere."""
        pw = self._make_peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z")):
            pw._on_press(k)
        pw._on_release(_make_key(char="z"))
        pw._on_press(_make_key(char="z"))
        assert [e[0] for e in _drain(pw)] == ["press", "press"]

    def test_a_superset_chord_still_fires(self, monkeypatch):
        """Holding an extra modifier keeps working (D-18)."""
        pw = self._make_peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="alt_l"),
                  _make_key(name="shift_l"), _make_key(char="z")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["press"]

    def test_a_handler_error_does_not_escape(self, monkeypatch):
        """An exception in a pynput callback stops the listener."""
        pw = self._make_peek(monkeypatch)
        monkeypatch.setattr(pw, "_normalize_key",
                            lambda key: (_ for _ in ()).throw(ValueError()))
        pw._on_press(_make_key(char="z"))
        pw._on_release(_make_key(char="z"))
        assert _drain(pw) == []


class _Suppress(Exception):
    """Stands in for pynput's `SuppressException`, an `Exception` subclass."""


class _Listener:
    def __init__(self):
        self.suppressed = 0

    def suppress_event(self):
        self.suppressed += 1
        raise _Suppress()


class _KbData:
    def __init__(self, vk, t=0):
        self.vkCode = vk
        self.time = t


class TestWin32Filter:
    """`_win32_event_filter`: decide inside a `try`, post, then suppress
    outside it (Threading model).
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    _VK_Z, _VK_Q, _VK_ESC = 0x5A, 0x51, 0x1B

    def _peek(self, monkeypatch, held=("ctrl", "shift")):
        pw = _new_peek(monkeypatch)
        pw._adapter = _FakeAdapter()
        pw._listener = _Listener()
        pw._pressed_keys.update(held)
        return pw

    def _feed(self, pw, msg, vk, t=0):
        """True when the filter suppressed the event."""
        try:
            pw._win32_event_filter(msg, _KbData(vk, t))
        except _Suppress:
            return True
        return False

    def test_a_matching_chord_posts_and_suppression_escapes(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=4321)
        assert _drain(pw) == [("press", "peek", 4321)]
        assert pw._listener.suppressed == 1
        assert pw._adapter.calls == [], "the hook never touches the window"

    def test_the_hook_timestamp_is_carried(self, monkeypatch):
        """Timed when pressed, not when the worker gets to it (and masked)."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)
        self._feed(pw, peek_mod._WM_SYSKEYDOWN, self._VK_Z, t=0x1_0000_0007)
        assert _drain(pw) == [("press", "peek", 7)]

    def test_another_key_passes_through(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Q)
        assert _drain(pw) == []

    def test_the_chord_key_without_its_modifiers_passes(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, held=("ctrl",))
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z)
        assert _drain(pw) == []

    def test_auto_repeat_is_suppressed_but_not_an_event(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=10)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=40)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=70)
        assert len(_drain(pw)) == 1

    def test_the_key_up_is_suppressed_and_rearms(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)
        self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=10)
        assert self._feed(pw, peek_mod._WM_KEYUP, self._VK_Z)
        assert pw._triggered is False
        self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=200)
        assert [e[2] for e in _drain(pw)] == [10, 200]

    def test_a_key_up_whose_down_passed_is_not_suppressed(self, monkeypatch):
        """`z` typed alone reaches the app; its key-up must too."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, held=())
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_Z)
        assert not self._feed(pw, peek_mod._WM_KEYUP, self._VK_Z)

    def test_a_raising_matcher_suppresses_nothing_and_does_not_escape(
            self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)

        def boom(name):
            raise KeyError(name)

        monkeypatch.setattr(pw, "_matches_chord", boom)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw._win32_event_filter(peek_mod._WM_KEYDOWN, _KbData(self._VK_Z))
        assert pw._listener.suppressed == 0
        assert _drain(pw) == []
        assert "KeyError" in caplog.text

    def test_esc_is_suppressed_while_a_peek_shows(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, held=())
        pw._peek_showing = True
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_ESC)
        assert self._feed(pw, peek_mod._WM_KEYUP, self._VK_ESC)
        assert _drain(pw) == [("esc",)]

    def test_esc_passes_through_otherwise(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, held=())
        pw._peek_showing = False
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_ESC)
        assert not self._feed(pw, peek_mod._WM_KEYUP, self._VK_ESC)
        assert _drain(pw) == []

    def test_esc_key_up_follows_its_suppressed_key_down(self, monkeypatch):
        """The worker ends the peek between Esc down and up: the up must
        still be suppressed, or the user's app gets a lone key-up
        (review fix 9)."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, held=())
        pw._peek_showing = True
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_ESC)
        pw._peek_showing = False  # the worker handled `esc`
        assert self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_ESC), \
            "auto-repeat of a suppressed Esc stays suppressed"
        assert self._feed(pw, peek_mod._WM_KEYUP, self._VK_ESC)
        assert _drain(pw) == [("esc",)], "auto-repeat is not another event"
        # The pair is over: the next Esc reaches the app again.
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_ESC)
        assert not self._feed(pw, peek_mod._WM_KEYUP, self._VK_ESC)

    def test_esc_key_up_follows_its_passed_key_down(self, monkeypatch):
        """Esc went down before the peek showed: the app got the down, so it
        gets the up too, even though a peek now shows."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, held=())
        pw._peek_showing = False
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, self._VK_ESC)
        pw._peek_showing = True
        assert not self._feed(pw, peek_mod._WM_KEYUP, self._VK_ESC)
        assert _drain(pw) == []

    def test_a_cleared_listener_does_not_raise_from_the_filter(self,
                                                               monkeypatch):
        """`stop()` clears `_listener` while the hook may still fire
        (review fix 12)."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch)
        pw._listener = None
        pw._win32_event_filter(peek_mod._WM_KEYDOWN, _KbData(self._VK_Z, 5))
        assert _drain(pw) == [("press", "peek", 5)]

    def test_on_windows_on_press_posts_nothing(self, monkeypatch):
        """The filter owns the chord and Esc on Windows; `_on_press` only
        records the keys it let through."""
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "win32")
        pw = self._peek(monkeypatch, held=("ctrl", "shift"))
        pw._peek_showing = True
        pw._on_press(_make_key(char="z"))
        pw._on_press(_make_key(name="esc"))
        assert _drain(pw) == []
        assert "z" in pw._pressed_keys


class TestParseHotkeyEdgeCases:
    """Additional edge-case tests for _parse_hotkey."""

    def test_trailing_plus(self):
        from power_atlas.peek import PeekWindow

        # Trailing + should not produce empty string in result
        result = PeekWindow._parse_hotkey("ctrl+shift+")
        assert "" not in result
        assert result == {"ctrl", "shift"}


class _Placement:
    """An opaque saved placement; identity is what the worker must carry."""

    def __init__(self, label, activates=False):
        self.label = label
        # A maximized placement: `SW_SHOWMAXIMIZED` has no non-activating
        # form, so re-applying it takes the foreground even when not focused.
        self.activates = activates

    def __repr__(self):
        return f"<placement {self.label}>"


class _FakeAdapter:
    """Records every window primitive the worker calls.

    `live` is what `get_placement` reads now (the user may resize in APP);
    `fg` is the foreground HWND; the window's own HWND is `HWND`.
    """

    HWND = 0x5151
    has_app_mode = True

    def __init__(self, fg=None):
        self.calls = []
        self.fg = fg
        self.live = _Placement("live-0")
        self.default = _Placement("default")

    def foreground(self):
        self.calls.append(("foreground",))
        return self.fg

    def is_foreground(self):
        self.calls.append(("is_foreground",))
        return self.fg == self.HWND

    def get_placement(self):
        self.calls.append(("get_placement",))
        return self.live

    def show_peek(self):
        self.calls.append(("show_peek",))

    def hide(self):
        self.calls.append(("hide",))

    def apply_app(self, placement, focused):
        self.calls.append(("apply_app", placement, focused))
        self.live = placement or self.default
        if focused or self.live.activates:
            self.fg = self.HWND
        return self.live

    def focus(self):
        self.calls.append(("focus",))
        self.fg = self.HWND

    def restore_foreground(self, prev, pa_fg):
        """Models the primitive: hands the foreground back only when told
        PowerAtlas holds it and `prev` is another window."""
        self.calls.append(("restore_foreground", prev, pa_fg))
        if pa_fg and prev and prev != self.HWND:
            self.fg = prev

    def put_below(self, prev):
        self.calls.append(("put_below", prev))

    def fire_reset_overlays(self):
        self.calls.append(("reset_overlays",))

    def reload(self, url):
        self.calls.append(("reload", url))

    def names(self):
        return [c[0] for c in self.calls]

    def window_calls(self):
        """The calls that change the window (reads left out)."""
        reads = {"foreground", "is_foreground", "get_placement"}
        return [c for c in self.calls if c[0] not in reads]


def _new_peek(monkeypatch):
    import power_atlas.peek as peek_mod
    monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
    return peek_mod.PeekWindow("http://127.0.0.1:4915", "ctrl+shift+z")


def _drain(pw):
    import queue
    out = []
    while True:
        try:
            out.append(pw._events.get_nowait())
        except queue.Empty:
            return out


OTHER_APP = 0x7777  # the user's app, foreground before a peek


class TestWindowStateMachine:
    """Every Hold-mode and common cell of the plan's state-machine table, fed
    straight into the worker's handler with a fake window adapter. Expected
    calls come from the table and its Definitions, not from the code.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def _peek(self, monkeypatch, fg=OTHER_APP):
        from power_atlas import web as web_mod
        pw = _new_peek(monkeypatch)
        pw._adapter = _FakeAdapter(fg=fg)
        pw._ready.set()
        pw._signed_gen = web_mod.local_secret_generation()
        return pw, pw._adapter

    # Event helpers; ticks in ms. `far` presses are never a double-tap.
    @staticmethod
    def _press(pw, t):
        pw._handle(("press", "peek", t))

    def _in_app(self, pw, a, foreground=True):
        """Put the window in APP, as tray Open would, then reset the log."""
        pw._handle(("show_app",))
        if not foreground:
            a.fg = OTHER_APP
        a.calls.clear()

    # -- Peek press, not a double-tap (Hold) --------------------------------

    def test_press_from_hidden_shows_peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        assert a.window_calls() == [("show_peek",)]
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, None)
        assert pw._peek_showing is True
        assert pw._prev_foreground == OTHER_APP

    def test_press_in_peek_is_a_no_op(self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        self._press(pw, 3000)
        assert a.window_calls() == []

    def test_press_in_peek_from_app_is_a_no_op(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        self._press(pw, 1000)
        a.calls.clear()
        self._press(pw, 3000)
        assert a.window_calls() == []
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")

    def test_press_from_app_saves_then_peeks(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        a.live = _Placement("resized")
        self._press(pw, 1000)
        assert pw._app_placement is a.live
        assert pw._app_was_foreground is True
        assert a.window_calls() == [("show_peek",)]
        # Saved before the peek showed.
        assert a.names().index("get_placement") < a.names().index("show_peek")
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")

    # -- Modifier release (Hold) --------------------------------------------

    def test_release_in_hidden_is_a_no_op(self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        pw._handle(("release",))
        assert a.calls == []

    def test_release_in_app_is_a_no_op(self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        pw._handle(("release",))
        assert a.window_calls() == []

    def test_release_ends_a_peek_to_hidden(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("release",))
        assert a.window_calls() == [
            ("reset_overlays",), ("hide",),
            ("restore_foreground", OTHER_APP, False)]
        # The foreground is read before the hide.
        assert a.names().index("is_foreground") < a.names().index("hide")
        assert pw._state == peek_mod.HIDDEN
        assert pw._peek_showing is False

    def test_release_ends_a_peek_back_to_app_without_reset(self, monkeypatch):
        """D-11: `resetOverlays` only on PEEK → HIDDEN."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        saved = a.live = _Placement("before-peek")
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("release",))
        assert ("reset_overlays",) not in a.calls
        assert a.window_calls()[0] == ("apply_app", saved, False)
        assert pw._state == peek_mod.APP

    # -- Esc ----------------------------------------------------------------

    def test_esc_ends_a_peek_to_hidden(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("esc",))
        assert ("reset_overlays",) in a.calls and ("hide",) in a.calls
        assert pw._state == peek_mod.HIDDEN

    def test_esc_ends_a_peek_back_to_app(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        saved = a.live
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("esc",))
        assert a.window_calls()[0] == ("apply_app", saved, False)
        assert ("reset_overlays",) not in a.calls
        assert pw._state == peek_mod.APP

    @pytest.mark.parametrize("where", ["hidden", "app"])
    def test_esc_outside_a_peek_does_nothing(self, monkeypatch, where):
        pw, a = self._peek(monkeypatch)
        if where == "app":
            self._in_app(pw, a)
        pw._handle(("esc",))
        assert a.window_calls() == []

    # -- User close (FormClosing, UserClosing) --------------------------------

    def test_user_close_in_app_saves_and_hides(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        pw._handle(("user_close",))
        assert a.window_calls() == [("hide",)]
        assert "get_placement" in a.names()
        assert pw._state == peek_mod.HIDDEN

    def test_user_close_in_a_peek_is_esc(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("user_close",))
        assert a.window_calls()[:2] == [("reset_overlays",), ("hide",)]
        assert pw._state == peek_mod.HIDDEN

    def test_user_close_in_a_peek_over_app_returns_to_app(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        self._press(pw, 1000)
        pw._handle(("user_close",))
        assert pw._state == peek_mod.APP

    def test_user_close_while_hidden_does_nothing(self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        pw._handle(("user_close",))
        assert a.calls == []

    def test_x_after_a_resize_restores_the_newer_placement(self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        first = a.live
        a.live = resized = _Placement("resized-by-user")
        pw._handle(("user_close",))
        a.calls.clear()
        pw._handle(("show_app",))
        assert ("apply_app", resized, True) in a.calls
        assert ("apply_app", first, True) not in a.calls

    # -- Tray Open PowerAtlas -------------------------------------------------

    def test_tray_open_from_hidden_first_time_uses_the_default(self,
                                                               monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert pw._app_placement is a.default
        assert (pw._state, pw._peek_showing) == (peek_mod.APP, False)

    def test_tray_open_from_a_peek_keeps_the_page(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("show_app",))
        assert ("reset_overlays",) not in a.calls
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_tray_open_from_a_peek_over_app(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        saved = a.live
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("show_app",))
        assert a.window_calls() == [("apply_app", saved, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_tray_open_in_app_reapplies_the_live_placement(self, monkeypatch):
        """Restores a minimized window and brings it to the front, without
        losing a resize made since the last exit from APP."""
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=False)
        a.live = minimized = _Placement("minimized-now")
        pw._handle(("show_app",))
        assert a.window_calls() == [("apply_app", minimized, True), ("focus",)]

    # -- Peek over a minimized / non-foreground APP ---------------------------

    def test_peek_over_a_minimized_app_returns_to_minimized(self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=False)
        a.live = minimized = _Placement("minimized")
        self._press(pw, 1000)
        pw._handle(("release",))
        assert ("apply_app", minimized, False) in a.calls

    def test_peek_over_a_background_app_restores_foreground_and_z_order(
            self, monkeypatch):
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=False)
        self._press(pw, 1000)
        assert pw._app_was_foreground is False
        a.calls.clear()
        pw._handle(("release",))
        calls = a.window_calls()
        assert calls[0][0] == "apply_app" and calls[0][2] is False
        # The outcome, not a call: the user's app is still foreground.
        assert a.fg == OTHER_APP
        assert ("put_below", OTHER_APP) in calls
        assert ("focus",) not in calls

    def test_peek_over_a_background_maximized_app_restores_the_foreground(
            self, monkeypatch):
        """A maximized placement has no non-activating show, so re-applying
        it takes the foreground; the user's app must get it back, with
        PowerAtlas put back below it (review fix 2)."""
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=False)
        a.live = _Placement("maximized", activates=True)
        self._press(pw, 1000)
        assert pw._app_was_foreground is False
        assert a.fg == OTHER_APP  # the peek did not take it
        a.calls.clear()
        pw._handle(("release",))
        assert a.fg == OTHER_APP
        names = a.names()
        assert ("restore_foreground", OTHER_APP, True) in a.calls
        assert names.index("restore_foreground") < names.index("put_below")
        assert ("focus",) not in a.calls

    def test_peek_over_a_foreground_app_gives_it_the_foreground_back(
            self, monkeypatch):
        """SC-3, same focus state: the chrome switch hides the window, which
        costs it the foreground, so it is focused again on the way back."""
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=True)
        self._press(pw, 1000)
        a.fg = OTHER_APP  # what the hide around the restyle did
        a.calls.clear()
        pw._handle(("release",))
        assert ("focus",) in a.calls
        assert not any(c[0] == "put_below" for c in a.calls)

    # -- Double-tap rule ------------------------------------------------------

    def test_hold_release_all_double_tap_from_hidden_opens_app(self,
                                                               monkeypatch):
        """Press the whole chord, release everything, press again: today's
        double-tap, now opening app mode."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        pw._handle(("release",))
        a.calls.clear()
        self._press(pw, 1300)
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_double_tap_with_modifiers_held_from_hidden_opens_app(
            self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        self._press(pw, 1200)  # still in PEEK(None)
        assert ("reset_overlays",) not in a.calls
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_double_tap_from_a_foreground_app_hides_it(self, monkeypatch):
        """Decided from `tap_origin` (APP, foreground) although the state at
        the second press is PEEK("app"); the hide skips `resetOverlays`."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=True)
        self._press(pw, 1000)
        assert pw._tap_origin == (peek_mod.APP, True)
        a.calls.clear()
        self._press(pw, 1100)
        assert a.window_calls() == [("hide",)]
        assert pw._state == peek_mod.HIDDEN

    def test_double_tap_from_a_foreground_app_after_release_hides_it(
            self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=True)
        self._press(pw, 1000)
        pw._handle(("release",))  # back to APP, focused again
        a.calls.clear()
        a.live = resized = _Placement("during")
        self._press(pw, 1300)
        assert a.window_calls() == [("hide",)]
        assert pw._app_placement is resized  # saved: it was an exit from APP
        assert pw._state == peek_mod.HIDDEN

    def test_double_tap_from_a_background_app_focuses_it(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=False)
        saved = a.live
        self._press(pw, 1000)
        assert pw._tap_origin == (peek_mod.APP, False)
        a.calls.clear()
        self._press(pw, 1100)
        assert a.window_calls() == [("apply_app", saved, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_a_third_tap_is_not_another_double_tap(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        self._press(pw, 1200)  # double-tap → APP
        assert pw._last_press is None
        a.calls.clear()
        self._press(pw, 1400)  # within 0.5 s of the second, but a fresh first
        assert a.window_calls() == [("show_peek",)]
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")

    @pytest.mark.parametrize("gap, double", [(499, True), (500, False)])
    def test_the_double_tap_boundary(self, monkeypatch, gap, double):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 7000)
        pw._handle(("release",))
        self._press(pw, 7000 + gap)
        assert (pw._state == peek_mod.APP) is double
        assert (pw._state == peek_mod.PEEK) is (not double)

    def test_the_tick_wrap_is_handled(self, monkeypatch):
        """`KBDLLHOOKSTRUCT.time` wraps every 49.7 days: t2 < t1."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 0xFFFFFF00)
        pw._handle(("release",))
        self._press(pw, 0x00000064)  # 0x164 = 356 ms later
        assert pw._state == peek_mod.APP

    def test_a_tick_wrap_far_apart_is_not_a_double_tap(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 0xFFFFFF00)
        pw._handle(("release",))
        self._press(pw, 0x00001000)  # 0x1100 = 4352 ms later
        assert pw._state == peek_mod.PEEK

    def test_without_app_mode_a_double_tap_opens_the_browser(self,
                                                             monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        a.has_app_mode = False
        opened = []
        monkeypatch.setattr(peek_mod._tray, "_open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        self._press(pw, 1000)
        self._press(pw, 1100)
        assert opened == ["http://127.0.0.1:4915/signed"]
        assert pw._state == peek_mod.HIDDEN  # the peek ended first
        assert not any(c[0] == "apply_app" for c in a.calls)


class TestFailedTransitions:
    """A window call that raises or times out never strands the state
    machine: an exit from PEEK always clears `_peek_showing` (otherwise the
    filter swallows every Esc), and a timed-out call never replaces the
    saved placement or advances the state.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fixes 4 and 6
    """

    def _peek(self, monkeypatch, fg=OTHER_APP):
        from power_atlas import web as web_mod
        pw = _new_peek(monkeypatch)
        pw._adapter = _FakeAdapter(fg=fg)
        pw._ready.set()
        pw._signed_gen = web_mod.local_secret_generation()
        return pw, pw._adapter

    @staticmethod
    def _raise(exc):
        def f(*args, **kwargs):
            raise exc
        return f

    def test_a_raising_re_place_on_end_peek_falls_back_to_hidden(
            self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        pw._handle(("press", "peek", 1000))
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")
        a.calls.clear()
        monkeypatch.setattr(a, "apply_app",
                            self._raise(OSError(1400, "Invalid window handle")))
        with pytest.raises(OSError):
            pw._handle(("release",))
        assert pw._peek_showing is False
        assert (pw._state, pw._return_to) == (peek_mod.HIDDEN, None)
        assert ("hide",) in a.calls, "best-effort hide"

    def test_a_raising_reset_on_end_peek_still_hides(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        a.calls.clear()
        monkeypatch.setattr(a, "fire_reset_overlays",
                            self._raise(RuntimeError("no form")))
        with pytest.raises(RuntimeError):
            pw._handle(("esc",))
        assert pw._peek_showing is False
        assert pw._state == peek_mod.HIDDEN
        assert ("hide",) in a.calls

    def test_a_raising_hide_on_double_tap_from_a_peek_clears_the_peek(
            self, monkeypatch):
        """Double-tap from a foreground APP: the second press arrives in
        PEEK("app") and goes to HIDDEN through `_to_hidden`."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        pw._handle(("press", "peek", 1000))
        monkeypatch.setattr(a, "hide", self._raise(OSError(5, "denied")))
        with pytest.raises(OSError):
            pw._handle(("press", "peek", 1100))
        assert pw._peek_showing is False
        assert pw._state == peek_mod.HIDDEN

    def test_a_raising_hide_from_app_keeps_app(self, monkeypatch):
        """Not an exit from PEEK: the window is still in app mode."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        monkeypatch.setattr(a, "hide", self._raise(OSError(5, "denied")))
        with pytest.raises(OSError):
            pw._handle(("user_close",))
        assert pw._state == peek_mod.APP
        assert pw._peek_showing is False

    def test_after_a_failed_end_peek_esc_reaches_the_users_app(
            self, monkeypatch):
        """The user-visible symptom: Esc swallowed system-wide."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._listener = _Listener()
        pw._handle(("press", "peek", 1000))
        monkeypatch.setattr(a, "hide", self._raise(OSError(5, "denied")))
        with pytest.raises(OSError):
            pw._handle(("esc",))
        pw._win32_event_filter(peek_mod._WM_KEYDOWN, _KbData(0x1B))
        assert pw._listener.suppressed == 0

    def test_a_timed_out_placement_read_keeps_the_saved_one(self,
                                                            monkeypatch):
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        a.live = kept = _Placement("user-resized")
        pw._handle(("user_close",))  # save: reads `kept`
        assert pw._app_placement is kept
        pw._handle(("show_app",))
        monkeypatch.setattr(a, "get_placement", lambda: None)  # timed out
        pw._handle(("press", "peek", 9000))
        assert pw._app_placement is kept

    def test_a_timed_out_re_read_in_app_keeps_the_saved_one(self,
                                                            monkeypatch):
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        kept = pw._app_placement
        monkeypatch.setattr(a, "get_placement", lambda: None)
        a.calls.clear()
        pw._handle(("show_app",))
        assert ("apply_app", kept, True) in a.calls
        assert pw._app_placement is kept

    def test_a_timed_out_app_show_from_hidden_changes_nothing(self,
                                                              monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        before = pw._app_placement = _Placement("saved-earlier")

        def timed_out(placement, focused):
            a.calls.append(("apply_app", placement, focused))
            return None

        monkeypatch.setattr(a, "apply_app", timed_out)
        pw._handle(("show_app",))
        assert pw._state == peek_mod.HIDDEN
        assert pw._app_placement is before
        assert ("focus",) not in a.calls

    def test_a_timed_out_app_show_in_app_stays_app(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("show_app",))
        kept = pw._app_placement
        monkeypatch.setattr(a, "get_placement", lambda: None)
        monkeypatch.setattr(a, "apply_app", lambda p, focused: None)
        a.calls.clear()
        pw._handle(("show_app",))
        assert pw._state == peek_mod.APP
        assert pw._app_placement is kept
        assert ("focus",) not in a.calls

    def test_a_timed_out_app_show_from_a_peek_clears_the_peek(self,
                                                              monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        monkeypatch.setattr(a, "apply_app", lambda p, focused: None)
        pw._handle(("show_app",))
        assert pw._peek_showing is False
        assert pw._state == peek_mod.HIDDEN
        assert pw._app_placement is None


class TestWindowWorker:
    """The worker thread: readiness, dropped events, error containment.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def _run(self, pw, timeout=5):
        import threading
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        t.join(timeout)
        assert not t.is_alive(), "the worker did not stop"

    def test_events_before_ready_are_dropped(self, monkeypatch):
        # `_establish_ready` returns the adapter; the worker sets `_ready`
        # after the drain (review fix 8), so the fake no longer sets it.
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: a)
        pw._events.put(("press", "peek", 1000))  # before readiness
        pw._events.put(("show_app",))
        pw._events.put(("stop",))
        self._run(pw)
        assert a.calls == []

    def test_ready_is_set_and_logged_after_a_successful_gate(self,
                                                             monkeypatch,
                                                             caplog):
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: a)
        import threading
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            t = threading.Thread(target=pw._window_worker, daemon=True)
            t.start()
            assert pw._ready.wait(5)
            pw._events.put(("stop",))
            t.join(5)
        assert not t.is_alive()
        assert pw._adapter is a
        assert "PowerAtlas window ready" in caplog.text

    def test_a_tray_open_right_at_readiness_is_not_dropped(self,
                                                           monkeypatch):
        """Tray Open can post only once `_ready` is set. A click landing
        right at readiness must be handled, never drained with the stale
        pre-ready events (review fix 8)."""
        import threading
        from power_atlas import web as web_mod
        pw = _new_peek(monkeypatch)
        pw._signed_gen = web_mod.local_secret_generation()
        a = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: a)
        handled = threading.Event()

        class _ClickOnReady(threading.Event):
            def set(inner):
                super().set()
                # The tray click: posted the moment `_ready` is visible.
                pw._events.put(("show_app",))
                pw._events.put(("stop",))

        pw._ready = _ClickOnReady()
        orig_focus = a.focus

        def focus():
            orig_focus()
            handled.set()

        a.focus = focus
        self._run(pw)
        assert handled.is_set(), "the tray click was dropped"

    def test_events_are_dropped_while_never_ready(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        a = pw._adapter = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: None)
        import threading
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        pw._events.put(("show_app",))
        pw._events.put(("stop",))
        t.join(5)
        assert not t.is_alive()
        assert a.calls == []

    def test_show_app_falls_back_to_the_browser_before_ready(self,
                                                             monkeypatch):
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod._tray, "_open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        assert pw.supports_app_mode is False
        pw.show_app()
        assert opened == ["http://127.0.0.1:4915/signed"]
        assert _drain(pw) == []

    def test_ready_is_not_set_when_native_is_none(self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "win32")
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.native = None
        win.events.shown.wait.return_value = True
        pw._window = win
        pw._window_created.set()
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            pw._establish_ready()
        assert not pw._ready.is_set()
        assert pw.supports_app_mode is False
        assert "PowerAtlas window ready" not in caplog.text

    def test_ready_is_not_set_when_the_form_is_never_shown(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_READY_TIMEOUT", 0.01)
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.events.shown.wait.return_value = False
        pw._window = win
        pw._window_created.set()
        pw._establish_ready()
        assert not pw._ready.is_set()

    def test_an_adapter_error_is_logged_and_the_worker_goes_on(
            self, monkeypatch, caplog):
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()

        def broken():
            raise OSError(1400, "Invalid window handle")

        monkeypatch.setattr(a, "show_peek", broken)

        def ready():
            from power_atlas import web as web_mod
            pw._signed_gen = web_mod.local_secret_generation()
            return a

        monkeypatch.setattr(pw, "_establish_ready", ready)
        import threading
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        import time as _t
        _t.sleep(0.05)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw._events.put(("press", "peek", 1000))
            pw._events.put(("show_app",))
            pw._events.put(("stop",))
            t.join(5)
        assert not t.is_alive()
        assert "press failed: OSError" in caplog.text
        assert ("focus",) in a.calls, "the next event still ran"


class TestResetOverlaysAndBrowserErrors:
    """Replaces `TestHideCallsResetOverlays`: `resetOverlays` is fired by the
    adapter without waiting, only on PEEK → HIDDEN (D-11), and a failing door
    never escapes or logs its URL. The per-show `load_url` is gone (D-13).
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def test_portable_reset_overlays_runs_off_the_worker(self, monkeypatch):
        import threading
        import power_atlas.peek as peek_mod
        win = MagicMock()
        ran = threading.Event()
        win.evaluate_js.side_effect = lambda js: ran.set()
        adapter = peek_mod._PortableWindow(None, win)
        adapter.fire_reset_overlays()
        assert ran.wait(5)
        win.evaluate_js.assert_called_once_with(
            "if(typeof resetOverlays==='function') resetOverlays()")

    def test_portable_reset_overlays_error_does_not_propagate(self):
        import power_atlas.peek as peek_mod
        win = MagicMock()
        win.evaluate_js.side_effect = RuntimeError("webview gone")
        peek_mod._PortableWindow(None, win).fire_reset_overlays()  # no raise

    def test_portable_peek_toggles_fullscreen_once_each_way(self):
        import power_atlas.peek as peek_mod
        win = MagicMock()
        a = peek_mod._PortableWindow(None, win)
        a.show_peek()
        a.hide()
        assert win.toggle_fullscreen.call_count == 2
        win.show.assert_called_once()
        win.hide.assert_called_once()

    def test_a_failing_sign_in_reload_does_not_propagate(self, monkeypatch,
                                                          caplog):
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        a = pw._adapter = _FakeAdapter()
        pw._ready.set()
        pw._signed_gen = -1  # differs: a reload is due
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")

        def broken(url):
            raise RuntimeError("webview gone " + url)

        monkeypatch.setattr(a, "reload", broken)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw._handle(("press", "peek", 1000))
        assert ("show_peek",) in a.calls, "the peek still showed"
        assert "could not sign in again" in caplog.text
        assert "/signed" not in caplog.text
        assert pw._signed_gen == -1, "retried on the next show"

    def test_double_tap_browser_exception_does_not_propagate(self, monkeypatch,
                                                              caplog):
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        a = pw._adapter = _FakeAdapter()
        a.has_app_mode = False
        pw._ready.set()
        from power_atlas import web as web_mod
        pw._signed_gen = web_mod.local_secret_generation()
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")

        def no_browser(url):
            raise RuntimeError("no default browser")

        monkeypatch.setattr(peek_mod._tray, "_open_in_browser", no_browser)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw._handle(("press", "peek", 1000))
            pw._handle(("press", "peek", 1100))  # must not raise
        assert "could not open the browser" in caplog.text
        assert "/signed" not in caplog.text


class TestSignInGeneration:
    """D-13: the window reloads through a fresh login code only after a
    local-secret rotation, on any event ending in PEEK or APP.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def _peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        pw = _new_peek(monkeypatch)
        a = pw._adapter = _FakeAdapter()
        pw._ready.set()
        pw._signed_gen = web_mod.local_secret_generation()
        minted = []

        def fake_login_url(u):
            minted.append(u)
            return f"{u}/signed/{len(minted)}"

        monkeypatch.setattr(peek_mod, "_login_url", fake_login_url)
        return pw, a, web_mod, minted

    def test_no_rotation_no_reload(self, monkeypatch):
        pw, a, _, minted = self._peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        pw._handle(("release",))
        pw._handle(("show_app",))
        assert not any(c[0] == "reload" for c in a.calls)
        assert minted == []

    def test_a_rotation_reloads_exactly_once(self, monkeypatch):
        pw, a, web_mod, minted = self._peek(monkeypatch)
        monkeypatch.setattr(web_mod, "_local_secret_generation",
                            web_mod.local_secret_generation() + 1)
        pw._handle(("press", "peek", 1000))
        pw._handle(("release",))
        pw._handle(("press", "peek", 5000))
        reloads = [c for c in a.calls if c[0] == "reload"]
        assert reloads == [("reload", "http://127.0.0.1:4915/signed/1")]
        # Reloaded before the window showed.
        assert a.names().index("reload") < a.names().index("show_peek")

    def test_tray_open_while_in_app_checks_too(self, monkeypatch):
        pw, a, web_mod, minted = self._peek(monkeypatch)
        pw._handle(("show_app",))
        monkeypatch.setattr(web_mod, "_local_secret_generation",
                            web_mod.local_secret_generation() + 1)
        pw._handle(("show_app",))
        assert [c[0] for c in a.calls].count("reload") == 1

    def test_ending_a_peek_does_not_check(self, monkeypatch):
        """Only events that end in PEEK or APP sign in; a hide does not."""
        pw, a, web_mod, minted = self._peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        monkeypatch.setattr(web_mod, "_local_secret_generation",
                            web_mod.local_secret_generation() + 1)
        pw._handle(("release",))
        assert minted == []

    def test_ending_a_peek_back_to_app_checks(self, monkeypatch):
        """End peek → APP ends in APP, so it signs in (D-13, review fix 1):
        a rotation during a peek over APP reloads exactly once, before the
        window is re-placed."""
        pw, a, web_mod, minted = self._peek(monkeypatch)
        pw._handle(("show_app",))
        pw._handle(("press", "peek", 1000))
        a.calls.clear()
        monkeypatch.setattr(web_mod, "_local_secret_generation",
                            web_mod.local_secret_generation() + 1)
        pw._handle(("release",))
        reloads = [c for c in a.calls if c[0] == "reload"]
        assert reloads == [("reload", "http://127.0.0.1:4915/signed/1")]
        assert a.names().index("reload") < a.names().index("apply_app")
        # Signed in: the next event does not reload again.
        pw._handle(("press", "peek", 5000))
        pw._handle(("release",))
        assert [c[0] for c in a.calls].count("reload") == 1


class _CloseArgs:
    def __init__(self, reason):
        self._reason = reason
        self.Cancel = False

    @property
    def CloseReason(self):
        if isinstance(self._reason, Exception):
            raise self._reason
        return self._reason


class TestFormClosing:
    """D-22: X hides; Windows shutdown and `stop()` go through; fail closed.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def test_user_closing_cancels_and_posts(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        args = _CloseArgs("UserClosing")
        pw._on_form_closing(None, args)
        assert args.Cancel is True
        assert _drain(pw) == [("user_close",)]

    @pytest.mark.parametrize("reason", ["WindowsShutDown",
                                        "TaskManagerClosing",
                                        "ApplicationExitCall"])
    def test_other_reasons_go_through(self, monkeypatch, reason):
        pw = _new_peek(monkeypatch)
        args = _CloseArgs(reason)
        pw._on_form_closing(None, args)
        assert args.Cancel is False
        assert _drain(pw) == []

    def test_stopping_lets_everything_through(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        pw._stopping = True
        for reason in ("UserClosing", RuntimeError("boom")):
            args = _CloseArgs(reason)
            pw._on_form_closing(None, args)
            assert args.Cancel is False
        assert _drain(pw) == []

    def test_an_exception_fails_closed(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        args = _CloseArgs(RuntimeError("boom"))
        pw._on_form_closing(None, args)
        assert args.Cancel is True


class TestStop:
    """`stop()` is idempotent and never waits on the worker.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    def test_a_second_stop_does_not_destroy_again(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        pw._window = MagicMock()
        listener = pw._listener = MagicMock()
        pw.stop()
        pw.stop()
        pw._window.destroy.assert_called_once()
        listener.stop.assert_called_once()
        assert _drain(pw) == [("stop",)]

    def test_a_hung_destroy_runs_the_shutdown_tail(self, monkeypatch):
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_DESTROY_TIMEOUT", 0.05)
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 0.05)
        pw = _new_peek(monkeypatch)
        release = threading.Event()
        pw._window = MagicMock()
        pw._window.native = None
        pw._window.destroy.side_effect = lambda: release.wait(5)
        tail = threading.Event()
        pw.shutdown_tail = tail.set
        pw.stop()
        try:
            assert tail.wait(5), "the watchdog never ran the tail"
        finally:
            release.set()

    def test_no_tail_when_the_ui_loop_ends(self, monkeypatch):
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_DESTROY_TIMEOUT", 0.05)
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 0.2)
        pw = _new_peek(monkeypatch)
        release = threading.Event()
        pw._window = MagicMock()
        pw._window.native = None
        pw._window.destroy.side_effect = lambda: release.wait(5)
        tail = threading.Event()
        pw.shutdown_tail = tail.set
        pw.stop()
        pw._start_returned.set()  # `webview.start()` returned in time
        assert not tail.wait(0.5)
        release.set()


class TestShowCmd:
    """Re-applying a saved `WINDOWPLACEMENT` (the plan's window primitives).
    Values from the Win32 `ShowWindow` constants.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    @pytest.mark.parametrize("show_cmd, flags, focused, expected", [
        (1, 0, True, 1),   # normal → SW_SHOWNORMAL
        (3, 0, True, 3),   # maximized → SW_SHOWMAXIMIZED
        (2, 2, True, 3),   # minimized, restore-to-maximized → maximized
        (2, 0, True, 1),   # minimized → restored normal
        (1, 0, False, 4),  # → SW_SHOWNOACTIVATE
        (2, 0, False, 7),  # → SW_SHOWMINNOACTIVE
        (2, 2, False, 7),  # stays minimized without activating
        (3, 0, False, 3),  # no non-activating maximize; kept
    ])
    def test_show_cmd(self, show_cmd, flags, focused, expected):
        import power_atlas.peek as peek_mod
        assert peek_mod._show_cmd_for(show_cmd, flags, focused) == expected


class TestPywebviewLoggerClamp:
    """F6. pywebview at DEBUG logs every URL it loads, and peek's carry a live
    login code; its logger is held at INFO or above.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review"""

    @pytest.fixture
    def pywebview_logger(self):
        logger = logging.getLogger("pywebview")
        before = logger.level
        yield logger
        logger.setLevel(before)

    @pytest.mark.parametrize("level", [logging.NOTSET, logging.DEBUG])
    def test_a_level_below_info_is_raised_to_info(self, pywebview_logger, level):
        import power_atlas.peek as peek_mod
        pywebview_logger.setLevel(level)
        peek_mod._clamp_pywebview_logger()
        assert pywebview_logger.level == logging.INFO

    def test_a_stricter_level_is_left_alone(self, pywebview_logger):
        import power_atlas.peek as peek_mod
        pywebview_logger.setLevel(logging.WARNING)
        peek_mod._clamp_pywebview_logger()
        assert pywebview_logger.level == logging.WARNING

    def test_the_clamp_runs_when_peek_is_imported(self, pywebview_logger,
                                                  monkeypatch):
        """Where pywebview applies `PYWEBVIEW_LOG`: at its own import, which
        peek's module body triggers. Re-executed with the env var at DEBUG."""
        import importlib
        import power_atlas.peek as peek_mod
        if not peek_mod.is_available():
            pytest.skip("pywebview is not installed")
        monkeypatch.setenv("PYWEBVIEW_LOG", "DEBUG")
        pywebview_logger.setLevel(logging.DEBUG)
        importlib.reload(peek_mod)
        assert pywebview_logger.level == logging.INFO


# ---- review fixes: Win32 adapter plumbing, stop(), the shutdown tail -------
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fixes


def _fake_dotnet(monkeypatch):
    """Stand-ins for pythonnet's `System` and `System.Windows.Forms`:
    `Action` is the identity, so `BeginInvoke` receives the callable."""
    import types
    system = types.ModuleType("System")
    system.Action = lambda f: f
    windows = types.ModuleType("System.Windows")
    forms = types.ModuleType("System.Windows.Forms")
    forms.Application = types.SimpleNamespace(Exit=object())
    windows.Forms = forms
    system.Windows = windows
    monkeypatch.setitem(sys.modules, "System", system)
    monkeypatch.setitem(sys.modules, "System.Windows", windows)
    monkeypatch.setitem(sys.modules, "System.Windows.Forms", forms)
    return forms


class _FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeNative:
    """A form whose UI thread drops the first `drop` callables (a busy UI
    thread: they are kept, and run later by hand) and runs the rest."""

    HWND = 0x5151

    def __init__(self, drop=0):
        import types
        self.drop = drop
        self.dropped = []
        self.IsHandleCreated = True
        self.FormClosing = _FakeEvent()
        self.Handle = types.SimpleNamespace(ToInt64=lambda: self.HWND)

    def BeginInvoke(self, action):
        if len(self.dropped) < self.drop:
            self.dropped.append(action)
            return
        action()


class _FakeUser32:
    def __init__(self):
        self.calls = []

    def IsWindow(self, h):
        self.calls.append(("IsWindow", h))
        return True

    def SetForegroundWindow(self, h):
        self.calls.append(("SetForegroundWindow", h))
        return True


class TestWin32Plumbing:
    def _adapter(self, monkeypatch, native):
        import types
        import power_atlas.peek as peek_mod
        _fake_dotnet(monkeypatch)
        pw = _new_peek(monkeypatch)
        win = types.SimpleNamespace(native=native)
        return pw, peek_mod._Win32Window(pw, win)

    def test_ui_gives_up_on_a_ui_thread_that_never_runs(self, monkeypatch,
                                                        caplog):
        """Fix 7: the bounded wait. A wait without a timeout would hang
        here and fail the join."""
        import threading
        import time as _t
        import power_atlas.peek as peek_mod
        native = _FakeNative(drop=1)
        pw, a = self._adapter(monkeypatch, native)
        a._native = native
        box = {}

        def call():
            t0 = _t.monotonic()
            box["v"] = a._ui(lambda: "ran", "the probe operation")
            box["dt"] = _t.monotonic() - t0

        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            t = threading.Thread(target=call, daemon=True)
            t.start()
            t.join(peek_mod._UI_TIMEOUT + 3)
        assert not t.is_alive(), "the wait had no timeout"
        assert box["v"] is None
        assert peek_mod._UI_TIMEOUT - 0.1 <= box["dt"] < peek_mod._UI_TIMEOUT + 1
        assert "did not run the probe operation" in caplog.text

    def test_attach_retries_a_busy_ui_thread_and_subscribes_once(
            self, monkeypatch):
        """Fix 5: the first two attempts time out; the third answers. The
        two late callables then run and must not subscribe again."""
        import time as _t
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        native = _FakeNative(drop=2)
        pw, a = self._adapter(monkeypatch, native)
        assert a.attach(_t.monotonic() + 5) == "ready"
        assert pw._hwnd == _FakeNative.HWND
        assert len(native.dropped) == 2
        for late in native.dropped:
            late()
        assert native.FormClosing.handlers == [pw._on_form_closing]

    def test_attach_without_a_form_is_not_a_timeout(self, monkeypatch):
        import time as _t
        pw, a = self._adapter(monkeypatch, None)
        assert a.attach(_t.monotonic() + 5) == "no_form"
        native = _FakeNative()
        native.IsHandleCreated = False
        pw, a = self._adapter(monkeypatch, native)
        assert a.attach(_t.monotonic() + 5) == "no_form"
        assert native.FormClosing.handlers == []

    def test_a_readiness_timeout_is_logged_distinctly(self, monkeypatch,
                                                      caplog):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "win32")
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        monkeypatch.setattr(peek_mod, "_READY_TIMEOUT", 0.3)
        _fake_dotnet(monkeypatch)
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.native = _FakeNative(drop=10_000)
        win.events.shown.wait.return_value = True
        pw._window = win
        pw._window_created.set()
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            assert pw._establish_ready() is None
        assert "did not answer the readiness check" in caplog.text
        assert "no native form" not in caplog.text
        assert len(win.native.dropped) >= 2, "it retried"
        # Quiet retries: one WARNING for the gate, not one per attempt.
        assert "did not run the readiness check" not in caplog.text

    def test_restore_foreground_runs_on_the_ui_thread(self, monkeypatch):
        """Fix 3: no Win32 call from the worker; the work is the callable."""
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        u = _FakeUser32()
        monkeypatch.setattr(peek_mod, "_win32", lambda: (u, None))
        native = _FakeNative(drop=1)
        pw, a = self._adapter(monkeypatch, native)
        a._native = native
        pw._hwnd = _FakeNative.HWND
        a.restore_foreground(OTHER_APP, True)
        assert u.calls == [], "called on the worker"
        native.dropped[0]()
        assert u.calls == [("IsWindow", OTHER_APP),
                           ("SetForegroundWindow", OTHER_APP)]

    @pytest.mark.parametrize("prev, pa_fg", [(OTHER_APP, False),
                                             (None, True)])
    def test_restore_foreground_without_cause_posts_nothing(
            self, monkeypatch, prev, pa_fg):
        import power_atlas.peek as peek_mod
        u = _FakeUser32()
        monkeypatch.setattr(peek_mod, "_win32", lambda: (u, None))
        native = _FakeNative(drop=1)
        pw, a = self._adapter(monkeypatch, native)
        a._native = native
        a.restore_foreground(prev, pa_fg)
        assert native.dropped == [] and u.calls == []

    def test_restore_foreground_never_targets_itself(self, monkeypatch):
        import power_atlas.peek as peek_mod
        u = _FakeUser32()
        monkeypatch.setattr(peek_mod, "_win32", lambda: (u, None))
        native = _FakeNative()
        pw, a = self._adapter(monkeypatch, native)
        a._native = native
        pw._hwnd = _FakeNative.HWND
        a.restore_foreground(_FakeNative.HWND, True)
        assert u.calls == []

    def test_a_hung_reload_is_bounded(self, monkeypatch, caplog):
        """Fix 10: `load_url` is a synchronous `Form.Invoke`."""
        import threading
        import time as _t
        import types
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.1)
        release = threading.Event()
        win = types.SimpleNamespace(load_url=lambda url: release.wait(5))
        a = peek_mod._Win32Window(None, win)
        url = "http://127.0.0.1:4915/signed/SECRET-CODE"
        t0 = _t.monotonic()
        try:
            with caplog.at_level(logging.WARNING, logger="power_atlas"):
                a.reload(url)
            assert _t.monotonic() - t0 < 2
            assert "did not load the sign-in page" in caplog.text
            assert "SECRET-CODE" not in caplog.text
        finally:
            release.set()

    def test_a_failing_reload_still_raises(self, monkeypatch):
        import types
        import power_atlas.peek as peek_mod

        def broken(url):
            raise RuntimeError("webview gone")

        a = peek_mod._Win32Window(None, types.SimpleNamespace(load_url=broken))
        with pytest.raises(RuntimeError):
            a.reload("http://127.0.0.1:4915/signed/1")

    def test_a_hung_destroy_posts_the_ui_loop_exit(self, monkeypatch):
        """Fix 7: the `Application.Exit` branch of `stop()`."""
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_DESTROY_TIMEOUT", 0.05)
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 5)
        forms = _fake_dotnet(monkeypatch)
        pw = _new_peek(monkeypatch)
        release = threading.Event()
        pw._window = MagicMock()
        pw._window.destroy.side_effect = lambda: release.wait(5)
        try:
            pw.stop()
            pw._window.native.BeginInvoke.assert_called_once_with(
                forms.Application.Exit)
        finally:
            pw._start_returned.set()  # the loop exited: no shutdown tail
            release.set()

    def test_a_prompt_destroy_posts_no_exit(self, monkeypatch):
        _fake_dotnet(monkeypatch)
        pw = _new_peek(monkeypatch)
        pw._window = MagicMock()
        pw.stop()
        pw._window.native.BeginInvoke.assert_not_called()


class TestReadinessOffWindows:
    """Fix 13: peek off Windows does not depend on `events.shown`."""

    def test_a_missing_shown_still_gives_a_portable_window(self, monkeypatch,
                                                           caplog):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        monkeypatch.setattr(peek_mod, "_READY_TIMEOUT", 0.01)
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.events.shown.wait.return_value = False
        pw._window = win
        pw._window_created.set()
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            adapter = pw._establish_ready()
        assert isinstance(adapter, peek_mod._PortableWindow)
        assert "peek goes on regardless" in caplog.text

    def test_a_missing_shown_on_windows_gives_nothing(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "win32")
        monkeypatch.setattr(peek_mod, "_READY_TIMEOUT", 0.01)
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.events.shown.wait.return_value = False
        pw._window = win
        pw._window_created.set()
        assert pw._establish_ready() is None


class TestShutdownTailOnce:
    """Fix 11: `__main__._run_once_or_wait` — the second caller waits for
    the first, bounded, instead of returning at once."""

    def test_runs_once_and_a_second_caller_waits(self):
        import threading
        from power_atlas import __main__ as main_mod
        gate = threading.Event()
        started = threading.Event()
        runs = []

        def body():
            runs.append(1)
            started.set()
            gate.wait(5)

        tail = main_mod._run_once_or_wait(body, wait=5)
        t1 = threading.Thread(target=tail, daemon=True)
        t1.start()
        assert started.wait(5)
        t2 = threading.Thread(target=tail, daemon=True)
        t2.start()
        t2.join(0.3)
        assert t2.is_alive(), "the second caller returned before the first ended"
        gate.set()
        t2.join(5)
        t1.join(5)
        assert not t2.is_alive() and not t1.is_alive()
        assert runs == [1]
        tail()  # after the run: returns at once, never runs again
        assert runs == [1]

    def test_the_second_callers_wait_is_bounded(self):
        import threading
        import time as _t
        from power_atlas import __main__ as main_mod
        gate = threading.Event()
        started = threading.Event()

        def body():
            started.set()
            gate.wait(10)

        tail = main_mod._run_once_or_wait(body, wait=0.2)
        threading.Thread(target=tail, daemon=True).start()
        assert started.wait(5)
        t0 = _t.monotonic()
        tail()
        try:
            assert 0.15 <= _t.monotonic() - t0 < 2
        finally:
            gate.set()

    def test_a_failing_first_run_releases_the_waiters(self):
        import threading
        from power_atlas import __main__ as main_mod
        started = threading.Event()
        proceed = threading.Event()

        def body():
            started.set()
            proceed.wait(5)
            raise RuntimeError("tail failed")

        tail = main_mod._run_once_or_wait(body, wait=10)
        errors = []

        def first():
            try:
                tail()
            except RuntimeError as e:
                errors.append(e)

        t1 = threading.Thread(target=first, daemon=True)
        t1.start()
        assert started.wait(5)
        t2 = threading.Thread(target=tail, daemon=True)
        t2.start()
        proceed.set()
        t2.join(3)
        assert not t2.is_alive()
        t1.join(3)
        assert len(errors) == 1
