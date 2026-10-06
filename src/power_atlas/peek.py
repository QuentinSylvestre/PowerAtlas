"""The PowerAtlas window and its global shortcut listener.

One pywebview window with two modes: peek mode (frameless, on top, full
screen, shown by the peek shortcut) and app mode (a framed taskbar window,
opened by a double-tap or tray Open PowerAtlas; Windows only). The listener
also serves the optional browser shortcut.
261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 4
"""

import logging
import queue
import sys
import threading
import time
from typing import Protocol
from urllib.parse import quote, urlsplit

log = logging.getLogger("power_atlas.peek")

# Every door here needs the `pa_local` cookie: the window at creation and
# whenever it finds itself signed out (a local-secret rotation, or a missing
# or invalid cookie), and the browser (the browser shortcut, app mode's
# browser fallback, and a same-origin link the window opens as a new window,
# through `_NewWindowBrowser`). The login code in this URL is exchanged for it
# on first load. The helper is shared with the tray through `doors`, which
# imports `web` lazily, so this module still loads without the web app.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL final review (F11);
# door list: 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 4;
# from `doors`, not `tray`: Phase 5 (follow-up 12)
from .doors import login_url as _login_url
# The module, not the function: `open_in_browser` is looked up at call time so
# the browser door here is `doors.open_in_browser`, patches included.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1;
# wording: Phase 5 review (A5)
from . import doors as _doors
# Shortcut names, parsing and validation, shared with the settings write path.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3
from . import hotkeys as _hotkeys


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
# A key-down of a chord key whose key-down the filter already suppressed is an
# auto-repeat only while repeats keep coming. Windows' keyboard repeat delay is
# at most about 1 s, but Filter Keys can set it up to about 2 s, so only a gap
# past 3 s means the key-up was never seen (for example the key went up on the
# secure desktop) and this is a new press.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3 review fix 1;
# 3000 ms (was 1500): final review (15)
_REPEAT_GAP_MS = 3000
# The browser shortcut opens at most one tab per this many milliseconds: each
# open mints a login code, and a burst must not cycle the 64-code store.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (12)
_BROWSER_MIN_GAP_MS = 500
# A browser press still queued when the window becomes ready is dropped once
# it is older than this: a press made during a slow startup must not open a
# tab many seconds later. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (13)
_BROWSER_STALE_MS = 1000

# How long the worker waits for a callable posted to the UI thread. Past it, a
# WARNING names the operation and the worker moves on: a hung UI thread must
# show in the log, not stall every later shortcut silently.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
_UI_TIMEOUT = 2.0
_READY_TIMEOUT = 30.0
_DESTROY_TIMEOUT = 5.0
_EXIT_WATCHDOG = 5.0
# The window's cookie is checked at most this often (seconds), before a show,
# so peeks stay cheap. `_now` is the clock, a seam for tests.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (2)
_SIGN_IN_CHECK_INTERVAL = 10.0
_now = time.monotonic
# A cookie read still running after this many seconds is given up on and a
# new read may start (one more daemon thread): pywebview's `get_cookies()`
# never returns when WebView2's `GetCookiesAsync` task faults, and without
# this the check would stay off for the run. While reads are skipped a
# WARNING says so, at most once per `_COOKIE_SKIP_WARN_INTERVAL` seconds.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (1)
_COOKIE_READ_ABANDON = 30.0
_COOKIE_SKIP_WARN_INTERVAL = 300.0
# How often (seconds) the worker checks that the keyboard listener is still
# running. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 15)
_LISTENER_CHECK_INTERVAL = 60.0

_RESET_OVERLAYS_JS = "if(typeof resetOverlays==='function') resetOverlays()"


# Window states.
HIDDEN, PEEK, APP = "hidden", "peek", "app"

# Peek modes (`Config.peek_mode`). Hold: the peek shows while the shortcut is
# held and ends when a chord modifier is released. Toggle: a press shows it and
# the next press ends it; modifier release does nothing.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2;
# defined once in `hotkeys`: final review (19); by name: final review cycle 2 (12)
_PEEK_MODES = _hotkeys.PEEK_MODES
HOLD = _hotkeys.HOLD
TOGGLE = _hotkeys.TOGGLE

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
_MODIFIER_NAMES = _hotkeys.MODIFIERS
# Generic VK codes the modifier check asks `GetAsyncKeyState` about: either
# side of the key counts. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 14)
_VK_GENERIC = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12}
# The Alt mask key: an unassigned virtual-key code, as AutoHotkey's default
# `MenuMaskKey` (vkE8). Sent down and up after the filter suppresses the key
# of a chord containing alt. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 10)
_VK_MASK = 0xE8
_KEYEVENTF_KEYUP = 0x2


def _real_modifier_down(name: str) -> bool:
    """Whether the keyboard says modifier `name` is down now (the high bit of
    `GetAsyncKeyState` for its generic VK). Off Windows there is no filter,
    and the answer is always yes.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 14)
    """
    if sys.platform != "win32":
        return True
    u, _ = _win32()
    return bool(u.GetAsyncKeyState(_VK_GENERIC[name]) & 0x8000)


def _real_send_mask_key() -> None:
    """Type the mask key, down then up (`keybd_event`). It only queues input,
    so it never blocks the hook; the events reach the hook again afterwards,
    and the filter ignores them by their VK.

    Injected input cannot reach a window of a higher integrity level (UIPI):
    over an elevated (administrator) foreground window the mask does nothing,
    and that window's menu bar may still activate on the Alt key-up.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 10);
    UIPI: Phase 5 review (A4)
    """
    u, _ = _win32()
    u.keybd_event(_VK_MASK, 0, 0, 0)
    u.keybd_event(_VK_MASK, 0, _KEYEVENTF_KEYUP, 0)


# The two keyboard seams the filter calls, looked up at call time (tests
# replace them so they never read the real keyboard or type into it).
_modifier_down = _real_modifier_down
_send_mask_key = _real_send_mask_key


def _tick_now() -> int:
    return int(time.monotonic() * 1000) & _TICK_MASK


def _event_tick_now() -> int:
    """Now, on the clock the posted presses carry: `GetTickCount` on Windows
    (the hook's `KBDLLHOOKSTRUCT.time`; `time.monotonic()` there is
    QueryPerformanceCounter, measured ~35 ms apart), `_tick_now()` elsewhere.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (13)
    """
    if sys.platform == "win32":
        import ctypes
        return ctypes.windll.kernel32.GetTickCount() & _TICK_MASK
    return _tick_now()


# pywebview's module that answers a new-window request (`target=_blank`,
# `window.open`) by calling its module-global `webbrowser.open(uri)`.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 6)
_NEW_WINDOW_MODULE = "webview.platforms.edgechromium"
# At most one login minted per this many seconds for new-window links
# (`_now()` clock). Phase 5 review (B1)
_NEW_WINDOW_MIN_GAP = 1.0
# What `quote` leaves alone when it re-encodes a new-window path-and-query:
# all of printable ASCII but the space. WebView2 hands the URI over in its
# unescaped form, so a space or a non-ASCII character may arrive raw.
_PRINTABLE_ASCII = "".join(chr(c) for c in range(0x21, 0x7F))


class _NewWindowBrowser:
    """Stands in for the `webbrowser` module inside pywebview's EdgeChromium
    module, without forking its handler: `open(url)` sends a link to the
    server's own origin (scheme, host and port, compared literally) to the
    default browser through a fresh login URL that lands on that page, and
    every other link to the real `webbrowser.open`. Any other attribute is
    the real module's. Never logs a URL.

    Signed only when the page that asked is PowerAtlas's own: the window's
    current URL (`current_url()`, read without waiting, since this runs on
    the UI thread) must be same-origin too. Another site the window has
    navigated to could otherwise open PowerAtlas links signed in; its links
    go to the real browser unchanged, as does a URL that cannot be read. And
    at most one login per `_NEW_WINDOW_MIN_GAP` seconds: a page opening
    windows in a loop gets unsigned tabs after the first (DEBUG line).
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 6);
    opener check and rate limit: Phase 5 review (B1)
    """

    def __init__(self, real, server_url: str, same_origin,
                 current_url) -> None:
        self._real = real
        self._server_url = server_url
        self._same_origin = same_origin
        self._current_url = current_url
        self._last_login = None  # `_now()` at the last login minted

    def open(self, url, *args, **kwargs):
        try:
            same = (self._same_origin(url)
                    and self._same_origin(self._current_url()))
        except Exception:
            same = False
        if not same:
            return self._real.open(url, *args, **kwargs)
        now = _now()
        last = self._last_login
        if last is not None and now - last < _NEW_WINDOW_MIN_GAP:
            log.debug("PowerAtlas window: a link opened within %.0f s of the "
                      "last signed one goes to the browser unsigned",
                      _NEW_WINDOW_MIN_GAP)
            return self._real.open(url, *args, **kwargs)
        self._last_login = now
        try:
            parts = urlsplit(url)
            target = quote(parts.path or "/", safe=_PRINTABLE_ASCII)
            if parts.query:
                target += "?" + quote(parts.query, safe=_PRINTABLE_ASCII)
            _doors.open_in_browser(_doors.login_url(self._server_url,
                                                    next=target))
        except Exception as e:
            log.warning("PowerAtlas window could not open a link in the "
                        "browser: %s", type(e).__name__)
        return True

    def __getattr__(self, name):
        return getattr(self._real, name)


class PeekWindow:
    """The PowerAtlas window and its global shortcut listener.

    Other threads use three never-raising members: `show_app()` (tray),
    `stop()` and `supports_app_mode`. Everything else that touches the window
    runs on the worker thread (`_window_worker`).
    """

    def __init__(self, server_url: str, hotkey: str = "ctrl+shift+z",
                 mode: str = HOLD, browser_hotkey: str = ""):
        if not _AVAILABLE:
            raise RuntimeError(f"PowerAtlas window unavailable: {_IMPORT_ERROR}")
        self._server_url = server_url
        # The peek chord in force, as `create_peek` computes it: an invalid
        # one is the default here too, never a chord that cannot fire.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (10)
        effective = _hotkeys.effective_peek_hotkey(hotkey)
        if effective != hotkey:
            log.warning("Peek shortcut %r is invalid; using %s", hotkey,
                        effective)
            hotkey = effective
        self._hotkey = hotkey
        # Set once here and only read afterwards (restart-to-apply, D-6).
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2
        self._mode = mode
        self._window = None
        self._listener = None
        # The browser chord is validated here too, not only in `create_peek`:
        # an invalid one, or one that overlaps the peek chord, is off (D-18
        # assumes conflicting chords never run together). The rule is
        # `hotkeys.effective_browser_hotkey`'s; only the warning is here.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3 review fix 4;
        # one rule: final review (5)
        browser = _hotkeys.effective_browser_hotkey(browser_hotkey, hotkey)
        if (not browser and isinstance(browser_hotkey, str)
                and browser_hotkey.strip()):
            log.warning("Browser shortcut %r is invalid or overlaps the peek "
                        "shortcut %r; it is off", browser_hotkey, hotkey)
        self._browser_hotkey = browser
        # Hook-side state: read and written only by the listener thread.
        # The chord table: "peek" always, "browser" None when off. Matching
        # picks among them per D-18 (`hotkeys.match_chord`).
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3
        self._trigger_keys = self._parse_hotkey(hotkey)
        self._chords: dict = {
            "peek": self._trigger_keys,
            "browser": self._parse_hotkey(browser) if browser else None,
        }
        self._pressed_keys: set = set()
        # Per chord: its key is down (auto-repeat guard).
        self._triggered = {"peek": False, "browser": False}
        # Key name -> (chord, tick of its last key-down) for a chord key whose
        # key-down the filter suppressed: its repeats and its key-up are
        # suppressed too, and the key-up re-arms its chords.
        self._chord_down: dict = {}
        self._esc_down_suppressed = False  # the filter suppressed Esc's key-down
        # The filter just suppressed the key of a chord containing alt: send
        # the mask key before returning (follow-up 10).
        self._mask_pending = False
        # A failed mask send was logged (once per run; Phase 5 review A2).
        self._mask_warned = False
        self._release_armed = False  # a press was posted since the last release
        # Written only by the worker, once, when readiness fails; read by the
        # hook. While set the peek chord matches nothing, so its keys reach
        # the user's app instead of being swallowed for a window that will
        # never show. The browser chord keeps working.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (3)
        self._peek_chord_off = False
        # Shared plumbing.
        self._events: queue.SimpleQueue = queue.SimpleQueue()
        self._ready = threading.Event()
        # Set by pywebview's `loaded` handler (`_on_loaded`, its own thread)
        # on the first page load and never cleared, unlike `events.loaded`,
        # which every navigation clears. A WebView2 that failed to initialize
        # never loads a page, so app mode waits for it.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 8)
        self._loaded = threading.Event()
        self._window_created = threading.Event()
        self._start_returned = threading.Event()
        self._stop_lock = threading.Lock()
        self._stopping = False
        # Written only by the worker; read by the keyboard filter (Esc).
        self._peek_showing = False
        # Worker-owned state.
        self._adapter: "_WindowAdapter | None" = None
        self._state = HIDDEN
        self._return_to = None
        self._app_placement = None
        self._app_was_foreground = False
        self._tap_origin = None
        self._last_press = None
        self._signed_gen = None
        self._prev_foreground = None
        self._hwnd = None
        # `_now()` at the last cookie check (`_sign_in_check`), and the tick
        # of the last browser shortcut honoured (`_control`).
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (2, 12)
        self._last_cookie_check = None
        self._last_browser = None
        # WebView2's browser keys and context menu: turned on at least once
        # (its INFO line logged), and a failure WARNING logged (worker-owned).
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 5);
        # the WARNING flag: Phase 5 review (A7)
        self._browser_keys_on = False
        self._browser_keys_warned = False
        # The INFO line for app mode waiting on the first page load was
        # logged (once per run; Phase 5 review A6). Read and written by the
        # tray thread and the worker; a race costs at most a second line.
        self._unloaded_noted = False
        # The listener health check (`_check_listener`): `_now()` at the last
        # check, and whether its one WARNING was logged (worker-owned).
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 15)
        self._last_listener_check = None
        self._listener_warned = False
        self._worker = None
        # `__main__`'s shutdown tail, run by the `stop()` watchdog when the UI
        # loop will not end. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        self.shutdown_tail = None

    # ---- public, any thread -------------------------------------------------

    @property
    def supports_app_mode(self) -> bool:
        """Windows, the window passed the readiness gate, it has loaded a
        page, and WebView2 is in use.

        A WebView2 initialization failure, which pywebview only logs, never
        loads a page, so app mode stays unavailable and tray Open uses the
        browser instead of showing a blank window.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 8)
        """
        try:
            if (sys.platform != "win32" or not self._ready.is_set()
                    or not self._loaded.is_set()):
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
                if self._only_the_first_load_missing():
                    self._note_unloaded_fallback()
                self._open_browser()
        except Exception as e:
            log.warning("PowerAtlas window could not open: %s", type(e).__name__)

    def _only_the_first_load_missing(self) -> bool:
        """App mode is off only because no page has loaded yet: Windows, the
        window passed the readiness gate with the Win32 adapter, and WebView2
        is in use. Never raises; never imports pywebview's WinForms module
        unless the Win32 adapter (which needs it) is in place."""
        try:
            if (sys.platform != "win32" or self._loaded.is_set()
                    or not self._ready.is_set()
                    or not isinstance(self._adapter, _Win32Window)):
                return False
            from webview.platforms import winforms
            return bool(winforms.is_chromium)
        except Exception:
            return False

    def _note_unloaded_fallback(self) -> None:
        """One INFO line per run: a tray Open or a double-tap used the
        browser only because the window has not loaded a page yet.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 review (A6)
        """
        if self._unloaded_noted:
            return
        self._unloaded_noted = True
        log.info("PowerAtlas window has not loaded a page yet, so it opens in "
                 "the browser instead (not logged again this run)")

    def start(self, on_main_thread: bool = False) -> None:
        """Start the worker, the hotkey listener and the webview.

        Args:
            on_main_thread: If True, webview.start() is called on the
                current thread (blocks). If False, starts on a new thread.
        """
        # A `stop()` that already ran leaves nothing to start: no worker, no
        # keyboard hook. The listener starts under the stop lock, so a
        # `stop()` racing this call either comes first (and this returns) or
        # finds the listener and stops it.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (14)
        with self._stop_lock:
            if self._stopping:
                self._start_returned.set()
                return
            self._worker = threading.Thread(target=self._window_worker,
                                            name="power-atlas-window",
                                            daemon=True)
            self._worker.start()
            self._start_listener()
        if on_main_thread:
            self._run_webview()  # blocks
        else:
            threading.Thread(target=self._run_webview, daemon=True).start()

    def stop(self) -> None:
        """Stop the listener and destroy the window. Idempotent and bounded.

        Final — call only at process exit. Never waits on the worker. If the
        destroy does not finish within 5 s the UI loop is asked to exit. On
        every path a watchdog then runs `shutdown_tail` if `webview.start()`
        has not returned 5 s later, so tray Quit and Restart always end the
        process: also when there is no window yet, when `destroy()` returns
        at once because pywebview has not built the form (it then never
        ends `start()`), and when `destroy()` raises.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1;
        every path: final review (1)
        """
        with self._stop_lock:
            if self._stopping:
                return
            self._stopping = True
        try:
            if self._listener:
                self._listener.stop()
        except Exception as e:
            log.warning("PowerAtlas window: hotkey listener did not stop: %s", type(e).__name__)
        self._listener = None
        self._events.put(("stop",))
        win = self._window
        if win is not None:
            t = threading.Thread(target=self._destroy, args=(win,),
                                 daemon=True)
            t.start()
            t.join(_DESTROY_TIMEOUT)
            if t.is_alive():
                log.warning("PowerAtlas window did not close within %.0f s; "
                            "asking the UI loop to exit", _DESTROY_TIMEOUT)
                try:
                    from System import Action  # type: ignore[import]
                    import System.Windows.Forms as WinForms  # type: ignore[import]
                    native = getattr(win, "native", None)
                    if native is not None:
                        native.BeginInvoke(Action(WinForms.Application.Exit))
                except Exception as e:
                    log.warning("Could not post the UI loop exit: %s",
                                type(e).__name__)
        # Armed on every path; it does nothing once `start()` has returned.
        if not self._start_returned.is_set():
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
        tail = self.shutdown_tail
        if tail is None:
            return
        log.warning("The UI loop did not exit; running the shutdown tail")
        tail()

    def _run_webview(self) -> None:
        """Create and run the pywebview window."""
        # The generation the creation URL signs in under, read before the
        # mint (D-13): a rotation between the two is then caught by the first
        # sign-in check. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        # A `stop()` that came first leaves nothing to run: return, so the
        # caller's shutdown tail runs now. One that lands later is covered by
        # its watchdog. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (1)
        if self._stopping:
            self._start_returned.set()
            return
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
        # Before `start`, so the first load cannot come first.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-ups 5, 8)
        try:
            self._window.events.loaded += self._on_loaded
        except Exception as e:
            log.warning("PowerAtlas window: cannot watch its page loads, so "
                        "app mode stays unavailable: %s", type(e).__name__)
        self._window_created.set()
        try:
            if not self._stopping:
                webview.start(debug=False)
        finally:
            self._start_returned.set()

    def _on_loaded(self) -> None:
        """pywebview's `loaded` handler, on a thread of its own: the
        first-load latch and an event for the worker, nothing else.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-ups 5, 8)
        """
        try:
            self._loaded.set()
            self._events.put(("loaded",))
        except Exception:
            pass

    # ---- worker -------------------------------------------------------------

    def _window_worker(self) -> None:
        """The one thread that touches the window. Loops on `_events`."""
        adapter = None
        try:
            adapter = self._establish_ready()
        except Exception as e:
            log.warning("PowerAtlas window readiness failed: %s: %s",
                        type(e).__name__, e)
        if adapter is None:
            # Nothing would ever act on the peek chord: stop matching it, so
            # its keystroke reaches the user's app, and say so.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (3)
            self._peek_chord_off = True
            log.warning("PowerAtlas window is not ready, so the peek shortcut "
                        "(%s) is off for this run%s", self._hotkey,
                        "; the browser shortcut still works"
                        if self._browser_hotkey else "")
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
            # A malformed event is logged and skipped; it never ends the
            # worker. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (7)
            try:
                done = self._control(ev, queued=True)
            except Exception as e:
                self._log_event_error(ev, e)
                continue
            if done:
                return
            if done is None:
                log.debug("PowerAtlas window: dropped %s before ready", ev[0])
        if adapter is not None:
            self._adapter = adapter
            self._ready.set()
            log.info("PowerAtlas window ready")
            self._route_new_windows()
            if self._loaded.is_set():
                # A `loaded` event that came before readiness was dropped
                # with the others; the latch it set is not.
                try:
                    self._enable_browser_keys()
                except Exception as e:
                    self._log_event_error(("loaded",), e)
        while True:
            # A bounded wait, so an idle worker still runs the listener
            # check (follow-up 15).
            try:
                ev = self._events.get(timeout=_LISTENER_CHECK_INTERVAL)
            except queue.Empty:
                ev = None
            self._check_listener()
            if ev is None:
                continue
            try:
                done = self._control(ev)
                if done:
                    return
                if done is not None:
                    continue
                if not self._ready.is_set():
                    log.debug("PowerAtlas window not ready; dropped %s", ev[0])
                    continue
                self._handle(ev)
            except Exception as e:
                self._log_event_error(ev, e)

    def _check_listener(self) -> None:
        """Log one WARNING per run when the keyboard listener has stopped:
        its thread is dead, or pynput no longer says it is running (an
        exception in a callback stops it). At most every
        `_LISTENER_CHECK_INTERVAL` seconds, on the worker, never in the hook.
        The first call only starts the clock. Silent while stopping, and
        without a listener (a failed start is logged when it happens).
        Not caught: Windows removing a hook that took too long
        (`LowLevelHooksTimeout`) leaves the thread alive and `running` True.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 15)
        """
        try:
            if self._listener_warned or self._stopping:
                return
            now = _now()
            last = self._last_listener_check
            if last is None or now - last < _LISTENER_CHECK_INTERVAL:
                if last is None:
                    self._last_listener_check = now
                return
            self._last_listener_check = now
            listener = self._listener
            if listener is None:
                return
            alive = bool(listener.is_alive())
            running = bool(listener.running)
            if alive and running:
                return
            self._listener_warned = True
            log.warning("PowerAtlas window: the hotkey listener has stopped "
                        "(thread alive: %s, running: %s); the peek and "
                        "browser shortcuts are off until PowerAtlas restarts",
                        alive, running)
        except Exception as e:
            log.debug("PowerAtlas window: listener check failed: %s",
                      type(e).__name__)

    @staticmethod
    def _log_event_error(ev, e: Exception) -> None:
        # No URL reaches this line: the sign-in reload and the browser door
        # catch their own errors and log only the type.
        kind = ev[0] if isinstance(ev, tuple) and ev else type(ev).__name__
        log.warning("PowerAtlas window: %s failed: %s: %s",
                    kind, type(e).__name__, e)

    def _control(self, ev: tuple, queued: bool = False) -> bool | None:
        """`stop` and `browser`, handled alike before and after readiness.

        Returns True for `stop` (the worker ends), False for a `browser`
        event (opened or dropped), None for a window event. The browser
        shortcut needs no window, so a press during startup or after a failed
        readiness gate still opens it; it leaves the window state, the
        double-tap timing and `tap_origin` alone. At most one tab per
        `_BROWSER_MIN_GAP_MS`, timed by the press's tick. A press drained from
        the queue when the worker first runs (`queued`) is dropped when it is
        older than `_BROWSER_STALE_MS`, so a press made during a slow startup
        never opens a tab long after.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3;
        one helper and the rate limit: final review (12, 20); stale presses:
        final review cycle 2 (13)
        """
        kind = ev[0]
        if kind == "stop":
            return True
        if kind != "browser":
            return None
        t = ev[1] & _TICK_MASK
        if queued:
            # Masked, so the tick wrap is handled; a press "from the future"
            # (age past half the range) is not called stale.
            age = (_event_tick_now() - t) & _TICK_MASK
            if _BROWSER_STALE_MS < age < 0x80000000:
                log.debug("Browser shortcut: dropped a press made %d ms "
                          "before the window was ready", age)
                return False
        last = self._last_browser
        if last is not None and ((t - last) & _TICK_MASK) < _BROWSER_MIN_GAP_MS:
            log.debug("Browser shortcut: dropped a press within %d ms of the "
                      "last one", _BROWSER_MIN_GAP_MS)
            return False
        self._last_browser = t
        self._open_browser()
        return False

    def _establish_ready(self):
        """The readiness gate (Threading model). Returns the adapter, or None.

        `start(func=...)` runs before the form exists, so readiness waits for
        `events.shown` (set for a hidden window too), then checks the form and
        its handle on the UI thread, subscribes `FormClosing` and caches the
        HWND. The worker then sets `_ready` and logs `PowerAtlas window ready`.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1
        """
        # One deadline for the whole gate, creation and `shown` included.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (18)
        deadline = time.monotonic() + _READY_TIMEOUT
        if not self._window_created.wait(_READY_TIMEOUT):
            log.warning("PowerAtlas window was not created within %.0f s; app "
                        "mode stays unavailable", _READY_TIMEOUT)
            return None
        win = self._window
        shown = win.events.shown.wait(max(0.0, deadline - time.monotonic()))
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
        # What is left of the readiness timeout, but at least one full
        # UI-thread wait for the check itself.
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
        elif kind == "release":
            # Modifier release ends a peek in Hold only; in Toggle the next
            # press ends it. Esc and X still end a peek in both modes.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2
            if self._state == PEEK and self._mode != TOGGLE:
                self._end_peek()
        elif kind == "esc":
            if self._state == PEEK:
                self._end_peek()
        elif kind == "user_close":
            if self._state == PEEK:
                self._end_peek()
            elif self._state == APP:
                self._save()
                self._to_hidden()
            else:
                # HIDDEN, yet the user closed it: a window shown late (an app
                # show whose wait timed out ran afterwards). A close always
                # hides. 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (13)
                self._to_hidden()
        elif kind == "show_app":
            self._to_app_focused()
        elif kind == "loaded":
            self._enable_browser_keys()
        # `browser` never reaches here: `_control` handles it in both worker
        # loops, before readiness is checked.

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
        # A press shows a peek from HIDDEN or APP in both modes. While a peek
        # shows it is a no-op in Hold, and ends the peek in Toggle (back to
        # HIDDEN or to APP, per `return_to`).
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2
        if self._state == HIDDEN:
            self._to_peek(None)
        elif self._state == APP:
            self._save()
            self._to_peek("app")
        elif self._state == PEEK and self._mode == TOGGLE:
            self._end_peek()

    def _double_tap(self, origin) -> None:
        """The double-tap rule, decided from `tap_origin`, never the current state."""
        if not self._adapter.has_app_mode:
            # No app mode (non-Windows, or no page loaded yet): the
            # double-tap opens the browser.
            if self._only_the_first_load_missing():
                self._note_unloaded_fallback()
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

    def _enable_browser_keys(self) -> None:
        """WebView2's browser accelerator keys (reload, find) and default
        context menu, on. pywebview turns both off for `debug=False` in
        `on_webview_ready`. The settings belong to the `CoreWebView2`, so a
        recreated core would lose them; they are set again on every page
        load instead of once: setting them is idempotent and the UI call is
        bounded. The
        first success logs one INFO line; the first failure (no
        `CoreWebView2` yet, a timed-out UI call, an error) one WARNING, and
        later failures only DEBUG. DevTools stay off.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 5);
        every load: Phase 5 review (A7)
        """
        why = "not ready"
        try:
            ok = bool(self._adapter.enable_browser_keys())
        except Exception as e:
            ok, why = False, type(e).__name__
        if ok:
            if not self._browser_keys_on:
                self._browser_keys_on = True
                log.info("PowerAtlas window: browser keys and the context "
                         "menu are on")
            return
        if not self._browser_keys_warned:
            self._browser_keys_warned = True
            log.warning("PowerAtlas window could not turn on its browser keys "
                        "and context menu (%s); tried again on each page load",
                        why)
        else:
            log.debug("PowerAtlas window: browser keys not set (%s)", why)

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
        self._prev_foreground = self._adapter.foreground()
        self._adapter.show_peek()
        self._peek_showing = True
        self._state, self._return_to = PEEK, return_to
        # After the show, so a cookie read (up to 2 s) never delays a peek; a
        # re-sign reloads the shown page, which a peek tolerates. The APP
        # paths keep theirs before the show.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (6)
        self._sign_in_check()

    def _fail_to_hidden(self, reset_fired: bool = False) -> None:
        """A failed exit from PEEK: best-effort hide, then HIDDEN.

        Never stays PEEK: `_peek_showing` would stay True and the filter would
        swallow every Esc system-wide. The caller re-raises, so the worker
        still logs the failure.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 4
        """
        if self._return_to is None and not reset_fired:
            # A peek over HIDDEN ends in HIDDEN, so its page resets as on any
            # end peek to HIDDEN (D-11): fired without waiting, best effort,
            # unless the failed transition already fired it.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (17)
            try:
                self._adapter.fire_reset_overlays()
            except Exception:
                pass
        try:
            self._adapter.hide()
        except Exception:
            pass
        self._peek_showing = False
        self._state, self._return_to = HIDDEN, None

    def _end_peek(self) -> None:
        a = self._adapter
        fired = False
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
                # Fired only once the call returned: a raising one did not
                # fire, so `_fail_to_hidden` tries it once more.
                # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (11)
                a.fire_reset_overlays()
                fired = True
                pa_fg = a.is_foreground()
                a.hide()
                self._peek_showing = False
                a.restore_foreground(self._prev_foreground, pa_fg)
                self._state, self._return_to = HIDDEN, None
        except Exception:
            self._fail_to_hidden(reset_fired=fired)
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
        # The window has app chrome now: the state says so before `focus()`,
        # whose failure the worker logs without leaving PEEK behind.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (14)
        self._state, self._return_to = APP, None
        self._adapter.focus()

    def _sign_in_check(self) -> None:
        """Sign the window in again when it is signed out.

        D-13: after a rotation (the local-secret generation changed). And,
        at most every `_SIGN_IN_CHECK_INTERVAL` seconds, when the window's
        own cookie jar has no valid `pa_local` (`web.window_signed_in`): its
        creation code was never exchanged (WebView2 slower than the code's
        TTL, or a failed first navigation), or the cookie expired. A
        signed-in window is never reloaded, and neither is one whose answer
        is unknown: a jar that cannot be read now (page still loading, UI
        thread busy), no local secret loaded, a rotation under way, or a
        window that has navigated to another origin (its jar is that
        origin's, so a missing `pa_local` there says nothing).
        `_signed_gen` advances only when the reload is known to have run.
        Before the show on the APP paths, after it on the peek path.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1;
        cookie check and reload result: final review (2, 16); one web
        helper, origin check, order: final review cycle 2 (2, 5, 6)
        """
        try:
            from . import web as _web
            gen = _web.local_secret_generation()
            if gen != self._signed_gen:
                self._last_cookie_check = _now()
                if self._adapter.reload(_login_url(self._server_url)):
                    self._signed_gen = gen
                return
            now = _now()
            last = self._last_cookie_check
            if last is not None and now - last < _SIGN_IN_CHECK_INTERVAL:
                return
            self._last_cookie_check = now
            read = self._adapter.read_cookies()
            if read is None:
                return
            cookies, url = read
            if _web.window_signed_in(cookies) is not False:
                return
            if not self._same_origin(url):
                log.debug("PowerAtlas window is on another origin; its "
                          "sign-in is not checked")
                return
            log.info("PowerAtlas window is signed out; signing it in again")
            self._adapter.reload(_login_url(self._server_url))
        except Exception as e:
            # The URL carries a live login code, so only the type is logged.
            log.warning("PowerAtlas window could not sign in again: %s",
                        type(e).__name__)

    def _same_origin(self, url) -> bool:
        """Whether `url` has the server's scheme, host and port, compared
        literally (`localhost` is not `127.0.0.1`). Anything missing or
        unparseable is not."""
        try:
            if not isinstance(url, str) or not url:
                return False
            a, b = urlsplit(url), urlsplit(self._server_url)
            return (a.scheme.lower(), a.netloc.lower()) == (
                b.scheme.lower(), b.netloc.lower())
        except Exception:
            return False

    def _window_url(self) -> str | None:
        """The page the window shows now, as pywebview last recorded it
        (`native.browser.url`, set on every navigation), or None. A plain
        attribute read: never `win.get_current_url()`, which waits for the
        page to load and is called from the UI thread here (Threading
        model). Never raises.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 review (B1)
        """
        try:
            url = self._window.native.browser.url
            return None if url is None else str(url)
        except Exception:
            return None

    def _route_new_windows(self) -> None:
        """Put `_NewWindowBrowser` in place of the `webbrowser` module that
        pywebview's EdgeChromium handler calls, so a same-origin link opened
        as a new window lands in the browser signed in. Worker, after
        readiness: the form exists by then, so pywebview has already imported
        that module; it is looked up, never imported here (importing it loads
        the .NET runtime). Absent (no EdgeChromium) means nothing to do. Once
        per module: a second call finds the shim and leaves it.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 6)
        """
        try:
            module = sys.modules.get(_NEW_WINDOW_MODULE)
            if module is None:
                return
            current = getattr(module, "webbrowser", None)
            if current is None or isinstance(current, _NewWindowBrowser):
                return
            module.webbrowser = _NewWindowBrowser(current, self._server_url,
                                                  self._same_origin,
                                                  self._window_url)
        except Exception as e:
            log.warning("PowerAtlas window: same-origin links will open the "
                        "browser signed out: %s", type(e).__name__)

    def _open_browser(self) -> None:
        """The browser door, through `doors`. Never logs the URL."""
        try:
            _doors.open_in_browser(_login_url(self._server_url))
        except Exception as e:
            log.warning("PowerAtlas window could not open the browser: %s", type(e).__name__)

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
                # Bind user32 now, not on the first chord inside the hook.
                try:
                    _win32()
                except Exception as e:
                    log.warning("PowerAtlas window: user32 is not "
                                "available: %s", type(e).__name__)
            self._listener = keyboard.Listener(**kwargs)
            self._listener.daemon = True
            self._listener.start()
            log.info("Peek hotkey listener started (hotkey: %s, mode: %s, "
                     "browser shortcut: %s)", self._hotkey, self._mode,
                     self._browser_hotkey or "off")
        except Exception as e:
            log.warning("PowerAtlas window: hotkey listener failed to start: %s", e)
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
        self._mask_pending = False
        try:
            suppress = self._filter_decide(msg, data.vkCode, data.time)
        except Exception as e:
            log.warning("PowerAtlas window: hotkey filter error: %s", type(e).__name__)
            suppress = False
        if suppress and self._mask_pending:
            # Here, in the hook, rather than on the worker: `keybd_event`
            # only queues the input, and from here the mask in practice
            # precedes the Alt key-up at the user's app (the worker may lag a
            # UI-thread timeout behind). Not guaranteed: a hook slow enough
            # can let an Alt key-up already queued through first. AutoHotkey
            # also sends it from its hook. Best effort: a failure is logged
            # by type, once per run (it would repeat on every Alt chord), and
            # never escapes.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 10);
            # wording and the once-per-run log: Phase 5 review (A1, A2)
            self._mask_pending = False
            try:
                _send_mask_key()
            except Exception as e:
                if not self._mask_warned:
                    self._mask_warned = True
                    log.warning("PowerAtlas window: could not send the Alt "
                                "mask key: %s (not logged again this run)",
                                type(e).__name__)
        if suppress and listener is not None:
            listener.suppress_event()

    def _filter_decide(self, msg, vk: int, t: int) -> bool:
        """Update key state, post an event, and say whether to suppress."""
        if vk in _VK_MODIFIERS:
            return False  # modifiers reach `_on_press`/`_on_release`
        if vk == _VK_MASK:
            # The Alt mask key this filter sent (follow-up 10): passed on to
            # the user's app untouched, never a chord key or an event.
            return False
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
        t &= _TICK_MASK
        if is_down:
            held = self._chord_down.get(name)
            if held is not None:
                if ((t - held[1]) & _TICK_MASK) <= _REPEAT_GAP_MS:
                    # Auto-repeat of a suppressed chord key: suppressed and
                    # never an event, whatever the modifiers do meanwhile, so
                    # the user's app never gets a key-down without its key-up
                    # (or the reverse) and the held key never switches chords.
                    # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3 review fix 1
                    self._chord_down[name] = (held[0], t)
                    return True
                # The key-up was never seen: a new press.
                del self._chord_down[name]
                self._rearm(name)
            self._drop_stale_modifiers(name)
            chord = self._matches_chord(name)
            if chord is None:
                return False
            self._chord_down[name] = (chord, t)
            if self._triggered[chord]:
                return True  # auto-repeat: suppressed, never an event
            self._triggered[chord] = True
            self._post_press(chord, t)
            if "alt" in self._chords[chord]:
                self._mask_pending = True
            return True
        if self._chord_down.pop(name, None) is not None:
            # The chord key's key-up: suppress it like its key-down, and
            # re-arm every chord the key belongs to (as `_on_release` does)
            # so a second tap with modifiers held is a new press.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3 review fix 3
            self._rearm(name)
            return True
        return False

    def _drop_stale_modifiers(self, name: str) -> None:
        """Before a chord key-down is matched: forget each tracked modifier
        the keyboard says is up. Its key-up was lost (it went up on the
        secure desktop: Ctrl+Alt+Del, UAC), and kept it would let a partial
        chord fire. Cheap: only for a key some chord uses, and only the
        modifiers tracked as held (at most three `GetAsyncKeyState` calls).
        A check that raises (user32 not bound, the call failing) keeps the
        modifier, as before this check existed: the filter must not stop
        matching chords because the keyboard could not be asked. Not logged:
        it would repeat on every chord key-down inside the hook.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 14);
        a raising check: Phase 5 review (A3)
        """
        if not any(keys and name in keys for keys in self._chords.values()):
            return
        for m in self._pressed_keys & _MODIFIER_NAMES:
            try:
                down = _modifier_down(m)
            except Exception:
                down = True
            if not down:
                self._pressed_keys.discard(m)

    def _rearm(self, name: str) -> None:
        """Clear `_triggered` for every chord that has `name` as a key."""
        for cid, keys in self._chords.items():
            if keys and name in keys:
                self._triggered[cid] = False

    def _matches_chord(self, name: str) -> str | None:
        """The chord a key-down of `name` fires, or None (D-18).

        A chord matches when `name` is its own non-modifier key and its other
        keys are held; with two matches the one with more keys wins, and a tie
        goes to the peek chord. One function for the Windows filter and the
        portable `_on_press`.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3
        """
        chords = self._chords
        if self._peek_chord_off:
            # Readiness failed: the peek chord is off (final review 3).
            chords = {"browser": chords["browser"]}
        return _hotkeys.match_chord(chords, name, self._pressed_keys)

    def _post_press(self, chord: str, t: int) -> None:
        if chord == "browser":
            # Not armed for `release`: modifier release stays tied to the peek.
            # The tick times the worker's rate limit (final review 12).
            self._events.put(("browser", t))
            return
        self._release_armed = True
        self._events.put(("press", "peek", t))

    @staticmethod
    def _vk_to_name(vk: int) -> str | None:
        """Map a Windows VK code to our normalized key name (`hotkeys.VK_NAMES`)."""
        return _hotkeys.VK_NAMES.get(vk)

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
                if self._triggered["peek"] or self._peek_showing:
                    self._triggered["peek"] = False
                    self._pressed_keys.clear()
                    self._events.put(("esc",))
                return
            chord = self._matches_chord(normalized)
            if chord is not None and not self._triggered[chord]:
                self._triggered[chord] = True
                self._post_press(chord, _tick_now())
        except Exception as e:
            log.warning("PowerAtlas window: key press handler error: %s", type(e).__name__)

    def _on_release(self, key) -> None:
        """Post a release when a peek chord modifier goes up. Never raises."""
        try:
            normalized = self._normalize_key(key)
            if normalized:
                self._pressed_keys.discard(normalized)
            if not normalized:
                return
            # A chord key's key-up re-arms its chord on every platform, and so
            # does the release of one of its modifiers.
            self._rearm(normalized)
            if normalized not in _MODIFIER_NAMES:
                return
            if normalized in self._trigger_keys:
                # Only the peek chord's modifiers end a Hold peek.
                if self._release_armed or self._peek_showing:
                    self._release_armed = False
                    self._events.put(("release",))
        except Exception as e:
            log.warning("PowerAtlas window: key release handler error: %s", type(e).__name__)

    @staticmethod
    def _parse_hotkey(hotkey: str) -> frozenset[str]:
        """Parse 'ctrl+shift+z' into {'ctrl', 'shift', 'z'} (`hotkeys.parse_hotkey`)."""
        return _hotkeys.parse_hotkey(hotkey)

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


class _WindowAdapter(Protocol):
    """What the worker calls on a window adapter: `_Win32Window`,
    `_PortableWindow`, and the tests' fakes. Every member runs on the worker
    thread; an adapter marshals to the UI thread itself.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (6)
    """

    has_app_mode: bool

    def foreground(self): ...

    def is_foreground(self) -> bool: ...

    def get_placement(self): ...

    def show_peek(self) -> None: ...

    def hide(self) -> None: ...

    def apply_app(self, placement, focused: bool) -> object | None:
        """App chrome and `placement` (None: the D-9 default). Returns the
        placement now in effect, or None when the UI thread did not answer
        in time (the window did not change)."""
        ...

    def focus(self) -> None: ...

    def restore_foreground(self, prev, pa_fg: bool) -> None: ...

    def put_below(self, prev) -> None: ...

    def fire_reset_overlays(self) -> None: ...

    def reload(self, url: str) -> bool:
        """Load `url`; False only when it is not known to have run.

        `_PortableWindow.reload` is not bounded: it calls pywebview's
        `load_url` on the worker and always returns True (off Windows a
        hung UI thread would stall the worker here).
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (15)
        """
        ...

    def read_cookies(self) -> "tuple[list, str | None] | None":
        """The window's cookie jar (pywebview's `get_cookies()`, a list of
        `SimpleCookie`) and the URL it was read for, or None when that
        cannot be told now."""
        ...

    def enable_browser_keys(self) -> bool:
        """Turn the browser's own keys and context menu on; False when it
        could not be done now. Called on every page load.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 5)"""
        ...


class _CookieReader:
    """`read_cookies` for both adapters: pywebview's `get_cookies()`, bounded.

    It reaches HttpOnly cookies (WebView2's cookie manager; measured on
    pywebview 6.2.1), but it waits up to 20 s for the page and marshals with
    a synchronous `Form.Invoke`, so it never runs on the worker or inside a
    UI-thread callable: a short-lived thread runs it, joined for at most 2 s,
    like `reload`. A page that has not finished loading is not asked at all.
    A read still running from an earlier check blocks a second one for
    `_COOKIE_READ_ABANDON` seconds; past that it is given up on, since a
    faulted `GetCookiesAsync` never releases pywebview's wait. The current
    URL is read on the same thread (WebView2 reads the jar for that URL).
    Returns None whenever the answer is unknown. Never logs a cookie.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (2);
    the jar, the URL and the abandon: final review cycle 2 (1, 2, 5)
    """

    _win = None
    _cookie_thread = None
    _cookie_started = 0.0
    _cookie_skip_warned = None

    def read_cookies(self) -> "tuple[list, str | None] | None":
        win = self._win
        try:
            if not win.events.loaded.is_set():
                return None
        except Exception:
            return None
        prev = self._cookie_thread
        if prev is not None and prev.is_alive():
            stuck_for = _now() - self._cookie_started
            if stuck_for < _COOKIE_READ_ABANDON:
                self._warn_rate_limited(
                    "PowerAtlas window: cookie reads are being skipped (an "
                    "earlier read is still running); the signed-out check "
                    "is paused")
                return None
            self._warn_rate_limited(
                f"PowerAtlas window: a cookie read has hung for "
                f"{stuck_for:.0f} s; giving up on it and reading again")
        box: dict = {}

        def run():
            try:
                url = win.get_current_url()
                box["v"] = (list(win.get_cookies() or ()), url)
            except Exception as e:
                box["e"] = type(e).__name__

        t = threading.Thread(target=run, name="power-atlas-cookies",
                             daemon=True)
        self._cookie_thread = t
        self._cookie_started = _now()
        t.start()
        t.join(_UI_TIMEOUT)
        if t.is_alive():
            log.warning("PowerAtlas window: the UI thread did not read the "
                        "window's cookies within %.0f s", _UI_TIMEOUT)
            return None
        if "e" in box:
            log.warning("PowerAtlas window could not read its cookies: %s",
                        box["e"])
            return None
        return box.get("v")

    def _warn_rate_limited(self, message: str) -> None:
        """A WARNING about a stuck cookie read, skipped or given up on: at
        most one per `_COOKIE_SKIP_WARN_INTERVAL`, shared by both."""
        now = _now()
        last = self._cookie_skip_warned
        if last is not None and now - last < _COOKIE_SKIP_WARN_INTERVAL:
            return
        self._cookie_skip_warned = now
        log.warning("%s", message)


class _PortableWindow(_CookieReader):
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

    def reload(self, url: str) -> bool:
        self._win.load_url(url)
        return True

    def enable_browser_keys(self) -> bool:
        # The WebView2 settings are Windows only; nothing to do here.
        return True


# ---- Win32 adapter ---------------------------------------------------------

_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_HWND_TOP, _HWND_TOPMOST, _HWND_NOTOPMOST = 0, -1, -2
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOZORDER = 0x1, 0x2, 0x4
_SWP_NOACTIVATE, _SWP_FRAMECHANGED = 0x10, 0x20
_SW_HIDE, _SW_SHOWNORMAL, _SW_SHOWMINIMIZED, _SW_SHOWMAXIMIZED = 0, 1, 2, 3
_SW_SHOWNOACTIVATE, _SW_SHOW, _SW_SHOWMINNOACTIVE, _SW_SHOWNA = 4, 5, 7, 8
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
        # The keyboard filter's modifier check and Alt mask (Phase 5,
        # follow-ups 14 and 10).
        "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
        "keybd_event": ([wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_size_t], None),
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


class _Win32Window(_CookieReader):
    """The Windows window adapter: Win32 primitives run on the UI thread.

    Never `win.show()`, `win.hide()`, `win.resize()`, `win.move()` or
    `native.TopMost` (the plan's window primitives). Taskbar presence keeps
    `ShowInTaskbar` True and toggles `WS_EX_TOOLWINDOW` (peek) and
    `WS_EX_APPWINDOW` (app), hiding the window around the restyle: candidate
    (b) of the taskbar probe, which kept the handle and the page across 20
    switches where toggling `ShowInTaskbar` recreated the handle every time
    and lost the WebView2 child in peek mode.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1

    A hidden window is always made visible by `ShowWindow` (or
    `SetWindowPlacement`), never by `SetWindowPos(SWP_SHOWWINDOW)`: that flag
    sends no `WM_SHOWWINDOW`, so WinForms keeps the form's `Visible` False.
    The WebView2 control then never sets its controller visible (it starts
    invisible, because pywebview creates the form hidden) and the page is not
    painted: a blank white window. `Form.Activate()` is also a no-op while
    `Visible` is False. The window is placed while hidden, then shown.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS blank-window fix
    """

    @property
    def has_app_mode(self) -> bool:
        """After the first page load only, like `supports_app_mode`, so the
        double-tap opens the browser rather than a blank window when WebView2
        failed to initialize.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 8)
        """
        return self._o._loaded.is_set()

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

    def _set_chrome(self, mode: str) -> int:
        """On the UI thread: border and taskbar ex-style. Returns the HWND.

        The handle is read from `native.Handle` after the switch (D-14) and
        returned, never stored here: the UI-thread callable hands it back and
        the worker caches it (`_keep_hwnd`), as the Threading model says.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (4)
        """
        if self._chrome == mode:
            # The handle as it is now, like the switch below, not the cache.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review cycle 2 (8)
            return int(self._native.Handle.ToInt64())
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
        return h

    def _keep_hwnd(self, h) -> None:
        """On the worker: cache the handle a UI-thread callable returned. A
        timed-out call (None) keeps the previous one."""
        if h:
            self._o._hwnd = h

    def show_peek(self) -> None:
        u, _ = _win32()

        def run():
            import System.Windows.Forms as WinForms  # type: ignore[import]
            h = self._set_chrome(PEEK)
            if u.IsIconic(h) or u.IsZoomed(h):
                u.ShowWindow(h, _SW_SHOWNOACTIVATE)
            b = WinForms.Screen.PrimaryScreen.Bounds
            u.SetWindowPos(h, _HWND_TOPMOST, b.X, b.Y, b.Width, b.Height,
                           _SWP_NOACTIVATE)
            # `ShowWindow`, never `SWP_SHOWWINDOW` (class docstring).
            u.ShowWindow(h, _SW_SHOWNA)
            return h

        self._keep_hwnd(self._ui(run, "show peek"))

    def hide(self) -> None:
        u, _ = _win32()
        self._ui(lambda: u.ShowWindow(self._h, _SW_HIDE), "hide")

    def apply_app(self, placement, focused: bool):
        """App chrome and placement; returns the placement now in effect."""
        u, WP = _win32()
        import ctypes

        def run():
            import System.Windows.Forms as WinForms  # type: ignore[import]
            h = self._set_chrome(APP)
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
                u.SetWindowPos(h, _HWND_TOP, x, y, w, hh, _SWP_NOACTIVATE)
                # `ShowWindow`, never `SWP_SHOWWINDOW` (class docstring).
                u.ShowWindow(h, _SW_SHOW if focused else _SW_SHOWNA)
                wp = WP()
                wp.length = ctypes.sizeof(WP)
                u.GetWindowPlacement(h, ctypes.byref(wp))
                return h, wp
            wp = _copy_placement(placement)
            wp.length = ctypes.sizeof(WP)
            wp.showCmd = _show_cmd_for(placement.showCmd, placement.flags,
                                       focused)
            if not u.SetWindowPlacement(h, ctypes.byref(wp)):
                raise OSError(ctypes.get_last_error(), "SetWindowPlacement")
            return h, placement

        result = self._ui(run, "apply app placement")
        if result is None:
            return None
        h, applied = result
        self._keep_hwnd(h)
        return applied

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

    def enable_browser_keys(self) -> bool:
        """On the UI thread, bounded: `AreBrowserAcceleratorKeysEnabled` and
        `AreDefaultContextMenusEnabled` on. False when `CoreWebView2` does
        not exist yet or the UI thread did not answer; logs nothing (it runs
        on every page load, and the worker logs the first failure)."""
        native = self._native

        def run():
            core = native.webview.CoreWebView2
            if core is None:
                return False
            settings = core.Settings
            settings.AreBrowserAcceleratorKeysEnabled = True
            settings.AreDefaultContextMenusEnabled = True
            return True

        return bool(self._ui(run, "enable browser keys", timed_out=False))

    def reload(self, url: str) -> bool:
        """pywebview's `load_url`, bounded. False when it timed out.

        It marshals itself with a synchronous `Form.Invoke` (never call it
        from inside a UI-thread callable), so a hung UI thread would block the
        worker for good. It runs on a short-lived thread instead; past 2 s a
        WARNING is logged, the worker moves on, and False tells the caller the
        sign-in is not known to have run (the load stays posted and may run
        when the UI thread recovers; the next show tries again).
        The URL carries a live login code: no log line names it.
        261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 review fix 10;
        result: final review (16)
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
            return False
        if "e" in box:
            raise box["e"]
        return True


def create_peek(server_url: str, hotkey: str = "ctrl+shift+z",
                mode: str = HOLD, browser_hotkey: str = "") -> PeekWindow | None:
    """Factory: create PeekWindow if available, else log warning and return None.

    An unknown `mode` (a hand-edited config.toml; the settings write path
    refuses one) logs a warning and uses Hold.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 2

    Both shortcuts go through `hotkeys.hotkey_error`, the validator the
    settings write path uses: an invalid peek shortcut falls back to
    ctrl+shift+z, and an invalid browser shortcut, or one that equals or
    contains the peek shortcut in force (or is contained by it), is turned
    off. Each logs a WARNING naming the problem.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 3
    """
    if not is_available():
        log.warning("PowerAtlas window disabled: %s", _IMPORT_ERROR)
        return None
    # The rule is `hotkeys`'s (one place, shared with the settings write
    # path); only the warnings are here.
    # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (5)
    effective = _hotkeys.effective_peek_hotkey(hotkey)
    if effective != hotkey:
        log.warning("Invalid peek_hotkey %r (%s). Falling back to %s",
                    hotkey, _hotkeys.hotkey_error(hotkey), effective)
        hotkey = effective
    browser = _hotkeys.effective_browser_hotkey(browser_hotkey, hotkey)
    if not browser and isinstance(browser_hotkey, str) and browser_hotkey.strip():
        problem = _hotkeys.hotkey_error(browser_hotkey.strip().lower())
        if problem is not None:
            log.warning("Invalid browser_hotkey %r (%s). The browser "
                        "shortcut is off", browser_hotkey, problem)
        else:
            log.warning("browser_hotkey %r conflicts with the peek shortcut "
                        "%r. The browser shortcut is off", browser_hotkey,
                        hotkey)
    normalized = mode.strip().lower() if isinstance(mode, str) else ""
    if normalized not in _PEEK_MODES:
        log.warning("Invalid peek_mode %r (need hold or toggle). Falling back to hold", mode)
        normalized = HOLD
    try:
        return PeekWindow(server_url, hotkey, normalized, browser)
    except Exception as e:
        log.warning("PowerAtlas window disabled: %s", e)
        return None
