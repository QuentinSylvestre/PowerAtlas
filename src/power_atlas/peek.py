"""Peek window: hotkey-held native overlay showing the dashboard."""

import logging
import queue
import sys
import threading
import time

log = logging.getLogger("power_atlas.peek")

# Both peek doors — the webview and the double-tap browser — need the
# `pa_local` cookie, and the login code in this URL is exchanged for it on
# first load. The tray's helper, shared rather than copied; it imports `web`
# lazily, so this module still loads without the web app.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F11)
from .tray import _login_url
# The module, not the function: `_open_in_browser` is looked up at call time so
# the browser door here is the tray's own, patches included.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
from . import tray as _tray


def _clamp_pywebview_logger() -> None:
    """Hold pywebview's logger at INFO or above, whatever the environment says.

    pywebview reads ``PYWEBVIEW_LOG`` when it is imported and, at DEBUG, logs
    ``Loading URL: <url>`` for every navigation. Peek's URLs carry a live login
    code, and the record propagates to the root handler, which is
    ``orchestrator.log``. Only ever raises the level, so an explicit WARNING
    stays WARNING.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F6)
    """
    logger = logging.getLogger("pywebview")
    if logger.level < logging.INFO:  # NOTSET (0) included
        logger.setLevel(logging.INFO)


_AVAILABLE = True
_IMPORT_ERROR = ""
try:
    import webview
    from pynput import keyboard
except ImportError as e:
    _AVAILABLE = False
    _IMPORT_ERROR = str(e)
else:
    # After the import, which is where pywebview applies ``PYWEBVIEW_LOG``.
    _clamp_pywebview_logger()


def is_available() -> bool:
    """Return True if peek dependencies are importable."""
    return _AVAILABLE


# Two presses of the peek shortcut closer than this are a double-tap. Times are
# 32-bit millisecond ticks: the hook's own `KBDLLHOOKSTRUCT.time` on Windows,
# `time.monotonic()` elsewhere, so a press is timed when it happened rather
# than when the worker got to it. Differences are masked, so the tick wrapping
# every 49.7 days does not break a pair.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
_DOUBLE_TAP_MS = 500
_TICK_MASK = 0xFFFFFFFF

# How long the worker waits for a callable posted to the UI thread. Past it, a
# WARNING names the operation and the worker moves on: a hung UI thread must
# show in the log, not stall every later shortcut silently.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
_UI_TIMEOUT = 2.0
_READY_TIMEOUT = 30.0
_DESTROY_TIMEOUT = 5.0
_EXIT_WATCHDOG = 5.0

_RESET_OVERLAYS_JS = "if(typeof resetOverlays==='function') resetOverlays()"


# Window states.
HIDDEN, PEEK, APP = "hidden", "peek", "app"

# Win32 message constants seen by the keyboard filter.
_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
# VK codes for the modifiers pynput reports through `_on_press`/`_on_release`.
_VK_MODIFIERS = frozenset({
    0xA0, 0xA1,  # VK_LSHIFT, VK_RSHIFT
    0xA2, 0xA3,  # VK_LCONTROL, VK_RCONTROL
    0xA4, 0xA5,  # VK_LMENU, VK_RMENU (Alt)
    0x10, 0x11, 0x12,  # VK_SHIFT, VK_CONTROL, VK_MENU (generic)
})
_MODIFIER_NAMES = frozenset({"ctrl", "shift", "alt"})


def _tick_now() -> int:
    return int(time.monotonic() * 1000) & _TICK_MASK


class PeekWindow:
    """The PowerAtlas window and its global shortcut listener.

    Other threads use three never-raising members: `show_app()` (tray),
    `stop()` and `supports_app_mode`. Everything else that touches the window
    runs on the worker thread (`_window_worker`).
    """

    def __init__(self, server_url: str, hotkey: str = "ctrl+shift+z"):
        if not _AVAILABLE:
            raise RuntimeError(f"Peek unavailable: {_IMPORT_ERROR}")
        self._server_url = server_url
        self._hotkey = hotkey
        self._window = None
        self._listener = None
        # Hook-side state: read and written only by the listener thread.
        self._trigger_keys = self._parse_hotkey(hotkey)
        self._pressed_keys: set = set()
        self._triggered = False  # the peek chord's key is down (auto-repeat guard)
        self._chord_key_down = False  # the filter suppressed the chord key's key-down
        self._esc_down_suppressed = False  # the filter suppressed Esc's key-down
        self._release_armed = False  # a press was posted since the last release
        # Shared plumbing.
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._ready = threading.Event()
        self._window_created = threading.Event()
        self._start_returned = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopping = False
        # Written only by the worker; read by the keyboard filter (Esc).
        self._peek_showing = False
        # Worker-owned state.
        self._adapter = None
        self._state = HIDDEN
        self._return_to = None
        self._app_placement = None
        self._app_was_foreground = False
        self._tap_origin = None
        self._last_press = None
        self._signed_gen = None
        self._prev_foreground = None
        self._hwnd = None
        self._worker = None
        # `__main__`'s shutdown tail, run by the `stop()` watchdog when the UI
        # loop will not end. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        self.shutdown_tail = None

    # ---- public, any thread -------------------------------------------------

    @property
    def supports_app_mode(self) -> bool:
        """Windows, the window passed the readiness gate, and WebView2 is in use."""
        try:
            if sys.platform != "win32" or not self._ready.is_set():
                return False
            from webview.platforms import winforms
            return bool(winforms.is_chromium)
        except Exception:
            return False

    def show_app(self) -> None:
        """Tray Open PowerAtlas: post an app-mode event; never blocks or raises.

        Without app mode it opens the browser instead, signed in.
        """
        try:
            if self.supports_app_mode:
                self._events.put(("show_app",))
            else:
                self._open_browser()
        except Exception as e:
            log.warning("PowerAtlas window could not open: %s", type(e).__name__)

    def start(self, on_main_thread: bool = False) -> None:
        """Start the worker, the hotkey listener and the webview.

        Args:
            on_main_thread: If True, webview.start() is called on the
                current thread (blocks). If False, starts on a new thread.
        """
        self._worker = threading.Thread(target=self._window_worker,
                                        name="power-atlas-window", daemon=True)
        self._worker.start()
        self._start_listener()
        if on_main_thread:
            self._run_webview()  # blocks
        else:
            threading.Thread(target=self._run_webview, daemon=True).start()

    def stop(self) -> None:
        """Stop the listener and destroy the window. Idempotent and bounded.

        Final — call only at process exit. Never waits on the worker. If the
        destroy does not finish within 5 s the UI loop is asked to exit, and if
        `webview.start()` still has not returned 5 s later a watchdog runs
        `shutdown_tail`, so tray Quit and Restart always end the process.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        """
        with self._stop_lock:
            if self._stopping:
                return
            self._stopping = True
        try:
            if self._listener:
                self._listener.stop()
        except Exception as e:
            log.warning("Peek hotkey listener did not stop: %s", type(e).__name__)
        self._listener = None
        self._events.put(("stop",))
        win = self._window
        if win is None:
            return
        t = threading.Thread(target=self._destroy, args=(win,), daemon=True)
        t.start()
        t.join(_DESTROY_TIMEOUT)
        if not t.is_alive():
            return
        log.warning("PowerAtlas window did not close within %.0f s; asking the "
                    "UI loop to exit", _DESTROY_TIMEOUT)
        try:
            from System import Action  # type: ignore[import]
            import System.Windows.Forms as WinForms  # type: ignore[import]
            native = getattr(win, "native", None)
            if native is not None:
                native.BeginInvoke(Action(WinForms.Application.Exit))
        except Exception as e:
            log.warning("Could not post the UI loop exit: %s", type(e).__name__)
        threading.Thread(target=self._exit_watchdog, daemon=True).start()

    # ---- webview ------------------------------------------------------------

    @staticmethod
    def _destroy(win) -> None:
        try:
            win.destroy()
        except Exception as e:
            log.warning("PowerAtlas window destroy failed: %s", type(e).__name__)

    def _exit_watchdog(self) -> None:
        if self._start_returned.wait(_EXIT_WATCHDOG):
            return
        log.warning("The UI loop did not exit; running the shutdown tail")
        tail = self.shutdown_tail
        if tail is not None:
            tail()

    def _run_webview(self) -> None:
        """Create and run the pywebview window."""
        # The generation the creation URL signs in under, read before the
        # mint (D-13): a rotation between the two is then caught by the first
        # sign-in check. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        from .web import local_secret_generation
        self._signed_gen = local_secret_generation()
        # The webview has its own cookie jar, so it gets its own code at
        # creation. 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
        #
        # `on_top=False`: z-order is set only with `SetWindowPos`. The WinForms
        # `TopMost` property would be reapplied by WinForms on each style
        # update, and its setter may activate the window.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        self._window = webview.create_window(
            "PowerAtlas",
            _login_url(self._server_url),
            frameless=True,
            on_top=False,
            hidden=True,
            width=1,
            height=1,
        )
        self._window_created.set()
        try:
            webview.start(debug=False)
        finally:
            self._start_returned.set()

    # ---- worker -------------------------------------------------------------

    def _window_worker(self) -> None:
        """The one thread that touches the window. Loops on `_events`."""
        adapter = None
        try:
            adapter = self._establish_ready()
        except Exception as e:
            log.warning("PowerAtlas window readiness failed: %s: %s",
                        type(e).__name__, e)
        # Events that arrived before readiness are dropped, not replayed. The
        # drain comes before `_ready` is set: a tray Open PowerAtlas can only
        # post once `_ready` is set, so it lands after the drain and is
        # handled, never dropped with the stale ones.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 8
        while True:
            try:
                ev = self._events.get_nowait()
            except queue.Empty:
                break
            if ev[0] == "stop":
                return
            log.debug("PowerAtlas window: dropped %s before ready", ev[0])
        if adapter is not None:
            self._adapter = adapter
            self._ready.set()
            log.info("PowerAtlas window ready")
        while True:
            ev = self._events.get()
            if ev[0] == "stop":
                return
            if not self._ready.is_set():
                log.debug("PowerAtlas window not ready; dropped %s", ev[0])
                continue
            try:
                self._handle(ev)
            except Exception as e:
                # No URL reaches this line: the sign-in reload and the browser
                # door catch their own errors and log only the type.
                log.warning("PowerAtlas window: %s failed: %s: %s",
                            ev[0], type(e).__name__, e)

    def _establish_ready(self):
        """The readiness gate (Threading model). Returns the adapter, or None.

        `start(func=...)` runs before the form exists, so readiness waits for
        `events.shown` (set for a hidden window too), then checks the form and
        its handle on the UI thread, subscribes `FormClosing` and caches the
        HWND. The worker then sets `_ready` and logs `PowerAtlas window ready`.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        """
        if not self._window_created.wait(_READY_TIMEOUT):
            log.warning("PowerAtlas window was not created within %.0f s; app "
                        "mode stays unavailable", _READY_TIMEOUT)
            return None
        win = self._window
        deadline = time.monotonic() + _READY_TIMEOUT
        shown = win.events.shown.wait(_READY_TIMEOUT)
        if sys.platform != "win32":
            # Peek off Windows needs no form handle, so a missing `shown`
            # never turns it off. On GTK `shown` is set for a hidden window
            # too (`show()` runs `show_all()` before `hide()`, and the
            # WebView's `notify::visible` sets it); this only guards other
            # backends and future pywebview versions.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 13
            if not shown:
                log.warning("PowerAtlas window was not shown within %.0f s; "
                            "peek goes on regardless", _READY_TIMEOUT)
            return _PortableWindow(self, win)
        if not shown:
            log.warning("PowerAtlas window was not shown within %.0f s; app "
                        "mode stays unavailable", _READY_TIMEOUT)
            return None
        adapter = _Win32Window(self, win)
        # The whole gate fits one readiness timeout, `shown` included, with at
        # least one full UI-thread wait for the check itself.
        deadline = max(deadline, time.monotonic() + _UI_TIMEOUT)
        result = adapter.attach(deadline)
        if result == "timeout":
            log.warning("PowerAtlas window: the UI thread did not answer the "
                        "readiness check within %.0f s; app mode stays "
                        "unavailable", _READY_TIMEOUT)
            return None
        if result != "ready":
            log.warning("PowerAtlas window has no native form; app mode "
                        "stays unavailable")
            return None
        return adapter

    def _handle(self, ev: tuple) -> None:
        """Run one event through the state machine (worker thread only)."""
        kind = ev[0]
        if kind == "press":
            self._on_peek_press(ev[2])
        elif kind in ("release", "esc"):
            if self._state == PEEK:
                self._end_peek()
        elif kind == "user_close":
            if self._state == PEEK:
                self._end_peek()
            elif self._state == APP:
                self._save()
                self._to_hidden()
        elif kind == "show_app":
            self._to_app_focused()

    def _on_peek_press(self, t: int) -> None:
        last = self._last_press
        if last is not None and ((t - last) & _TICK_MASK) < _DOUBLE_TAP_MS:
            # A third tap is not another double-tap.
            self._last_press = None
            self._double_tap(self._tap_origin)
            return
        self._last_press = t
        app_fg = self._state == APP and self._adapter.is_foreground()
        self._tap_origin = (self._state, app_fg)
        # Hold mode: a press shows a peek; while one shows it is a no-op.
        if self._state == HIDDEN:
            self._to_peek(None)
        elif self._state == APP:
            self._save()
            self._to_peek("app")

    def _double_tap(self, origin) -> None:
        """The double-tap rule, decided from `tap_origin`, never the current state."""
        if not self._adapter.has_app_mode:
            # No app mode (non-Windows): the double-tap opens the browser.
            if self._state == PEEK:
                self._end_peek()
            self._open_browser()
            return
        if origin is not None and origin[0] == APP and origin[1]:
            if self._state == APP:
                self._save()
            self._to_hidden()
        else:
            self._to_app_focused()

    # ---- transitions (worker thread only) -----------------------------------

    def _save(self) -> None:
        """The Save rule: placement and foreground, on every exit from APP.

        A placement read that timed out (None) keeps the last saved one: the
        user's placement is never replaced by nothing.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 6
        """
        placement = self._adapter.get_placement()
        if placement is not None:
            self._app_placement = placement
        self._app_was_foreground = self._adapter.is_foreground()

    def _to_peek(self, return_to) -> None:
        self._sign_in_check()
        self._prev_foreground = self._adapter.foreground()
        self._adapter.show_peek()
        self._peek_showing = True
        self._state, self._return_to = PEEK, return_to

    def _fail_to_hidden(self) -> None:
        """A failed exit from PEEK: best-effort hide, then HIDDEN.

        Never stays PEEK: `_peek_showing` would stay True and the filter would
        swallow every Esc system-wide. The caller re-raises, so the worker
        still logs the failure.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 4
        """
        try:
            self._adapter.hide()
        except Exception:
            pass
        self._peek_showing = False
        self._state, self._return_to = HIDDEN, None

    def _end_peek(self) -> None:
        a = self._adapter
        try:
            if self._return_to == "app":
                # Ends in APP, so it signs in first (D-13).
                self._sign_in_check()
                pa_fg = a.is_foreground()
                a.apply_app(self._app_placement, focused=False)
                self._peek_showing = False
                if self._app_was_foreground:
                    # The chrome switch hides the window, which costs it the
                    # foreground; it was foreground before the peek, so it gets
                    # it back (SC-3). 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
                    a.focus()
                else:
                    # A maximized placement has no non-activating show, so
                    # the re-place itself may have taken the foreground:
                    # read it again after, not only before.
                    # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 2
                    pa_fg = pa_fg or a.is_foreground()
                    a.restore_foreground(self._prev_foreground, pa_fg)
                    a.put_below(self._prev_foreground)
                self._state, self._return_to = APP, None
            else:
                a.fire_reset_overlays()
                pa_fg = a.is_foreground()
                a.hide()
                self._peek_showing = False
                a.restore_foreground(self._prev_foreground, pa_fg)
                self._state, self._return_to = HIDDEN, None
        except Exception:
            self._fail_to_hidden()
            raise

    def _to_hidden(self) -> None:
        """→ HIDDEN from APP or by double-tap: no `resetOverlays`."""
        try:
            self._adapter.hide()
        except Exception:
            if self._state == PEEK:
                self._fail_to_hidden()
            raise
        self._peek_showing = False
        self._state, self._return_to = HIDDEN, None

    def _to_app_focused(self) -> None:
        self._sign_in_check()
        if self._state == APP:
            # Already in app mode: its live placement is newer than the saved
            # one (a resize since the last exit). A timed-out read keeps it.
            live = self._adapter.get_placement()
            if live is not None:
                self._app_placement = live
        try:
            placement = self._adapter.apply_app(self._app_placement,
                                                focused=True)
        except Exception:
            if self._state == PEEK:
                self._fail_to_hidden()
            raise
        if placement is None:
            # The UI thread did not answer (already logged): the window did
            # not change, so neither does the state, nor the saved placement.
            # From PEEK that would leave Esc swallowed, so it falls back.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 6
            if self._state == PEEK:
                self._fail_to_hidden()
            return
        self._app_placement = placement
        self._peek_showing = False
        self._adapter.focus()
        self._state, self._return_to = APP, None

    def _sign_in_check(self) -> None:
        """D-13: reload through a fresh login code only after a rotation."""
        try:
            from .web import local_secret_generation
            gen = local_secret_generation()
            if gen == self._signed_gen:
                return
            self._adapter.reload(_login_url(self._server_url))
            self._signed_gen = gen
        except Exception as e:
            # The URL carries a live login code, so only the type is logged.
            log.warning("PowerAtlas window could not sign in again: %s",
                        type(e).__name__)

    def _open_browser(self) -> None:
        """The browser door, through the tray's helper. Never logs the URL."""
        try:
            _tray._open_in_browser(_login_url(self._server_url))
        except Exception as e:
            log.warning("Peek could not open the browser: %s", type(e).__name__)

    # ---- keyboard hook (listener thread) ------------------------------------

    def _start_listener(self) -> None:
        """Start the pynput keyboard listener."""
        try:
            kwargs: dict = dict(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            # On Windows, use win32_event_filter to suppress the hotkey keystroke
            # so it doesn't propagate to the focused application (e.g. terminal
            # echoing ^Z repeatedly).
            if sys.platform == "win32":
                kwargs["win32_event_filter"] = self._win32_event_filter
            self._listener = keyboard.Listener(**kwargs)
            self._listener.daemon = True
            self._listener.start()
            log.info("Peek hotkey listener started (hotkey: %s)", self._hotkey)
        except Exception as e:
            log.warning("Failed to start hotkey listener: %s", e)
            self._listener = None

    def _win32_event_filter(self, msg, data) -> None:
        """Decide, post and suppress — nothing else (Threading model, D-21).

        Runs inside the WH_KEYBOARD_LL hook. pynput stops the listener on any
        exception raised here other than its own suppression, so the decision
        is computed inside a `try`, and `suppress_event()` — which raises — is
        called after it, outside any `try`.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        """
        suppress = False
        # Read once: `stop()` clears `_listener` while the hook may still
        # fire, and an AttributeError here would escape the filter.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 12
        listener = self._listener
        try:
            suppress = self._filter_decide(msg, data.vkCode, data.time)
        except Exception as e:
            log.warning("Peek hotkey filter error: %s", type(e).__name__)
            suppress = False
        if suppress and listener is not None:
            listener.suppress_event()

    def _filter_decide(self, msg, vk: int, t: int) -> bool:
        """Update key state, post an event, and say whether to suppress."""
        if vk in _VK_MODIFIERS:
            return False  # modifiers reach `_on_press`/`_on_release`
        name = self._vk_to_name(vk)
        if not name:
            return False
        is_down = msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN)
        if name == "esc":
            # Peek no longer takes focus (D-20), so an Esc that dismisses it
            # must not also reach the user's app. Otherwise it passes through.
            # A key-up is suppressed exactly when its key-down was: the worker
            # may end the peek between the two, and the user's app must never
            # see half a keystroke (a lone key-up, or a key-down whose key-up
            # it never gets). Auto-repeat of a suppressed Esc stays suppressed
            # and is not another event.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 9
            if not is_down:
                if self._esc_down_suppressed:
                    self._esc_down_suppressed = False
                    return True
                return False
            if self._esc_down_suppressed:
                return True
            if not self._peek_showing:
                return False
            self._esc_down_suppressed = True
            self._events.put(("esc",))
            return True
        if is_down:
            if not self._matches_chord(name):
                return False
            self._chord_key_down = True
            if self._triggered:
                return True  # auto-repeat: suppressed, never an event
            self._triggered = True
            self._post_press(t & _TICK_MASK)
            return True
        if self._chord_key_down and name in self._trigger_keys:
            # The chord key's key-up: suppress it like its key-down, and
            # re-arm so a second tap with modifiers held is a new press.
            self._chord_key_down = False
            self._triggered = False
            return True
        return False

    def _matches_chord(self, name: str) -> bool:
        """The key is the chord's own non-modifier key and its modifiers are held."""
        keys = self._trigger_keys
        if name not in keys or name in _MODIFIER_NAMES:
            return False
        return keys.issubset(self._pressed_keys | {name})

    def _post_press(self, t: int) -> None:
        self._release_armed = True
        self._events.put(("press", "peek", t))

    @staticmethod
    def _vk_to_name(vk: int) -> str | None:
        """Map a Windows VK code to our normalized key name."""
        # Letters A-Z: VK 0x41-0x5A
        if 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        # Digits 0-9: VK 0x30-0x39
        if 0x30 <= vk <= 0x39:
            return chr(vk)
        # F-keys: VK 0x70-0x87
        if 0x70 <= vk <= 0x87:
            return f"f{vk - 0x6F}"
        # Common special keys
        _SPECIAL = {
            0x1B: "esc", 0x20: "space", 0x09: "tab", 0x0D: "enter",
            0x08: "backspace", 0x2E: "delete", 0x24: "home", 0x23: "end",
            0x21: "page_up", 0x22: "page_down",
            0xBF: "/", 0xBE: ".", 0xBC: ",", 0xBA: ";",
            0xBB: "=", 0xBD: "-", 0xDB: "[", 0xDD: "]", 0xDC: "\\",
            0xC0: "`", 0xDE: "'",
        }
        return _SPECIAL.get(vk)

    def _on_press(self, key) -> None:
        """Track pressed keys; off Windows also post chord presses and Esc.

        On Windows the filter has already handled the chord key and Esc; only
        the keys it let through arrive here. Never raises: an exception in a
        pynput callback stops the listener.
        """
        try:
            normalized = self._normalize_key(key)
            if normalized:
                self._pressed_keys.add(normalized)
            if sys.platform == "win32" or not normalized:
                return
            if normalized == "esc":
                if self._triggered or self._peek_showing:
                    self._triggered = False
                    self._pressed_keys.clear()
                    self._events.put(("esc",))
                return
            if self._matches_chord(normalized) and not self._triggered:
                self._triggered = True
                self._post_press(_tick_now())
        except Exception as e:
            log.warning("Peek key press handler error: %s", type(e).__name__)

    def _on_release(self, key) -> None:
        """Post a release when a peek chord modifier goes up. Never raises."""
        try:
            normalized = self._normalize_key(key)
            if normalized:
                self._pressed_keys.discard(normalized)
            if not normalized:
                return
            if normalized in self._trigger_keys and normalized not in _MODIFIER_NAMES:
                # The chord key's key-up re-arms the chord on every platform.
                self._triggered = False
                return
            if normalized in self._trigger_keys and normalized in _MODIFIER_NAMES:
                self._triggered = False
                if self._release_armed or self._peek_showing:
                    self._release_armed = False
                    self._events.put(("release",))
        except Exception as e:
            log.warning("Peek key release handler error: %s", type(e).__name__)

    @staticmethod
    def _parse_hotkey(hotkey: str) -> set[str]:
        """Parse 'ctrl+shift+z' into {'ctrl', 'shift', 'z'}."""
        return {part.strip().lower() for part in hotkey.split("+") if part.strip()}

    @staticmethod
    def _normalize_key(key) -> str | None:
        """Normalize a pynput key to a string."""
        if hasattr(key, "char") and key.char:
            ch = key.char
            # When Ctrl is held, character keys report control codes (0x01-0x1a).
            # Map them back to letters: 0x01='a', 0x02='b', ..., 0x1a='z'.
            if len(ch) == 1 and 1 <= ord(ch) <= 26:
                return chr(ord(ch) + ord('a') - 1)
            return ch.lower()
        if hasattr(key, "name"):
            name = key.name.lower()
            if name in ("ctrl_l", "ctrl_r", "ctrl"):
                return "ctrl"
            if name in ("shift_l", "shift_r", "shift"):
                return "shift"
            if name in ("alt_l", "alt_r", "alt_gr", "alt"):
                return "alt"
            return name
        return None

    # ---- FormClosing (UI thread) --------------------------------------------

    def _on_form_closing(self, sender, args) -> None:
        """X hides instead of closing (D-2, D-22). Runs on the UI thread.

        Cancels only `UserClosing` (the X button, Alt+F4: `WM_SYSCOMMAND`
        `SC_CLOSE`) and posts `user_close`; Windows shutdown, Task Manager and
        `Application.Exit` go through, so PowerAtlas never blocks a logoff. A
        bare `WM_CLOSE` is Task Manager's End task to WinForms
        (`TaskManagerClosing`, measured by the Phase 1 window probe), so it
        closes the window and ends PowerAtlas, as an End task should.
        Fails closed: an exception cancels the close unless `stop()` is under
        way (its `destroy()` arrives as `UserClosing` too).
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        """
        try:
            if self._stopping:
                return
            if str(args.CloseReason) == "UserClosing":
                args.Cancel = True
                self._events.put(("user_close",))
        except Exception:
            if not self._stopping:
                try:
                    args.Cancel = True
                except Exception:
                    pass


class _PortableWindow:
    """The non-Windows window adapter: today's peek, no app mode."""

    has_app_mode = False

    def __init__(self, owner: PeekWindow, win):
        self._o = owner
        self._win = win
        self._fullscreen = False

    def foreground(self):
        return None

    def is_foreground(self) -> bool:
        return False

    def get_placement(self):
        return None

    def show_peek(self) -> None:
        self._win.show()
        if not self._fullscreen:
            self._win.toggle_fullscreen()
            self._fullscreen = True

    def hide(self) -> None:
        if self._fullscreen:
            self._win.toggle_fullscreen()
            self._fullscreen = False
        self._win.hide()

    def apply_app(self, placement, focused: bool):
        raise RuntimeError("app mode is Windows only")

    def focus(self) -> None:
        pass

    def restore_foreground(self, prev, pa_fg: bool) -> None:
        pass

    def put_below(self, prev) -> None:
        pass

    def fire_reset_overlays(self) -> None:
        # `evaluate_js` can wait 20 s for the page; never on the worker.
        win = self._win

        def run():
            try:
                win.evaluate_js(_RESET_OVERLAYS_JS)
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    def reload(self, url: str) -> None:
        self._win.load_url(url)


# ---- Win32 adapter ---------------------------------------------------------

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_HWND_TOP, _HWND_TOPMOST, _HWND_NOTOPMOST = 0, -1, -2
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER = 0x1, 0x2, 0x4
_SWP_NOACTIVATE, _SWP_FRAMECHANGED, _SWP_SHOWWINDOW = 0x10, 0x20, 0x40
_SW_HIDE, _SW_SHOWNORMAL, _SW_SHOWMINIMIZED, _SW_SHOWMAXIMIZED = 0, 1, 2, 3
_SW_SHOWNOACTIVATE, _SW_SHOWMINNOACTIVE = 4, 7
_WPF_RESTORETOMAXIMIZED = 0x2
_DEFAULT_APP_SIZE = (1280, 800)  # logical pixels (D-9)

_user32 = None
_WINDOWPLACEMENT = None


def _win32():
    """user32 with explicit argtypes, and the WINDOWPLACEMENT structure.

    Every handle is declared `HWND`: without it ctypes passes `HWND_TOPMOST`
    (-1) as a 32-bit int into a 64-bit slot and `SetWindowPos` fails (measured
    by the Phase 1 taskbar probe).
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """
    global _user32, _WINDOWPLACEMENT
    if _user32 is not None:
        return _user32, _WINDOWPLACEMENT
    import ctypes
    from ctypes import wintypes as wt

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wt.UINT), ("flags", wt.UINT),
                    ("showCmd", wt.UINT), ("ptMinPosition", wt.POINT),
                    ("ptMaxPosition", wt.POINT),
                    ("rcNormalPosition", wt.RECT)]

    u = ctypes.WinDLL("user32", use_last_error=True)
    H = wt.HWND
    sigs = {
        "GetForegroundWindow": ([], H),
        "SetForegroundWindow": ([H], wt.BOOL),
        "IsWindow": ([H], wt.BOOL),
        "IsWindowVisible": ([H], wt.BOOL),
        "IsIconic": ([H], wt.BOOL),
        "IsZoomed": ([H], wt.BOOL),
        "ShowWindow": ([H, ctypes.c_int], wt.BOOL),
        "SetWindowPos": ([H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, wt.UINT], wt.BOOL),
        "GetWindowLongW": ([H, ctypes.c_int], ctypes.c_long),
        "SetWindowLongW": ([H, ctypes.c_int, ctypes.c_long], ctypes.c_long),
        "GetWindowPlacement": ([H, ctypes.POINTER(WINDOWPLACEMENT)], wt.BOOL),
        "SetWindowPlacement": ([H, ctypes.POINTER(WINDOWPLACEMENT)], wt.BOOL),
        "GetDpiForWindow": ([H], wt.UINT),
        "GetWindowThreadProcessId": ([H, ctypes.POINTER(wt.DWORD)], wt.DWORD),
        "AttachThreadInput": ([wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
    }
    for name, (args, res) in sigs.items():
        fn = getattr(u, name)
        fn.argtypes = args
        fn.restype = res
    _user32, _WINDOWPLACEMENT = u, WINDOWPLACEMENT
    return u, WINDOWPLACEMENT


def _show_cmd_for(show_cmd: int, flags: int, focused: bool) -> int:
    """The `showCmd` that re-applies a saved placement (window primitives).

    Focused: normal, or maximized when the window was maximized or would be
    restored maximized (`WPF_RESTORETOMAXIMIZED`, set on a minimized window
    that was maximized). Not focused: the non-activating form of the saved
    state; maximized has none and is kept (the foreground restore repairs it).
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """
    if focused:
        maximized = (show_cmd == _SW_SHOWMAXIMIZED
                     or bool(flags & _WPF_RESTORETOMAXIMIZED))
        return _SW_SHOWMAXIMIZED if maximized else _SW_SHOWNORMAL
    return {
        _SW_SHOWNORMAL: _SW_SHOWNOACTIVATE,
        _SW_SHOWMINIMIZED: _SW_SHOWMINNOACTIVE,
        _SW_SHOWMAXIMIZED: _SW_SHOWMAXIMIZED,
    }.get(show_cmd, _SW_SHOWNOACTIVATE)


def _copy_placement(wp):
    _, WP = _win32()
    out = WP()
    import ctypes
    ctypes.memmove(ctypes.byref(out), ctypes.byref(wp), ctypes.sizeof(WP))
    return out


class _Win32Window:
    """The Windows window adapter: Win32 primitives run on the UI thread.

    Never `win.show()`, `win.hide()`, `win.resize()`, `win.move()` or
    `native.TopMost` (the plan's window primitives). Taskbar presence keeps
    `ShowInTaskbar` True and toggles `WS_EX_TOOLWINDOW` (peek) and
    `WS_EX_APPWINDOW` (app), hiding the window around the restyle: candidate
    (b) of the taskbar probe, which kept the handle and the page across 20
    switches where toggling `ShowInTaskbar` recreated the handle every time
    and lost the WebView2 child in peek mode.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
    """

    has_app_mode = True

    def __init__(self, owner: PeekWindow, win):
        self._o = owner
        self._win = win
        self._native = None
        self._chrome = None
        # UI thread only: `FormClosing` is subscribed once, however many
        # readiness attempts the UI thread runs late.
        self._closing_hooked = False

    # Bounded UI-thread call.
    def _ui(self, fn, what: str, wait: bool = True, timeout: float | None = None,
            timed_out=None):
        """Run `fn` on the UI thread and wait at most `timeout` (2 s) for it.

        On a timeout, logs a WARNING (unless `timed_out` is given, for a
        caller that retries) and returns `timed_out`, None by default.
        """
        from System import Action  # type: ignore[import]
        done = threading.Event()
        box: dict = {}

        def run():
            try:
                box["v"] = fn()
            except Exception as e:
                box["e"] = e
            finally:
                done.set()

        self._native.BeginInvoke(Action(run))
        if not wait:
            return None
        limit = _UI_TIMEOUT if timeout is None else timeout
        if not done.wait(limit):
            if timed_out is not None:
                return timed_out
            log.warning("PowerAtlas window: the UI thread did not run %s within "
                        "%.0f s", what, limit)
            return None
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def attach(self, deadline: float) -> str:
        """The readiness check, retried until `deadline` (`time.monotonic`).

        Returns "ready", "no_form" (no native form or no handle) or
        "timeout" (the UI thread never answered in time). A UI thread busy
        past one 2 s wait at startup is retried rather than leaving app mode,
        and the peek shortcut with it, off for the whole run.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 5
        """
        native = getattr(self._win, "native", None)
        if native is None:
            return "no_form"
        self._native = native
        owner = self._o

        def run():
            if not native.IsHandleCreated:
                return 0
            if not self._closing_hooked:
                # A timed-out attempt still runs later; subscribe once.
                native.FormClosing += owner._on_form_closing
                self._closing_hooked = True
            return int(native.Handle.ToInt64())

        timed_out = object()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return "timeout"
            hwnd = self._ui(run, "the readiness check",
                            timeout=min(_UI_TIMEOUT, left), timed_out=timed_out)
            if hwnd is timed_out:
                continue
            if not hwnd:
                return "no_form"
            owner._hwnd = hwnd
            return "ready"

    @property
    def _h(self):
        return self._o._hwnd

    def foreground(self):
        u, _ = _win32()
        return u.GetForegroundWindow() or None

    def is_foreground(self) -> bool:
        u, _ = _win32()
        h = self._h
        return bool(h) and (u.GetForegroundWindow() or 0) == h

    def get_placement(self):
        u, WP = _win32()
        import ctypes

        def run():
            wp = WP()
            wp.length = ctypes.sizeof(WP)
            if not u.GetWindowPlacement(self._h, ctypes.byref(wp)):
                raise OSError(ctypes.get_last_error(), "GetWindowPlacement")
            return wp

        return self._ui(run, "GetWindowPlacement")

    def _set_chrome(self, mode: str) -> None:
        """On the UI thread: border, taskbar ex-style, and the cached HWND."""
        if self._chrome == mode:
            return
        import System.Windows.Forms as WinForms  # type: ignore[import]
        u, _ = _win32()
        native = self._native
        h = self._h
        if u.IsWindowVisible(h):
            u.ShowWindow(h, _SW_HIDE)  # the taskbar re-reads the style on show
        if mode == APP:
            native.FormBorderStyle = WinForms.FormBorderStyle.Sizable
        else:
            native.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
        h = int(native.Handle.ToInt64())
        self._o._hwnd = h
        ex = u.GetWindowLongW(h, _GWL_EXSTYLE)
        if mode == APP:
            ex = (ex & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
        else:
            ex = (ex & ~_WS_EX_APPWINDOW) | _WS_EX_TOOLWINDOW
        u.SetWindowLongW(h, _GWL_EXSTYLE, ex)
        u.SetWindowPos(h, None, 0, 0, 0, 0,
                       _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
                       | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
        self._chrome = mode

    def show_peek(self) -> None:
        u, _ = _win32()

        def run():
            import System.Windows.Forms as WinForms  # type: ignore[import]
            self._set_chrome(PEEK)
            h = self._h
            if u.IsIconic(h) or u.IsZoomed(h):
                u.ShowWindow(h, _SW_SHOWNOACTIVATE)
            b = WinForms.Screen.PrimaryScreen.Bounds
            u.SetWindowPos(h, _HWND_TOPMOST, b.X, b.Y, b.Width, b.Height,
                           _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

        self._ui(run, "show peek")

    def hide(self) -> None:
        u, _ = _win32()
        self._ui(lambda: u.ShowWindow(self._h, _SW_HIDE), "hide")

    def apply_app(self, placement, focused: bool):
        """App chrome and placement; returns the placement now in effect."""
        u, WP = _win32()
        import ctypes

        def run():
            import System.Windows.Forms as WinForms  # type: ignore[import]
            self._set_chrome(APP)
            h = self._h
            u.SetWindowPos(h, _HWND_NOTOPMOST, 0, 0, 0, 0,
                           _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)
            if placement is None:
                # First app show: the D-9 default in screen coordinates, never
                # written into `rcNormalPosition` (workspace coordinates).
                scale = (u.GetDpiForWindow(h) or 96) / 96
                wa = WinForms.Screen.PrimaryScreen.WorkingArea
                w = min(int(_DEFAULT_APP_SIZE[0] * scale), wa.Width)
                hh = min(int(_DEFAULT_APP_SIZE[1] * scale), wa.Height)
                x = wa.X + (wa.Width - w) // 2
                y = wa.Y + (wa.Height - hh) // 2
                flags = _SWP_SHOWWINDOW | (0 if focused else _SWP_NOACTIVATE)
                u.SetWindowPos(h, _HWND_TOP, x, y, w, hh, flags)
                wp = WP()
                wp.length = ctypes.sizeof(WP)
                u.GetWindowPlacement(h, ctypes.byref(wp))
                return wp
            wp = _copy_placement(placement)
            wp.length = ctypes.sizeof(WP)
            wp.showCmd = _show_cmd_for(placement.showCmd, placement.flags,
                                       focused)
            if not u.SetWindowPlacement(h, ctypes.byref(wp)):
                raise OSError(ctypes.get_last_error(), "SetWindowPlacement")
            return placement

        return self._ui(run, "apply app placement")

    def focus(self) -> None:
        """Focus property: `Activate()`, then the AttachThreadInput fallback."""
        u, _ = _win32()
        import ctypes

        def run():
            h = self._h
            self._native.Activate()
            if u.GetForegroundWindow() == h:
                return True
            fg = u.GetForegroundWindow()
            kernel32 = ctypes.windll.kernel32
            me = kernel32.GetCurrentThreadId()
            other = u.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(other and other != me
                            and u.AttachThreadInput(me, other, True))
            try:
                u.SetForegroundWindow(h)
            finally:
                if attached:
                    u.AttachThreadInput(me, other, False)
            return u.GetForegroundWindow() == h

        if self._ui(run, "focus") is False:
            log.info("PowerAtlas window could not take the foreground")

    def restore_foreground(self, prev, pa_fg: bool) -> None:
        """On the UI thread, like every other primitive (review fix 3)."""
        if not (pa_fg and prev):
            return
        u, _ = _win32()
        h = self._h

        def run():
            if prev != h and u.IsWindow(prev):
                u.SetForegroundWindow(prev)

        self._ui(run, "restore the foreground")

    def put_below(self, prev) -> None:
        u, _ = _win32()
        h = self._h
        if prev and prev != h and u.IsWindow(prev):
            self._ui(lambda: u.SetWindowPos(
                h, prev, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE), "restore z-order")

    def fire_reset_overlays(self) -> None:
        """`resetOverlays` without waiting: `ExecuteScriptAsync`, not awaited."""
        native = self._native

        def run():
            try:
                core = native.webview.CoreWebView2
                if core is not None:
                    core.ExecuteScriptAsync(_RESET_OVERLAYS_JS)
            except Exception:
                pass

        self._ui(run, "resetOverlays", wait=False)

    def reload(self, url: str) -> None:
        """pywebview's `load_url`, bounded.

        It marshals itself with a synchronous `Form.Invoke` (never call it
        from inside a UI-thread callable), so a hung UI thread would block the
        worker for good. It runs on a short-lived thread instead; past 2 s a
        WARNING is logged and the worker moves on. The load stays posted and
        runs when the UI thread recovers, so the caller treats it as done.
        The URL carries a live login code: no log line names it.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 10
        """
        win = self._win
        box: dict = {}

        def run():
            try:
                win.load_url(url)
            except Exception as e:
                box["e"] = e

        t = threading.Thread(target=run, name="power-atlas-reload", daemon=True)
        t.start()
        t.join(_UI_TIMEOUT)
        if t.is_alive():
            log.warning("PowerAtlas window: the UI thread did not load the "
                        "sign-in page within %.0f s", _UI_TIMEOUT)
            return
        if "e" in box:
            raise box["e"]


def create_peek(server_url: str, hotkey: str = "ctrl+shift+z") -> PeekWindow | None:
    """Factory: create PeekWindow if available, else log warning and return None."""
    if not is_available():
        log.warning("Peek window disabled: %s", _IMPORT_ERROR)
        return None
    _KNOWN_MODIFIERS = {"ctrl", "shift", "alt"}
    parts = {p.strip().lower() for p in hotkey.split("+") if p.strip()}
    modifiers = parts & _KNOWN_MODIFIERS
    non_modifiers = parts - _KNOWN_MODIFIERS
    if not modifiers or not non_modifiers:
        log.warning("Invalid peek_hotkey '%s' (need modifier+key). Falling back to ctrl+shift+z", hotkey)
        hotkey = "ctrl+shift+z"
    try:
        return PeekWindow(server_url, hotkey)
    except Exception as e:
        log.warning("Peek window disabled: %s", e)
        return None
