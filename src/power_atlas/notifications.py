"""OS toast notifications for ACP session events.

**Event-shaped, not poll-shaped.** Each entry point below names one discrete
thing that happened -- a turn ended, a permission request blocked a turn, the
agent raised an error mid-turn -- and fires exactly one toast for it. Callers
are expected to have already decided *whether* to notify; this module decides
only how a notification looks and how it reaches the desktop.

The previous model was a status-transition table (``(working, waiting)`` and
``(working, errored)``) plus a per-session cooldown, driven by a polling
caller. It was removed on 2026-09-19 for two reasons. It had never run: its
only caller was ``web._session_status``, which itself had no production
callers, and ``mark_initialized()`` -- the gate that armed it -- was never
called at all, so the transition check returned early on every path. And it
was the wrong shape even once connected: a turn ending is an event, not a
status a poll samples, and the 60-second cooldown would have silently
swallowed a second permission request raised within a minute of the first,
which is precisely the case that leaves a turn stalled with nobody told.
"""
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("power_atlas.notifications")

# The Windows toast template. Index 5 is `ToastText02`: one bold header line
# plus one body line, no image.
#
# This was index 0 (`ToastImageAndText01`) until 2026-09-19, and that was a
# silent defect rather than a styling choice. Measured on this Windows build,
# template 0 has exactly **one** `<text>` slot, but the code wrote two -- so
# `$text[1].AppendChild(...)` raised `InvokeMethodOnNull` and the body was
# dropped. Nothing surfaced it: a PowerShell non-terminating error leaves the
# exit code at 0, `Popen` routes stderr to DEVNULL, and the `except` below
# guards only process *spawn*, never what the child does. The toast rendered
# with a title and an empty `<image src="">` placeholder, and every observable
# signal reported success. Verify a template change with
# `GetTemplateContent(<i>).GetElementsByTagName("text").Length` before trusting
# a slot count -- the numbering is not self-describing.
_WINDOWS_TOAST_TEMPLATE = 5

# Read by the PowerShell child out of its own environment rather than
# interpolated into the script text. Both fields can carry agent-authored
# content (a tool-call title, a workspace path), and PowerShell expands `$...`
# and honours backticks inside a double-quoted string -- so text spliced into
# the script body would be executable. `$env:` lookups are resolved at runtime
# and never re-parsed, which closes that hole; WinRT's `CreateTextNode` does
# the XML escaping itself, so no pre-escaping is needed either.
_TOAST_TITLE_VAR = "PA_TOAST_TITLE"
_TOAST_BODY_VAR = "PA_TOAST_BODY"

# A toast body is a glance, not a transcript. Agent-authored text arrives
# clamped by the caller as well; this is the last-resort backstop.
MAX_TOAST_BODY_CHARS = 180


def notify_turn_end(label: str, stop_reason: str) -> None:
    """A turn finished on a session nobody is watching."""
    body = ("Hit an error" if stop_reason == "error"
            else "Done — waiting for you")
    _fire_toast(f"PowerAtlas — {label}", body)


def notify_permission_needed(label: str, tool_title: str) -> None:
    """The agent is blocked on a tool-permission decision.

    Fires whether or not the session is watched: the turn stops here either
    way, so this is the one event whose cost of being missed is a stalled
    session rather than a late glance.
    """
    detail = tool_title.strip() or "a tool"
    _fire_toast(f"PowerAtlas — {label}", f"Needs approval: {detail}")


def notify_agent_error(label: str, message: str) -> None:
    """The agent reported an error mid-turn.

    Distinct from `notify_turn_end`'s error case: a `display_error` does not
    necessarily end the turn, so this can fire while the session keeps running.
    """
    detail = message.strip() or "see the transcript"
    _fire_toast(f"PowerAtlas — {label}", f"Error: {detail}")


def _fire_toast(title: str, body: str) -> None:
    """Platform dispatch. The single seam tests patch -- keep it that way.

    Never raises: a notification failing is never worth failing the caller,
    which is an ACP turn boundary or an inbound permission request.
    """
    body = body[:MAX_TOAST_BODY_CHARS]
    try:
        if sys.platform == "win32":
            _fire_windows_toast(title, body)
        else:
            _fire_linux_notify(title, body)
    except Exception:
        log.debug("toast dispatch failed", exc_info=True)


def _fire_windows_toast(title: str, body: str) -> None:
    """Windows toast via the WinRT API, driven by a detached PowerShell child.

    The script is a fixed literal -- no caller text is spliced into it. See
    `_TOAST_TITLE_VAR` for why.
    """
    script = (
        '[Windows.UI.Notifications.ToastNotificationManager,'
        ' Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; '
        '$template = [Windows.UI.Notifications.ToastNotificationManager]'
        f'::GetTemplateContent({_WINDOWS_TOAST_TEMPLATE}); '
        # `@(...)` materialises the live WinRT node list into a plain array.
        # Indexing the collection directly re-enumerates it, and the first
        # AppendChild invalidates that enumerator ("Collection was modified").
        '$text = @($template.GetElementsByTagName("text")); '
        f'$text[0].AppendChild($template.CreateTextNode($env:{_TOAST_TITLE_VAR}))'
        ' | Out-Null; '
        # The `}}` is an f-string escape for one literal `}`, closing the
        # PowerShell `if` block -- not a typo.
        'if ($text.Length -gt 1) { $text[1].AppendChild('
        f'$template.CreateTextNode($env:{_TOAST_BODY_VAR})) | Out-Null }}; '
        '$toast = [Windows.UI.Notifications.ToastNotification]::new($template); '
        '[Windows.UI.Notifications.ToastNotificationManager]'
        '::CreateToastNotifier("PowerAtlas").Show($toast)'
    )
    import base64
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ)
    env[_TOAST_TITLE_VAR] = title
    env[_TOAST_BODY_VAR] = body
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("Windows toast failed", exc_info=True)


def _fire_linux_notify(title: str, body: str) -> None:
    """Linux notification via notify-send.

    Arguments are passed as a list, so caller text is never shell-parsed --
    the injection concern `_fire_windows_toast` documents does not apply here.
    """
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
