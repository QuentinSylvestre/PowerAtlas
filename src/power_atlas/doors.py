"""The doors: how a browser or the PowerAtlas window gets signed in.

Every loopback route needs the `pa_local` cookie, and a door is how a client
gets one: a URL carrying a fresh one-time login code, exchanged for the cookie
on first load. The tray (Open PowerAtlas without app mode, Open in browser,
Copy login link) and the PowerAtlas window (creation, signing in again, the
browser shortcut, the double-tap without app mode) share these two helpers.
A neutral module, so `peek` no longer imports the tray UI module for them.
261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS Phase 5 (follow-up 12)
"""

import logging
import sys
import webbrowser

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


def open_in_browser(url: str) -> None:
    """Open ``url`` in the default browser. Never raises; never logs the URL."""
    try:
        if sys.platform == "win32":
            webbrowser.open(url)
        else:
            import subprocess as _sp
            _sp.Popen(["xdg-open", url],
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
    except Exception as e:
        # The type only: `url` carries a live login code, and an exception's
        # message can quote it.
        # 261006_MERGED_PEEK_AND_APP_WINDOW_WITH_CONFIGURABLE_SHORTCUTS final review (11)
        log.error("Failed to open browser: %s", type(e).__name__)
