"""System tray icon and menu."""

import logging
import os
import sys
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from .config import Config, CONFIG_DIR, load_config
# The door helpers, looked up through the module at call time so a patch of
# `doors.open_in_browser` reaches every tray action.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 12)
from . import doors

log = logging.getLogger("power_atlas.tray")

_shutdown_event = threading.Event()
_restart_requested = False
_icon_instance = None
# The PowerAtlas window (`peek.PeekWindow`), registered by `__main__`. The tray
# never imports `peek` (D-15); both import their door helpers from `doors`.
# It exposes `show_app()`, `stop()` and `supports_app_mode`.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 (D-15);
# doors: Phase 5 (follow-up 12)
_window_controller = None


def set_window_controller(ctrl) -> None:
    """Register the PowerAtlas window: tray Open uses it, Quit/Restart stop it."""
    global _window_controller
    _window_controller = ctrl


def _stop_window() -> None:
    ctrl = _window_controller
    if ctrl is not None:
        try:
            ctrl.stop()
        except Exception:
            pass


def _app_mode_possible() -> bool:
    """Static facts only: Windows and a window registered.

    Decides whether **Open in browser** is listed. pystray on Windows builds
    the menu once at setup, before the window is ready, so readiness cannot
    decide visibility; it decides routing at click time instead. A window is
    registered only when `create_peek` built one, which needs peek's
    dependencies, so asking `peek.is_available()` here added nothing but the
    tray-to-peek import D-15 avoids.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1;
    import dropped: final review (21)
    """
    return sys.platform == "win32" and _window_controller is not None


def _app_mode_available() -> bool:
    """Click-time check: the window is registered and app mode is ready."""
    ctrl = _window_controller
    if ctrl is None:
        return False
    try:
        return bool(ctrl.supports_app_mode)
    except Exception:
        return False


def _create_icon() -> Image.Image:
    static_dir = Path(__file__).parent / "static"
    if sys.platform == "win32":
        icon_path = static_dir / "poweratlas-tray.ico"
    else:
        icon_path = static_dir / "poweratlas-tray.png"
    try:
        with Image.open(icon_path) as img:
            img.load()
            return img.copy()
    except OSError:
        log.warning("Tray icon not found at %s, using fallback", icon_path)
        img = Image.new("RGBA", (16, 16), (60, 120, 220, 255))
        ImageDraw.Draw(img).text((3, 1), "P", fill="white")
        return img


# `OpenClipboard` fails while another process holds the clipboard, which
# clipboard managers and remote-desktop clients do for a few milliseconds at a
# time. A handful of tries tens of milliseconds apart rides that out without a
# noticeable delay on the tray thread.
# 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
_CLIPBOARD_ATTEMPTS = 5
_CLIPBOARD_RETRY_SECONDS = 0.05


def _has_clipboard() -> bool:
    """Whether this platform has a clipboard mechanism `_copy_to_clipboard` uses.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 review
    """
    return sys.platform == "win32"


def _copy_to_clipboard(text: str) -> bool:
    """Put ``text`` on the Windows clipboard. False when it could not.

    `pywin32`'s `win32clipboard`, already a declared Windows dependency, so
    "Copy login link" adds no package. There is no clipboard elsewhere without
    one, and the caller displays the link instead. On Windows a failed attempt
    is retried `_CLIPBOARD_ATTEMPTS` times before giving up, and only the final
    failure is logged — never ``text``, which is a live login link.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 (retry:
    Phase 5 review)
    """
    if not _has_clipboard():
        return False
    try:
        import win32clipboard
    except Exception as e:
        log.error("Could not copy the login link: no clipboard module (%s)",
                  type(e).__name__)
        return False
    error = None
    for attempt in range(_CLIPBOARD_ATTEMPTS):
        if attempt:
            time.sleep(_CLIPBOARD_RETRY_SECONDS)
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text,
                                                win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            error = e
    log.error("Could not copy the login link to the clipboard after %d "
              "attempts: %s", _CLIPBOARD_ATTEMPTS, error)
    return False


def copy_login_link(server_url: str, icon=None) -> str:
    """The "Copy login link" tray action: an in-process callback, never a route.

    A network-reachable mint would hand a login to any local process that
    asked (D-3), so this exists only as a menu item. The link opens PowerAtlas
    in another browser, or re-enters one that lost its cookie; it works once,
    within the login code's TTL. It is never logged: it is a live credential
    until used. Returns the link for the caller's (and a test's) benefit.

    Where a clipboard exists (Windows) but the copy failed, the notification
    says so and does **not** show the link: a Windows toast persists in Action
    Center, its text cannot be selected, and the link would sit there as a
    live credential. Only a platform with no clipboard mechanism displays it.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5 (failure
    branch: Phase 5 review)
    """
    url = doors.login_url(server_url)
    if _copy_to_clipboard(url):
        message, title = ("Paste it into a browser within 2 minutes. It "
                          "works once."), "Login link copied"
    elif _has_clipboard():
        message, title = ("The clipboard was busy. Choose \"Copy login link\" "
                          "again."), "Could not copy the login link"
    else:
        # No clipboard mechanism: display the link for manual copy.
        message, title = url, "PowerAtlas login link (works once, 2 minutes)"
    if icon is not None:
        try:
            icon.notify(message, title)
        except Exception as e:
            # The type only: on a platform with no clipboard the message is
            # the login link, and a backend's exception can quote it.
            # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 17)
            log.error("Could not display the login link notification: %s",
                      type(e).__name__)
    return url


def _warmup() -> None:
    from .data import warmup_pinned
    from .config import load_config as _load_config
    threading.Thread(target=warmup_pinned,
                     args=(_load_config().pinned_folders,), daemon=True).start()


def _build_menu(server_url: str) -> "pystray.Menu":
    """The tray menu, built apart from the icon so tests can inspect it.

    **Open PowerAtlas** (default) shows the window in app mode, or opens the
    browser when app mode is unavailable; **Open in browser** is listed only
    where app mode is possible.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 1 (D-5)
    """

    def on_open_browser(icon, item):
        _warmup()
        # A fresh login code per open, so the browser lands signed in.
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
        doors.open_in_browser(doors.login_url(server_url))

    def on_open_app(icon, item):
        if _app_mode_available():
            _warmup()
            _window_controller.show_app()
        else:
            on_open_browser(icon, item)

    def on_copy_login_link(icon, item):
        copy_login_link(server_url, icon)

    def on_logs(icon, item):
        log_path = CONFIG_DIR / "orchestrator.log"
        if log_path.exists():
            if sys.platform == "win32":
                os.startfile(str(log_path))
            else:
                import subprocess as _sp
                _sp.Popen(["xdg-open", str(log_path)])

    def on_quit(icon, item):
        _stop_window()
        _shutdown_event.set()
        icon.stop()

    def on_restart(icon, item):
        global _restart_requested
        _restart_requested = True
        _stop_window()
        _shutdown_event.set()
        icon.stop()

    return pystray.Menu(
        pystray.MenuItem("Open PowerAtlas", on_open_app, default=True),
        pystray.MenuItem("Open in browser", on_open_browser,
                         visible=lambda item: _app_mode_possible()),
        # 260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5
        pystray.MenuItem("Copy login link", on_copy_login_link),
        pystray.MenuItem("Logs", on_logs),
        pystray.MenuItem("Restart", on_restart),
        pystray.MenuItem("Quit", on_quit),
    )


def run_tray(server_url: str, config: Config) -> None:
    """Run pystray on the calling thread (blocks)."""
    icon = pystray.Icon("power-atlas", _create_icon(), "PowerAtlas",
                        _build_menu(server_url))
    global _icon_instance
    _icon_instance = icon
    icon.run()


def get_shutdown_event() -> threading.Event:
    return _shutdown_event


def restart_requested() -> bool:
    return _restart_requested


def trigger_restart() -> None:
    """Request a restart from outside the tray thread (e.g. from web.py).

    Mirrors what ``on_restart`` does: sets the restart flag, fires the shutdown
    event, and stops the icon.  Safe to call from any thread; pystray's
    ``Icon.stop()`` is thread-safe.
    """
    global _restart_requested
    _restart_requested = True
    _stop_window()
    _shutdown_event.set()
    if _icon_instance is not None:
        _icon_instance.stop()
