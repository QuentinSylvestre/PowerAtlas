"""Toast notifications for session status transitions."""
import base64
import html
import logging
import shutil
import subprocess
import sys
import time
from collections import OrderedDict

log = logging.getLogger("power_atlas.notifications")

_COOLDOWN_SECONDS = 60.0
_MAX_STATES = 100

# Transitions that trigger notification
_NOTIFY_TRANSITIONS = frozenset({
    ("working", "waiting"),
    ("working", "errored"),
})


class _SessionState:
    __slots__ = ("last_status", "last_notified_at")

    def __init__(self):
        self.last_status: str = "closed"
        self.last_notified_at: float = 0.0


_session_states: OrderedDict[str, _SessionState] = OrderedDict()
_initialized = False


def mark_initialized() -> None:
    """Call after the first complete render pass to establish status baselines.

    Prevents a burst of notifications on startup (all sessions transition
    from unknown->current_status on the first render).
    """
    global _initialized
    _initialized = True


def check_and_notify(session_id: str, session_title: str,
                     new_status: str, enabled: bool) -> None:
    """Check if status transition warrants a notification. Fire if so.

    Before mark_initialized() is called, transitions are tracked but no
    notifications are fired (startup baseline establishment).
    """
    if not enabled:
        return

    # Get or create state entry
    if session_id in _session_states:
        state = _session_states[session_id]
        # Move to end for LRU ordering
        _session_states.move_to_end(session_id)
    else:
        state = _SessionState()
        _session_states[session_id] = state
        # Evict oldest if over limit
        while len(_session_states) > _MAX_STATES:
            _session_states.popitem(last=False)

    old_status = state.last_status
    state.last_status = new_status

    # Don't fire before initialization (startup baseline)
    if not _initialized:
        return

    if (old_status, new_status) not in _NOTIFY_TRANSITIONS:
        return

    now = time.monotonic()
    if now - state.last_notified_at < _COOLDOWN_SECONDS:
        return

    state.last_notified_at = now
    _fire_toast(session_title, new_status)


def _fire_toast(title: str, status: str) -> None:
    """Platform-specific toast notification."""
    messages = {
        "waiting": "Done \u2014 waiting for you",
        "errored": "Hit an error",
    }
    body = messages.get(status, status)
    if sys.platform == "win32":
        _fire_windows_toast(f"PowerAtlas \u2014 {title}", body)
    else:
        _fire_linux_notify(f"PowerAtlas \u2014 {title}", body)


def _fire_windows_toast(title: str, body: str) -> None:
    """Windows toast via PowerShell WinRT API with base64-encoded command."""
    safe_title = html.escape(title)
    safe_body = html.escape(body)
    script = (
        '[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; '
        '$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); '
        '$text = $template.GetElementsByTagName("text"); '
        f'$text[0].AppendChild($template.CreateTextNode("{safe_title}")) | Out-Null; '
        f'$text[1].AppendChild($template.CreateTextNode("{safe_body}")) | Out-Null; '
        '$toast = [Windows.UI.Notifications.ToastNotification]::new($template); '
        '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("PowerAtlas").Show($toast)'
    )
    # Use -EncodedCommand to avoid string injection
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("Windows toast failed", exc_info=True)


def _fire_linux_notify(title: str, body: str) -> None:
    """Linux notification via notify-send."""
    if not shutil.which("notify-send"):
        log.debug("notify-send not found")
        return
    try:
        subprocess.Popen(
            ["notify-send", title, body, "--app-name=PowerAtlas"],
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("Linux notify-send failed", exc_info=True)
