"""Tests for power_atlas.peek — works without pywebview/pynput installed."""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_real_keyboard(monkeypatch):
    """No test reads the real keyboard or types into the desktop: the
    filter's modifier check sees every tracked modifier as down, and the
    mask key is recorded, not sent. Tests that check either override these.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-ups 10, 14)
    """
    import power_atlas.peek as peek_mod
    sent = []
    monkeypatch.setattr(peek_mod, "_modifier_down", lambda name: True,
                        raising=False)
    monkeypatch.setattr(peek_mod, "_send_mask_key", lambda: sent.append(1),
                        raising=False)
    return sent


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

        def mock_init(self, server_url, hotkey="ctrl+shift+z", mode="hold",
                      browser_hotkey=""):
            captured_args["server_url"] = server_url
            captured_args["hotkey"] = hotkey
            captured_args["mode"] = mode
            # Minimal init to avoid real webview/pynput usage
            self._server_url = server_url
            self._hotkey = hotkey
            self._mode = mode

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000", "nope")
        assert result is not None
        assert captured_args["hotkey"] == "ctrl+shift+z"

    def test_invalid_hotkey_only_modifier(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        captured_args = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z", mode="hold",
                      browser_hotkey=""):
            captured_args["server_url"] = server_url
            captured_args["hotkey"] = hotkey
            captured_args["mode"] = mode
            # Minimal init to avoid real webview/pynput usage
            self._server_url = server_url
            self._hotkey = hotkey
            self._mode = mode

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000", "ctrl+shift")
        assert result is not None
        assert captured_args["hotkey"] == "ctrl+shift+z"

    def test_valid_hotkey(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        captured_args = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z", mode="hold",
                      browser_hotkey=""):
            captured_args["server_url"] = server_url
            captured_args["hotkey"] = hotkey
            captured_args["mode"] = mode
            # Minimal init to avoid real webview/pynput usage
            self._server_url = server_url
            self._hotkey = hotkey
            self._mode = mode

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000", "ctrl+alt+p")
        assert result is not None
        assert isinstance(result, peek_mod.PeekWindow)
        assert captured_args["server_url"] == "http://localhost:8000"
        assert captured_args["hotkey"] == "ctrl+alt+p"

    def test_exception_in_init_returns_none(self, monkeypatch):
        import power_atlas.peek as peek_mod

        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)

        def mock_init(self, server_url, hotkey="ctrl+shift+z", mode="hold",
                      browser_hotkey=""):
            raise RuntimeError("Something broke")

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)

        result = peek_mod.create_peek("http://localhost:8000")
        assert result is None

    # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2:
    # `create_peek(server_url, hotkey, mode)`; an unknown mode warns and holds.

    def _capture(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        captured = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z", mode="hold",
                      browser_hotkey=""):
            captured["mode"] = mode
            captured["hotkey"] = hotkey

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)
        return peek_mod, captured

    def test_mode_defaults_to_hold(self, monkeypatch):
        peek_mod, captured = self._capture(monkeypatch)
        assert peek_mod.create_peek("http://localhost:8000", "ctrl+alt+p")
        assert captured["mode"] == "hold"

    def test_toggle_mode_is_passed_through(self, monkeypatch, caplog):
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            assert peek_mod.create_peek("http://localhost:8000", "ctrl+alt+p",
                                        "toggle")
        assert captured == {"mode": "toggle", "hotkey": "ctrl+alt+p"}
        assert not [r for r in caplog.records if "peek_mode" in r.getMessage()]

    def test_a_hand_edited_mode_is_normalised(self, monkeypatch):
        peek_mod, captured = self._capture(monkeypatch)
        assert peek_mod.create_peek("http://localhost:8000", "ctrl+alt+p",
                                    " Toggle ")
        assert captured["mode"] == "toggle"

    @pytest.mark.parametrize("mode", ["x", "", "togle", 3])
    def test_an_unknown_mode_falls_back_to_hold_with_a_warning(
            self, monkeypatch, caplog, mode):
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            assert peek_mod.create_peek("http://localhost:8000", "ctrl+alt+p",
                                        mode)
        assert captured["mode"] == "hold"
        assert captured["hotkey"] == "ctrl+alt+p"  # the hotkey is untouched
        assert any("peek_mode" in r.getMessage() and r.levelname == "WARNING"
                   for r in caplog.records)

    def test_peek_window_keeps_its_mode(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        assert peek_mod.PeekWindow("http://x", "ctrl+shift+z")._mode == "hold"
        assert peek_mod.PeekWindow("http://x", "ctrl+shift+z",
                                   "toggle")._mode == "toggle"


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
        assert pw._triggered["peek"] is False  # per-chord since Phase 3 (D-18)

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
        assert pw._triggered["peek"] is False  # per-chord since Phase 3 (D-18)
        assert pw._pressed_keys == set()  # cleared on escape

    def test_partial_combo_does_not_trigger(self, monkeypatch):
        pw = self._make_peek(monkeypatch)
        pw._on_press(_make_key(name="ctrl_l"))
        pw._on_press(_make_key(char="z"))
        # Missing shift — should not trigger
        assert _drain(pw) == []
        assert pw._triggered["peek"] is False  # per-chord since Phase 3 (D-18)

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
    def __init__(self, vk, t=0, flags=0):
        self.vkCode = vk
        self.time = t
        self.flags = flags


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
        assert pw._triggered["peek"] is False  # per-chord since Phase 3 (D-18)
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
        # The window's `pa_local`: None (the jar cannot be read: unknown)
        # unless a test sets it, "" for a jar without one. `url` is the page
        # the jar was read for. Reads are counted apart from `calls`, which
        # tests compare whole.
        self.cookie = None
        self.url = "http://127.0.0.1:4915/acp"
        self.cookie_reads = 0
        self.reload_ok = True
        # `enable_browser_keys` calls, counted apart from `calls`; `keys_ok`
        # is what it returns (False: `CoreWebView2` not there yet).
        # Phase 5 (follow-up 5)
        self.browser_keys = 0
        self.keys_ok = True

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
        return self.reload_ok

    def enable_browser_keys(self):
        self.browser_keys += 1
        return self.keys_ok

    def read_cookies(self):
        from http.cookies import SimpleCookie
        self.cookie_reads += 1
        if self.cookie is None:
            return None
        other = SimpleCookie()
        other["other"] = "x"
        jar = [other]
        if self.cookie:
            c = SimpleCookie()
            c["pa_local"] = self.cookie
            jar.append(c)
        return jar, self.url

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


# Worker-event helpers, for tests written or touched since Phase 5: one place
# builds the event tuples, so a change of their shape is one edit.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 11)
def _press(pw, t):
    """A peek press at tick `t`, handled now on the calling thread."""
    pw._handle(("press", "peek", t))


def _post(pw, kind, *args):
    """Queue a worker event, as the hook, the tray or pywebview would."""
    pw._events.put((kind, *args))


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

    def test_user_close_while_hidden_hides(self, monkeypatch):
        """Final review fix 13 (was "does nothing"): a window shown late,
        after an app show's wait timed out, is visible while the state says
        HIDDEN. A user close hides it in every state."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        pw._handle(("user_close",))
        assert a.window_calls() == [("hide",)]
        assert pw._state == peek_mod.HIDDEN
        assert pw._peek_showing is False

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
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        self._press(pw, 1000)
        self._press(pw, 1100)
        assert opened == ["http://127.0.0.1:4915/signed"]
        assert pw._state == peek_mod.HIDDEN  # the peek ended first
        assert not any(c[0] == "apply_app" for c in a.calls)


class TestToggleStateMachine:
    """Every Toggle cell of the plan's state-machine table, plus the
    double-tap rule under Toggle, fed straight into the worker's handler.
    Expected calls come from the table and its Definitions ("end peek →
    HIDDEN", "end peek → APP", "→ APP, focused"), not from the code.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2
    """

    # What "end peek → HIDDEN" does when the peek never took the foreground.
    END_TO_HIDDEN = [("reset_overlays",), ("hide",),
                     ("restore_foreground", OTHER_APP, False)]

    def _peek(self, monkeypatch, fg=OTHER_APP):
        from power_atlas import web as web_mod
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        pw = peek_mod.PeekWindow("http://127.0.0.1:4915", "ctrl+shift+z",
                                 "toggle")
        pw._adapter = _FakeAdapter(fg=fg)
        pw._ready.set()
        pw._signed_gen = web_mod.local_secret_generation()
        return pw, pw._adapter

    @staticmethod
    def _press(pw, t):
        pw._handle(("press", "peek", t))

    def _in_app(self, pw, a, foreground=True):
        pw._handle(("show_app",))
        if not foreground:
            a.fg = OTHER_APP
        a.calls.clear()

    # -- Peek press, not a double-tap (Toggle) ------------------------------

    def test_press_from_hidden_shows_peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        assert a.window_calls() == [("show_peek",)]
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, None)
        assert pw._peek_showing is True

    def test_a_second_press_after_the_interval_ends_the_peek(self,
                                                             monkeypatch):
        """Press, then press 0.8 s later: the peek ends → HIDDEN, with
        `resetOverlays` (D-11). Hold would have ignored this press."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        self._press(pw, 1800)
        assert a.window_calls() == self.END_TO_HIDDEN
        assert (pw._state, pw._return_to) == (peek_mod.HIDDEN, None)
        assert pw._peek_showing is False

    def test_a_press_in_a_peek_over_app_returns_to_app(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        saved = a.live = _Placement("before-peek")
        self._press(pw, 1000)
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")
        a.calls.clear()
        self._press(pw, 1800)
        assert a.window_calls()[0] == ("apply_app", saved, False)
        assert ("reset_overlays",) not in a.calls
        assert (pw._state, pw._peek_showing) == (peek_mod.APP, False)

    def test_press_from_app_saves_then_peeks(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        a.live = _Placement("resized")
        self._press(pw, 1000)
        assert pw._app_placement is a.live
        assert a.window_calls() == [("show_peek",)]
        assert a.names().index("get_placement") < a.names().index("show_peek")
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")

    def test_presses_far_apart_cycle_peek_and_hidden(self, monkeypatch):
        """HIDDEN → PEEK → HIDDEN → PEEK → HIDDEN, one transition per press."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        states = []
        for t in (1000, 2000, 3000, 4000):
            self._press(pw, t)
            states.append(pw._state)
        assert states == [peek_mod.PEEK, peek_mod.HIDDEN,
                          peek_mod.PEEK, peek_mod.HIDDEN]

    # -- Modifier release (Toggle): a no-op everywhere ----------------------

    def test_release_in_a_peek_is_a_no_op(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("release",))
        assert a.calls == []
        assert (pw._state, pw._peek_showing) == (peek_mod.PEEK, True)
        # The peek still ends on the next press.
        self._press(pw, 1800)
        assert pw._state == peek_mod.HIDDEN

    def test_release_in_a_peek_over_app_is_a_no_op(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("release",))
        assert a.calls == []
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")

    @pytest.mark.parametrize("where", ["hidden", "app"])
    def test_release_outside_a_peek_is_a_no_op(self, monkeypatch, where):
        pw, a = self._peek(monkeypatch)
        if where == "app":
            self._in_app(pw, a)
        pw._handle(("release",))
        assert a.window_calls() == []

    # -- Esc and X still end a peek in Toggle -------------------------------

    def test_esc_ends_a_peek_to_hidden(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("esc",))
        assert a.window_calls() == self.END_TO_HIDDEN
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
        assert pw._state == peek_mod.APP

    def test_user_close_in_a_peek_is_esc(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("user_close",))
        assert a.window_calls() == self.END_TO_HIDDEN
        assert pw._state == peek_mod.HIDDEN

    def test_user_close_in_app_saves_and_hides(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a)
        pw._handle(("user_close",))
        assert a.window_calls() == [("hide",)]
        assert pw._state == peek_mod.HIDDEN

    def test_tray_open_from_a_peek_opens_app(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        pw._handle(("show_app",))
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert pw._state == peek_mod.APP

    # -- Double-tap rule under Toggle ---------------------------------------

    def test_press_press_from_hidden_opens_app(self, monkeypatch):
        """Press, press within 0.5 s from HIDDEN: the first shows a peek, the
        second is a double-tap and opens APP focused (not a toggle-off)."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        a.calls.clear()
        self._press(pw, 1200)
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert ("reset_overlays",) not in a.calls
        assert (pw._state, pw._peek_showing) == (peek_mod.APP, False)

    def test_press_press_from_a_peek_ends_it_then_opens_app(self,
                                                            monkeypatch):
        """From PEEK(None) the first press ends the peek and the second, within
        0.5 s, opens APP: the accepted flicker of the double-tap rule."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)  # PEEK(None)
        a.calls.clear()
        self._press(pw, 3000)  # ends it
        assert a.window_calls() == self.END_TO_HIDDEN
        assert pw._tap_origin == (peek_mod.PEEK, False)
        a.calls.clear()
        self._press(pw, 3200)  # double-tap
        assert a.window_calls() == [("apply_app", None, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_double_tap_from_a_foreground_app_hides_it(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=True)
        self._press(pw, 1000)
        assert pw._tap_origin == (peek_mod.APP, True)
        a.calls.clear()
        self._press(pw, 1100)
        assert a.window_calls() == [("hide",)]
        assert pw._state == peek_mod.HIDDEN

    def test_double_tap_from_a_background_app_focuses_it(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._in_app(pw, a, foreground=False)
        saved = a.live
        self._press(pw, 1000)
        a.calls.clear()
        self._press(pw, 1100)
        assert a.window_calls() == [("apply_app", saved, True), ("focus",)]
        assert pw._state == peek_mod.APP

    def test_a_third_tap_is_a_fresh_press(self, monkeypatch):
        """After a double-tap opened APP, a third quick press is a first
        press from APP: save, → PEEK("app")."""
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        self._press(pw, 1000)
        self._press(pw, 1200)
        a.calls.clear()
        self._press(pw, 1400)
        assert a.window_calls() == [("show_peek",)]
        assert (pw._state, pw._return_to) == (peek_mod.PEEK, "app")

    @pytest.mark.parametrize("gap, expected", [(499, "app"), (500, "hidden")])
    def test_the_double_tap_boundary(self, monkeypatch, gap, expected):
        """At 499 ms the second press is a double-tap (APP); at 500 ms it is
        an ordinary Toggle press, which ends the peek (HIDDEN)."""
        pw, a = self._peek(monkeypatch)
        self._press(pw, 7000)
        self._press(pw, 7000 + gap)
        assert pw._state == expected

    def test_without_app_mode_press_press_opens_the_browser(self,
                                                            monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = self._peek(monkeypatch)
        a.has_app_mode = False
        opened = []
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        self._press(pw, 1000)
        self._press(pw, 1100)
        assert opened == ["http://127.0.0.1:4915/signed"]
        assert pw._state == peek_mod.HIDDEN


class TestToggleHook:
    """Toggle at the hook: a held chord key's auto-repeat is never an event,
    or every repeat would flip the peek on and off.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2
    """

    def _peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        pw = peek_mod.PeekWindow("http://127.0.0.1:4915", "ctrl+shift+z",
                                 "toggle")
        pw._adapter = _FakeAdapter()
        return pw

    @staticmethod
    def _feed(pw, msg, vk, t):
        try:
            pw._win32_event_filter(msg, _KbData(vk, t))
        except _Suppress:
            return True
        return False

    def test_win32_auto_repeat_is_one_event(self, monkeypatch):
        pw = self._peek(monkeypatch)
        pw._listener = _Listener()
        pw._pressed_keys.update(("ctrl", "shift"))
        # Key-downs with no key-up between them: auto-repeat.
        suppressed = [self._feed(pw, 0x0100, 0x5A, t)
                      for t in (100, 130, 160, 190)]
        assert suppressed == [True] * 4  # every repeat is still swallowed
        assert _drain(pw) == [("press", "peek", 100)]
        # The key-up re-arms: the next key-down is a second press.
        self._feed(pw, 0x0101, 0x5A, 250)
        self._feed(pw, 0x0100, 0x5A, 900)
        assert _drain(pw) == [("press", "peek", 900)]

    def test_portable_auto_repeat_is_one_event(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        pw = self._peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z"), _make_key(char="z"),
                  _make_key(char="z")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["press"]

    def test_a_held_chord_keeps_its_peek(self, monkeypatch):
        """End to end: a held chord in Toggle shows one peek and keeps it,
        even with repeats more than 0.5 s apart."""
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        pw = self._peek(monkeypatch)
        pw._listener = _Listener()
        pw._ready.set()
        pw._signed_gen = web_mod.local_secret_generation()
        pw._pressed_keys.update(("ctrl", "shift"))
        for t in (100, 700, 1300):
            self._feed(pw, 0x0100, 0x5A, t)
        for ev in _drain(pw):
            pw._handle(ev)
        assert pw._state == peek_mod.PEEK


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
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
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
            # The message quotes the URL, as a real failure may (final
            # review fix 11): only the type may reach the log.
            raise RuntimeError("no default browser for " + url)

        monkeypatch.setattr(peek_mod._doors, "open_in_browser", no_browser)
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
        # Reloaded after the peek showed (final review cycle 2, fix 6: the
        # check no longer delays a peek).
        assert a.names().index("reload") > a.names().index("show_peek")

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


class _ShowUser32:
    """A user32 stand-in that records the calls the show primitives make.

    The window starts hidden, as it is after `create_window(hidden=True)`.
    """

    def __init__(self):
        self.calls = []
        self.visible = False

    def IsWindowVisible(self, h):
        return self.visible

    def ShowWindow(self, h, cmd):
        self.calls.append(("ShowWindow", h, cmd))
        self.visible = cmd != 0
        return True

    def GetWindowLongW(self, h, index):
        return 0

    def SetWindowLongW(self, h, index, value):
        self.calls.append(("SetWindowLongW", h, index, value))
        return 0

    def SetWindowPos(self, h, after, x, y, w, hh, flags):
        self.calls.append(("SetWindowPos", h, after, x, y, w, hh, flags))
        return True

    def IsIconic(self, h):
        return False

    def IsZoomed(self, h):
        return False

    def GetDpiForWindow(self, h):
        return 96

    def GetWindowPlacement(self, h, wp):
        return True

    def SetWindowPlacement(self, h, wp):
        self.calls.append(("SetWindowPlacement", h))
        return not self.placement_fails

    placement_fails = False

    def GetForegroundWindow(self):
        return 0


class TestWin32ShowPrimitives:
    """A window shown by `SetWindowPos(SWP_SHOWWINDOW)` alone stayed blank
    white: that flag sends no `WM_SHOWWINDOW`, so WinForms kept the form's
    `Visible` False and the WebView2 control never made its controller
    visible (pixel probe 2026-10-06: the first app show and a peek from
    hidden before any app show were white; every show through
    `ShowWindow` or `SetWindowPlacement` painted). Each show primitive
    therefore places the hidden window first, with no show flag, and then
    shows it with `ShowWindow`. Values from winuser.h: `SWP_SHOWWINDOW`
    0x40, `SWP_NOACTIVATE` 0x10, `SW_SHOW` 5, `SW_SHOWNA` 8,
    `SW_SHOWNORMAL` 1, `HWND_TOPMOST` -1.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS blank-window fix
    """

    SCREEN = (-1280, 40, 1920, 1080)       # asymmetric, off-origin bounds
    WORK = (-1280, 40, 1920, 1032)

    def _adapter(self, monkeypatch):
        import ctypes
        import types
        import power_atlas.peek as peek_mod

        class WP(ctypes.Structure):
            _fields_ = [("length", ctypes.c_uint), ("flags", ctypes.c_uint),
                        ("showCmd", ctypes.c_uint),
                        ("ptMinPosition", ctypes.c_long * 2),
                        ("ptMaxPosition", ctypes.c_long * 2),
                        ("rcNormalPosition", ctypes.c_long * 4)]

        forms = _fake_dotnet(monkeypatch)
        rect = lambda x, y, w, h: types.SimpleNamespace(X=x, Y=y, Width=w,
                                                        Height=h)
        forms.Screen = types.SimpleNamespace(PrimaryScreen=types.SimpleNamespace(
            Bounds=rect(*self.SCREEN), WorkingArea=rect(*self.WORK)))
        forms.FormBorderStyle = types.SimpleNamespace(Sizable="sizable",
                                                      **{"None": "none"})
        u = _ShowUser32()
        monkeypatch.setattr(peek_mod, "_win32", lambda: (u, WP))
        pw = _new_peek(monkeypatch)
        native = _FakeNative()
        a = peek_mod._Win32Window(pw, types.SimpleNamespace(native=native))
        a._native = native
        pw._hwnd = _FakeNative.HWND
        return a, u, WP

    @staticmethod
    def _shows(u):
        return [(i, c[2]) for i, c in enumerate(u.calls)
                if c[0] == "ShowWindow" and c[2] != 0]

    @staticmethod
    def _no_show_flag(u):
        return all(not (c[7] & 0x40) for c in u.calls if c[0] == "SetWindowPos")

    def test_peek_is_shown_by_showwindow_without_activation(self, monkeypatch):
        a, u, _ = self._adapter(monkeypatch)
        a.show_peek()
        assert self._no_show_flag(u), u.calls
        placed = [i for i, c in enumerate(u.calls) if c[0] == "SetWindowPos"
                  and c[2] == -1 and c[3:7] == self.SCREEN]
        assert placed and u.calls[placed[0]][7] & 0x10, "topmost, not activating"
        shows = self._shows(u)
        assert [cmd for _, cmd in shows] == [8], "one SW_SHOWNA, nothing activating"
        assert shows[0][0] > placed[0], "shown after it is placed"
        assert u.visible

    @pytest.mark.parametrize("focused, cmd", [(True, 5), (False, 8)])
    def test_first_app_show_is_shown_by_showwindow(self, monkeypatch,
                                                   focused, cmd):
        a, u, _ = self._adapter(monkeypatch)
        assert a.apply_app(None, focused) is not None
        assert self._no_show_flag(u), u.calls
        # D-9 default, centred in the asymmetric work area.
        x, y = -1280 + (1920 - 1280) // 2, 40 + (1032 - 800) // 2
        placed = [i for i, c in enumerate(u.calls)
                  if c[0] == "SetWindowPos" and c[3:7] == (x, y, 1280, 800)]
        assert placed, u.calls
        shows = self._shows(u)
        assert [c for _, c in shows] == [cmd]
        assert shows[0][0] > placed[0], "shown after it is placed"

    def test_a_saved_placement_is_shown_by_setwindowplacement(self,
                                                             monkeypatch):
        """The path that already painted: no show flag and no extra show."""
        a, u, WP = self._adapter(monkeypatch)
        saved = WP()
        saved.showCmd = 1
        a.apply_app(saved, True)
        assert self._no_show_flag(u)
        assert ("SetWindowPlacement", _FakeNative.HWND) in u.calls
        assert self._shows(u) == []


class _FakeController:
    """`CoreWebView2Controller` stand-in: records `IsVisible` writes into
    the shared call log, so their order against `ShowWindow` shows."""

    def __init__(self, calls):
        self._calls = calls
        self._v = True

    @property
    def IsVisible(self):
        return self._v

    @IsVisible.setter
    def IsVisible(self, v):
        self._calls.append(("IsVisible", v))
        self._v = v


class _FakeClrType:
    """A .NET `Type` stand-in: private fields by name, and a base type."""

    def __init__(self, fields, base=None):
        self.fields = fields
        self.BaseType = base
        self.asked = []

    def GetField(self, name, flags):
        self.asked.append((name, flags))
        return self.fields.get(name)


class _FakeFieldInfo:
    def __init__(self, value):
        self.value = value

    def GetValue(self, obj):
        return self.value


class TestHiddenPageVisibility:
    """After X or an end peek, `ShowWindow(SW_HIDE)` (and `Form.Hide()`)
    left the WebView2 controller's `IsVisible` True, so the page kept
    `document.visibilityState == "visible"` while the window was hidden and
    the pages' poll-pause logic never ran (pixel probe 2026-10-06: form and
    control `Visible` False, controller `IsVisible` True). Property: hiding
    sets `IsVisible` False after the window is hidden; every show sets it
    True after the window is shown; a missing controller field never stops
    a show or a hide and is logged once.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS hidden-page fix
    """

    def _adapter(self, monkeypatch, field="direct", controller=True):
        import types
        import power_atlas.peek as peek_mod
        a, u, WP = TestWin32ShowPrimitives()._adapter(monkeypatch)
        reflection = types.ModuleType("System.Reflection")
        reflection.BindingFlags = types.SimpleNamespace(Instance=4,
                                                        NonPublic=32)
        monkeypatch.setitem(sys.modules, "System.Reflection", reflection)
        ctl = _FakeController(u.calls) if controller else None
        info = _FakeFieldInfo(ctl)
        if field == "direct":
            t = _FakeClrType({"_coreWebView2Controller": info})
        elif field == "base":
            t = _FakeClrType({}, base=_FakeClrType(
                {"_coreWebView2Controller": info}))
        else:
            t = _FakeClrType({}, base=_FakeClrType({}))
        webview = types.SimpleNamespace(GetType=lambda: t)
        a._native.browser = types.SimpleNamespace(webview=webview)
        return a, u, WP, t

    @staticmethod
    def _seq(u):
        return [c[:1] + c[2:3] if c[0] == "ShowWindow" else c
                for c in u.calls if c[0] in ("ShowWindow", "IsVisible",
                                             "SetWindowPlacement")]

    def test_hide_makes_the_page_hidden_after_the_window(self, monkeypatch):
        a, u, _, t = self._adapter(monkeypatch)
        u.visible = True
        a.hide()
        assert self._seq(u) == [("ShowWindow", 0), ("IsVisible", False)]
        assert not u.visible
        assert t.asked == [("_coreWebView2Controller", 4 | 32)]

    def test_show_peek_makes_the_page_visible_after_the_show(self,
                                                             monkeypatch):
        a, u, _, _ = self._adapter(monkeypatch)
        a.show_peek()
        assert self._seq(u) == [("ShowWindow", 8), ("IsVisible", True)]

    @pytest.mark.parametrize("focused, cmd", [(True, 5), (False, 8)])
    def test_first_app_show_makes_the_page_visible(self, monkeypatch,
                                                   focused, cmd):
        a, u, _, _ = self._adapter(monkeypatch)
        a.apply_app(None, focused)
        assert self._seq(u) == [("ShowWindow", cmd), ("IsVisible", True)]

    def test_a_saved_placement_makes_the_page_visible(self, monkeypatch):
        a, u, WP, _ = self._adapter(monkeypatch)
        saved = WP()
        saved.showCmd = 1
        a.apply_app(saved, False)
        assert self._seq(u) == [("SetWindowPlacement", _FakeNative.HWND),
                                ("IsVisible", True)]

    def test_hide_then_show_round_trips(self, monkeypatch):
        """Asymmetric sequence: hide, peek, hide, app."""
        a, u, _, t = self._adapter(monkeypatch)
        a.hide()
        a.show_peek()
        a.hide()
        a.apply_app(None, True)
        assert [c[1] for c in u.calls if c[0] == "IsVisible"] == [
            False, True, False, True]
        assert len(t.asked) == 1, "the field is looked up once"

    def test_app_to_peek_restyle_ends_visible(self, monkeypatch):
        """`_set_chrome` hides a visible app window with a bare
        `ShowWindow(SW_HIDE)` (no `IsVisible` write) before the restyle;
        the peek show that follows leaves the page visible.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final fix 5
        """
        import power_atlas.peek as peek_mod
        a, u, _, _ = self._adapter(monkeypatch)
        a._chrome = peek_mod.APP
        u.visible = True
        a.show_peek()
        assert self._seq(u) == [("ShowWindow", 0), ("ShowWindow", 8),
                                ("IsVisible", True)]
        assert u.visible and a._chrome == peek_mod.PEEK

    def test_a_failed_placement_then_fail_to_hidden_ends_hidden(
            self, monkeypatch):
        """An end peek back to app mode whose `SetWindowPlacement` fails
        falls to HIDDEN (`_fail_to_hidden`), and the page ends hidden too.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final fix 5
        """
        import power_atlas.peek as peek_mod
        a, u, WP, _ = self._adapter(monkeypatch)
        pw = a._o
        pw._adapter = a
        monkeypatch.setattr(pw, "_sign_in_check", lambda: None)
        saved = WP()
        saved.showCmd = 1
        pw._app_placement = saved
        a._chrome = peek_mod.PEEK
        u.visible = True
        pw._state, pw._return_to = peek_mod.PEEK, "app"
        pw._peek_showing = True
        u.placement_fails = True
        with pytest.raises(OSError):
            pw._end_peek()
        assert self._seq(u) == [("ShowWindow", 0),
                                ("SetWindowPlacement", _FakeNative.HWND),
                                ("ShowWindow", 0), ("IsVisible", False)]
        assert not u.visible
        assert pw._state == peek_mod.HIDDEN and not pw._peek_showing

    def test_the_field_is_found_on_a_base_type(self, monkeypatch):
        a, u, _, t = self._adapter(monkeypatch, field="base")
        a.hide()
        assert ("IsVisible", False) in u.calls
        assert t.asked == [("_coreWebView2Controller", 4 | 32)]
        assert t.BaseType.asked == [("_coreWebView2Controller", 4 | 32)]

    def test_no_controller_yet_is_quiet(self, monkeypatch, caplog):
        """Before WebView2 initializes the field is null: nothing to do."""
        a, u, _, _ = self._adapter(monkeypatch, controller=False)
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            a.hide()
            a.show_peek()
        assert [c[0] for c in u.calls if c[0] == "ShowWindow"] == [
            "ShowWindow", "ShowWindow"]
        assert caplog.records == []

    def test_a_missing_field_never_stops_a_show_or_hide(self, monkeypatch,
                                                        caplog):
        a, u, _, _ = self._adapter(monkeypatch, field="missing")
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            a.show_peek()
            a.hide()
            a.show_peek()
            a.hide()
        assert [c[2] for c in u.calls if c[0] == "ShowWindow"] == [8, 0, 8, 0]
        assert not u.visible
        warnings = [r for r in caplog.records
                    if "could not tell the page" in r.getMessage()]
        assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
        assert "LookupError" in warnings[0].getMessage()


_CONTROLLER_FIELD_PROBE = r'''
import os, sys
try:
    try:
        import clr
    except Exception:
        os.environ["PYTHONNET_RUNTIME"] = "coreclr"
        import clr
    from webview.util import interop_dll_path
    clr.AddReference("System.Windows.Forms")
    clr.AddReference(interop_dll_path("Microsoft.Web.WebView2.Core.dll"))
    clr.AddReference(interop_dll_path("Microsoft.Web.WebView2.WinForms.dll"))
    from Microsoft.Web.WebView2.WinForms import WebView2
    from System.Reflection import BindingFlags
    t = clr.GetClrType(WebView2)
except Exception as e:
    print("cannot load: " + type(e).__name__)
    sys.exit(4)
flags = BindingFlags.Instance | BindingFlags.NonPublic
field = None
while t is not None and field is None:
    field = t.GetField("_coreWebView2Controller", flags)
    t = t.BaseType
if field is None:
    print("no _coreWebView2Controller field")
    sys.exit(3)
print(field.FieldType.FullName)
sys.exit(0)
'''


@pytest.mark.skipif(sys.platform != "win32", reason="WebView2 is Windows only")
def test_webview2_controller_field_exists():
    """`_Win32Window._set_page_visible` reads the WinForms control's private
    field `_coreWebView2Controller` by reflection. A pywebview upgrade that
    bundles a WebView2 WinForms assembly without it fails here rather than
    quietly costing the hidden page its `hidden` state. Run in a child
    process: loading the real CLR here would make `System.Reflection` real
    for every test that stands in a fake one. Skipped when pythonnet or the
    bundled assembly cannot load.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final fix 3
    """
    import subprocess
    r = subprocess.run([sys.executable, "-c", _CONTROLLER_FIELD_PROBE],
                       capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip()
    if r.returncode == 4:
        pytest.skip(out)
    assert r.returncode == 0, out
    assert out.splitlines()[-1] == (
        "Microsoft.Web.WebView2.Core.CoreWebView2Controller"), out


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


# ---- 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3 ----


class TestHotkeysModule:
    """`hotkeys.py`: names, parsing, validation and conflicts (SC-7), and the
    D-18 chord choice. Expected values come from the plan (Phase 3 bullets,
    D-18) and the Win32 virtual-key table, not from running the code.
    """

    # The plan's additions, with their Win32 VK codes; the names match
    # `pynput.keyboard.Key` member names (checked against pynput 1.8.2).
    @pytest.mark.parametrize("vk, name", [
        (0x26, "up"), (0x28, "down"), (0x25, "left"), (0x27, "right"),
        (0x2D, "insert"), (0x13, "pause"), (0x2C, "print_screen"),
        (0x91, "scroll_lock"), (0x90, "num_lock"), (0x5D, "menu"),
        # And a sample of the table `_vk_to_name` had before.
        (0x41, "a"), (0x5A, "z"), (0x30, "0"), (0x39, "9"), (0x70, "f1"),
        (0x87, "f24"), (0x1B, "esc"), (0xBF, "/"), (0xDE, "'"),
    ])
    def test_vk_names(self, vk, name):
        from power_atlas import hotkeys
        from power_atlas.peek import PeekWindow
        assert hotkeys.VK_NAMES[vk] == name
        assert PeekWindow._vk_to_name(vk) == name

    def test_unknown_vk_has_no_name(self):
        from power_atlas.peek import PeekWindow
        assert PeekWindow._vk_to_name(0xA6) is None  # VK_BROWSER_BACK
        assert PeekWindow._vk_to_name(0x5B) is None  # VK_LWIN

    def test_every_key_name_is_accepted_and_esc_is_not(self):
        from power_atlas import hotkeys
        names = set(hotkeys.VK_NAMES.values())
        assert len(names) == len(hotkeys.VK_NAMES), "a name maps two VK codes"
        for name in names - {"esc"}:
            assert hotkeys.hotkey_error(f"ctrl+{name}") is None, name
            assert hotkeys.hotkey_error(f"alt+shift+{name}") is None, name
        assert "esc" not in hotkeys.KEY_NAMES
        assert "esc" in hotkeys.hotkey_error("ctrl+esc")

    @pytest.mark.parametrize("hotkey, fragment", [
        ("z", "needs a modifier"),
        ("f5", "needs a modifier"),
        ("ctrl+shift", "needs a key"),
        ("ctrl", "needs a key"),
        ("ctrl+foo", "unknown key 'foo'"),
        ("win+z", "unknown key 'win'"),
        ("ctrl+shift+ctrl_l", "unknown key 'ctrl_l'"),
        ("", "empty"),
        (" + ", "empty"),
        # Phase 3 review fix 5: a chord fires on its one non-modifier key.
        ("ctrl+a+b", "more than one key besides the modifiers"),
        ("ctrl+shift+z+f1", "more than one key besides the modifiers"),
    ])
    def test_invalid(self, hotkey, fragment):
        from power_atlas import hotkeys
        problem = hotkeys.hotkey_error(hotkey)
        assert problem is not None and fragment in problem

    @pytest.mark.parametrize("hotkey", [
        "ctrl+shift+z", " Ctrl + Shift + Z ", "alt+f1", "ctrl+alt+up",
        "shift+print_screen", "ctrl+shift+/", "ctrl+shift+alt+z",
    ])
    def test_valid(self, hotkey):
        from power_atlas import hotkeys
        assert hotkeys.hotkey_error(hotkey) is None

    @pytest.mark.parametrize("a, b, conflict", [
        ("ctrl+shift+z", "ctrl+shift+z", True),
        ("ctrl+shift+z", "SHIFT+ctrl+Z", True),          # equal, other spelling
        ("ctrl+shift+z", "ctrl+shift+alt+z", True),      # a inside b
        ("ctrl+shift+alt+z", "ctrl+shift+z", True),      # b inside a
        ("alt+z", "ctrl+alt+z", True),
        ("ctrl+shift+z", "ctrl+alt+z", False),           # same key, not nested
        ("ctrl+shift+z", "ctrl+shift+b", False),
        ("alt+z", "ctrl+shift+z", False),
        ("ctrl+shift+z", "", False),                     # off
        ("", "", False),
    ])
    def test_conflict(self, a, b, conflict):
        from power_atlas import hotkeys
        assert hotkeys.hotkeys_conflict(a, b) is conflict
        assert hotkeys.hotkeys_conflict(b, a) is conflict

    def test_effective_peek_hotkey(self):
        from power_atlas import hotkeys
        assert hotkeys.effective_peek_hotkey("alt+p") == "alt+p"
        assert hotkeys.effective_peek_hotkey("nope") == "ctrl+shift+z"
        assert hotkeys.effective_peek_hotkey("ctrl+esc") == "ctrl+shift+z"

    # D-18: most keys wins; a tie goes to the peek chord.
    @pytest.mark.parametrize("peek, browser, held, key, expected", [
        ("alt+z", "ctrl+shift+z", {"ctrl", "shift", "alt"}, "z", "browser"),
        ("ctrl+shift+z", "alt+z", {"ctrl", "shift", "alt"}, "z", "peek"),
        ("ctrl+shift+z", "ctrl+alt+z", {"ctrl", "shift", "alt"}, "z", "peek"),
        ("ctrl+alt+z", "ctrl+shift+z", {"ctrl", "shift", "alt"}, "z", "peek"),
        ("ctrl+shift+z", "ctrl+alt+z", {"ctrl", "alt"}, "z", "browser"),
        ("ctrl+shift+z", "ctrl+alt+b", {"ctrl", "alt"}, "z", None),
        ("ctrl+shift+z", "ctrl+alt+b", {"ctrl", "alt"}, "b", "browser"),
        ("ctrl+shift+z", None, {"ctrl", "shift"}, "z", "peek"),
        ("ctrl+shift+z", None, {"ctrl", "shift"}, "shift", None),
        # Phase 3 review fix 10: the chord fully held, but the key-down is
        # another key; only the chord's own key matches.
        ("ctrl+shift+z", None, {"ctrl", "shift", "z"}, "x", None),
        ("ctrl+shift+z", "ctrl+alt+b", {"ctrl", "alt", "b"}, "z", None),
    ])
    def test_match_chord(self, peek, browser, held, key, expected):
        from power_atlas import hotkeys
        chords = {"peek": hotkeys.parse_hotkey(peek),
                  "browser": hotkeys.parse_hotkey(browser) if browser else None}
        assert hotkeys.match_chord(chords, key, held) == expected

    @pytest.mark.parametrize("browser, peek, expected", [
        ("ctrl+alt+b", "ctrl+shift+z", "ctrl+alt+b"),
        (" Ctrl+Alt+B ", "ctrl+shift+z", "ctrl+alt+b"),
        ("", "ctrl+shift+z", ""),
        ("ctrl+bogus", "ctrl+shift+z", ""),               # invalid
        ("ctrl+z", "ctrl+shift+z", ""),                   # inside the peek one
        ("ctrl+shift+z", "nope", ""),                     # equals the fallback
        ("ctrl+z", "alt+p", "ctrl+z"),                    # no overlap
    ])
    def test_effective_browser_hotkey(self, browser, peek, expected):
        """Phase 3 review fix 7: the browser shortcut the window runs is off
        when invalid or overlapping the peek shortcut in force."""
        from power_atlas import hotkeys
        assert hotkeys.effective_browser_hotkey(browser, peek) == expected

    def test_the_tie_goes_to_peek_whatever_the_table_order(self):
        from power_atlas import hotkeys
        chords = {"browser": hotkeys.parse_hotkey("ctrl+alt+z"),
                  "peek": hotkeys.parse_hotkey("ctrl+shift+z")}
        assert hotkeys.match_chord(chords, "z",
                                   {"ctrl", "shift", "alt"}) == "peek"

    def test_hotkeys_imports_no_ui_library(self):
        """`web.py` imports it, so it must load without pywebview or pynput."""
        import ast
        import inspect
        from power_atlas import hotkeys
        tree = ast.parse(inspect.getsource(hotkeys))
        imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, ast.Import)}
        imported |= {(n.module or "").split(".")[0] for n in ast.walk(tree)
                     if isinstance(n, ast.ImportFrom)}
        assert not imported & {"webview", "pynput"}


def _two_chord_peek(monkeypatch, peek="ctrl+shift+z", browser="ctrl+alt+b",
                    mode="hold"):
    import power_atlas.peek as peek_mod
    monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
    pw = peek_mod.PeekWindow("http://127.0.0.1:4915", peek, mode, browser)
    pw._adapter = _FakeAdapter()
    return pw


class TestTwoChordFilter:
    """The Windows filter with a peek and a browser chord (D-18, per-chord
    `_triggered`)."""

    _VK = {"z": 0x5A, "b": 0x42}

    def _peek(self, monkeypatch, held, **kw):
        pw = _two_chord_peek(monkeypatch, **kw)
        pw._listener = _Listener()
        pw._pressed_keys.update(held)
        return pw

    def _feed(self, pw, msg, key, t=0):
        try:
            pw._win32_event_filter(msg, _KbData(self._VK[key], t))
        except _Suppress:
            return True
        return False

    def test_the_browser_chord_posts_browser_and_is_suppressed(
            self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "alt"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "b", t=500)
        # The event carries its tick for the worker's rate limit (final
        # review fix 12).
        assert _drain(pw) == [("browser", 500)]
        assert pw._triggered == {"peek": False, "browser": True}
        # Auto-repeat: suppressed, no event.
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "b", t=530)
        assert _drain(pw) == []
        # Its key-up is suppressed and re-arms the browser chord only.
        assert self._feed(pw, peek_mod._WM_KEYUP, "b")
        assert pw._triggered == {"peek": False, "browser": False}
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "b", t=900)
        assert _drain(pw) == [("browser", 900)]
        assert pw._adapter.calls == [], "the hook never touches the window"

    def test_the_larger_chord_wins(self, monkeypatch):
        """Peek alt+z, browser ctrl+shift+z, all three modifiers held: the
        browser chord has more keys (D-18)."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift", "alt"),
                        peek="alt+z", browser="ctrl+shift+z")
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=10)
        assert _drain(pw) == [("browser", 10)]
        assert pw._triggered["peek"] is False

    def test_the_larger_chord_wins_the_other_way(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift", "alt"),
                        peek="ctrl+shift+z", browser="alt+z")
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=10)
        assert _drain(pw) == [("press", "peek", 10)]
        assert pw._triggered["browser"] is False

    def test_a_tie_goes_to_peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift", "alt"),
                        peek="ctrl+shift+z", browser="ctrl+alt+z")
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=10)
        assert _drain(pw) == [("press", "peek", 10)]

    def test_same_key_other_modifiers_picks_by_modifiers(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "alt"),
                        peek="ctrl+shift+z", browser="ctrl+alt+z")
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=10)
        assert _drain(pw) == [("browser", 10)]

    def _release(self, pw, name):
        pw._on_release(_make_key(name=name))

    def _press_mod(self, pw, name):
        pw._on_press(_make_key(name=name))

    def test_a_modifier_released_before_the_key_keeps_the_keystroke_whole(
            self, monkeypatch):
        """Phase 3 review fix 1: Ctrl+Shift+Z down, Shift up while Z still
        auto-repeats. The repeat no longer matches any chord, but the user's
        app has not seen Z's key-down, so the repeat stays suppressed (no
        event) and so does Z's key-up: never half a keystroke."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100)
        assert _drain(pw) == [("press", "peek", 100)]
        self._release(pw, "shift_l")
        assert _drain(pw) == [("release",)]
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=600)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=633)
        assert _drain(pw) == []
        assert self._feed(pw, peek_mod._WM_KEYUP, "z")
        # Z alone (Ctrl only) now reaches the app in full.
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=900)
        assert not self._feed(pw, peek_mod._WM_KEYUP, "z")
        assert _drain(pw) == []

    def test_a_held_key_never_switches_chords(self, monkeypatch):
        """Phase 3 review fixes 1-3: peek ctrl+shift+z fires, Shift goes up
        and Alt goes down while Z repeats. The repeats now satisfy the
        browser chord, but a held key is not a new press: suppressed, no
        `browser` event. After Z's key-up both chords are armed: a fresh Z
        fires the browser chord, and the peek chord fires again later."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift"),
                        peek="ctrl+shift+z", browser="ctrl+alt+z")
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100)
        assert _drain(pw) == [("press", "peek", 100)]
        self._release(pw, "shift_l")
        self._press_mod(pw, "alt_l")
        _drain(pw)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=600)
        assert _drain(pw) == []
        assert self._feed(pw, peek_mod._WM_KEYUP, "z")
        assert pw._triggered == {"peek": False, "browser": False}
        assert pw._chord_down == {}
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=900)
        assert _drain(pw) == [("browser", 900)]
        assert self._feed(pw, peek_mod._WM_KEYUP, "z")
        self._release(pw, "alt_l")
        self._press_mod(pw, "shift_l")
        _drain(pw)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=1200)
        assert _drain(pw) == [("press", "peek", 1200)]

    def test_a_key_up_rearms_every_chord_of_its_key(self, monkeypatch):
        """Phase 3 review fix 3: the key-up re-arms every chord that has the
        key, not only the one recorded at its key-down (as `_on_release` does
        off Windows), so no chord is left swallowing its next tap."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "alt"),
                        peek="ctrl+shift+z", browser="ctrl+alt+z")
        pw._chord_down["z"] = ("browser", 0)
        pw._triggered.update(peek=True, browser=True)
        assert self._feed(pw, peek_mod._WM_KEYUP, "z")
        assert pw._triggered == {"peek": False, "browser": False}

    def test_a_lost_key_up_does_not_swallow_the_key(self, monkeypatch):
        """Phase 3 review fix 1: if a chord key's key-up never reaches the
        hook (it went up on the secure desktop), a key-down after a gap no
        auto-repeat leaves is a new press, not a repeat: plain Z reaches the
        app, and the chord fires again. The gap is 3000 ms since final
        review fix 15 (Filter Keys repeat delays reach about 2 s; it was
        1500, which those delays crossed), so both sides of 3000 are fed."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100)
        assert _drain(pw) == [("press", "peek", 100)]
        # A 2 s Filter Keys delay, then exactly 3000: still repeats, so
        # suppressed and never a second press.
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100 + 2000)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100 + 2000 + 3000)
        assert _drain(pw) == []
        # Key-up lost; modifiers lost too.
        pw._pressed_keys.clear()
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, "z",
                              t=100 + 2000 + 3000 + 3001)
        assert not self._feed(pw, peek_mod._WM_KEYUP, "z")
        assert _drain(pw) == []
        pw._pressed_keys.update(("ctrl", "shift"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=20000)
        assert _drain(pw) == [("press", "peek", 20000)]

    def test_the_repeat_gap_wraps_with_the_tick(self, monkeypatch):
        """The gap is measured modulo 2**32 ms, like the double-tap."""
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "shift"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=0xFFFFFFF0)
        _drain(pw)
        self._release(pw, "shift_l")
        _drain(pw)
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=0x10)
        assert _drain(pw) == []
        # Across the wrap with a gap past the limit: a new press (Shift is up,
        # so Z alone passes). Unmasked, the gap would be negative: a repeat.
        # 0x1100 ms = 4352, past the 3000 ms limit of final review fix 15
        # (the old 0x900 = 2304 was past only the old 1500).
        pw._chord_down["z"] = ("peek", 0xFFFFFF00)
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=0x1000)

    def test_no_browser_chord_lets_its_keys_through(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = self._peek(monkeypatch, ("ctrl", "alt"), browser="")
        assert pw._chords["browser"] is None
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, "b")
        assert not self._feed(pw, peek_mod._WM_KEYUP, "b")
        assert _drain(pw) == []


class TestTwoChordPortable:
    """The same rules through `_on_press`/`_on_release` (non-Windows)."""

    def _peek(self, monkeypatch, **kw):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        return _two_chord_peek(monkeypatch, **kw)

    def test_the_browser_chord_posts_browser_once(self, monkeypatch):
        pw = self._peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="alt_l"),
                  _make_key(char="b"), _make_key(char="b")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["browser"]

    def test_the_larger_chord_wins(self, monkeypatch):
        pw = self._peek(monkeypatch, peek="alt+z", browser="ctrl+shift+z")
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(name="alt_l"), _make_key(char="z")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["browser"]

    def test_a_browser_press_never_arms_release(self, monkeypatch):
        """Ctrl is a modifier of both chords; releasing it after a browser
        press is not the end of a peek (modifier release stays tied to the
        peek chord)."""
        pw = self._peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="alt_l"),
                  _make_key(char="b")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["browser"]
        pw._on_release(_make_key(char="b"))
        pw._on_release(_make_key(name="ctrl_l"))
        assert _drain(pw) == []

    def test_a_peek_press_then_release_still_posts_release(self, monkeypatch):
        pw = self._peek(monkeypatch)
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["press"]
        pw._on_release(_make_key(name="ctrl_l"))
        assert _drain(pw) == [("release",)]

    def test_the_browser_key_up_rearms_only_the_browser_chord(self,
                                                             monkeypatch):
        pw = self._peek(monkeypatch, peek="ctrl+shift+z", browser="ctrl+alt+b")
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z"), _make_key(name="alt_l"),
                  _make_key(char="b")):
            pw._on_press(k)
        assert pw._triggered == {"peek": True, "browser": True}
        pw._on_release(_make_key(char="b"))
        assert pw._triggered == {"peek": True, "browser": False}


def _browser_through_worker(pw, adapter):
    """Run one `browser` event through the real worker: the hook-side post,
    then the worker's main loop once it is ready (Phase 3 review fix 6;
    `_handle` has no `browser` branch)."""
    import threading
    pw._ready.clear()
    pw._establish_ready = lambda: adapter
    t = threading.Thread(target=pw._window_worker, daemon=True)
    t.start()
    assert pw._ready.wait(5)
    pw._post_press("browser", 0)
    pw._events.put(("stop",))
    t.join(5)
    assert not t.is_alive()


class TestBrowserEvent:
    """The worker's `browser` event: a signed-in tab, nothing else (SC-6)."""

    def _peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        pw = _two_chord_peek(monkeypatch)
        pw._ready.set()
        pw._signed_gen = web_mod.local_secret_generation()
        opened = []
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        return peek_mod, pw, pw._adapter, opened

    @pytest.mark.parametrize("where", ["hidden", "peek", "app"])
    def test_opens_the_browser_and_leaves_the_window(self, monkeypatch,
                                                     where):
        peek_mod, pw, a, opened = self._peek(monkeypatch)
        if where == "peek":
            pw._handle(("press", "peek", 1000))
        elif where == "app":
            pw._handle(("show_app",))
        state, placement = pw._state, pw._app_placement
        a.calls.clear()
        _browser_through_worker(pw, a)
        assert opened == ["http://127.0.0.1:4915/signed"]
        assert a.window_calls() == []
        assert (pw._state, pw._app_placement) == (state, placement)

    def test_a_browser_press_between_two_taps_keeps_the_double_tap(
            self, monkeypatch):
        """It does not touch the double-tap timing: peek, browser, peek
        within 0.5 s is still a double-tap (here: app mode)."""
        peek_mod, pw, a, opened = self._peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        _browser_through_worker(pw, a)
        pw._handle(("press", "peek", 1300))
        assert pw._state == peek_mod.APP
        assert len(opened) == 1

    def test_handle_ignores_browser(self, monkeypatch):
        """Only the worker loops open the browser; `_handle` has no branch for
        it (it was unreachable)."""
        peek_mod, pw, a, opened = self._peek(monkeypatch)
        pw._handle(("browser", 0))
        assert opened == []

    def test_honoured_before_and_without_readiness(self, monkeypatch):
        """The browser needs no window: a press during startup, or after a
        failed readiness gate, still opens it; window events are dropped."""
        import threading
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(pw, "_establish_ready", lambda: None)
        # Drained at tick 1000: both presses are within 1 s, so neither is
        # stale (final review cycle 2, fix 13).
        monkeypatch.setattr(peek_mod, "_event_tick_now", lambda: 1000)
        # Ticks 1000 ms apart: past the 500 ms rate limit (final review 12).
        for ev in (("browser", 0), ("press", "peek", 1), ("show_app",),
                   ("browser", 1000), ("stop",)):
            pw._events.put(ev)
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        t.join(5)
        assert not t.is_alive()
        assert len(opened) == 2
        assert not pw._ready.is_set()
        assert pw._adapter.calls == []


class TestCreatePeekBrowserShortcut:
    """`create_peek` validates the browser shortcut with the save-time
    validator and turns it off, with a WARNING, when it is invalid or
    overlaps the peek shortcut in force."""

    def _capture(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        captured = {}

        def mock_init(self, server_url, hotkey="ctrl+shift+z", mode="hold",
                      browser_hotkey=""):
            captured.update(hotkey=hotkey, browser=browser_hotkey)

        monkeypatch.setattr(peek_mod.PeekWindow, "__init__", mock_init)
        return peek_mod, captured

    def test_defaults_to_off(self, monkeypatch):
        peek_mod, captured = self._capture(monkeypatch)
        assert peek_mod.create_peek("http://x", "ctrl+shift+z", "hold")
        assert captured["browser"] == ""

    @pytest.mark.parametrize("given, passed", [
        ("ctrl+alt+b", "ctrl+alt+b"),
        (" Ctrl+Alt+B ", "ctrl+alt+b"),
        ("ctrl+alt+z", "ctrl+alt+z"),  # same key, not nested
    ])
    def test_a_valid_one_is_passed_through(self, monkeypatch, caplog, given,
                                           passed):
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            assert peek_mod.create_peek("http://x", "ctrl+shift+z", "hold",
                                        given)
        assert captured == {"hotkey": "ctrl+shift+z", "browser": passed}
        assert not caplog.records

    @pytest.mark.parametrize("given", ["b", "ctrl+bogus", "ctrl+esc", "alt"])
    def test_an_invalid_one_is_off_with_a_warning(self, monkeypatch, caplog,
                                                  given):
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            assert peek_mod.create_peek("http://x", "ctrl+shift+z", "hold",
                                        given)
        assert captured == {"hotkey": "ctrl+shift+z", "browser": ""}
        assert any("browser_hotkey" in r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("peek, given", [
        ("ctrl+shift+z", "shift+ctrl+z"),       # equal
        ("ctrl+shift+z", "ctrl+shift+alt+z"),   # contains the peek one
        ("ctrl+shift+alt+p", "alt+p"),          # inside the peek one
        ("nope", "ctrl+shift+z"),               # equals the fallback in force
    ])
    def test_a_conflicting_one_is_off_with_a_warning(self, monkeypatch, caplog,
                                                     peek, given):
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            assert peek_mod.create_peek("http://x", peek, "hold", given)
        assert captured["browser"] == ""
        assert any("conflicts" in r.getMessage() for r in caplog.records)

    def test_an_unknown_peek_key_falls_back_naming_it(self, monkeypatch,
                                                      caplog):
        """R12: a stored peek shortcut with a key outside `VK_NAMES` falls
        back to ctrl+shift+z, and the warning names the rejected key."""
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            assert peek_mod.create_peek("http://x", "ctrl+media_next")
        assert captured["hotkey"] == "ctrl+shift+z"
        assert "media_next" in caplog.text

    @pytest.mark.parametrize("browser", [
        "ctrl+bogus", "b", "ctrl+a+b",                 # invalid
        "ctrl+shift+z", "ctrl+z", "ctrl+shift+alt+z",  # equal, inside, contains
    ])
    def test_the_constructor_turns_off_a_bad_browser_chord(
            self, monkeypatch, caplog, browser):
        """Phase 3 review fix 4: `PeekWindow` itself refuses an invalid or
        overlapping browser chord (D-18 assumes conflicting chords never run
        together), not only `create_peek`."""
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            pw = peek_mod.PeekWindow("http://x", "ctrl+shift+z", "hold",
                                     browser)
        assert pw._chords["browser"] is None
        assert pw._browser_hotkey == ""
        assert any("Browser shortcut" in r.getMessage()
                   for r in caplog.records)

    def test_the_constructor_keeps_a_good_browser_chord(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        pw = peek_mod.PeekWindow("http://x", "ctrl+shift+z", "hold",
                                 " Ctrl+Alt+Z ")
        assert pw._chords["browser"] == {"ctrl", "alt", "z"}
        assert pw._browser_hotkey == "ctrl+alt+z"

    def test_a_warning_quotes_the_raw_value(self, monkeypatch, caplog):
        """Phase 3 review fix 9: a hand-edited value with a newline is logged
        with `%r`, so it cannot start a forged log line."""
        peek_mod, captured = self._capture(monkeypatch)
        with caplog.at_level("WARNING", logger="power_atlas.peek"):
            peek_mod.create_peek("http://x", "ctrl+shift+z\nFAKE peek",
                                 "hold", "ctrl+b\nFAKE browser")
        messages = [r.getMessage() for r in caplog.records]
        assert len(messages) == 2
        for m in messages:
            assert "\n" not in m and "\\n" in m

    def test_the_real_window_builds_its_chord_table(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        pw = peek_mod.create_peek("http://x", "ctrl+shift+z", "hold",
                                  "ctrl+alt+b")
        assert pw._chords == {"peek": {"ctrl", "shift", "z"},
                              "browser": {"ctrl", "alt", "b"}}
        pw = peek_mod.create_peek("http://x", "ctrl+shift+z", "hold",
                                  "ctrl+shift+z")
        assert pw._chords["browser"] is None


# ---- 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review

def _ready_peek(monkeypatch, fg=OTHER_APP, **kw):
    """A ready window over `_FakeAdapter`, signed in under the current
    generation."""
    import power_atlas.peek as peek_mod
    from power_atlas import web as web_mod
    monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
    pw = peek_mod.PeekWindow("http://127.0.0.1:4915", "ctrl+shift+z", **kw)
    pw._adapter = _FakeAdapter(fg=fg)
    pw._ready.set()
    pw._signed_gen = web_mod.local_secret_generation()
    return pw, pw._adapter


class TestStopOnEveryPath:
    """Final review fix 1: `stop()` arms the exit watchdog whenever
    `webview.start()` has not returned, not only after a hung destroy. A
    destroy that returns at once (pywebview has no form yet, so it never ends
    `start()`), one that raises, and no window at all each leave `start()`
    blocking the main thread unless the watchdog runs the shutdown tail."""

    def _stop(self, monkeypatch, window):
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_DESTROY_TIMEOUT", 0.05)
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 0.05)
        pw = _new_peek(monkeypatch)
        pw._window = window
        tail = threading.Event()
        pw.shutdown_tail = tail.set
        pw.stop()
        return pw, tail

    def test_a_prompt_destroy_without_start_returning_runs_the_tail(
            self, monkeypatch):
        win = MagicMock()
        win.native = None
        pw, tail = self._stop(monkeypatch, win)
        win.destroy.assert_called_once()
        assert tail.wait(5), "start() never returns, so the tail must run"

    def test_a_raising_destroy_runs_the_tail(self, monkeypatch):
        win = MagicMock()
        win.native = None
        win.destroy.side_effect = RuntimeError("no instance")
        pw, tail = self._stop(monkeypatch, win)
        assert tail.wait(5)

    def test_no_window_yet_runs_the_tail(self, monkeypatch):
        pw, tail = self._stop(monkeypatch, None)
        assert tail.wait(5)

    def test_a_healthy_stop_runs_no_tail(self, monkeypatch):
        """The destroy ends the UI loop: `start()` returns, the watchdog
        stands down and the main thread runs the tail itself."""
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 0.3)
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.native = None
        win.destroy.side_effect = lambda: pw._start_returned.set()
        pw._window = win
        tail = threading.Event()
        pw.shutdown_tail = tail.set
        pw.stop()
        assert not tail.wait(0.8)

    def test_a_stop_before_creation_starts_nothing(self, monkeypatch):
        """`stop()` first: `_run_webview` creates no window and never enters
        `webview.start()`, so the main thread goes straight to its tail."""
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 0.05)
        pw = _new_peek(monkeypatch)
        pw.stop()
        fake = MagicMock()
        monkeypatch.setattr(peek_mod, "webview", fake, raising=False)
        pw._run_webview()
        fake.create_window.assert_not_called()
        fake.start.assert_not_called()
        assert pw._start_returned.is_set()

    def test_a_stop_between_creation_and_start_skips_start(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        fake = MagicMock()
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")

        def create(*a, **kw):
            pw._stopping = True  # `stop()` lands right here
            return MagicMock()

        fake.create_window.side_effect = create
        monkeypatch.setattr(peek_mod, "webview", fake, raising=False)
        pw._run_webview()
        fake.start.assert_not_called()
        assert pw._start_returned.is_set()


class TestSignInCookieCheck:
    """Final review fix 2: before a show the window reads its own `pa_local`
    cookie, at most every 10 s, and re-signs when it is missing or invalid.
    It never reloads a window whose cookie is valid or unreadable, nor when
    no local secret is loaded (a re-sign could not fix that)."""

    SECRET = "S" * 43

    def _peek(self, monkeypatch, cookie, secret=SECRET):
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        monkeypatch.setattr(web_mod, "_LOCAL_SECRET", secret)
        pw, a = _ready_peek(monkeypatch)
        a.cookie = cookie
        clock = [1000.0]
        monkeypatch.setattr(peek_mod, "_now", lambda: clock[0])
        minted = []

        def fake_login_url(u):
            minted.append(u)
            return f"{u}/signed/{len(minted)}"

        monkeypatch.setattr(peek_mod, "_login_url", fake_login_url)
        return peek_mod, web_mod, pw, a, clock

    @staticmethod
    def _reloads(a):
        return [c for c in a.calls if c[0] == "reload"]

    def test_a_missing_cookie_re_signs_after_a_peek_shows(self, monkeypatch):
        """Final review cycle 2, fix 6: on the peek path the check runs after
        the show, so a cookie read never delays a peek."""
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, "")
        reads_at_show = []
        orig_show = a.show_peek

        def show_peek():
            reads_at_show.append(a.cookie_reads)
            orig_show()

        a.show_peek = show_peek
        pw._handle(("press", "peek", 1000))
        assert self._reloads(a) == [("reload", "http://127.0.0.1:4915/signed/1")]
        assert a.names().index("reload") > a.names().index("show_peek")
        assert reads_at_show == [0] and a.cookie_reads == 1

    def test_a_missing_cookie_re_signs_before_an_app_show(self, monkeypatch):
        """The APP paths keep their check before the window is placed."""
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, "")
        pw._handle(("show_app",))
        assert len(self._reloads(a)) == 1
        assert a.names().index("reload") < a.names().index("apply_app")

    def test_a_valid_cookie_is_never_reloaded(self, monkeypatch):
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, None)
        a.cookie = web_mod.make_local_cookie()
        pw._handle(("press", "peek", 1000))
        pw._handle(("release",))
        clock[0] += 60
        pw._handle(("show_app",))
        assert a.cookie_reads == 2
        assert self._reloads(a) == []

    @pytest.mark.parametrize("damage", ["signature", "subject", "expired"])
    def test_an_invalid_cookie_re_signs(self, monkeypatch, damage):
        import time as _t
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, None)
        good = web_mod.make_local_cookie()
        subject, stamp, sig = good.split(".")
        if damage == "signature":
            bad = f"{subject}.{stamp}.{'A' if sig[0] != 'A' else 'B'}{sig[1:]}"
        elif damage == "subject":
            bad = f"device.{stamp}.{sig}"
        else:
            old = int(_t.time()) - web_mod.LOCAL_COOKIE_MAX_AGE_SECONDS - 60
            bad = web_mod.make_local_cookie(issued_at=old)
        assert bad != good
        a.cookie = bad
        pw._handle(("show_app",))
        assert len(self._reloads(a)) == 1

    def test_a_cookie_signed_under_another_key_re_signs(self, monkeypatch):
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, None)
        monkeypatch.setattr(web_mod, "_LOCAL_SECRET", "T" * 43)
        a.cookie = web_mod.make_local_cookie()
        monkeypatch.setattr(web_mod, "_LOCAL_SECRET", self.SECRET)
        pw._handle(("press", "peek", 1000))
        assert len(self._reloads(a)) == 1

    def test_an_unreadable_cookie_is_left_alone(self, monkeypatch):
        """None: the page is still loading or the UI thread is busy."""
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, None)
        pw._handle(("press", "peek", 1000))
        assert a.cookie_reads == 1
        assert self._reloads(a) == []

    def test_without_a_local_secret_nothing_is_reloaded(self, monkeypatch):
        """No secret: `web.window_signed_in` answers unknown, so nothing is
        reloaded. The jar may be read first: the one web helper decides
        (final review cycle 2, fix 2), so the read count is not pinned."""
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, "", secret="")
        pw._handle(("press", "peek", 1000))
        assert self._reloads(a) == []

    def test_the_check_runs_at_most_every_ten_seconds(self, monkeypatch):
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, "")
        pw._handle(("press", "peek", 1000))
        pw._handle(("release",))
        assert len(self._reloads(a)) == 1
        clock[0] += 9.999  # still inside the interval
        pw._handle(("press", "peek", 20000))
        pw._handle(("release",))
        assert a.cookie_reads == 1 and len(self._reloads(a)) == 1
        clock[0] += 0.001  # exactly 10 s after the first check
        pw._handle(("press", "peek", 40000))
        assert a.cookie_reads == 2 and len(self._reloads(a)) == 2

    def test_a_hide_does_not_check(self, monkeypatch):
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, "")
        pw._handle(("press", "peek", 1000))
        clock[0] += 60
        a.calls.clear()
        pw._handle(("release",))
        assert self._reloads(a) == []

    def test_a_rotation_reload_comes_first_and_resets_the_interval(
            self, monkeypatch):
        """D-13's reload is not rate-limited, and the cookie is not read
        right after it (the new page is still loading)."""
        peek_mod, web_mod, pw, a, clock = self._peek(monkeypatch, "")
        pw._signed_gen = -1
        pw._handle(("press", "peek", 1000))
        assert len(self._reloads(a)) == 1
        assert a.cookie_reads == 0


class TestSignInReloadResult:
    """Final review fix 16: `_signed_gen` advances only when the reload is
    known to have run. A timed-out reload (False) is retried on the next
    show."""

    def test_a_timed_out_reload_is_retried(self, monkeypatch):
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        pw, a = _ready_peek(monkeypatch)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        gen = web_mod.local_secret_generation()
        pw._signed_gen = gen - 1
        a.reload_ok = False
        pw._handle(("press", "peek", 1000))
        pw._handle(("release",))
        assert pw._signed_gen == gen - 1
        a.reload_ok = True
        pw._handle(("press", "peek", 9000))
        pw._handle(("release",))
        assert pw._signed_gen == gen
        pw._handle(("press", "peek", 20000))
        assert [c[0] for c in a.calls].count("reload") == 2

    def test_win32_reload_reports_a_timeout(self, monkeypatch):
        import threading
        import types
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        release = threading.Event()
        try:
            hung = peek_mod._Win32Window(
                None, types.SimpleNamespace(load_url=lambda u: release.wait(5)))
            assert hung.reload("http://x/signed") is False
        finally:
            release.set()
        ok = peek_mod._Win32Window(
            None, types.SimpleNamespace(load_url=lambda u: None))
        assert ok.reload("http://x/signed") is True


class TestCookieReader:
    """`read_cookies`, shared by both adapters: bounded, off the worker,
    never waiting on an unloaded page, never logging the value."""

    VALUE = "loopback.1700000000.SECRETSIG"
    URL = "http://127.0.0.1:4915/acp"

    @staticmethod
    def _pa_local(read):
        """The `pa_local` value in a `read_cookies()` result, "" if none."""
        jar, url = read
        for c in jar:
            if "pa_local" in c:
                return c["pa_local"].value
        return ""

    def _win(self, loaded=True, cookies=None, get=None):
        import threading
        import types
        from http.cookies import SimpleCookie
        ev = threading.Event()
        if loaded:
            ev.set()
        win = types.SimpleNamespace(events=types.SimpleNamespace(loaded=ev),
                                    get_current_url=lambda: self.URL)
        calls = []

        def get_cookies():
            calls.append(1)
            if get is not None:
                return get()
            out = []
            for name, value in (cookies or {}).items():
                c = SimpleCookie()
                c[name] = value
                c[name]["httponly"] = True
                out.append(c)
            return out

        win.get_cookies = get_cookies
        return win, calls

    @pytest.mark.parametrize("cls", ["_Win32Window", "_PortableWindow"])
    def test_reads_the_pa_local_value(self, cls, caplog):
        import power_atlas.peek as peek_mod
        win, calls = self._win(cookies={"other": "x", "pa_local": self.VALUE})
        a = getattr(peek_mod, cls)(None, win)
        with caplog.at_level(logging.DEBUG):
            read = a.read_cookies()
        assert self._pa_local(read) == self.VALUE
        assert read[1] == self.URL
        assert "SECRETSIG" not in caplog.text

    def test_no_pa_local_is_empty(self):
        import power_atlas.peek as peek_mod
        win, calls = self._win(cookies={"other": "x"})
        assert self._pa_local(
            peek_mod._Win32Window(None, win).read_cookies()) == ""

    def test_an_unloaded_page_is_not_asked(self):
        import power_atlas.peek as peek_mod
        win, calls = self._win(loaded=False, cookies={"pa_local": self.VALUE})
        assert peek_mod._Win32Window(None, win).read_cookies() is None
        assert calls == []

    def test_a_raising_read_is_unknown_and_logs_the_type_only(self, caplog):
        import power_atlas.peek as peek_mod

        def broken():
            raise RuntimeError("cookie " + self.VALUE)

        win, calls = self._win(get=broken)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            assert peek_mod._Win32Window(None, win).read_cookies() is None
        assert "RuntimeError" in caplog.text
        assert "SECRETSIG" not in caplog.text

    def test_a_hung_read_is_bounded_and_not_doubled(self, monkeypatch, caplog):
        import threading
        import time as _t
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.1)
        release = threading.Event()
        win, calls = self._win(get=lambda: release.wait(5) and [])
        a = peek_mod._Win32Window(None, win)
        try:
            t0 = _t.monotonic()
            with caplog.at_level(logging.WARNING, logger="power_atlas"):
                assert a.read_cookies() is None
            assert _t.monotonic() - t0 < 2
            assert "did not read the window's cookies" in caplog.text
            # The first read is still stuck: no second thread.
            assert a.read_cookies() is None
            assert len(calls) == 1
        finally:
            release.set()

    def test_a_read_hung_past_30_s_is_given_up_on(self, monkeypatch, caplog):
        """Final review cycle 2, fix 1: a faulted `GetCookiesAsync` never
        releases pywebview's wait, so the read thread never ends. Within 30 s
        of its start a second read is refused; past 30 s a new read starts,
        so the signed-out check is not off for the rest of the run."""
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        clock = [500.0]
        monkeypatch.setattr(peek_mod, "_now", lambda: clock[0])
        release = threading.Event()
        hang = [True]

        def get():
            if hang[0]:
                release.wait(10)
            return []

        win, calls = self._win(get=get)
        a = peek_mod._Win32Window(None, win)
        try:
            assert a.read_cookies() is None  # hangs
            clock[0] += 29.9
            assert a.read_cookies() is None
            assert len(calls) == 1, "refused inside 30 s"
            clock[0] += 0.2  # 30.1 s after the stuck read started
            hang[0] = False
            assert a.read_cookies() == ([], self.URL)
            assert len(calls) == 2, "a new read past 30 s"
        finally:
            release.set()

    def test_skipped_reads_warn_at_most_every_five_minutes(self, monkeypatch,
                                                          caplog):
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        clock = [500.0]
        monkeypatch.setattr(peek_mod, "_now", lambda: clock[0])
        release = threading.Event()
        win, calls = self._win(get=lambda: release.wait(10) and [])
        a = peek_mod._Win32Window(None, win)
        # Skips and give-ups share one 5 min limit.
        skip, gave_up = "cookie reads are being skipped", "giving up on it"
        try:
            with caplog.at_level(logging.WARNING, logger="power_atlas"):
                a.read_cookies()  # the stuck read; not a skip
                assert skip not in caplog.text
                clock[0] += 1
                a.read_cookies()  # skipped: warns
                clock[0] += 1
                a.read_cookies()  # skipped again: quiet
                assert caplog.text.count(skip) == 1
                # Given up on at 30 s and read again: each new read is a
                # fresh stuck one; the give-ups stay inside the same 5 min.
                for _ in range(9):  # 9 x 30.5 s = 274.5 s after the warning
                    clock[0] += 30.5
                    a.read_cookies()
                assert caplog.text.count(skip) == 1
                assert gave_up not in caplog.text
                clock[0] += 30.5  # 305 s after the first warning
                a.read_cookies()
                assert caplog.text.count(gave_up) == 1
                assert caplog.text.count(skip) == 1
        finally:
            release.set()


class TestPeekChordOffWhenNotReady:
    """Final review fix 3: when readiness fails nothing would act on the
    peek chord, so the filter stops suppressing it (the keystroke reaches
    the user's app), a WARNING says the peek shortcut is off, and the
    browser chord keeps working."""

    _VK = {"z": 0x5A, "b": 0x42}

    def _feed(self, pw, msg, key, t=0):
        try:
            pw._win32_event_filter(msg, _KbData(self._VK[key], t))
        except _Suppress:
            return True
        return False

    def _run_worker(self, pw):
        import threading
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        return t

    def test_failed_readiness_turns_the_peek_chord_off(self, monkeypatch,
                                                       caplog):
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        pw._listener = _Listener()
        monkeypatch.setattr(pw, "_establish_ready", lambda: None)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            t = self._run_worker(pw)
            pw._events.put(("stop",))
            t.join(5)
        assert not t.is_alive()
        assert "peek shortcut (ctrl+shift+z) is off" in caplog.text
        assert "browser shortcut still works" in caplog.text
        # Ctrl+Shift+Z now reaches the app whole, and posts nothing.
        pw._pressed_keys.update(("ctrl", "shift"))
        assert not self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100)
        assert not self._feed(pw, peek_mod._WM_KEYUP, "z")
        assert _drain(pw) == []
        # The browser chord still fires and is suppressed.
        pw._pressed_keys.clear()
        pw._pressed_keys.update(("ctrl", "alt"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "b", t=200)
        assert _drain(pw) == [("browser", 200)]

    def test_portable_peek_chord_is_off_too(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        pw = _two_chord_peek(monkeypatch)
        pw._peek_chord_off = True
        for k in (_make_key(name="ctrl_l"), _make_key(name="shift_l"),
                  _make_key(char="z")):
            pw._on_press(k)
        assert _drain(pw) == []

    def test_a_successful_readiness_keeps_it_on(self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        a = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: a)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            t = self._run_worker(pw)
            assert pw._ready.wait(5)
            pw._events.put(("stop",))
            t.join(5)
        assert pw._peek_chord_off is False
        assert "is off" not in caplog.text
        pw._listener = _Listener()
        pw._pressed_keys.update(("ctrl", "shift"))
        assert self._feed(pw, peek_mod._WM_KEYDOWN, "z", t=100)
        assert _drain(pw) == [("press", "peek", 100)]


class TestBrowserRateLimit:
    """Final review fix 12: at most one browser tab (one login code) per
    500 ms from the browser chord, timed by the hook's tick."""

    def _run(self, monkeypatch, ticks):
        import threading
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(pw, "_establish_ready", lambda: pw._adapter)
        # The presses are drained before readiness: "now" is the last one,
        # so none is stale (final review cycle 2, fix 13).
        monkeypatch.setattr(peek_mod, "_event_tick_now", lambda: ticks[-1])
        for t in ticks:
            pw._events.put(("browser", t))
        pw._events.put(("stop",))
        th = threading.Thread(target=pw._window_worker, daemon=True)
        th.start()
        th.join(5)
        assert not th.is_alive()
        return len(opened)

    def test_both_sides_of_the_boundary(self, monkeypatch, caplog):
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            # 1000 opens; 1200 and 1499 are inside 500 ms of it; 1500 opens;
            # 1999 is inside 500 ms of 1500; 2000 opens.
            n = self._run(monkeypatch, [1000, 1200, 1499, 1500, 1999, 2000])
        assert n == 3
        assert "dropped a press within 500 ms" in caplog.text

    def test_a_burst_opens_once(self, monkeypatch):
        assert self._run(monkeypatch, list(range(5000, 5400, 10))) == 1

    def test_the_tick_wrap(self, monkeypatch):
        # 0xFFFFFF00 then 0x100: 512 ms apart across the wrap, so both open;
        # unmasked the gap would be negative. 0x10 is 272 ms after: dropped.
        assert self._run(monkeypatch, [0xFFFFFF00, 0x100]) == 2
        assert self._run(monkeypatch, [0xFFFFFF00, 0x10]) == 1


class TestFocusFailureAfterAppShow:
    """Final review fix 14: once `apply_app` has put on app chrome, a raising
    `focus()` must not leave the state PEEK (the filter would then swallow
    Esc system-wide)."""

    def test_from_a_peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a = _ready_peek(monkeypatch)
        pw._handle(("press", "peek", 1000))

        def broken():
            raise OSError(5, "Access is denied")

        monkeypatch.setattr(a, "focus", broken)
        with pytest.raises(OSError):
            pw._handle(("show_app",))
        assert (pw._state, pw._return_to) == (peek_mod.APP, None)
        assert pw._peek_showing is False


class TestFailToHiddenResetsOverlays:
    """Final review fix 17: a failed exit from a peek over HIDDEN still
    fires `resetOverlays` (D-11: PEEK -> HIDDEN), once, without waiting; a
    peek over APP keeps its page."""

    def test_a_timed_out_app_show_from_a_peek_resets(self, monkeypatch):
        pw, a = _ready_peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        a.calls.clear()
        monkeypatch.setattr(a, "apply_app", lambda p, focused: None)
        pw._handle(("show_app",))
        assert a.window_calls() == [("reset_overlays",), ("hide",)]

    def test_a_raising_hide_on_end_peek_resets_once(self, monkeypatch):
        pw, a = _ready_peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        a.calls.clear()
        orig_hide = a.hide

        def broken():
            orig_hide()
            raise OSError(5, "denied")

        monkeypatch.setattr(a, "hide", broken)
        with pytest.raises(OSError):
            pw._handle(("esc",))
        assert [c[0] for c in a.calls].count("reset_overlays") == 1

    def test_a_failed_return_to_app_does_not_reset(self, monkeypatch):
        pw, a = _ready_peek(monkeypatch)
        pw._handle(("show_app",))
        pw._handle(("press", "peek", 1000))
        a.calls.clear()

        def broken(placement, focused):
            raise OSError(1400, "Invalid window handle")

        monkeypatch.setattr(a, "apply_app", broken)
        with pytest.raises(OSError):
            pw._handle(("release",))
        assert ("reset_overlays",) not in a.calls


class TestReadinessDeadline:
    """Final review fix 18: one readiness timeout covers creation and
    `shown`: time spent waiting for creation is taken off the `shown` wait."""

    def test_the_shown_wait_gets_what_is_left(self, monkeypatch):
        import threading
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        monkeypatch.setattr(peek_mod, "_READY_TIMEOUT", 1.0)
        pw = _new_peek(monkeypatch)
        win = MagicMock()
        win.events.shown.wait.return_value = True
        pw._window = win
        threading.Timer(0.4, pw._window_created.set).start()
        assert pw._establish_ready() is not None
        (timeout,), _ = win.events.shown.wait.call_args
        assert 0 <= timeout <= 0.7


class TestHandleKeptOnTheWorker:
    """Final review fix 4: the chrome switch reads the new HWND on the UI
    thread and hands it back; only the worker writes `_hwnd`."""

    def _adapter(self, monkeypatch):
        import threading
        import types
        import power_atlas.peek as peek_mod
        forms = _fake_dotnet(monkeypatch)
        forms.FormBorderStyle = types.SimpleNamespace(Sizable="sizable",
                                                      **{"None": "none"})
        forms.Screen = types.SimpleNamespace(PrimaryScreen=types.SimpleNamespace(
            Bounds=types.SimpleNamespace(X=0, Y=0, Width=800, Height=600)))

        class U:
            def __getattr__(self, name):
                return lambda *a: 0

        monkeypatch.setattr(peek_mod, "_win32", lambda: (U(), None))
        pw = _new_peek(monkeypatch)
        pw._hwnd = 0x1111
        seen = {}
        worker = threading.current_thread()

        class Native:
            FormBorderStyle = None
            Handle = types.SimpleNamespace(ToInt64=lambda: 0x2222)

            def BeginInvoke(self, action):
                def ui():
                    action()
                    seen["hwnd_on_ui"] = pw._hwnd
                    seen["ui_is_worker"] = threading.current_thread() is worker
                th = threading.Thread(target=ui)
                th.start()
                th.join()

        a = peek_mod._Win32Window(pw, types.SimpleNamespace(native=None))
        a._native = Native()
        return pw, a, seen

    def test_show_peek(self, monkeypatch):
        pw, a, seen = self._adapter(monkeypatch)
        a.show_peek()
        # The UI thread switched the chrome but left `_hwnd` alone; the
        # worker cached the new handle once the callable returned it.
        assert seen == {"hwnd_on_ui": 0x1111, "ui_is_worker": False}
        assert pw._hwnd == 0x2222

    def test_a_timed_out_switch_keeps_the_handle(self, monkeypatch):
        pw, a, seen = self._adapter(monkeypatch)
        a._ui = lambda fn, what, **kw: None  # the UI thread never answered
        a.show_peek()
        assert a.apply_app(None, focused=True) is None
        assert pw._hwnd == 0x1111


class TestAdapterProtocol:
    """Final review fix 6: one declared adapter interface; both real
    adapters and the test fake expose every member the worker calls."""

    def test_every_adapter_has_every_member(self):
        import power_atlas.peek as peek_mod
        proto = peek_mod._WindowAdapter
        members = ({n for n in vars(proto) if not n.startswith("_")}
                   | set(proto.__annotations__))
        assert {"has_app_mode", "show_peek", "apply_app", "reload",
                "read_cookies"} <= members
        for cls in (peek_mod._Win32Window, peek_mod._PortableWindow,
                    _FakeAdapter):
            missing = sorted(m for m in members if not hasattr(cls, m))
            assert missing == [], (cls.__name__, missing)


class TestReleaseOfAnotherModifier:
    """Final review fix 7: only a modifier of the peek chord ends a Hold
    peek; releasing Alt (not in ctrl+shift+z, but in the browser chord)
    posts nothing."""

    def test_alt_release_does_not_end_a_hold_peek(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        pw = _two_chord_peek(monkeypatch, peek="ctrl+shift+z",
                             browser="alt+b")
        for k in (_make_key(name="alt_l"), _make_key(name="ctrl_l"),
                  _make_key(name="shift_l"), _make_key(char="z")):
            pw._on_press(k)
        assert [e[0] for e in _drain(pw)] == ["press"]
        assert pw._release_armed is True
        pw._on_release(_make_key(name="alt_l"))
        assert _drain(pw) == []
        pw._on_release(_make_key(name="ctrl_l"))
        assert _drain(pw) == [("release",)]


class TestSupportsAppMode:
    """Final review fix 8: app mode needs WebView2 (`winforms.is_chromium`)."""

    @staticmethod
    def _win32_peek(monkeypatch, chromium=True):
        import types
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "win32")
        winforms = types.ModuleType("webview.platforms.winforms")
        winforms.is_chromium = chromium
        platforms = types.ModuleType("webview.platforms")
        platforms.winforms = winforms
        monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
        monkeypatch.setitem(sys.modules, "webview.platforms.winforms", winforms)
        if "webview" in sys.modules:
            monkeypatch.setattr(sys.modules["webview"], "platforms", platforms,
                                raising=False)
        return _new_peek(monkeypatch)

    @pytest.mark.parametrize("chromium", [True, False])
    def test_follows_is_chromium(self, monkeypatch, chromium):
        pw = self._win32_peek(monkeypatch, chromium)
        pw._ready.set()
        # A page has loaded (Phase 5, follow-up 8), so only `is_chromium`
        # and readiness decide here.
        pw._loaded.set()
        assert pw.supports_app_mode is chromium
        pw._ready.clear()
        assert pw.supports_app_mode is False

    def test_needs_a_first_page_load(self, monkeypatch):
        """Phase 5 (follow-up 8): a WebView2 that failed to initialize never
        loads a page, so ready and Chromium are not enough; tray Open and
        `show_app` then use the browser."""
        import power_atlas.peek as peek_mod
        pw = self._win32_peek(monkeypatch)
        pw._ready.set()
        assert pw.supports_app_mode is False
        opened = []
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        pw.show_app()
        assert opened == ["http://127.0.0.1:4915/signed"]
        assert _drain(pw) == []
        # pywebview's `loaded` handler: the latch, and an event for the
        # worker. Later loads (the latch already set) keep app mode on.
        pw._on_loaded()
        assert pw.supports_app_mode is True
        assert _drain(pw) == [("loaded",)]
        pw.show_app()
        assert _drain(pw) == [("show_app",)]

    def test_the_double_tap_needs_a_first_page_load_too(self, monkeypatch):
        """The Win32 adapter's `has_app_mode` follows the same latch, so the
        worker's double-tap opens the browser, not a blank window, until a
        page has loaded (follow-up 8)."""
        import types
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        a = peek_mod._Win32Window(pw, types.SimpleNamespace(native=None))
        assert a.has_app_mode is False
        pw._on_loaded()
        assert a.has_app_mode is True

    def _unloaded_lines(self, caplog):
        return [r for r in caplog.records
                if "has not loaded a page yet" in r.getMessage()]

    def test_the_first_load_fallback_is_logged_once(self, monkeypatch,
                                                    caplog):
        """Phase 5 review (A6): a tray Open or a double-tap that uses the
        browser only because no page has loaded says so at INFO, once per
        run, whichever comes first."""
        import types
        import power_atlas.peek as peek_mod
        pw = self._win32_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        pw._adapter = peek_mod._Win32Window(pw, types.SimpleNamespace(
            native=None))
        pw._ready.set()
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            pw.show_app()
            pw.show_app()
            pw._double_tap(None)  # the worker's double-tap, no app mode
        lines = self._unloaded_lines(caplog)
        assert len(lines) == 1 and lines[0].levelno == logging.INFO
        assert len(opened) == 3
        assert _drain(pw) == []

    def test_the_double_tap_logs_it_too(self, monkeypatch, caplog):
        import types
        import power_atlas.peek as peek_mod
        pw = self._win32_peek(monkeypatch)
        monkeypatch.setattr(peek_mod._doors, "open_in_browser",
                            lambda u: None)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        pw._adapter = peek_mod._Win32Window(pw, types.SimpleNamespace(
            native=None))
        pw._ready.set()
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            pw._double_tap(None)
        assert len(self._unloaded_lines(caplog)) == 1

    @pytest.mark.parametrize("why", ["not ready", "not chromium",
                                     "not win32", "other adapter", "loaded"])
    def test_other_reasons_are_not_logged_as_it(self, monkeypatch, caplog,
                                                why):
        """Only when the first load is the one thing missing."""
        import types
        import power_atlas.peek as peek_mod
        pw = self._win32_peek(monkeypatch, chromium=(why != "not chromium"))
        monkeypatch.setattr(peek_mod._doors, "open_in_browser",
                            lambda u: None)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        pw._adapter = peek_mod._Win32Window(pw, types.SimpleNamespace(
            native=None))
        pw._ready.set()
        if why == "not ready":
            pw._ready.clear()
        elif why == "not win32":
            monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        elif why == "other adapter":
            pw._adapter = _FakeAdapter()
        elif why == "loaded":
            pw._loaded.set()
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            pw.show_app()
        assert self._unloaded_lines(caplog) == []


class TestOneShortcutRule:
    """Final review fix 5: `create_peek` and `PeekWindow` take the shortcut
    in force from `hotkeys`, not from a copy of its rule; fix 19: the peek
    modes are defined once."""

    def test_both_call_the_hotkeys_helpers(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        calls = []

        def eff_browser(browser, peek):
            calls.append(("browser", browser, peek))
            return "alt+q"

        def eff_peek(hotkey):
            calls.append(("peek", hotkey))
            return "ctrl+shift+y"

        monkeypatch.setattr(peek_mod._hotkeys, "effective_browser_hotkey",
                            eff_browser)
        monkeypatch.setattr(peek_mod._hotkeys, "effective_peek_hotkey",
                            eff_peek)
        pw = peek_mod.create_peek("http://x", "ctrl+shift+z", "hold",
                                  "ctrl+alt+b")
        assert pw._hotkey == "ctrl+shift+y"
        assert pw._browser_hotkey == "alt+q"
        assert ("peek", "ctrl+shift+z") in calls
        assert ("browser", "ctrl+alt+b", "ctrl+shift+y") in calls

    def test_peek_modes_are_defined_once(self):
        import power_atlas.config as config_mod
        import power_atlas.hotkeys as hotkeys_mod
        import power_atlas.peek as peek_mod
        assert config_mod.PEEK_MODES is hotkeys_mod.PEEK_MODES
        assert peek_mod._PEEK_MODES is hotkeys_mod.PEEK_MODES
        assert (peek_mod.HOLD, peek_mod.TOGGLE) == ("hold", "toggle")
        # By name, not by position (final review cycle 2, fix 12).
        assert peek_mod.HOLD is hotkeys_mod.HOLD
        assert peek_mod.TOGGLE is hotkeys_mod.TOGGLE
        assert hotkeys_mod.PEEK_MODES == (hotkeys_mod.HOLD, hotkeys_mod.TOGGLE)
        assert hotkeys_mod.DEFAULT_PEEK_MODE == "hold"
        assert config_mod.Config().peek_mode == hotkeys_mod.DEFAULT_PEEK_MODE
        assert config_mod._normalize_peek_mode("x") == "hold"


# ---- 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2

class TestSignInOrigin:
    """Fix 5: WebView2 reads the jar for the current URL, so a window that
    navigated in place to another origin has no `pa_local` there without
    being signed out. Only the server's own scheme, host and port count."""

    SECRET = "S" * 43

    def _run(self, monkeypatch, url):
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        monkeypatch.setattr(web_mod, "_LOCAL_SECRET", self.SECRET)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        pw, a = _ready_peek(monkeypatch)
        a.cookie = ""
        a.url = url
        pw._handle(("show_app",))
        return [c for c in a.calls if c[0] == "reload"]

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:4915/acp",
        "http://127.0.0.1:4915/local-auth?x=1",
        "HTTP://127.0.0.1:4915/",
    ])
    def test_the_servers_origin_re_signs(self, monkeypatch, url):
        assert len(self._run(monkeypatch, url)) == 1

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:4916/acp",       # another port
        "http://localhost:4915/acp",       # another host name
        "https://127.0.0.1:4915/acp",      # another scheme
        "http://127.0.0.1/acp",            # no port
        "https://example.com/4915",
        None, "", "about:blank",
    ])
    def test_another_or_unknown_origin_is_left_alone(self, monkeypatch, url):
        assert self._run(monkeypatch, url) == []


class TestSignInReloadNeverLogsTheUrl:
    """Fix 3: a cookie-path re-sign whose reload raises with the URL in its
    message logs the exception type only."""

    def test_a_raising_reload_logs_the_type_only(self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        from power_atlas import web as web_mod
        monkeypatch.setattr(web_mod, "_LOCAL_SECRET", "S" * 43)
        url = "http://127.0.0.1:4915/local-auth?code=SECRETCODE123"
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: url)
        pw, a = _ready_peek(monkeypatch)
        a.cookie = ""

        def reload(u):
            a.calls.append(("reload", u))
            raise RuntimeError(f"navigation to {u} failed")

        a.reload = reload
        with caplog.at_level(logging.DEBUG):
            pw._handle(("press", "peek", 1000))
        assert ("reload", url) in a.calls, "the cookie path re-signed"
        assert "could not sign in again: RuntimeError" in caplog.text
        records = "\n".join(r.getMessage() for r in caplog.records)
        for text in (caplog.text, records):
            assert "SECRETCODE123" not in text
            assert "code=" not in text and "/local-auth" not in text


class TestMalformedEventKeepsTheWorker:
    """Fix 7: `_control` runs inside the loops' try, so a malformed event is
    logged and skipped, and later events still run, before and after
    readiness."""

    def test_before_and_after_readiness(self, monkeypatch, caplog):
        import threading
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_event_tick_now", lambda: 100)
        a = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: a)
        pw._events.put(("browser",))  # no tick: IndexError in `_control`
        pw._events.put(("browser", 100))
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            t = threading.Thread(target=pw._window_worker, daemon=True)
            t.start()
            assert pw._ready.wait(5)
            pw._events.put(("browser",))
            pw._events.put(("browser", 5000))
            pw._events.put(("stop",))
            t.join(5)
        assert not t.is_alive()
        assert len(opened) == 2
        assert caplog.text.count("browser failed: IndexError") == 2


class TestChromeHandleUnchangedMode:
    """Fix 8: the unchanged-mode branch of `_set_chrome` returns the handle
    as it is now, not the cached one."""

    def test_returns_the_fresh_handle(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, a, seen = TestHandleKeptOnTheWorker()._adapter(monkeypatch)
        a._chrome = peek_mod.PEEK
        assert pw._hwnd == 0x1111
        a.show_peek()
        assert pw._hwnd == 0x2222


class TestAdapterProtocolCoversTheWorker:
    """Fix 9: every member the worker reads off its adapter is declared in
    `_WindowAdapter`. Source check over `self._adapter.<name>` and over
    locals bound to `self._adapter`."""

    def test_every_used_member_is_declared(self):
        import ast
        import inspect
        import power_atlas.peek as peek_mod
        tree = ast.parse(inspect.getsource(peek_mod.PeekWindow))

        def is_self_adapter(node):
            return (isinstance(node, ast.Attribute) and node.attr == "_adapter"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self")

        used = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            aliases = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                       and is_self_adapter(n.value)
                       for t in n.targets if isinstance(t, ast.Name)}
            for n in ast.walk(fn):
                if not isinstance(n, ast.Attribute):
                    continue
                if is_self_adapter(n.value) or (
                        isinstance(n.value, ast.Name) and n.value.id in aliases):
                    used.add(n.attr)
        proto = peek_mod._WindowAdapter
        declared = ({n for n in vars(proto) if not n.startswith("_")}
                    | set(proto.__annotations__))
        # The check sees the worker's real calls, aliases included.
        assert {"show_peek", "apply_app", "read_cookies", "reload",
                "fire_reset_overlays", "restore_foreground"} <= used
        assert sorted(used - declared) == []

    def test_annotations(self):
        import inspect
        import power_atlas.peek as peek_mod
        ann = peek_mod._WindowAdapter.apply_app.__annotations__
        assert ann["return"] == (object | None)
        init = inspect.getsource(peek_mod.PeekWindow.__init__)
        assert 'self._adapter: "_WindowAdapter | None" = None' in init


class TestConstructorPeekHotkey:
    """Fix 10: `PeekWindow` applies the peek shortcut in force, as
    `create_peek` does: an invalid one becomes the default."""

    @pytest.mark.parametrize("bad", ["ctrl+shift", "z", "ctrl+shift+nope",
                                     "ctrl+esc"])
    def test_an_invalid_peek_shortcut_is_the_default(self, monkeypatch,
                                                      caplog, bad):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw = peek_mod.PeekWindow("http://x", bad)
        assert pw._hotkey == "ctrl+shift+z"
        assert pw._trigger_keys == {"ctrl", "shift", "z"}
        assert pw._chords["peek"] == {"ctrl", "shift", "z"}
        assert "is invalid" in caplog.text

    def test_a_valid_one_is_kept(self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", True)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw = peek_mod.PeekWindow("http://x", "alt+q", browser_hotkey="ctrl+alt+b")
        assert pw._hotkey == "alt+q"
        assert pw._chords == {"peek": {"alt", "q"},
                              "browser": {"ctrl", "alt", "b"}}
        assert caplog.text == ""


class TestResetFiredOnlyWhenItReturned:
    """Fix 11: `_end_peek` counts `resetOverlays` as fired only once the call
    returned, so a raising one is tried again by `_fail_to_hidden`."""

    def test_a_reset_that_raises_once_is_retried(self, monkeypatch):
        pw, a = _ready_peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        a.calls.clear()
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("no form")
            a.calls.append(("reset_overlays",))

        monkeypatch.setattr(a, "fire_reset_overlays", flaky)
        with pytest.raises(RuntimeError):
            pw._handle(("esc",))
        assert len(attempts) == 2
        assert a.calls.count(("reset_overlays",)) == 1

    def test_a_reset_that_returned_is_not_fired_again(self, monkeypatch):
        pw, a = _ready_peek(monkeypatch)
        pw._handle(("press", "peek", 1000))
        a.calls.clear()
        orig_hide = a.hide

        def broken():
            orig_hide()
            raise OSError(5, "denied")

        monkeypatch.setattr(a, "hide", broken)
        with pytest.raises(OSError):
            pw._handle(("esc",))
        assert a.calls.count(("reset_overlays",)) == 1


class TestStaleQueuedBrowserPress:
    """Fix 13: a browser press still queued when the worker first drains is
    dropped once older than 1 s, with a DEBUG line; a fresh one opens. The
    age is masked, so the tick wrap is handled."""

    def _run(self, monkeypatch, now, ticks, caplog=None):
        import threading
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_event_tick_now", lambda: now)
        monkeypatch.setattr(pw, "_establish_ready", lambda: pw._adapter)
        for t in ticks:
            pw._events.put(("browser", t))
        pw._events.put(("stop",))
        th = threading.Thread(target=pw._window_worker, daemon=True)
        th.start()
        th.join(5)
        assert not th.is_alive()
        return len(opened)

    def test_both_sides_of_one_second(self, monkeypatch, caplog):
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            assert self._run(monkeypatch, 11000, [9999]) == 0  # 1001 ms old
        assert "dropped a press made 1001 ms before" in caplog.text
        assert self._run(monkeypatch, 11000, [10000]) == 1  # 1000 ms old

    def test_the_tick_wrap(self, monkeypatch):
        # 0xFFFFFF00 is 0x300 (768 ms) before 0x200 across the wrap: fresh.
        assert self._run(monkeypatch, 0x200, [0xFFFFFF00]) == 1
        # 0xFFFFF000 is 0x1200 (4608 ms) before 0x200: stale.
        assert self._run(monkeypatch, 0x200, [0xFFFFF000]) == 0

    def test_only_the_drain_drops_stale_presses(self, monkeypatch):
        """After readiness a press is never judged by age (the main loop
        gets it as it happens)."""
        import threading
        import power_atlas.peek as peek_mod
        pw = _two_chord_peek(monkeypatch)
        opened = []
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        monkeypatch.setattr(peek_mod, "_event_tick_now", lambda: 10**6)
        monkeypatch.setattr(pw, "_establish_ready", lambda: pw._adapter)
        th = threading.Thread(target=pw._window_worker, daemon=True)
        th.start()
        assert pw._ready.wait(5)
        pw._events.put(("browser", 0))
        pw._events.put(("stop",))
        th.join(5)
        assert len(opened) == 1

    def test_the_event_clock_is_the_hook_clock_on_windows(self, monkeypatch):
        import ctypes
        import power_atlas.peek as peek_mod
        if sys.platform != "win32":
            pytest.skip("GetTickCount is Windows only")
        before = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        now = peek_mod._event_tick_now()
        after = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        assert ((now - before) & 0xFFFFFFFF) <= ((after - before) & 0xFFFFFFFF)


class TestStartAfterStop:
    """Fix 14: `start()` after `stop()` starts nothing: no worker, no
    keyboard hook, no webview."""

    def test_start_after_stop_starts_nothing(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_EXIT_WATCHDOG", 0.05)
        built = []
        monkeypatch.setattr(peek_mod, "keyboard",
                            MagicMock(Listener=lambda **kw: built.append(kw)),
                            raising=False)
        pw = _new_peek(monkeypatch)
        ran = []
        monkeypatch.setattr(pw, "_run_webview", lambda: ran.append(1))
        pw.stop()
        pw.start(on_main_thread=True)
        assert built == []
        assert pw._worker is None
        assert pw._listener is None
        assert ran == []
        assert pw._start_returned.is_set()

    def test_start_before_stop_starts_the_listener(self, monkeypatch):
        import power_atlas.peek as peek_mod
        listener = MagicMock()
        monkeypatch.setattr(peek_mod, "keyboard",
                            MagicMock(Listener=lambda **kw: listener),
                            raising=False)
        pw = _new_peek(monkeypatch)
        monkeypatch.setattr(pw, "_window_worker", lambda: None)
        monkeypatch.setattr(pw, "_run_webview", lambda: None)
        pw.start(on_main_thread=True)
        assert pw._listener is listener
        listener.start.assert_called_once()


class TestLogVocabulary:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5
    (follow-up 13): log lines call the window "PowerAtlas window", never
    "Peek ...", except the smoke token `Peek hotkey listener started` (section
    7 and AGENTS.md grep for it) and "Peek shortcut", the setting's name."""

    _ALLOWED = ("Peek hotkey listener started", "Peek shortcut ")

    def _log_messages(self):
        import ast
        import inspect
        import power_atlas.peek as peek_mod
        tree = ast.parse(inspect.getsource(peek_mod))
        out = []
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "log" and n.args):
                first = n.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value,
                                                                  str):
                    out.append(first.value)
                elif isinstance(first, ast.JoinedStr):
                    out.append("".join(v.value for v in first.values
                                       if isinstance(v, ast.Constant)))
        return out

    def test_no_line_says_peek_for_the_window(self):
        messages = self._log_messages()
        assert len(messages) > 30  # the scan sees the module's log calls
        bad = [m for m in messages if m.startswith("Peek")
               and not m.startswith(self._ALLOWED)]
        assert bad == []

    def test_the_smoke_token_is_kept(self):
        assert any(m.startswith("Peek hotkey listener started (")
                   for m in self._log_messages())

    def test_the_unavailable_error_names_the_window(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_AVAILABLE", False)
        monkeypatch.setattr(peek_mod, "_IMPORT_ERROR", "no pynput")
        with pytest.raises(RuntimeError,
                           match="^PowerAtlas window unavailable: no pynput$"):
            peek_mod.PeekWindow("http://127.0.0.1:4915")


class TestBrowserKeys:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5
    (follow-up 5): pywebview runs with `debug=False`, which turns WebView2's
    browser accelerator keys and default context menu off in
    `on_webview_ready` (edgechromium.py, the only place it sets them). The
    worker turns both on once a page has loaded (`CoreWebView2` exists by
    then), through a bounded UI-thread call, and never from the hook. Since
    the Phase 5 review (A7) it sets them again on every page load, so a
    recreated WebView2 core gets them back; failures stay quiet after the
    first WARNING."""

    def _run_worker(self, pw, a, before=(), after=()):
        import threading
        from power_atlas import web as web_mod
        pw._signed_gen = web_mod.local_secret_generation()
        for ev in before:
            ev()
        pw._establish_ready = lambda: a
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        assert pw._ready.wait(5)
        for ev in after:
            ev()
        _post(pw, "stop")
        t.join(5)
        assert not t.is_alive()

    def test_a_load_before_readiness_is_not_lost(self, monkeypatch):
        """The pre-ready drain drops the `loaded` event; the latch it set
        still turns the keys on once the window is ready. A later load sets
        them again (A7: once at readiness, once for the load: 2)."""
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        self._run_worker(pw, a, before=[pw._on_loaded],
                         after=[lambda: _post(pw, "loaded")])
        assert a.browser_keys == 2
        assert pw._browser_keys_on is True

    def test_every_load_sets_them_again(self, monkeypatch, caplog):
        """A7: one call per load (was: once per run), and one INFO line.
        The first load comes before readiness (its event is drained; the
        latch makes readiness call once), so the count does not depend on
        whether the worker sees a load's latch before or after readiness."""
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        with caplog.at_level(logging.INFO, logger="power_atlas"):
            self._run_worker(pw, a, before=[pw._on_loaded],
                             after=[pw._on_loaded, pw._on_loaded,
                                    pw._on_loaded])
        assert a.browser_keys == 4
        on = [r for r in caplog.records
              if "browser keys and the context menu are on" in r.getMessage()]
        assert len(on) == 1

    def test_no_load_no_call(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        self._run_worker(pw, a)
        assert a.browser_keys == 0
        assert pw._browser_keys_on is False

    def test_a_failed_attempt_is_retried_on_the_next_load(self, monkeypatch):
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        a.keys_ok = False
        pw._adapter = a
        pw._ready.set()
        pw._handle(("loaded",))
        assert pw._browser_keys_on is False
        a.keys_ok = True
        pw._handle(("loaded",))
        pw._handle(("loaded",))
        # A7: every load calls (was 2: none after the first success).
        assert a.browser_keys == 3
        assert pw._browser_keys_on is True

    @pytest.mark.parametrize("failure", ["false", "raise"])
    def test_failures_warn_once_then_stay_quiet(self, monkeypatch, caplog,
                                                failure):
        """A7: the first failure is one WARNING (type only); the next ones
        are DEBUG, and a raising call never escapes the handler."""
        pw = _new_peek(monkeypatch)
        a = _FakeAdapter()
        if failure == "false":
            a.keys_ok = False
        else:
            def boom():
                a.browser_keys += 1
                raise OSError("SECRET-DETAIL")
            a.enable_browser_keys = boom
        pw._adapter = a
        pw._ready.set()
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            for _ in range(4):
                pw._handle(("loaded",))
        assert a.browser_keys == 4
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "browser keys" in warnings[0].getMessage()
        if failure == "raise":
            assert "OSError" in warnings[0].getMessage()
        assert "SECRET-DETAIL" not in caplog.text
        assert pw._browser_keys_on is False

    def test_the_win32_adapter_sets_both_settings_on_the_ui_thread(
            self, monkeypatch, caplog):
        import types
        import power_atlas.peek as peek_mod
        _fake_dotnet(monkeypatch)
        pw = _new_peek(monkeypatch)
        native = _FakeNative()
        ran_on_ui = []
        settings = types.SimpleNamespace(
            AreBrowserAcceleratorKeysEnabled=False,
            AreDefaultContextMenusEnabled=False,
            AreDevToolsEnabled=False)
        native.webview = types.SimpleNamespace(CoreWebView2=None)
        orig = native.BeginInvoke

        def begin(action):
            ran_on_ui.append(1)
            orig(action)

        native.BeginInvoke = begin
        a = peek_mod._Win32Window(pw, types.SimpleNamespace(native=native))
        a._native = native
        # No CoreWebView2 yet: nothing set, and the caller retries.
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            assert a.enable_browser_keys() is False
            native.webview.CoreWebView2 = types.SimpleNamespace(
                Settings=settings)
            assert a.enable_browser_keys() is True
        assert settings.AreBrowserAcceleratorKeysEnabled is True
        assert settings.AreDefaultContextMenusEnabled is True
        assert settings.AreDevToolsEnabled is False  # never turned on
        assert len(ran_on_ui) == 2
        # A7: it runs on every page load, so the adapter logs nothing; the
        # worker logs the first success and the first failure.
        assert caplog.records == []

    def test_the_win32_adapter_times_out_quietly(self, monkeypatch, caplog):
        """A7: a UI thread that does not answer is False, without the
        bounded call's own WARNING (the worker logs the first failure)."""
        import types
        import power_atlas.peek as peek_mod
        _fake_dotnet(monkeypatch)
        monkeypatch.setattr(peek_mod, "_UI_TIMEOUT", 0.05)
        pw = _new_peek(monkeypatch)
        native = _FakeNative()
        native.BeginInvoke = lambda action: None  # never runs
        a = peek_mod._Win32Window(pw, types.SimpleNamespace(native=native))
        a._native = native
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            assert a.enable_browser_keys() is False
        assert caplog.records == []

    def test_the_portable_adapter_has_nothing_to_do(self, monkeypatch):
        import types
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        a = peek_mod._PortableWindow(pw, types.SimpleNamespace())
        assert a.enable_browser_keys() is True

    def test_run_webview_watches_page_loads(self, monkeypatch):
        """The handler is attached before `webview.start`, so the first
        load cannot come before it."""
        import types
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        order = []

        class _Loaded:
            def __iadd__(self, handler):
                order.append(("subscribe", handler))
                return self

        win = types.SimpleNamespace(events=types.SimpleNamespace(
            loaded=_Loaded()))

        class _Webview:
            @staticmethod
            def create_window(*a, **kw):
                return win

            @staticmethod
            def start(**kw):
                order.append(("start",))

        monkeypatch.setattr(peek_mod, "webview", _Webview, raising=False)
        monkeypatch.setattr(peek_mod, "_login_url", lambda u: u + "/signed")
        pw._run_webview()
        assert [o[0] for o in order] == ["subscribe", "start"]
        assert order[0][1] == pw._on_loaded


def _feed_filter(pw, msg, vk, t=0, flags=0):
    """One event through the Windows filter; True when it was suppressed."""
    try:
        pw._win32_event_filter(msg, _KbData(vk, t, flags))
    except _Suppress:
        return True
    return False


class TestStaleModifiers:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5
    (follow-up 14): a modifier key-up lost on the secure desktop leaves the
    modifier in `_pressed_keys`. Before matching a chord key-down, the filter
    asks the keyboard (`GetAsyncKeyState`, generic VK) about each tracked
    modifier and drops the ones that are up, so a partial chord never
    fires."""

    _VK_Z, _VK_Q, _VK_ESC = 0x5A, 0x51, 0x1B

    def _peek(self, monkeypatch, down, held=("ctrl", "shift")):
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        pw._adapter = _FakeAdapter()
        pw._listener = _Listener()
        pw._pressed_keys.update(held)
        asked = []

        def modifier_down(name):
            asked.append(name)
            return name in down

        monkeypatch.setattr(peek_mod, "_modifier_down", modifier_down)
        return pw, asked

    def test_a_stale_ctrl_does_not_fire_a_partial_chord(self, monkeypatch):
        """Tracked: ctrl and shift. Really down: shift only. `z` is then
        shift+z, not ctrl+shift+z: it passes, posts nothing, and only the
        stale modifier is dropped."""
        import power_atlas.peek as peek_mod
        pw, asked = self._peek(monkeypatch, down={"shift"})
        assert not _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=50)
        assert _drain(pw) == []
        assert pw._pressed_keys == {"shift"}
        assert sorted(asked) == ["ctrl", "shift"]

    def test_a_stale_shift_is_dropped_too(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch, down={"ctrl"})
        assert not _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=50)
        assert pw._pressed_keys == {"ctrl"}
        assert _drain(pw) == []

    def test_modifiers_really_down_fire_the_chord(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch, down={"ctrl", "shift"})
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=50)
        assert _drain(pw) == [("press", "peek", 50)]
        assert pw._pressed_keys == {"ctrl", "shift"}

    def test_only_tracked_modifiers_are_asked(self, monkeypatch):
        """Cheap: alt is not tracked, so it is not asked about; a key that is
        in no chord asks nothing at all."""
        import power_atlas.peek as peek_mod
        pw, asked = self._peek(monkeypatch, down={"ctrl", "shift", "alt"})
        _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Q, t=10)
        assert asked == []
        _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=20)
        assert sorted(asked) == ["ctrl", "shift"]

    def test_a_key_up_and_esc_ask_nothing(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, asked = self._peek(monkeypatch, down=set())
        _feed_filter(pw, peek_mod._WM_KEYUP, self._VK_Z)
        _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_ESC)
        assert asked == []
        assert pw._pressed_keys == {"ctrl", "shift"}

    def test_the_real_check_reads_the_generic_vk_high_bit(self, monkeypatch):
        """`GetAsyncKeyState` with VK_CONTROL (0x11), VK_SHIFT (0x10) and
        VK_MENU (0x12); down is the high bit (0x8000), not the low
        "pressed since last call" bit."""
        import power_atlas.peek as peek_mod
        states = {0x11: -0x8000, 0x10: 0x0001, 0x12: 0x0000}
        asked = []

        class _U:
            @staticmethod
            def GetAsyncKeyState(vk):
                asked.append(vk)
                return states[vk]

        monkeypatch.setattr(peek_mod.sys, "platform", "win32")
        monkeypatch.setattr(peek_mod, "_win32", lambda: (_U, None))
        check = peek_mod._real_modifier_down
        assert check("ctrl") is True
        assert check("shift") is False
        assert check("alt") is False
        assert asked == [0x11, 0x10, 0x12]

    @pytest.mark.parametrize("where", ["check", "user32"])
    def test_a_raising_check_keeps_the_modifier(self, monkeypatch, where):
        """Phase 5 review (A3): a check that raises (or a user32 bind that
        fails inside it) counts the modifier as down, as before the check
        existed, so the chord still fires instead of the filter raising."""
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch, down=set())

        def boom(*a):
            raise OSError("no user32")

        if where == "check":
            monkeypatch.setattr(peek_mod, "_modifier_down", boom)
        else:
            monkeypatch.setattr(peek_mod.sys, "platform", "win32")
            monkeypatch.setattr(peek_mod, "_win32", boom)
            monkeypatch.setattr(peek_mod, "_modifier_down",
                                peek_mod._real_modifier_down)
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=50)
        assert _drain(pw) == [("press", "peek", 50)]
        assert pw._pressed_keys == {"ctrl", "shift"}
        assert pw._listener.suppressed == 1

    def test_off_windows_the_check_says_down(self, monkeypatch):
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod.sys, "platform", "linux")
        monkeypatch.setattr(peek_mod, "_win32",
                            lambda: (_ for _ in ()).throw(AssertionError))
        assert peek_mod._real_modifier_down("ctrl") is True


class TestEatenModifiers:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS
    follow-up 20: a modifier another program's hook ate after ours saw it
    (remote desktop, VM, remapper) never sets the async key state, so the
    stale-modifier check alone would drop it and stop the shortcut over that
    app. The check now drops a modifier only when the keyboard says it is up
    AND the filter's own last key-down for it (repeats included) is more
    than 2000 ms older than the chord key-down. Ages are hook ticks
    (`KBDLLHOOKSTRUCT.time`), compared modulo 2**32. VK values from
    winuser.h: VK_LCONTROL 0xA2, VK_RCONTROL 0xA3, VK_LSHIFT 0xA0, 'Z'
    0x5A."""

    _VK_Z, _LCTRL, _RCTRL, _LSHIFT = 0x5A, 0xA2, 0xA3, 0xA0

    def _peek(self, monkeypatch, down=frozenset()):
        """Ctrl and shift tracked as held; the keyboard says `down`."""
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        pw._adapter = _FakeAdapter()
        pw._listener = _Listener()
        pw._pressed_keys.update({"ctrl", "shift"})
        asked = []

        def modifier_down(name):
            asked.append(name)
            return name in down

        monkeypatch.setattr(peek_mod, "_modifier_down", modifier_down)
        return pw, asked

    def _mods_down(self, pw, *pairs):
        import power_atlas.peek as peek_mod
        for vk, t in pairs:
            assert not _feed_filter(pw, peek_mod._WM_KEYDOWN, vk, t=t)

    def test_recent_eaten_modifiers_still_fire_the_chord(self, monkeypatch):
        """Both key-downs were seen by our hook moments ago; the keyboard
        says neither is down (another hook ate them). The chord fires and
        the keyboard is not even asked."""
        import power_atlas.peek as peek_mod
        pw, asked = self._peek(monkeypatch)
        self._mods_down(pw, (self._LCTRL, 1000), (self._LSHIFT, 1400))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=2900)
        assert _drain(pw) == [("press", "peek", 2900)]
        assert pw._pressed_keys == {"ctrl", "shift"}
        assert asked == []

    def test_only_the_old_modifier_is_dropped(self, monkeypatch):
        """Ctrl down at 1000, shift at 2500, z at 3100: ctrl is 2100 ms old
        and dropped, shift 600 ms old and kept, so z is shift+z and passes
        with no event."""
        import power_atlas.peek as peek_mod
        pw, asked = self._peek(monkeypatch)
        self._mods_down(pw, (self._LCTRL, 1000), (self._LSHIFT, 2500))
        assert not _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=3100)
        assert _drain(pw) == []
        assert pw._pressed_keys == {"shift"}
        assert asked == ["ctrl"]

    @pytest.mark.parametrize("z_at, fires", [
        (2999, True),   # 1999 ms: kept
        (3000, True),   # exactly 2000 ms: kept (dropped only when older)
        (3001, False),  # 2001 ms: dropped
    ])
    def test_both_sides_of_the_threshold(self, monkeypatch, z_at, fires):
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch)
        # Shift is fresh in every case; only ctrl's age varies.
        self._mods_down(pw, (self._LCTRL, 1000), (self._LSHIFT, z_at - 10))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z,
                            t=z_at) is fires
        assert bool(_drain(pw)) is fires
        assert ("ctrl" in pw._pressed_keys) is fires

    def test_an_old_modifier_the_keyboard_says_is_down_is_kept(
            self, monkeypatch):
        """The age alone never drops: held still for 5 s and really down."""
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch, down={"ctrl", "shift"})
        self._mods_down(pw, (self._LCTRL, 1000), (self._LSHIFT, 1200))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=6000)
        assert _drain(pw) == [("press", "peek", 6000)]

    def test_repeats_keep_a_held_modifier_fresh(self, monkeypatch):
        """Ctrl went down at 0 and auto-repeated until 2900; the last
        key-down counts, so at 3100 it is 200 ms old."""
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch)
        self._mods_down(pw, *[(self._LCTRL, t) for t in (0, 500, 1700, 2900)])
        self._mods_down(pw, (self._LSHIFT, 3000))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=3100)
        assert [e[0] for e in _drain(pw)] == ["press"]

    @pytest.mark.parametrize("z_at, fires", [(0x500, True), (0x900, False)])
    def test_the_age_wraps_with_the_tick(self, monkeypatch, z_at, fires):
        """Ctrl down at 0xFFFFFF00, just before GetTickCount wraps: z at
        0x500 is 0x600 = 1536 ms later (kept), at 0x900 2560 ms (dropped)."""
        import power_atlas.peek as peek_mod
        pw, _ = self._peek(monkeypatch)
        self._mods_down(pw, (self._LCTRL, 0xFFFFFF00), (self._LSHIFT, z_at - 5))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z,
                            t=z_at) is fires

    def test_the_right_hand_key_counts_and_a_key_up_forgets(self,
                                                            monkeypatch):
        """Right Ctrl records "ctrl" too. Its key-up removes the tick, so a
        ctrl still tracked afterwards (a lost `_on_release`) counts as old
        and is checked against the keyboard again."""
        import power_atlas.peek as peek_mod
        pw, asked = self._peek(monkeypatch)
        self._mods_down(pw, (self._RCTRL, 1000), (self._LSHIFT, 1100))
        assert pw._modifier_tick == {"ctrl": 1000, "shift": 1100}
        assert not _feed_filter(pw, peek_mod._WM_KEYUP, self._RCTRL, t=1200)
        assert pw._modifier_tick == {"shift": 1100}
        assert not _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=1300)
        assert asked == ["ctrl"]
        assert pw._pressed_keys == {"shift"}
        assert _drain(pw) == []


class TestAltMask:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5
    (follow-up 10): with a chord containing alt, the user's app sees Alt
    down and Alt up around the suppressed key and would activate its menu
    bar. The filter sends a mask key (VK 0xE8, unassigned) right after
    suppressing the chord's key-down, as AutoHotkey does, so the app sees
    Alt+mask and the Alt key-up opens no menu. The filter ignores the mask
    key it sent.

    Follow-up 18: a chord with two or more modifiers is masked too, alt or
    not. With Windows' optional Ctrl+Shift layout hotkey on, the default
    ctrl+shift+z would otherwise leave the user's app a bare Ctrl+Shift
    press and release, which switches the keyboard layout. A one-modifier
    chord without alt leaves a bare Ctrl (or Shift) press, which does
    nothing, and is not masked."""

    _VK_F1, _VK_Z, _VK_B = 0x70, 0x5A, 0x42

    def _peek(self, monkeypatch, peek, browser="", held=()):
        pw = _two_chord_peek(monkeypatch, peek=peek, browser=browser)
        pw._listener = _Listener()
        pw._pressed_keys.update(held)
        return pw

    def test_an_alt_chord_sends_one_mask_per_press(self, monkeypatch,
                                                   _no_real_keyboard):
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, "alt+f1", held=("alt",))
        assert _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_F1, t=10)
        assert sent == [1]
        # Auto-repeat: suppressed, no second mask.
        assert _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_F1, t=40)
        assert sent == [1]
        # The key-up: suppressed, no mask (Alt is still down).
        assert _feed_filter(pw, peek_mod._WM_SYSKEYUP, self._VK_F1)
        assert sent == [1]
        # A second press with Alt still held: another mask.
        assert _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_F1, t=300)
        assert sent == [1, 1]
        assert [e[0] for e in _drain(pw)] == ["press", "press"]

    @pytest.mark.parametrize("chord, held, vk", [
        ("ctrl+f1", ("ctrl",), 0x70),
        ("shift+f2", ("shift",), 0x71),
    ])
    def test_a_one_modifier_chord_without_alt_sends_none(
            self, monkeypatch, _no_real_keyboard, chord, held, vk):
        """Follow-up 18, the lower side of the boundary: one modifier, no
        alt. (Before follow-up 18 this test used ctrl+shift+z, which the
        follow-up now masks.)"""
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, chord, held=held)
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, vk, t=10)
        assert sent == []
        assert [e[0] for e in _drain(pw)] == ["press"]

    def test_a_ctrl_shift_chord_sends_one_mask_per_press(self, monkeypatch,
                                                         _no_real_keyboard):
        """Follow-up 18, the upper side: two modifiers, no alt (the default
        shortcut). One mask on the press, none on a repeat or the key-up,
        another on a second press with the modifiers still held."""
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, "ctrl+shift+z", held=("ctrl", "shift"))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=10)
        assert sent == [1]
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=45)
        assert sent == [1], "a repeat sends no second mask"
        assert _feed_filter(pw, peek_mod._WM_KEYUP, self._VK_Z)
        assert sent == [1], "the key-up sends none"
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_Z, t=400)
        assert sent == [1, 1]
        assert [e[0] for e in _drain(pw)] == ["press", "press"]

    def test_a_two_modifier_browser_chord_without_alt_masks(
            self, monkeypatch, _no_real_keyboard):
        """Follow-up 18 applies to either chord: ctrl+shift+b as the browser
        shortcut beside a one-modifier peek shortcut."""
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, "ctrl+f1", browser="ctrl+shift+b",
                        held=("ctrl", "shift"))
        assert _feed_filter(pw, peek_mod._WM_KEYDOWN, self._VK_B, t=10)
        assert sent == [1]
        assert _drain(pw) == [("browser", 10)]

    @pytest.mark.parametrize("keys, want", [
        ({"ctrl", "f1"}, False),
        ({"shift", "f1"}, False),
        ({"alt", "f1"}, True),
        ({"ctrl", "shift", "z"}, True),
        ({"ctrl", "alt", "b"}, True),
        ({"shift", "alt", "q"}, True),
        ({"ctrl", "shift", "alt", "k"}, True),
    ])
    def test_needs_mask(self, keys, want):
        """Expected values from follow-up 18: alt, or two or more
        modifiers."""
        import power_atlas.peek as peek_mod
        assert peek_mod._needs_mask(frozenset(keys)) is want

    def test_the_browser_chord_masks_too(self, monkeypatch,
                                         _no_real_keyboard):
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, "ctrl+shift+z", browser="ctrl+alt+b",
                        held=("ctrl", "alt"))
        assert _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_B, t=10)
        assert sent == [1]
        assert _drain(pw) == [("browser", 10)]

    def test_a_key_that_passes_sends_none(self, monkeypatch,
                                          _no_real_keyboard):
        """Alt+F1 with the peek chord alt+f2: F1 is not a chord key."""
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, "alt+f2", held=("alt",))
        assert not _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_F1)
        assert sent == []

    @pytest.mark.parametrize("flags", [0, 0x10])
    def test_the_mask_key_is_ignored(self, monkeypatch, _no_real_keyboard,
                                     flags):
        """Its key-down and key-up pass untouched and change nothing,
        whether or not Windows marks them injected."""
        import power_atlas.peek as peek_mod
        sent = _no_real_keyboard
        pw = self._peek(monkeypatch, "alt+f1", held=("alt",))
        before = (set(pw._pressed_keys), dict(pw._triggered),
                  dict(pw._chord_down))
        for msg in (peek_mod._WM_SYSKEYDOWN, peek_mod._WM_SYSKEYUP):
            assert not _feed_filter(pw, msg, peek_mod._VK_MASK, flags=flags)
        assert (set(pw._pressed_keys), dict(pw._triggered),
                dict(pw._chord_down)) == before
        assert _drain(pw) == [] and sent == []
        assert peek_mod._VK_MASK == 0xE8
        assert peek_mod._VK_MASK not in peek_mod._hotkeys.VK_NAMES

    def test_a_failing_send_still_suppresses_and_posts(self, monkeypatch,
                                                       caplog):
        """The mask is best effort: a raising send is logged by type and
        never escapes the filter (pynput would stop the listener)."""
        import power_atlas.peek as peek_mod

        def boom():
            raise OSError("send failed")

        monkeypatch.setattr(peek_mod, "_send_mask_key", boom)
        pw = self._peek(monkeypatch, "alt+f1", held=("alt",))
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            assert _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_F1,
                                t=10)
        assert _drain(pw) == [("press", "peek", 10)]
        assert pw._listener.suppressed == 1
        assert "mask key" in caplog.text and "OSError" in caplog.text

    def test_a_failing_send_is_logged_once_per_run(self, monkeypatch, caplog):
        """Phase 5 review (A2): the hook would log on every Alt chord; one
        WARNING per run, type only, and every press still suppressed and
        posted."""
        import power_atlas.peek as peek_mod
        tries = []

        def boom():
            tries.append(1)
            raise OSError("SECRET-DETAIL")

        monkeypatch.setattr(peek_mod, "_send_mask_key", boom)
        pw = self._peek(monkeypatch, "alt+f1", held=("alt",))
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            for t in (10, 300, 600):
                assert _feed_filter(pw, peek_mod._WM_SYSKEYDOWN, self._VK_F1,
                                    t=t)
                assert _feed_filter(pw, peek_mod._WM_SYSKEYUP, self._VK_F1)
        assert len(tries) == 3, "every press still tries the mask"
        assert [e[0] for e in _drain(pw)] == ["press"] * 3
        masks = [r for r in caplog.records if "mask key" in r.getMessage()]
        assert len(masks) == 1 and masks[0].levelno == logging.WARNING
        assert "OSError" in masks[0].getMessage()
        assert "SECRET-DETAIL" not in caplog.text

    def test_the_real_send_is_a_down_and_an_up_of_0xe8(self, monkeypatch):
        import power_atlas.peek as peek_mod
        calls = []

        class _U:
            @staticmethod
            def keybd_event(vk, scan, flags, extra):
                calls.append((vk, scan, flags, extra))

        monkeypatch.setattr(peek_mod, "_win32", lambda: (_U, None))
        peek_mod._real_send_mask_key()
        assert calls == [(0xE8, 0, 0, 0), (0xE8, 0, 0x2, 0)]


class TestListenerHealth:
    """261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5
    (follow-up 15): the worker, never the hook, checks the pynput listener
    every `_LISTENER_CHECK_INTERVAL` seconds and logs one WARNING per run
    when its thread has died or it no longer says it is running."""

    class _Lst:
        def __init__(self, alive=True, running=True):
            self.alive = alive
            self.running = running

        def is_alive(self):
            return self.alive

    def _peek(self, monkeypatch, listener):
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        pw._listener = listener
        clock = [1000.0]
        monkeypatch.setattr(peek_mod, "_now", lambda: clock[0])
        return pw, clock

    def _warnings(self, caplog):
        return [r for r in caplog.records if r.levelno == logging.WARNING
                and "hotkey listener" in r.getMessage()]

    @pytest.mark.parametrize("alive,running", [(False, True), (True, False),
                                               (False, False)])
    def test_a_stopped_listener_is_reported_once(self, monkeypatch, caplog,
                                                 alive, running):
        import power_atlas.peek as peek_mod
        pw, clock = self._peek(monkeypatch, self._Lst(alive, running))
        step = peek_mod._LISTENER_CHECK_INTERVAL
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw._check_listener()  # the first call only starts the clock
            assert self._warnings(caplog) == []
            clock[0] += step - 0.5  # not yet due
            pw._check_listener()
            assert self._warnings(caplog) == []
            clock[0] += 0.5  # due
            pw._check_listener()
            assert len(self._warnings(caplog)) == 1
            for _ in range(3):
                clock[0] += step
                pw._check_listener()
        msgs = [r.getMessage() for r in self._warnings(caplog)]
        assert len(msgs) == 1
        assert f"thread alive: {alive}, running: {running}" in msgs[0]
        assert not msgs[0].startswith("Peek")

    def test_a_healthy_listener_is_silent(self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        pw, clock = self._peek(monkeypatch, self._Lst())
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            for _ in range(4):
                pw._check_listener()
                clock[0] += peek_mod._LISTENER_CHECK_INTERVAL
        assert self._warnings(caplog) == []

    def test_a_listener_that_dies_later_is_reported(self, monkeypatch,
                                                    caplog):
        import power_atlas.peek as peek_mod
        lst = self._Lst()
        pw, clock = self._peek(monkeypatch, lst)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            pw._check_listener()
            clock[0] += peek_mod._LISTENER_CHECK_INTERVAL
            pw._check_listener()
            assert self._warnings(caplog) == []
            lst.alive = False
            clock[0] += peek_mod._LISTENER_CHECK_INTERVAL
            pw._check_listener()
        assert len(self._warnings(caplog)) == 1

    @pytest.mark.parametrize("why", ["stopping", "no listener"])
    def test_nothing_is_reported_while_stopping_or_without_one(
            self, monkeypatch, caplog, why):
        """`stop()` stops the listener on purpose; a listener that never
        started was already logged by `_start_listener`."""
        import power_atlas.peek as peek_mod
        pw, clock = self._peek(monkeypatch, self._Lst(alive=False))
        if why == "stopping":
            pw._stopping = True
        else:
            pw._listener = None
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            for _ in range(3):
                pw._check_listener()
                clock[0] += peek_mod._LISTENER_CHECK_INTERVAL
        assert self._warnings(caplog) == []

    def test_the_idle_worker_runs_the_check(self, monkeypatch, caplog):
        """No event needed: the worker's wait times out and checks."""
        import threading
        import time as _t
        import power_atlas.peek as peek_mod
        monkeypatch.setattr(peek_mod, "_LISTENER_CHECK_INTERVAL", 0.02)
        pw = _new_peek(monkeypatch)
        pw._listener = self._Lst(alive=False)
        a = _FakeAdapter()
        monkeypatch.setattr(pw, "_establish_ready", lambda: a)
        with caplog.at_level(logging.WARNING, logger="power_atlas"):
            t = threading.Thread(target=pw._window_worker, daemon=True)
            t.start()
            deadline = _t.monotonic() + 5
            while not self._warnings(caplog) and _t.monotonic() < deadline:
                _t.sleep(0.01)
            _post(pw, "stop")
            t.join(5)
        assert not t.is_alive()
        assert len(self._warnings(caplog)) == 1
        assert a.calls == []


class TestNewWindowRouting:
    """A link opened as a new window from the PowerAtlas window: one to the
    server's own origin goes to the browser through a login URL that lands on
    that page; any other goes to the real `webbrowser.open`, unchanged.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 6)
    """

    SERVER = "http://127.0.0.1:4915"

    class _RealBrowser:
        def __init__(self):
            self.opened = []
            self.name = "real"

        def open(self, url, *args, **kwargs):
            self.opened.append((url, args, kwargs))
            return True

    def _shim(self, monkeypatch, page="http://127.0.0.1:4915/acp?sid=a"):
        """The shim, with the window showing `page` (a list cell, so a test
        can navigate it) and a clock the test moves."""
        import power_atlas.peek as peek_mod
        pw, _ = _ready_peek(monkeypatch)
        real = self._RealBrowser()
        logins, opened = [], []
        self.page = [page]
        self.clock = [1000.0]
        monkeypatch.setattr(peek_mod, "_now", lambda: self.clock[0])

        def fake_login_url(server_url, next=None):
            logins.append((server_url, next))
            return f"LOGIN<{next}>"

        monkeypatch.setattr(peek_mod._doors, "login_url", fake_login_url)
        monkeypatch.setattr(peek_mod._doors, "open_in_browser", opened.append)
        shim = peek_mod._NewWindowBrowser(real, self.SERVER, pw._same_origin,
                                          lambda: self.page[0])
        return shim, real, logins, opened

    @pytest.mark.parametrize("url, target", [
        ("http://127.0.0.1:4915/acp?sid=x", "/acp?sid=x"),
        ("http://127.0.0.1:4915/", "/"),
        ("http://127.0.0.1:4915", "/"),
        ("http://127.0.0.1:4915/acp#frag", "/acp"),
        # WebView2 hands over the unescaped URI: a raw space is re-encoded.
        ("http://127.0.0.1:4915/acp?q=a b", "/acp?q=a%20b"),
        # Scheme and host are case-insensitive (RFC 3986 3.1, 3.2.2).
        ("HTTP://127.0.0.1:4915/acp?sid=x", "/acp?sid=x"),
    ])
    def test_same_origin_goes_through_a_login_url(self, monkeypatch, url,
                                                  target):
        shim, real, logins, opened = self._shim(monkeypatch)
        assert shim.open(url) is True
        assert logins == [(self.SERVER, target)]
        assert opened == [f"LOGIN<{target}>"]
        assert real.opened == []

    @pytest.mark.parametrize("url", [
        "https://evil.example/acp",
        "http://localhost:4915/acp",      # another host spelling
        "http://127.0.0.1:4916/acp",      # another port
        "https://127.0.0.1:4915/acp",     # another scheme
        "mailto:someone@example.com",
        # Phase 5 review (B3): userinfo spellings. The first is host `evil`
        # with user `127.0.0.1:4915`; the second is the server with a user,
        # which no PowerAtlas page links to.
        "http://127.0.0.1:4915@evil/acp",
        "http://x@127.0.0.1:4915/acp",
        # The default port is port 80, not the server's.
        "http://127.0.0.1/acp",
        "http://127.0.0.1:80/acp",
    ])
    def test_other_links_go_to_the_real_browser_unchanged(self, monkeypatch,
                                                          url):
        shim, real, logins, opened = self._shim(monkeypatch)
        shim.open(url, 2, True)
        assert real.opened == [(url, (2, True), {})]
        assert logins == [] and opened == []

    def test_other_attributes_are_the_real_modules(self, monkeypatch):
        shim, real, _, _ = self._shim(monkeypatch)
        assert shim.name == "real"

    @pytest.mark.parametrize("page", [
        "https://evil.example/",
        "http://localhost:4915/acp",
        "http://127.0.0.1:4916/",
        "http://127.0.0.1:4915@evil/",
        None,          # pywebview has no URL yet
        "",
        "about:blank",
    ])
    def test_a_link_from_another_origin_is_not_signed(self, monkeypatch,
                                                      page):
        """Phase 5 review (B1): the window navigated to another site (or
        its URL cannot be told), and that page opens a PowerAtlas link: it
        goes to the real browser unchanged, never through a login."""
        shim, real, logins, opened = self._shim(monkeypatch, page=page)
        url = "http://127.0.0.1:4915/acp?sid=x"
        shim.open(url)
        assert real.opened == [(url, (), {})]
        assert logins == [] and opened == []

    def test_an_unreadable_window_url_is_not_signed(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, _ = _ready_peek(monkeypatch)
        real = self._RealBrowser()
        logins = []
        monkeypatch.setattr(peek_mod._doors, "login_url",
                            lambda *a, **k: logins.append(1))

        def boom():
            raise RuntimeError("no window")

        shim = peek_mod._NewWindowBrowser(real, self.SERVER, pw._same_origin,
                                          boom)
        shim.open("http://127.0.0.1:4915/acp")
        assert real.opened == [("http://127.0.0.1:4915/acp", (), {})]
        assert logins == []

    def test_the_window_url_is_read_without_waiting(self, monkeypatch):
        """`_window_url` reads pywebview's recorded URL (`native.browser.url`)
        and never calls `get_current_url()`, which waits for the page."""
        import types
        pw, _ = _ready_peek(monkeypatch)

        class _Uri:
            def __str__(self):
                return "http://127.0.0.1:4915/acp"

        def waits():
            raise AssertionError("get_current_url waits for the page")

        pw._window = types.SimpleNamespace(
            get_current_url=waits,
            native=types.SimpleNamespace(
                browser=types.SimpleNamespace(url=_Uri())))
        assert pw._window_url() == "http://127.0.0.1:4915/acp"
        pw._window.native.browser.url = None
        assert pw._window_url() is None
        pw._window = types.SimpleNamespace(native=None)
        assert pw._window_url() is None

    def test_at_most_one_login_per_second(self, monkeypatch, caplog):
        """Phase 5 review (B1): a second same-origin link inside the window
        goes to the real browser unsigned (DEBUG); at the boundary it is
        signed again."""
        import power_atlas.peek as peek_mod
        shim, real, logins, opened = self._shim(monkeypatch)
        assert peek_mod._NEW_WINDOW_MIN_GAP == 1.0
        first = "http://127.0.0.1:4915/acp?sid=a"
        second = "http://127.0.0.1:4915/acp?sid=b"
        with caplog.at_level(logging.DEBUG, logger="power_atlas"):
            assert shim.open(first) is True
            self.clock[0] += 0.999
            shim.open(second, 2)
        assert logins == [(self.SERVER, "/acp?sid=a")]
        assert real.opened == [(second, (2,), {})]
        assert "unsigned" in caplog.text
        assert "sid=b" not in caplog.text
        # The unsigned one did not restart the window: 1.0 s after the
        # first login, the next link is signed.
        self.clock[0] += 0.001
        shim.open(second)
        assert logins[-1] == (self.SERVER, "/acp?sid=b")
        assert len(opened) == 2

    def test_a_failure_logs_the_type_only(self, monkeypatch, caplog):
        import power_atlas.peek as peek_mod
        shim, real, _, _ = self._shim(monkeypatch)
        url = "http://127.0.0.1:4915/acp?sid=SECRETSESSION"

        def quoting(u):
            raise OSError(f"cannot open {u}")

        monkeypatch.setattr(peek_mod._doors, "open_in_browser", quoting)
        with caplog.at_level(logging.DEBUG):
            assert shim.open(url) is True
        assert "could not open a link in the browser: OSError" in caplog.text
        assert "SECRETSESSION" not in caplog.text
        assert "LOGIN" not in caplog.text
        assert real.opened == []

    def test_installed_once_into_pywebviews_module(self, monkeypatch):
        import types
        import power_atlas.peek as peek_mod
        pw, _ = _ready_peek(monkeypatch)
        fake = types.ModuleType(peek_mod._NEW_WINDOW_MODULE)
        real = self._RealBrowser()
        fake.webbrowser = real
        monkeypatch.setitem(sys.modules, peek_mod._NEW_WINDOW_MODULE, fake)
        pw._route_new_windows()
        shim = fake.webbrowser
        assert isinstance(shim, peek_mod._NewWindowBrowser)
        assert shim._real is real
        assert shim._current_url == pw._window_url
        pw._route_new_windows()
        assert fake.webbrowser is shim, "a shim is never wrapped in a shim"

    def test_absent_module_is_not_imported(self, monkeypatch):
        import power_atlas.peek as peek_mod
        pw, _ = _ready_peek(monkeypatch)
        monkeypatch.delitem(sys.modules, peek_mod._NEW_WINDOW_MODULE,
                            raising=False)
        pw._route_new_windows()
        assert peek_mod._NEW_WINDOW_MODULE not in sys.modules

    def test_the_worker_installs_it_at_readiness(self, monkeypatch):
        import threading
        import types
        import power_atlas.peek as peek_mod
        pw = _new_peek(monkeypatch)
        fake = types.ModuleType(peek_mod._NEW_WINDOW_MODULE)
        fake.webbrowser = self._RealBrowser()
        monkeypatch.setitem(sys.modules, peek_mod._NEW_WINDOW_MODULE, fake)
        monkeypatch.setattr(pw, "_establish_ready", lambda: _FakeAdapter())
        pw._ready.clear()
        t = threading.Thread(target=pw._window_worker, daemon=True)
        t.start()
        assert pw._ready.wait(5)
        pw._events.put(("stop",))
        t.join(5)
        assert not t.is_alive()
        assert isinstance(fake.webbrowser, peek_mod._NewWindowBrowser)
