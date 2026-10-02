"""Translates a provider's full transcript into the ACP wire-protocol's own
frame shapes, so the dashboard's static transcript panel and the live ACP
page can share one rendering path (this session's converged dashboard/ACP
merge plan, Phase 1c/2).

Deliberately independent of `acp.py`: `acp.py` is optional/guarded-import
prototype code (see `web.py`'s `try: from . import acp` -- an unguarded
import failure there would take down the whole dashboard, guarded it only
costs `/acp`), so this module must be usable even when `acp` failed to
import. The frame *shape* (`{"type", "sessionId", "payload"}`) and the
frame-type/tool-status vocabularies below are copied from `acp.py`'s
`envelope()`/`SERVER_TYPES` and `_tool_payload`, not imported from it.
"""

import json
import time

from .data import TranscriptEvent

# The subset of acp.py's SERVER_TYPES this module ever emits.
_FRAME_TYPES = frozenset({"chunk", "rendered", "tool_call", "tool_update"})

# Bounds on the markdown work one `translate_transcript` call does. mistune's
# inline parser is quadratic on a hostile paragraph (`"[a](" * n`: 8 KiB took 1.0 s,
# 16 KiB 4.1 s, 32 KiB 15.9 s, 256 KiB about 20 minutes, in a worker thread that
# shares the server's GIL), and a hostile `[` run makes an AST 30 times its text.
# Every limit degrades to the `chunk` frame alone (or, for one paragraph, a plain
# text token), which the renderer already shows for any message with no `rendered`
# frame; none of them changes what a normal message renders. Measured 2026-10-02
# on the development machine (the `[a](` worst case): 8 KiB paragraph 1.0 s,
# so a call is bounded by MD_BUDGET_SECONDS plus about one such paragraph.
# 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4
MD_MAX_MESSAGE_CHARS = 64 * 1024      # a longer message gets no `rendered` frame
MD_MAX_INLINE_CHARS = 8 * 1024        # a longer paragraph, heading or table cell stays plain text
MD_BUDGET_SECONDS = 5.0               # markdown time for the whole call; after it, plain text only
# JSON size of all `rendered` frames together. Tokens run up to about 11 times a message's
# text on dense markup (measured), so this still renders about 1.5 MB of such text; a
# hostile table reaches 45 times (65 KiB gave 3.0 MB), which is what it bounds.
MD_MAX_RENDERED_BYTES = 16 * 1024 * 1024
# The clock the budget reads, a seam so a test can move time.
_clock = time.monotonic

# Same known ACP tool "kind" vocabulary as acp.html's TOOL_KIND_ICON, so the
# shared renderer's icon lookup resolves for a translated call the same way
# it does for a live one.
_TOOL_KIND_BY_NAME: dict[str, str] = {
    "fs_write": "edit", "write": "edit", "str_replace": "edit",
    "fs_read": "read", "read": "read",
    "shell": "execute", "execute": "execute", "execute_bash": "execute", "bash": "execute",
    "search": "search", "grep": "search", "fs_search": "search",
    "delete": "delete", "rm": "delete",
    "move": "move", "rename": "move",
    "fetch": "fetch", "web_fetch": "fetch",
    # Codex tool names (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, D14)
    "shell_command": "execute", "exec": "execute", "apply_patch": "edit", "view_image": "read",
}

_TOOL_TITLE_BY_NAME: dict[str, str] = {
    "fs_write": "Write file", "write": "Write file", "str_replace": "Replace text",
    "fs_read": "Read file", "read": "Read file",
    "shell": "Run command", "execute": "Run command", "execute_bash": "Run command", "bash": "Run command",
    "search": "Search", "grep": "Search", "fs_search": "Search",
    "delete": "Delete", "rm": "Delete",
    "move": "Move", "rename": "Rename",
    "fetch": "Fetch URL", "web_fetch": "Fetch URL",
    "shell_command": "Run command", "exec": "Run command",
    "apply_patch": "Apply patch", "view_image": "View image",
}

# Same input-key priority acp.py's own _tool_input_text uses to pick the
# "command" a tool call is about to run out of an untyped input dict.
_TOOL_INPUT_KEYS = ("command", "path", "file_path", "query", "content")


def _envelope(type_: str, payload: dict, session_id: str) -> dict:
    """Same wire-frame shape as acp.py's envelope() -- duplicated, not
    imported, per this module's isolation requirement (see module docstring)."""
    if type_ not in _FRAME_TYPES:
        raise ValueError(f"'{type_}' is not a frame type this translator emits")
    return {"type": type_, "sessionId": session_id, "payload": payload}


def _derive_tool_display(tool_name: str, tool_args: dict) -> tuple[str, str, str]:
    """Best-effort (title, kind, command) for a tool call read from a file.

    Unlike a live ACP session, where kiro-cli's own agent sends a
    human-authored `title`/`kind` per call, a persisted transcript only ever
    stores the raw tool name and arguments (confirmed: v3's `messages.jsonl`
    tool_call payload carries `toolName`+`args`+`toolCallId` and nothing
    else; v2's is the same shape under different field names). Even a
    *live* `session/load` replay has this same gap -- kiro-cli's own reload
    path falls back to reconstructing title/kind from the stored tool name
    too (see acp.py's `_tool_locations` docstring) -- so this mirrors that
    same fallback client-side rather than inventing a new problem.
    """
    kind = _TOOL_KIND_BY_NAME.get(tool_name, "other")
    title = _TOOL_TITLE_BY_NAME.get(tool_name) or (tool_name.replace("_", " ").title() or "Tool call")

    command = ""
    if isinstance(tool_args, dict):
        for key in _TOOL_INPUT_KEYS:
            value = tool_args.get(key)
            if isinstance(value, str) and value.strip():
                command = value
                break
        else:
            try:
                command = json.dumps(tool_args, sort_keys=True)
            except (TypeError, ValueError):
                command = ""
    return title, kind, command


def _get_markdown_parser():
    """Same mistune configuration as acp.py's `_markdown` (raw AST tokens, no
    HTML renderer, table plugin) so a translated `rendered` frame's tokens
    match what the shared renderer's markdown builder already knows how to
    walk. Not imported from acp.py (see module docstring); duplicated the
    same way `web.py`'s own dashboard-tooltip markdown rendering already
    does. Returns None (never raises) if mistune is unavailable -- the
    caller degrades to plain `chunk` text only, the same "a rendering is an
    upgrade, never load-bearing" rule acp.py's own `_close_bubble` follows.
    """
    try:
        import mistune
        return mistune.create_markdown(renderer=None, plugins=["table"])
    except Exception:
        return None


class _BoundedInline:
    """The markdown instance's inline parser with `MD_MAX_INLINE_CHARS` and a
    deadline on it. The inline parser is called once per paragraph, heading and
    table cell, so a hostile paragraph is never parsed past the size limit and
    a long run of them stops costing time once the deadline has passed: the
    block is returned as one plain `text` token, which the renderer shows as it
    shows any text. 261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 4"""

    def __init__(self, inner, deadline: float):
        self._inner = inner
        self._deadline = deadline

    def __call__(self, text, env):
        if len(text) > MD_MAX_INLINE_CHARS or _clock() >= self._deadline:
            return [{"type": "text", "raw": text}]
        return self._inner(text, env)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def translate_transcript(events: list[TranscriptEvent], session_id: str) -> list[dict]:
    """Turn a provider's full transcript into a list of ACP wire frames.

    Feeds the dashboard's static transcript panel through the same shared
    renderer the live ACP page uses (Phase 2 of this session's plan) --
    `chunk`+`rendered` for each user/assistant turn, `tool_call` (status
    "started") for each tool call and a following `tool_update` (status
    "completed"/"failed") once its matching `tool_result` arrives, mirroring
    the live protocol's own two-step tool-call lifecycle.

    A `tool_result` with no preceding `tool_call` for the same id, or whose
    outcome is unknown (`success is None`), is dropped rather than guessed
    at -- the call simply stays at "started", the same as a live call this
    client never saw resolve. One exception: a result flagged `outcome_unknown`
    (set only by the Codex adapter, for a call that finished but whose file
    records neither an exit code nor an outcome marker) is shown as "finished"
    rather than left at "started": a neutral status that claims neither success
    nor failure (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, D13 as amended
    2026-10-01).
    """
    frames: list[dict] = []
    open_tool_calls: set[str] = set()
    md = _get_markdown_parser()
    deadline = _clock() + MD_BUDGET_SECONDS
    rendered_bytes = 0
    rendered_full = False
    if md is not None and callable(getattr(md, "inline", None)):
        md.inline = _BoundedInline(md.inline, deadline)

    for event in events:
        if event.kind in ("user", "assistant"):
            if not event.text:
                continue
            role = "user" if event.kind == "user" else "agent"
            frames.append(_envelope("chunk", {"role": role, "text": event.text}, session_id))
            if (md is not None and len(event.text) <= MD_MAX_MESSAGE_CHARS
                    and _clock() < deadline and not rendered_full):
                try:
                    tokens = md(event.text)
                except Exception:
                    tokens = None
                if isinstance(tokens, list) and tokens:
                    # The JSON size of this frame, so the total the response carries in
                    # markup holds the limit (a frame that would pass it is not sent).
                    size = len(json.dumps(tokens))
                    if rendered_bytes + size <= MD_MAX_RENDERED_BYTES:
                        rendered_bytes += size
                        frames.append(_envelope("rendered", {"tokens": tokens}, session_id))
                    else:
                        rendered_full = True    # no later message is parsed either
        elif event.kind == "tool_call":
            if not event.tool_call_id:
                continue
            title, kind, command = _derive_tool_display(event.tool_name, event.tool_args)
            frames.append(_envelope("tool_call", {
                "toolCallId": event.tool_call_id, "title": title, "kind": kind,
                "status": "started", "command": command,
            }, session_id))
            open_tool_calls.add(event.tool_call_id)
        elif event.kind == "tool_result":
            if event.tool_call_id not in open_tool_calls:
                continue
            if event.success is True:
                status = "completed"
            elif event.success is False:
                status = "failed"
            elif event.outcome_unknown:
                status = "finished"
            else:
                continue
            frames.append(_envelope("tool_update", {
                "toolCallId": event.tool_call_id, "status": status,
            }, session_id))
        # Any other TranscriptEvent.kind is silently skipped -- matching the
        # provider adapters' own "unrecognized shape -> no content" rule
        # rather than raising on a future/unknown event kind.
    return frames
