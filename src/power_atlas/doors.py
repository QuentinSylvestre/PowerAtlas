"""The doors: how a browser or the PowerAtlas window gets signed in.

Every loopback route needs the `pa_local` cookie, and a door is how a client
gets one: a URL carrying a fresh one-time login code, exchanged for the cookie
on first load. The tray (Open PowerAtlas without app mode, Open in browser,
Copy login link) and the PowerAtlas window (creation, signing in again, the
browser shortcut, the double-tap without app mode) share these two helpers.
A neutral module, so `peek` no longer imports the tray UI module for them.
261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 12)
"""

import atexit
import html
import json
import logging
import os
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

log = logging.getLogger("power_atlas.doors")


def login_url(server_url: str, next: str | None = None) -> str:
    """``server_url`` plus a fresh one-time login code, via `web.login_url`.

    ``next`` is a same-origin path-and-query to land on after sign-in
    (`web.login_path` keeps it only when the exchange would accept it).

    Minted in-process — the tray, the window and the server share one
    process — never through an HTTP route. `web` is imported lazily, so
    importing this module does not pull in the web app.
    260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL Phase 5; moved from
    `tray._login_url`: 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 12);
    ``next``: Phase 5 (follow-up 6)
    """
    from .web import login_url as _mint_login_url
    if next is None:
        return _mint_login_url(server_url)
    return _mint_login_url(server_url, next=next)


# Off Windows, how long a landing file (see `_write_landing_file`) stays on
# disk. The browser reads it once, right after `xdg-open` starts it.
# 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS follow-up 19
LANDING_FILE_TTL_S = 30.0
_landing_lock = threading.Lock()
_landing_files: set = set()


def open_in_browser(url: str) -> None:
    """Open ``url`` in the default browser. Never raises; never logs the URL.

    Windows: `webbrowser.open` (ShellExecute; no process command line holds
    the URL). Elsewhere `xdg-open` would carry the URL, and with it a live
    login code, on its command line, which any local user can read in
    `/proc/<pid>/cmdline`. So `xdg-open` gets a `file://` URL of a private
    landing file that forwards the browser to ``url`` instead.
    261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS follow-up 19
    """
    try:
        if sys.platform == "win32":
            webbrowser.open(url)
        else:
            _open_via_landing_file(url)
    except Exception as e:
        # The type only: `url` carries a live login code, and an exception's
        # message can quote it.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (11)
        log.error("Failed to open browser: %s", type(e).__name__)


def _landing_dir() -> str:
    """The per-user runtime directory (`XDG_RUNTIME_DIR`, mode 0700 by the
    XDG spec) when there is one, else the system temporary directory. The
    file itself is 0600 either way (`tempfile.mkstemp`)."""
    run = os.environ.get("XDG_RUNTIME_DIR")
    if run and os.path.isdir(run):
        return run
    return tempfile.gettempdir()


def _write_landing_file(url: str) -> str:
    """Write a page that forwards to ``url`` into a new private file and
    return its path. `tempfile.mkstemp` creates it exclusively, readable and
    writable by this user only (0600). The URL is escaped for the attribute
    and for the script; a write failure removes the file and raises."""
    fd, path = tempfile.mkstemp(prefix="power-atlas-login-", suffix=".html",
                                dir=_landing_dir())
    try:
        target = json.dumps(url).replace("<", "\\u003c")
        page = ('<!doctype html><html><head><meta charset="utf-8">'
                '<meta name="referrer" content="no-referrer">'
                '<meta http-equiv="refresh" content="0;url='
                + html.escape(url, quote=True) + '">'
                '<title>PowerAtlas</title>'
                '<script>location.replace(' + target + ');</script>'
                '</head><body></body></html>')
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            f.write(page)
    except BaseException:
        if fd is not None:
            os.close(fd)
        _remove_landing_file(path)
        raise
    return path


def _remove_landing_file(path: str) -> None:
    """Delete a landing file if it is still there. Never raises."""
    with _landing_lock:
        _landing_files.discard(path)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Could not remove a browser landing file: %s",
                    type(e).__name__)


def _remove_all_landing_files() -> None:
    with _landing_lock:
        paths = list(_landing_files)
    for path in paths:
        _remove_landing_file(path)


# A normal interpreter exit removes the files still waiting for their timer.
# PowerAtlas's own shutdown ends in `os._exit`, which skips `atexit`; there a
# file outlives the process (0600, holding a one-time code that expires).
atexit.register(_remove_all_landing_files)


def _open_via_landing_file(url: str) -> None:
    """`xdg-open` a private landing file for ``url``, never ``url`` itself;
    the file is deleted after `LANDING_FILE_TTL_S` (a daemon timer), at
    interpreter exit if still there, and at once if `xdg-open` cannot start.
    Raises on failure, for `open_in_browser` to log by type. There is no
    fallback that passes ``url`` on the command line."""
    import subprocess as _sp
    path = _write_landing_file(url)
    with _landing_lock:
        _landing_files.add(path)
    try:
        _sp.Popen(["xdg-open", Path(path).as_uri()],
                  stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    except BaseException:
        _remove_landing_file(path)
        raise
    timer = threading.Timer(LANDING_FILE_TTL_S, _remove_landing_file,
                            args=(path,))
    timer.daemon = True
    timer.start()
