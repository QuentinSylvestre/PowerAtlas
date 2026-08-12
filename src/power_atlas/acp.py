"""Browser-facing half of the ACP prototype, and the agent process behind it.

This module owns everything that happens on a ``/ws/acp`` socket once
``web.py`` has accepted it — envelope parsing, the connection and session
registry, the outbound fan-out machinery — and, since Phase 3b, the supervised
``kiro-cli acp`` subprocess it all talks to: spawn, the JSON-RPC handshake,
``session/new``, and teardown. Phase 4 adds ``session/prompt``, the
``agent_message_chunk`` and tool-call fan-out behind it, and the per-session
ring buffer that a reload replays. Phase 5 adds ``session/load``, which reaches
sessions this process never created — including ones started from a terminal.
Phase 6 adds ``session/cancel``, session close — which is *not*
``session/close``; see ``CLOSE_METHOD`` — and the context-window telemetry
that arrives alongside them.

Isolation boundary — this module imports exactly two names from the rest of
``power_atlas``: ``config.CONFIG_DIR``, to place the agent's neutral cwd where
every other PowerAtlas artifact lives, and ``launcher._SESSION_ID_RE``, so the
guard in front of a client-supplied session id is the one the launch path
already applies rather than a second copy free to drift from it. ``launcher``
imports one name from ``config`` and nothing else. Two caches elsewhere in the
package are plain unlocked ``OrderedDict``s, safe only because every current
caller runs on the event loop — and this module now runs an OS reader thread
that does not. Neither of the two modules holding them is imported here, and
neither is reachable from ``config`` (which imports nothing from the package at
all) or from ``launcher``. So the property is still held by the import graph
rather than by discipline; it is just no longer stated as "imports nothing".
The plan's exit criterion greps this file for those two module names, which is
why they are described here rather than spelled.

Wire contract, identical in both directions::

    {"type": <str>, "sessionId": <str|null>, "payload": <object>}

Client to server: ``subscribe`` (attach this socket to a session and replay its
buffer), ``new`` (create a session against a cwd), ``load`` (adopt a session
that exists in the agent's own store but not in this process), ``prompt``,
``cancel``, ``close``.

Server to client: ``session`` (id and metadata after ``new``/``subscribe``),
``chunk``, ``rendered``, ``tool_call``, ``tool_update``, ``meta``, ``error``,
``agent_died``, ``session_closed``, ``history_truncated``, ``history`` (the
whole replay, coalesced into one frame), ``thought`` (an ``agent_thought_chunk``
notification's text, if the agent ever sends one — see ``_on_notification``),
``subagents`` (the current sub-agent crew for a session that has dispatched a
fan-out — see ``_Supervisor._on_subagent_list``). A sub-agent's own session id
is subscribable exactly like a real one — same ``subscribe``/``session``/
``history``/``chunk``/``tool_call`` frames — except its ``session`` frame
carries ``readOnly: true`` and ``prompt``/``cancel``/``close`` against it are
refused: see ``_Supervisor.subagent_sessions``.
``envelope`` refuses any type not in ``SERVER_TYPES``.

Session identity survives a reload because the page carries ``?sid=…`` and
re-sends ``subscribe`` on connect.

Everything below the registry runs against one lazily spawned process holding N
sessions. Its health is judged from the JSON-RPC channel alone — never from an
exit code and never from ``stderr``, which is ``DEVNULL`` here: the agent has
been observed dying with exit 0 and no stderr at all, and an undrained pipe
deadlocks the child once ~64 KB accumulates in it.
"""

import asyncio
import base64
import binascii
import collections
import contextlib
import itertools
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from .config import CONFIG_DIR
from .launcher import _SESSION_ID_RE

log = logging.getLogger("power_atlas.acp")

try:
    import psutil
except Exception as _e:  # pragma: no cover - only when the dep is missing
    psutil = None
    log.warning("psutil unavailable — ACP teardown falls back to the job object: %s", _e)

try:
    # The teardown *guarantee*. `lifespan` tree-kill is only the fast path: it
    # is structurally unreachable from `--stop`/`--restart` (TerminateProcess),
    # from a crash, and from Task Manager. A job with
    # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE covers all of them, because Windows
    # destroys the whole tree when the last handle to the job closes — which
    # process death does whether or not any of our code runs.
    import win32api
    import win32con
    import win32job
except Exception as _e:  # pragma: no cover - non-Windows or missing pywin32
    win32api = win32con = win32job = None
    log.warning("pywin32 unavailable — no ACP job object, so no teardown "
                "guarantee at all; the supervisor refuses to spawn: %s", _e)

try:
    # What a peer reset mid-send actually raises, measured rather than assumed:
    # uvicorn raises `ClientDisconnected` (`uvicorn/protocols/utils.py`, an
    # `IOError`, so *not* a `WebSocketDisconnect`), and Starlette's
    # `WebSocket.send` re-wraps any `OSError` out of `_send` into
    # `WebSocketDisconnect(1006)` with the uvicorn error as `__cause__`. On
    # starlette 0.37.2 and 1.3.1 the writer therefore sees the wrapped form.
    # Naming the uvicorn type anyway costs nothing and covers the paths the
    # wrap does not reach — Starlette only wraps in its CONNECTED branch — and
    # any future narrowing of it, which would otherwise turn every routine
    # mid-stream disconnect in Phase 3b into an ERROR with a traceback.
    from uvicorn.protocols.utils import ClientDisconnected
except Exception:  # pragma: no cover - only reached if uvicorn moves the symbol
    class ClientDisconnected(OSError):
        """Placeholder so the ``except`` arm below still names a real type."""

try:
    # The markdown the agent writes, parsed to a **token tree** and never to
    # HTML. `renderer=None` is what selects that mode, and it is the whole
    # reason this page can keep its no-HTML-parser property: the client walks
    # the tree with createElement + textContent and never parses markup.
    #
    # It also means none of mistune's sanitizing applies here, and that is
    # measured rather than assumed against the installed 3.3.4. `escape=` is
    # consumed only by `HTMLRenderer(escape=escape)` (`mistune/__init__.py`),
    # and `safe_url()` lives in `renderers/html.py` — neither is on this path.
    # With `renderer=None`, `<script>alert(1)</script>` comes back as a
    # `block_html` token holding that exact string and
    # `[x](javascript:alert(1))` as a `link` whose `attrs.url` is the
    # `javascript:` URL. **The client's allowlist is the entire security
    # boundary**; this side does no sanitizing at all and must not be read as
    # if it did.
    #
    # Guarded like `web.py`'s import of this module, and for the same reason: a
    # dependency declared in pyproject.toml but absent from the running
    # interpreter has broken this project before. Absent, `/acp` degrades to
    # exactly the plain-text transcript it had before this existed.
    #
    # `table` is a plugin because pipe tables are GFM and not CommonMark, and
    # mistune ships only the latter by default. Without it the parser never
    # sees a table at all: the rows come back as paragraphs of literal pipes,
    # which the client's fall-through arm then flattens onto one line. `web.py`
    # carries the same plugin for the dashboard tooltip and for the same
    # reason. It adds token types and nothing else — no renderer, and so no
    # HTML — so the no-HTML-parser property above is untouched by it.
    import mistune
    _markdown = mistune.create_markdown(renderer=None, plugins=["table"])
except Exception as _e:  # pragma: no cover - only when the dep is missing
    mistune = None
    _markdown = None
    log.warning("mistune unavailable — /acp renders the agent's markdown as "
                "plain text: %s", _e)

CLIENT_TYPES = frozenset({"subscribe", "new", "load", "prompt", "cancel", "close", "steer"})
SERVER_TYPES = frozenset({
    "session", "chunk", "rendered", "tool_call", "tool_update", "meta", "error",
    "agent_died", "session_closed", "history_truncated", "history", "thought",
    "subagents", "steer_ack",
})

# The largest legitimate client frame is a `prompt` payload: prose a human
# typed or pasted into the page. 256 KiB is far more of that than anyone
# sends. Note what this cap is and is not: uvicorn has already decoded the
# frame by the time we see it, so this rejects oversized frames at the
# protocol layer rather than at the transport. The transport ceiling is
# `__main__.WS_MAX_SIZE_BYTES`, which replaces uvicorn's 16 MiB `ws_max_size`
# default with a megabyte — above this cap, so the typed refusal below stays
# the one a client actually meets.
MAX_MESSAGE_BYTES = 256 * 1024

# Image attachments on one prompt: how many, and what they may weigh between
# them once decoded.
#
# Both are **floors of defence, not the budget the page aims at**. `/ws/acp` is
# reachable by a non-browser client holding the device cookie and the token, so
# an `images` array is untrusted input rather than "whatever the page sent" —
# every limit the browser rations itself against is re-checked here.
#
# The byte figure sits deliberately *below* what MAX_MESSAGE_BYTES physically
# admits. Measured 2026-08-04: at most 196,485 raw image bytes survive base64
# and JSON inside a 256 KiB `prompt` frame carrying a short prompt. Capping at
# 176 KiB leaves ~21 KB of that frame for prose and — the reason it matters —
# keeps this typed refusal *in front of* the frame check in `serve_socket`.
# That one does not answer: it closes the socket with 1009, and
# `restorePendingPrompt` on the page is not reachable from a transport close,
# so a user who pasted one screenshot too many would lose the prompt and the
# connection with nothing anywhere saying the image was why.
#
# Four rather than one so a pathological array cannot be decoded element by
# element before the byte bound notices. Four *useful* images do not fit under
# the byte cap and are not meant to: the bytes bind first, the count only
# bounds the work done before they can.
MAX_PROMPT_IMAGES = 4
MAX_PROMPT_IMAGE_BYTES = 176 * 1024

# The image types an attachment may declare. All three were driven against
# kiro-cli 2.16.0 on 2026-08-04 and answered `stopReason: end_turn` with the
# model demonstrably reading the picture.
#
# Two measured failure modes are why `_validate_images` exists at all rather
# than forwarding what arrives:
#
# 1. A declared type that disagrees with the bytes is **fatal and unreadable**.
#    PNG bytes labelled `image/jpeg` came back `-32603 Internal error` quoting
#    Bedrock, with nothing in the message naming an image. Checking the
#    signature here turns that into a refusal somebody can act on.
# 2. A `data` field that is not valid base64 is **not an error at all**. The
#    agent answered the prompt's text with `stopReason: end_turn` exactly as if
#    no image had been attached — a confident answer about a picture nobody
#    saw. Nothing downstream catches it, so the decode below is `validate=True`
#    rather than Python's default, which silently discards stray characters and
#    would reproduce the same silence one layer earlier.
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

# A single-user UI in which **one socket drives many sessions**. The session
# rail lists every session on the machine and switches between them in place,
# so one tab is the normal case however many sessions are live. The reading
# this comment used to give — "one tab in practice, two while comparing" — was
# written when MAX_SESSIONS was 3 and a session effectively meant a tab; it
# survived the rail unchanged and described a world that no longer exists.
#
# Eight is kept, and deliberately not re-derived from MAX_SESSIONS. What this
# number has to cover is a socket per *viewer*, not per session: the tab in
# front of you, a second one while comparing two conversations, a phone on the
# remote surface, and the sockets that linger for a moment either side of a
# reload. That is a handful, and eight sits comfortably above it while still
# bounding what a page can pin — one send queue per socket, and one fan-out
# target on every agent event of the session it is attached to.
#
# Tying it to MAX_SESSIONS would re-assert the one-tab-per-session model the
# rail removed, and would make a change to the session cap silently move a
# socket cap that answers a different question. The two are independent.
MAX_CONNECTIONS = 8

# Per-socket outbound queue, bounded on both count and bytes so that one
# browser tab that has stopped reading drops its own socket instead of growing
# the server's memory while chunks stream at it.
#
# The count alone is not a memory bound, for the same reason it is not one for
# `_History`: nothing caps a queued frame's size below MAX_AGENT_LINE_BYTES, so
# 256 frames is 256 MiB on one socket and 2 GiB across MAX_CONNECTIONS —
# against 256 KiB in the opposite direction. 8 MiB is comfortably above the
# largest single frame the server builds (a `history` replay, capped at
# HISTORY_MAX_BYTES) so the bound cannot kill the socket a `subscribe` exists
# to serve, and 64 MiB across every socket is a bound rather than a hope.
SEND_QUEUE_MAXSIZE = 256
SEND_QUEUE_MAX_BYTES = 8 * 1024 * 1024

# The agent→client direction's size cap, and the block size the reader works
# in. Until Phase 4 nothing streamed, so `for line in proc.stdout` had no
# ceiling on a single line while the client→server path enforced
# MAX_MESSAGE_BYTES; tool output under `-a` is what makes that a live path.
#
# 1 MiB rather than the client's 256 KiB because the two directions carry
# different things: the largest legitimate client frame is prose a human typed,
# while a `tool_call_update` legitimately carries whatever a command printed —
# a measured one already ran ~1 KB, and a file read is unbounded by nature. A
# megabyte is far above any observed line and still small enough that the
# reader thread's buffer cannot grow into a leak. `readline()` would be no
# better than the old loop: both accumulate until a newline arrives, whatever
# that costs. Reading fixed blocks and splitting them is the only shape that
# can decide to *stop* accumulating, so an over-long line is discarded up to
# the next newline instead of buffered.
#
# The block size is what streaming granularity costs: `TextIOWrapper.read(n)`
# blocks until it has n characters, so the reader works on the underlying
# buffered binary stream with `read1`, which returns whatever one OS read
# yields. 64 KiB is a comfortable multiple of the ~4-64 KB Windows pipe buffer.
MAX_AGENT_LINE_BYTES = 1024 * 1024
READ_BLOCK_BYTES = 64 * 1024

# The per-session replay buffer: how many events it holds, and what they may
# weigh. The count is the bound the reload path cares about. The byte budget
# exists because the count alone is not a memory bound — at MAX_AGENT_LINE_BYTES
# a line, HISTORY_MAXLEN events could hold hundreds of megabytes for one
# session, which is the same unbounded-buffer shape the reader cap closes.
# Whichever binds first sets `truncated`, so a replay that has degraded to a
# suffix says so either way.
HISTORY_MAXLEN = 2000
HISTORY_MAX_BYTES = 2 * 1024 * 1024

# The shortest gap between two replays on one socket. A `subscribe` is ~60
# bytes and answering one rebuilds the whole buffer — up to HISTORY_MAX_BYTES
# of it — into a single `history` frame, on the event loop that also serves the
# dashboard, with `_dispatch` willing to queue SEND_QUEUE_MAXSIZE of them. The
# page sends exactly one per socket, from `onopen`, so nothing legitimate is
# ever within a second of its own predecessor; a reconnect loop is a new socket
# each time and pays the handshake rather than this.
SUBSCRIBE_MIN_INTERVAL_SECONDS = 1.0

# The shortest gap between two ``load`` frames on one socket. A `load` is the
# most expensive thing a client can ask for with ~60 bytes: two blocking calls
# on the shared thread pool — a lock read and a cwd resolve, the latter against
# a path the trust-all-tools agent wrote, where a UNC path to an unreachable
# host measured 42 s — plus a registry claim and an agent round-trip. The page
# sends at most one per session per socket (`loadTried`), and a reconnect is a
# new socket with its own floor, so nothing legitimate is ever within a second
# of its own predecessor.
LOAD_MIN_INTERVAL_SECONDS = 1.0

# How much of a tool call's input the page is shown. Under `-a` there is no
# permission gate, so what the operator can read here is the only account of
# what ran — a command clipped to a shell's first token would be worse than
# not rendering it. 4000 characters is far above any command yet observed and
# still bounded, because this string is agent-authored, is recorded in the
# replay buffer, and is rendered into the DOM. A clipped command says so on
# the page rather than looking complete.
MAX_TOOL_INPUT_CHARS = 4000

# The most agent prose one bubble may accumulate before this module stops
# offering to render it. Past the cap the bubble stays exactly the plain text
# it already is on the page — the degradation is invisible rather than broken.
#
# Two costs sit behind it. `_markdown` runs on the event loop, so parsing is
# time uvicorn is not serving anything else with; and the token tree is emitted
# as a frame that goes into the replay buffer beside the chunks it summarises.
# A measured 2,251-character answer produced a 2,548-byte token frame against
# the 23,018 bytes of the 185 chunk frames carrying it, so the tree is roughly
# a tenth of what it describes and 128 KiB of prose is a bounded few hundred
# KiB of tokens — while an answer that large is already far outside anything
# observed (the largest measured turn is under 3 KB).
MAX_BUBBLE_CHARS = 128 * 1024

# The kiro-private notification carrying a turn's token accounting, and the
# field in it that answers "how full is the context window".
#
# Matched on the **method name**, which is the opposite of the tool-call path
# directly below it in `_on_notification`. Both rules are measured rather than
# chosen: `session/update` is one method carrying at least six different update
# kinds, so only `update.sessionUpdate` separates them — while every
# `_kiro.dev/*` notification arrives with no `sessionUpdate` field at all, so
# keying those off the update would drop every one of them. First measured on
# 2.14.2; **re-measured on 2.16.0, 2026-08-03** — 5 `_kiro.dev/*` frames across
# one turn, none carrying `sessionUpdate`.
METADATA_METHOD = "_kiro.dev/metadata"
CONTEXT_PERCENT_KEY = "contextUsagePercentage"

# What releases one session on the agent. **Not** ``session/close``, which the
# plan's own wording implies and which the ACP spec does not define: kiro-cli
# 2.14.2 answers it ``-32601 Method not found``. This kiro-private extension
# method is the one that works, and it is the whole basis of the per-session
# memory budget §4 and §6 accept — re-measured in Phase 2 on kiro-cli 2.16.0,
# one close released 3 processes and 169.7 MB of MCP servers and removed the
# session's ``.lock`` (an earlier run read 3 processes and 172.6 MB).
# ``plans/ROADMAP.md`` holds the cost model those figures feed and is where a
# re-measurement lands; a copy here drifts from it within the day.
#
# Because it is an extension rather than protocol, a kiro-cli that drops it
# takes the memory lever with it. That surfaces as a typed ``agent_error``
# naming ``-32601`` on the page rather than as a close that quietly does
# nothing: ``close_session`` drops no local state until the agent has answered.
CLOSE_METHOD = "_kiro.dev/session/terminate"

# The kiro-private notification carrying the current sub-agent crew for
# whichever session is running a fan-out — see ``_Supervisor._on_subagent_list``.
# Like ``METADATA_METHOD``, matched on the method name rather than on
# ``update.sessionUpdate``: it carries no ``update`` key at all, only
# ``subagents`` (and, per its own docstring, no ``sessionId`` either) plus a
# sibling ``pendingStages`` list this file does not read — measured
# 2026-08-11, always ``[]`` across two captured fan-outs and left unhandled
# rather than guessed at.
SUBAGENT_LIST_METHOD = "_kiro.dev/subagent/list_update"

# The kiro-private notification carrying a *pre-announcement* tool-call chunk
# — a method distinct from plain ``session/update`` (see ``_stamp_activity``'s
# P0-3 note). **Not sub-agent-exclusive**: measured 2026-08-11 against a real
# kiro-cli 2.16.2 subprocess, spawned and driven directly (outside
# PowerAtlas) through a 3-stage ``subagent`` fan-out — it also fires for the
# *parent* session's own tool calls, including one that ran before any
# sub-agent existed. What makes ``_on_notification`` handle that correctly is
# the ``subagent_sessions`` membership gate at the ``tool_call_chunk`` branch,
# not this method name; the constant itself is never compared against
# ``method`` anywhere — dispatch there is entirely ``update.sessionUpdate``-
# gated.
#
# Every frame captured on this method carried ``sessionUpdate: tool_call_chunk``
# with ``toolCallId``/``title`` and also ``kind`` (a third field beyond the two
# named below — harmless, since ``_tool_payload`` already defaults absent
# fields). It never once carried ``agent_message_chunk``: every
# ``agent_message_chunk`` observed, for the parent and for every child, arrived
# on plain ``session/update`` instead, keyed by whichever ``sessionId`` was
# speaking.
SUBAGENT_ACTIVITY_METHOD = "_kiro.dev/session/update"

# ``status.type`` values a sub-agent list entry may carry that mean "still
# running" — everything else is treated as terminal. ``""`` is in here rather
# than treated as terminal: an entry can be seen before kiro-cli has assigned
# it a status at all.
#
# **Measured 2026-08-11 against kiro-cli 2.16.2** — a real subprocess, spawned
# and driven directly outside PowerAtlas — across two runs: a 3-stage fan-out
# where every stage succeeded, and a 2-stage fan-out where one stage's own
# shell command was made to fail deliberately. Only two ``status.type`` values
# were ever observed: ``working`` while active, and **``terminated``** for
# every terminal entry — including the stage whose command failed.
# ``done``/``completed``/``failed``/``error`` were never seen even once. The
# exclusion design below still classifies ``terminated`` correctly (it is
# simply absent from this active set), but see the ``error =`` line in
# ``_on_subagent_list`` for what "no observed ``failed``/``error`` value"
# means for surfacing *why* a stage failed. This was previously corroborated
# only against kirodotdev/kirocrew's ACP client (``acp/client.py``,
# ``acp/_dispatch.py``, ``dashboard/chat_runner.py:_native_subagent_sync``) —
# real but second-hand evidence; the terminal vocabulary above is now this
# app's own.
_SUBAGENT_ACTIVE_STATUSES = frozenset(
    {"working", "running", "pending", "queued", "in_progress", ""})

# Field names a sub-agent list entry may use for its session id, its display
# role, and the task it was given — kirocrew reads ``role`` with an
# ``agentName`` fallback, and ``initialQuery`` with a ``sessionName`` fallback,
# for exactly the reason named there: kiro-cli has sent both across builds.
#
# Measured 2026-08-11 against kiro-cli 2.16.2: every entry in both captured
# runs carried all four keys at once. ``role`` and ``agentName`` were always
# identical — the underlying agent name (e.g. ``"kiro_default"``), not a
# per-stage label — so the fallback never actually triggers on this build.
# ``initialQuery`` was always non-empty too, so it always won over the short
# ``sessionName`` label (e.g. ``"count_src"``) a UI would probably rather show.
# Left in kirocrew's order rather than reversed — that would be an unverified
# UX call, not a wire-shape correction — but see ``MAX_SUBAGENT_TASK_CHARS``
# just below for why the untruncated ``initialQuery`` winning matters
# regardless of which key is shown.
_SUBAGENT_ROLE_KEYS = ("role", "agentName")
_SUBAGENT_TASK_KEYS = ("initialQuery", "sessionName")

# How much of a crew entry's ``task`` (see ``_SUBAGENT_TASK_KEYS`` above) rides
# the ``subagents`` wire frame. Unlike every other agent-authored string this
# file renders — ``MAX_TOOL_INPUT_CHARS``, ``MAX_ERROR_DETAIL_CHARS``,
# ``MAX_BUBBLE_CHARS`` — ``task`` had no bound at all until this one: measured
# 2026-08-11, ``initialQuery`` (the fallback that always wins, per the note
# above) is the sub-agent's full task prompt, not a short label, and
# ``_subagents_payload`` forwarded it verbatim on every broadcast. Sized the
# same as ``MAX_TOOL_INPUT_CHARS``: far above the few hundred characters
# measured on trivial tasks, generous enough for a real one, and still
# bounded.
MAX_SUBAGENT_TASK_CHARS = 4000

# What `_handle_prompt`/`_handle_close`/`_handle_cancel` answer a frame
# targeting a sub-agent's own session id with. One string rather than one
# per call site, so the three refusals cannot read differently for the same
# reason.
_READ_ONLY_SUBAGENT_MESSAGE = (
    "This is a sub-agent's own conversation — it can only be watched, not "
    "prompted. Switch back to the main session to send it a message.")

# How many sub-agents (across every crew) one parent session may accumulate
# before the oldest **finished** ones are evicted to make room. Exempt from
# MAX_SESSIONS by design (Q&A, 2026-08-11) — a crew is bounded by how many
# stages one fan-out actually runs, not by the cap that gates real sessions —
# but "exempt" must not mean "unbounded": a session that runs many fan-outs
# over a long life must not grow this without limit. A currently-running entry
# is never evicted; eviction only ever removes a `done` one, so a bound this
# high is never reached by one fan-out in practice (ten stages is the largest
# measured, `memory/MEMORY.md`) and only bites a session with an unusually long
# history of them.
MAX_SUBAGENTS_PER_SESSION = 64

# How long a server-initiated close waits for the outbound queue to reach the
# wire before giving up on it. Bounded because the peer this is waiting on may
# already be dead, and shutdown must not hang on it; two seconds is far longer
# than a loopback socket needs and short enough that a wedged tab does not
# stall the close path.
DRAIN_TIMEOUT_SECONDS = 2.0

# The agent, and the flags it is spawned with. `-a` is trust-all-tools: `/acp`
# replaces the kiro-cli TUI, and the TUI is where the permission gate lives, so
# this removes the only gate that exists rather than matching a default. A
# knowing prototype-scoped choice (plan §3), to be re-decided before a rebuild.
KIRO_BINARY = "kiro-cli"
ACP_ARGS = ("acp", "-a")

# ACP protocol version. Re-confirmed against kiro-cli 2.16.0 on 2026-08-01
# (it was first measured on 2.14.1): `initialize` still answers
# `{"protocolVersion": 1, ...}`, and `agentInfo.version` is where the build
# number comes from.
PROTOCOL_VERSION = 1

# How many sessions one agent may hold at once.
#
# **A module-level rebindable name on purpose.** `apply_config` rewrites it at
# startup from `Config.acp_max_sessions`; `at_capacity()` and
# `_session_limit_message()` read it at call time, so the rebind reaches both
# without either of them touching the disk. Moving it onto `_Supervisor` would
# read better and break every test site that patches `acp.MAX_SESSIONS`.
#
# The default moved 3 -> 8 once the idle sweeper existed to reclaim what an
# unattended session holds. Re-measured 2026-08-01 against kiro-cli 2.16.0
# rather than carried forward: the earlier ``~254 MB`` per session was a
# two-session reading of a build two versions back. See
# ``plans/260731_ACP_REMOTE_CLIENT_PRODUCTIZATION.md`` for the eight-session
# measurement this default rests on; ``plans/ROADMAP.md`` holds the cost model.
MAX_SESSIONS = 8

# How long a session may sit unused before the sweeper releases it, and how
# often the sweeper looks. "Unused" is `last_used` — prompts and subscriber
# attach/detach — deliberately *not* `last_activity`, which any agent
# notification advances. See `_stamp_activity` for why the two cannot share a
# field. Both are rebound by `apply_config`/rebindable by tests.
ACP_IDLE_TTL_SECONDS = 1800.0
SWEEP_INTERVAL_SECONDS = 60.0

# Wall-clock ceilings on JSON-RPC requests. Every pending future carries one:
# an agent that has stopped answering is otherwise indistinguishable from one
# that is merely slow, and `session/new` used to be genuinely slow — ~5.4 s for
# the first session of a process and ~2.5 s for each one after, on 2.14.x. On
# 2.16.0, measured across eight consecutive sessions on one agent, it is 1.1 s
# for the first and ~0.5 s thereafter. The ceiling stays where it is: it is
# sized for the pathological case, and a build that regresses this is exactly
# what it is for.
INITIALIZE_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 90.0

# A prompt is the one request whose duration is the model's, not the channel's,
# and it is the one request bounded by **silence** rather than by wall clock.
#
# The wall-clock ceiling this replaces (600 s) answered the wrong question. A
# turn running tools under `-a` is legitimately minutes to hours, so the bound
# abandoned working turns; and because `_request` popped the future and raised
# without sending any cancellation, the agent carried on working while
# `_handle_prompt`'s finally cleared `inflight` — the session read idle while
# actively running. What the ceiling exists for is detecting an agent that has
# *stopped*, and silence is what that actually looks like.
#
# `PROMPT_TICK_SECONDS` is how often the wait wakes to re-read the deadline, so
# worst-case cancel latency is `silence + tick`. `PROMPT_SILENCE_SECONDS` is
# the window with no notification of any kind that condemns a turn.
PROMPT_TICK_SECONDS = 15.0
PROMPT_SILENCE_SECONDS = 900.0

# The safety property the wall clock used to provide, kept deliberately.
#
# Without it a turn emitting one chunk just under the silence window runs
# forever — and `inflight` makes that session simultaneously un-closable
# (`_handle_close` refuses `turn_in_progress`) and un-sweepable (sweep
# condition 3), so its memory and its processes are unreclaimable for the
# app's lifetime with no operator path short of a restart. "Long turns" means
# generous, not unbounded.
PROMPT_ABSOLUTE_MAX_SECONDS = 14400.0

# How long the ceiling waits, after writing `session/cancel`, for the
# outstanding `session/prompt` to answer before giving up on it.
#
# Sized against a measurement rather than a worst case. On kiro-cli 2.16.0 the
# cancel is honoured at the protocol layer and the prompt answers
# `{"stopReason":"cancelled"}` **9 ms** later, as a *matched* response on the
# pending future — so `_on_response`'s "late or unmatched" drop is not on this
# route at all and the window `inflight` guards is ~9 ms, not open-ended. Three
# seconds is ~330x the measured latency: ample margin for a loaded machine or a
# slower future build, and short enough that a wedged agent does not hold the
# turn boundary the page reads. It was 30 s when the hazard was assumed rather
# than measured.
#
# What this grace does **not** buy is the tool's OS children. Measured
# 2026-08-01: neither `session/cancel` nor `_kiro.dev/session/terminate` kills
# them — a `pwsh.exe`/`PING.EXE` pair survived both, and only the Windows job
# object closing at process exit reaped them. See `_await_inactivity`.
CANCEL_GRACE_SECONDS = 3.0

# What the prompt path passes in `timeout` to ask for the inactivity ceiling
# instead of a wall-clock one.
#
# A sentinel in the existing slot rather than a new parameter, and the reason is
# the test suite rather than taste: ~19 fixed-signature `_request` stubs would
# raise `TypeError` the moment the signature grew a parameter, and they are
# replacing the very function whose new behaviour is under test. `_request`
# branches on it with `is` *before* its try block, because the wall-clock arm
# formats `{timeout:.0f}` into its message and a sentinel would blow up there.
_INACTIVITY = object()

# How long the tree-kill fast path waits for the tree to actually go, so that
# teardown has a post-condition rather than only an intent.
#
# It is *not* bounded inside `__main__.py`'s 5 s server-thread join, and the
# arithmetic says so: uvicorn's 0.1 s shutdown poll, plus the fixed 0.1 s sleep
# in `Server.shutdown`, plus up to DRAIN_TIMEOUT_SECONDS of socket drain, plus
# this, comes to ~5.2 s worst case. The sweeper adds nothing measurable to it:
# `lifespan` cancels it and gathers it *before* calling `shutdown()`, and a
# cancelled task raises out of whichever `await` it was parked on immediately —
# including one inside `close_session`. That last part holds with or without a
# shield, so "a shielded close would hold teardown" is *not* why the sweeper
# must not `asyncio.shield` its close: cancelling the awaiter of
# `await asyncio.shield(x)` cancels the shield's own outer future and raises
# `CancelledError` there at once (measured at ~0.1 ms), leaving only the inner
# coroutine running. The inner coroutine is the reason. Shielded, it survives
# as an orphan task with nothing left to await it, running a terminate
# round-trip against an agent `shutdown()` is killing in the same breath, on a
# loop that is about to close. Typical is ~0.3-0.5 s, because a killed
# tree is gone long before the ceiling. The overrun is benign for exactly one
# reason: `os._exit(0)` closes the job handle on the way out and the OS kills
# whatever the fast path had not finished killing — which is why a spawn that
# cannot obtain a job object refuses to spawn at all rather than degrading.
KILL_WAIT_SECONDS = 3.0

# kiro-cli's own session store. `load` names a session id that arrived from the
# browser and this is the directory it is joined into, so the id is validated
# against `_SESSION_ID_RE` before it ever forms a path.
KIRO_SESSION_DIR = Path.home() / ".kiro" / "sessions" / "cli"

# The cap `launcher.py` applies alongside `_SESSION_ID_RE`. Both halves are
# needed and neither implies the other: the pattern refuses separators and dots
# (so no traversal), the cap refuses a path component no filesystem accepts.
MAX_SESSION_ID_CHARS = 128

# How much of a JSON-RPC error's `data` field is carried into the exception
# text. `data` is agent-controlled and ends up in a message the user reads, so
# it is bounded rather than trusted; 512 is generous against the only shape
# measured (72 characters, `Failed to start session: Session is active in
# another process (PID n)`) and small enough that a hostile or looping agent
# cannot turn one refusal into a large frame.
MAX_ERROR_DETAIL_CHARS = 512

# Called with `(frozenset of live session ids, agent pid or None)` whenever the
# set of sessions this supervisor holds changes. `None` until something wires
# it, which is the state for every test that does not opt in.
#
# **A hook and not an import, for a reason this module states about itself.**
# The header above declares an isolation boundary — exactly two names from the
# rest of the package — and a plan exit criterion greps this file for module
# names to keep it honest. The consumer is `presence`, which needs to know
# which locks written by *our* agent are orphans (D32). Importing it here would
# break that boundary; importing `acp` from `presence` is the direction D9
# rejected, because `presence` runs on worker threads and `sessions` is
# loop-owned. So neither module knows the other exists and `web.py` — which
# already imports both — connects them.
#
# Only ever called from the event loop, so the callee can rebind a global
# without a lock.
#
# Set it through `set_sessions_changed_hook`, not by assignment: the initial
# publish matters and is easy to forget.
sessions_changed_hook = None


def set_sessions_changed_hook(hook) -> None:
    """Install the hook and publish the current set once, immediately.

    The immediate publish is the point of having a function at all. Without it
    the consumer holds its "nothing published" default until the first session
    is created or closed — which on a fresh start is indefinitely, since a
    PowerAtlas nobody has opened `/acp` on never mutates `sessions`. That
    default has to mean "no answer" rather than "no sessions", so the consumer
    stays conservative, and the two states are only distinguishable if someone
    says so.

    Loop-thread only, like the hook itself.
    """
    global sessions_changed_hook
    sessions_changed_hook = hook
    _supervisor._publish_live()

# How much of a session's `<sid>.json` is read to recover the directory it was
# created against. `cwd` is that file's second key, while the rest of it is the
# whole conversation state and runs to megabytes — `presence.py` parses it
# whole, behind a cache this module deliberately cannot reach.
SESSION_JSON_PREFIX_BYTES = 16 * 1024

# How much of a lock file is read before giving up on it. A lock is a JSON
# object holding a pid and a timestamp — ~100 bytes in this machine's store —
# and the directory it sits in is written by an agent running trust-all-tools,
# so its size is not ours to assume. A whole-file read has no ceiling and
# ``MemoryError`` is not in any caught set on this path.
LOCK_MAX_BYTES = 4 * 1024

# How far after its own timestamp a lock file's holder may have started before
# the lock is judged stale rather than live. A session writes its lock *after*
# its process starts, so the honest relation is `create_time <= started_at`;
# this only tolerates two clocks disagreeing.
#
# It is not decoration. Measured on this machine's store: 803 lock files, 22 of
# which name a pid that still exists — and all 22 are recycled pids belonging
# to svchost, firefox, RuntimeBroker and friends, every one created weeks after
# the lock was written. A pre-flight resting on `pid_exists` alone would have
# refused 22 perfectly loadable sessions and been wrong every time it fired.
LOCK_START_SKEW_SECONDS = 5.0

# What the agent says when the session is open somewhere else. Matched on the
# message because the code it arrives with is -32603, "internal error", which
# says no more than "something went wrong" — and this particular something is
# the only one the operator can act on.
_IN_USE_MARKER = "active in another process"

# The JSON-RPC code kiro-cli refuses a busy `session/load` with — 2.14.2 and
# **re-measured on 2.16.0, 2026-08-03**. It is the generic "internal error"
# code, so on its own it names nothing — but it is
# the only refusal this method has been observed answering with, and forwarding
# it verbatim reaches the page as "Internal error", which says neither what
# happened nor what to do. Matched against the string `_on_response` builds,
# which is this module's own format rather than the agent's.
_OPAQUE_REFUSAL_MARKER = "(code -32603)"

# The lock's own timestamp, to second resolution — a prefix match feeding a
# fixed `strptime` rather than `datetime.fromisoformat`.
#
# Not because fromisoformat cannot read the value. It can: kiro-cli writes RFC
# 3339 with *nanoseconds* ("2026-06-01T21:19:24.509198600Z") and on this
# project's interpreter (3.13.13, checked directly) fromisoformat parses that
# string, truncating the fraction to microseconds. The three-or-six-digit
# restriction was lifted in an earlier release than the one that runs here.
#
# The reason is that this file belongs to another program. It is written by
# kiro-cli into kiro-cli's own store, its format is not ours to depend on, and
# a whole-string parser answers any change in the tail — a different offset
# spelling, a suffix, a trailing space — with `ValueError`, which becomes
# `None`, which `_lock_holder` reads as "no identifiable holder" and lets a
# genuinely held session through the pre-flight. Matching only as far as the
# seconds field makes everything after it irrelevant. Seconds are ample against
# LOCK_START_SKEW_SECONDS, so the precision the prefix discards costs nothing.
_LOCK_TIME_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")

# `cwd` as it appears in a session's stored metadata, with JSON's own escaping
# left intact so `json.loads` can undo it — Windows paths are full of `\\`.
_STORED_CWD_RE = re.compile(r'"cwd"\s*:\s*("(?:[^"\\]|\\.)*")')

# No console window per spawn. Absent this, every session flashes one.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Wrapper extensions that cannot be spawned with pipes: a `.cmd`/`.bat` shim
# needs `shell=True`, which is incompatible with holding clean stdio. Verified
# clear on this machine (`kiro-cli.EXE`); the check guards other installs.
_WRAPPER_SUFFIXES = frozenset({".cmd", ".bat"})


class AcpError(Exception):
    """An agent-side failure with a stable code the page can branch on."""

    code = "acp_error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class AgentUnavailable(AcpError):
    code = "agent_unavailable"


class AgentSpawnFailed(AcpError):
    code = "agent_spawn_failed"


class AgentDied(AcpError):
    code = "agent_died"


class AgentTimeout(AcpError):
    code = "agent_timeout"


class AgentRejected(AcpError):
    """The agent answered, with a JSON-RPC error."""

    code = "agent_error"


class SessionLimit(AcpError):
    code = "too_many_sessions"


class BadCwd(AcpError):
    code = "bad_cwd"


def envelope(type_: str, payload: dict | None = None,
             session_id: str | None = None) -> dict:
    """Build a wire frame. The only place the envelope shape is written.

    Raises rather than logs on an unknown type, because every call site passes
    a literal: a type this refuses is a typo, not input. Left unchecked it
    reaches the page as a frame no branch matches, where it renders as a line
    of raw JSON in the transport log and is easy to read as agent noise —
    ``SERVER_TYPES`` existed for three phases without ever being consulted.
    """
    if type_ not in SERVER_TYPES:
        raise ValueError(f"'{type_}' is not a declared server frame type")
    return {"type": type_, "sessionId": session_id, "payload": payload or {}}


def error_frame(code: str, message: str, session_id: str | None = None) -> dict:
    """Build a typed ``error`` frame.

    ``code`` is for the client to branch on and stays stable; ``message`` is
    for a human reading the page's log and may be reworded freely.
    """
    return envelope("error", {"code": code, "message": message}, session_id)


def _content_text(content) -> str:
    """Pull the text out of an ACP content block, or a list of them.

    ``agent_message_chunk`` carries a single ``{"type": "text", "text": …}``
    object, not the list the spec's content blocks suggest elsewhere. First
    measured on kiro-cli 2.14.2; **re-measured on 2.16.0, 2026-08-03** — every
    chunk in a driven turn carried an object. Both are accepted because only
    one of them is
    what this build happens to send.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(content, list):
        return "".join(_content_text(item) for item in content)
    return ""


def _content_image_count(content) -> int:
    """How many image blocks a content payload carries.

    The counterpart to ``_content_text``, and it exists for the case that
    function cannot express: an image block has no ``text`` key, so a turn made
    only of images extracts as ``""`` and reads as nothing happening.
    """
    if isinstance(content, dict):
        return 1 if content.get("type") == "image" else 0
    if isinstance(content, list):
        return sum(_content_image_count(item) for item in content)
    return 0


def _with_image_markers(text: str, count: int) -> str:
    """Name a prompt's attachments in the prose that stands for it.

    ``[Image 1] [Image 2]`` is the convention kiro-cli and Claude Code both
    print, for the reason a terminal has: it cannot draw pixels. This page can,
    and still wants the markers, for two reasons a terminal never faces.

    The first is that the marked-up text is what the *transcript* carries. The
    image bytes are sent to the agent and deliberately never enter the `chunk`
    frame — at `_frame_weight`'s reckoning base64 costs full freight, and eight
    image-bearing chunks would evict a whole 2 MiB replay buffer and set
    `truncated` for good. So the marker is what a second tab, and this tab
    after a reload, have instead of the picture.

    The second is that the same string is what goes to the *agent*, as its text
    block. Without it the model has the images positionally and no labels to
    bind them to, so a prompt saying "compare image 1 with image 2" names
    nothing it can see. Numbering them costs a few bytes and makes that
    phrasing work.
    """
    if count <= 0:
        return text
    markers = " ".join(f"[Image {n}]" for n in range(1, count + 1))
    return f"{text}\n\n{markers}" if text.strip() else markers


def _image_bytes_match(mime: str, blob: bytes) -> bool:
    """Whether decoded bytes actually are the type the block declares.

    A closed set rather than a magic-number library: three types are admitted
    (IMAGE_MIME_TYPES) and anything else has already been refused before this
    is reached. WebP is the one that needs two checks — ``RIFF`` opens the
    container and the four bytes naming the payload sit past a length field.
    """
    if mime == "image/png":
        return blob.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return blob.startswith(b"\xff\xd8\xff")
    if mime == "image/webp":
        return blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
    return False


def _validate_images(raw) -> tuple[list[dict], str]:
    """Narrow a client's ``images`` payload into content blocks, or refuse it.

    Returns ``(blocks, "")`` when every attachment is admissible, or
    ``([], reason)`` with prose naming which one failed and why. The index in
    that prose is 1-based to match the ``[Image N]`` markers the user is
    looking at.

    Every check is ordered cheapest-first — shape, then count, then the
    per-item decode — so a hostile array is refused before anything expensive
    runs on it.
    """
    if raw is None:
        return [], ""
    if not isinstance(raw, list):
        return [], "'images' must be a list."
    if len(raw) > MAX_PROMPT_IMAGES:
        return [], (f"At most {MAX_PROMPT_IMAGES} images may be attached to "
                    f"one prompt; this one carried {len(raw)}.")
    blocks: list[dict] = []
    total = 0
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return [], f"Image {index} is not an object."
        mime = item.get("mimeType")
        data = item.get("data")
        if not isinstance(mime, str) or mime not in IMAGE_MIME_TYPES:
            return [], (f"Image {index} declares an unsupported type. "
                        f"Supported: {', '.join(sorted(IMAGE_MIME_TYPES))}.")
        if not isinstance(data, str) or not data:
            return [], f"Image {index} carries no base64 data."
        try:
            blob = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return [], f"Image {index} is not valid base64."
        if not _image_bytes_match(mime, blob):
            return [], (f"Image {index} does not look like {mime} — the bytes "
                        "and the declared type disagree. The agent answers a "
                        "mismatch with an internal error that names no image, "
                        "so it is refused here instead.")
        total += len(blob)
        if total > MAX_PROMPT_IMAGE_BYTES:
            return [], (f"The attached images come to more than "
                        f"{MAX_PROMPT_IMAGE_BYTES // 1024} KiB between them "
                        "once decoded. Send fewer, or smaller ones.")
        # Re-built rather than forwarded, so no other key an untrusted client
        # put on the item travels to the agent.
        blocks.append({"type": "image", "mimeType": mime, "data": data})
    return blocks, ""


def _as_text(value) -> str:
    """A payload field the page will render, or ``""`` if it is not a string.

    Every field of an agent notification is agent-authored and none of it is
    schema-checked on the way in. Narrowing to ``str`` here is what keeps the
    frame's shape stable for `_frame_weight` and for the renderer, which reads
    these as text and nothing else.
    """
    return value if isinstance(value, str) else ""


def _tool_input_text(update: dict) -> str:
    """The command — or the nearest thing to one — a tool call is about to run.

    ACP puts the model's own arguments under ``rawInput`` with no schema, so
    the shape differs per tool. The named keys are the ones that carry the
    thing an operator needs to read; anything else is serialized whole rather
    than reported as absent, because "a tool ran and we cannot say what it did"
    is the state this rendering exists to remove.
    """
    raw = update.get("rawInput")
    if raw is None:
        raw = update.get("input")
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""
    for key in ("command", "path", "file_path", "query", "content"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    try:
        return json.dumps(raw, sort_keys=True)
    except (TypeError, ValueError):
        return ""


def _tool_payload(update: dict) -> dict:
    """What a tool-call notification carries to the page.

    Deliberately not the tool's *output*: a file read or a build log is
    unbounded by nature and every byte of it would be recorded in the replay
    buffer, evicting the conversation it is meant to annotate. What ran, under
    what name, and how it ended is the operator's question here.
    """
    payload = {
        "toolCallId": _as_text(update.get("toolCallId")),
        "title": _as_text(update.get("title")),
        "kind": _as_text(update.get("kind")),
        "status": _as_text(update.get("status")),
    }
    command = _tool_input_text(update)
    if len(command) > MAX_TOOL_INPUT_CHARS:
        payload["commandLength"] = len(command)
        payload["commandTruncated"] = True
        command = command[:MAX_TOOL_INPUT_CHARS]
    payload["command"] = command
    return payload


def _context_percent(params: dict) -> float | None:
    """How full the context window is, from a ``_kiro.dev/metadata`` payload.

    ``None`` for anything that is not a real number in 0-100, because this
    value is rendered as a bar width: a negative or absurd one would silently
    become a bar that says something false rather than a bar that is absent.
    ``bool`` is excluded explicitly — it is an ``int`` in Python, and ``True``
    would round-trip as 1%.

    The agent sends a float with four decimals (``5.8399`` measured), which is
    four digits more precision than a percentage display can use; rounding here
    rather than in the page keeps the wire value and the rendered value the
    same number.
    """
    value = params.get(CONTEXT_PERCENT_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= value <= 100:
        return None
    return round(float(value), 1)


def _string_bytes(value) -> int:
    """UTF-8 bytes held by every string reachable from ``value``.

    Recursive because payloads nest: a ``tool_call`` carries its command under
    a key, and a ``history`` frame carries every buffered event under
    ``events``. A top-level-only sum priced both at the envelope allowance
    however large they really were.

    ``errors="replace"`` because a lone surrogate is representable in JSON and
    not in UTF-8 — measuring must not be the thing that raises.
    """
    if isinstance(value, str):
        return len(value.encode("utf-8", "replace"))
    if isinstance(value, dict):
        return sum(_string_bytes(k) + _string_bytes(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_string_bytes(item) for item in value)
    return 0


def _frame_weight(frame: dict) -> int:
    """Roughly what holding one frame costs, in **bytes**.

    Bytes and not characters: ``len()`` on a ``str`` counts characters, and a
    buffer of astral-plane text accounted at 2,087,398 was measured holding
    8.24 MB. Summing the payload's strings plus a fixed envelope allowance is
    close enough to drive eviction without paying for a second ``json.dumps``
    per chunk on the streaming path.

    UTF-8 bytes are also what ``_dumps_frame`` now puts on the wire, so this
    prices a frame at what sending it costs rather than at a third of it.
    """
    return 128 + _string_bytes(frame.get("payload") or {})


def _dumps_frame(frame: dict) -> str:
    """Serialize one outbound frame for ``ws.send_text``.

    ``ensure_ascii=False`` because the escaped form is roughly three times the
    size on the wire for non-ASCII agent output — 12 bytes per astral character
    against 4, 6 per other non-ASCII character against 2 or 3.

    The fallback covers a shape ``json.loads`` produces and UTF-8 cannot
    carry: a ``\\udXXX`` escape in the agent's own output becomes a lone
    surrogate, which ``str.encode("utf-8")`` cannot represent. Unguarded, the
    encode the transport performs would raise inside ``_write_loop``, where the
    catch-all retires the socket — a healthy socket lost to one bad character.
    The escaped form is pure ASCII, so it always encodes.
    """
    text = json.dumps(frame, ensure_ascii=False)
    try:
        # The encode the transport is about to perform, done here where it can
        # fall back rather than end the socket.
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(frame)
    return text


class _History:
    """One session's replayable event log, bounded on both count and bytes.

    ``deque(maxlen=…)`` makes the count bound structural: there is no code path
    that can grow it, so it holds whatever a future caller does. The byte
    budget is checked rather than structural, and is the one that actually
    binds when a turn produces few but enormous events.

    Either eviction sets ``truncated`` and it never clears again. That flag is
    the whole reason the buffer is honest: a deque silently discards its oldest
    entry, so without it a replay that has degraded from "the conversation" to
    "the last N events" looks identical to a complete one.
    """

    def __init__(self) -> None:
        self._events: collections.deque = collections.deque(maxlen=HISTORY_MAXLEN)
        self._bytes = 0
        self.truncated = False

    def append(self, frame: dict) -> None:
        weight = _frame_weight(frame)
        if len(self._events) == HISTORY_MAXLEN:
            self._bytes -= self._events[0][0]
            self.truncated = True
        self._events.append((weight, frame))
        self._bytes += weight
        # Never evicts to empty: a single frame heavier than the whole budget
        # is still the most recent thing that happened, and dropping it would
        # leave a reload with nothing at all rather than with a suffix.
        while self._bytes > HISTORY_MAX_BYTES and len(self._events) > 1:
            weight, _ = self._events.popleft()
            self._bytes -= weight
            self.truncated = True

    def events(self) -> list[dict]:
        return [frame for _, frame in self._events]

    def __len__(self) -> int:
        return len(self._events)


# Names one socket across its own log lines. Every socket-scoped line used to
# report only counts, so with more than one open — MAX_CONNECTIONS allows eight,
# and two tabs on one session produce two routinely — an open could not be
# matched to its close and a retire could not be attributed. A process-local
# counter rather than a random id: consecutive ids read as a sequence in the
# log, which is what a single-user local app's log is read as.
_next_conn_id = itertools.count(1)


class _Connection:
    """One browser socket: an outbound queue drained by a single writer task.

    Sends are queued rather than awaited so that Phase 3b's fan-out never
    blocks the agent dispatch path on the slowest attached tab.
    """

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.cid = f"s{next(_next_conn_id)}"
        self.session_id: str | None = None
        # Each entry is ``(weight, frame)``: the weight is computed once, on
        # the way in, so the writer can release it again without a second walk
        # of the frame it has just serialized.
        self._out: asyncio.Queue = asyncio.Queue(maxsize=SEND_QUEUE_MAXSIZE)
        self._queued_bytes = 0
        self._writer: asyncio.Task | None = None
        self._overflowed = False
        # When this socket was last served a replay, or ``None`` for never.
        # Per-socket and never global: a reload is a new socket, and it must not
        # be throttled by the one it replaces.
        self.replayed_at: float | None = None
        # The same, for ``load``. Kept apart from ``replayed_at`` because
        # ``_deliver_load`` deliberately clears that one — the replay a load
        # paid an agent round-trip for is the one thing the replay floor must
        # not discard — and clearing this one with it would remove the floor
        # from the frame that costs the most.
        self.loaded_at: float | None = None

    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop())

    def send(self, frame: dict) -> None:
        """Queue a frame for delivery. Never blocks and never raises."""
        weight = _frame_weight(frame)
        # Never refuses onto an empty queue: a single frame heavier than the
        # whole budget is still the only thing the socket is waiting for, and
        # dropping it would leave the writer parked on `get()` with the
        # overflow flag it will never wake up to read.
        if self._queued_bytes and self._queued_bytes + weight > SEND_QUEUE_MAX_BYTES:
            self._overflowed = True
            return
        try:
            self._out.put_nowait((weight, frame))
        except asyncio.QueueFull:
            # A socket that has not drained SEND_QUEUE_MAXSIZE frames is not
            # coming back. Let the writer close it rather than keep buffering.
            self._overflowed = True
            return
        self._queued_bytes += weight

    async def _write_loop(self) -> None:
        # Names the server-side fault that ended the writer, and is empty for a
        # routine peer-gone exit. It selects the close *code and log level*, not
        # whether a close happens: `_retire` closes on every path it is reached
        # by. Gating the close on this string is what left a live socket behind
        # a dead writer on the routine arm.
        close_reason = ""
        try:
            while True:
                weight, frame = await self._out.get()
                self._queued_bytes -= weight
                try:
                    await self.ws.send_text(_dumps_frame(frame))
                finally:
                    # Paired with every `put_nowait`, and the only thing that
                    # lets `drain()` below know a frame reached the wire.
                    self._out.task_done()
                if self._overflowed:
                    close_reason = "outbound backlog"
                    break
        except asyncio.CancelledError:
            # `stop()` cancelling us on the normal teardown path. The caller
            # owns deregistration there; re-raise so the task ends cancelled.
            raise
        except (WebSocketDisconnect, ClientDisconnected, ConnectionError, RuntimeError):
            # The peer went away mid-send, or the socket was already closed.
            # Routine, and it stays routine once Phase 3b streams chunks.
            log.debug("ACP socket %s writer stopped: peer gone", self.cid)
        except Exception:
            # Anything else is a bug in the frames we build. Do not leave a
            # registered socket with a dead writer behind it: it would hold one
            # of MAX_CONNECTIONS slots and swallow every outbound frame in
            # silence, which is indistinguishable from a hung agent.
            log.exception("ACP socket %s writer failed; retiring the socket",
                          self.cid)
            close_reason = "writer failed"
        await self._retire(close_reason)

    async def _retire(self, close_reason: str) -> None:
        """Drop this connection from the registry and close the socket.

        Only ever called from ``_write_loop``'s own exit paths, which is what
        makes closing here safe: the writer is the single sender on this socket,
        so nothing else can be mid-send. ``serve_socket``'s ``finally`` repeats
        the deregistration; both are idempotent.

        The close is unconditional. Deregistering without it left exactly the
        zombie this path exists to remove — a socket the server still holds
        open with no writer behind it, silently swallowing every frame queued
        at it. A peer that is genuinely gone makes ``close()`` raise instead,
        which costs nothing and is why it is swallowed.

        ``close_reason`` only chooses the code: 1011 names a server-side fault
        the client should surface, while a routine peer-gone exit closes with
        1001 and stays out of the error path.
        """
        _registry.detach(self)
        _registry.connections.discard(self)
        try:
            if close_reason:
                await self.ws.close(code=1011, reason=close_reason)
            else:
                await self.ws.close(code=1001)
        except Exception:
            pass
        log.info("ACP socket %s retired by writer (%s); %d open",
                 self.cid, close_reason or "peer gone",
                 len(_registry.connections))

    async def drain(self, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
        """Wait for queued frames to reach the wire. Bounded; never raises.

        Call this before ``stop()`` on every server-initiated close. Cancelling
        the writer while its queue is non-empty silently discards whatever is
        still in it, and on a server-initiated close those are exactly the
        frames that explain the close — ``agent_died``, ``history_truncated``,
        the tail of a streamed response.

        Racing the queue's ``join()`` against the writer task means a writer
        that has already died ends the wait immediately instead of parking here
        for the whole timeout.
        """
        writer = self._writer
        if writer is None or writer.done():
            return
        # No `empty()` shortcut: the queue reads empty while the writer is
        # awaiting `send_text` on the frame it has already taken, and that frame
        # is as droppable as any still queued. `join()` returns immediately when
        # there is genuinely nothing outstanding, so the race costs nothing.
        joined = asyncio.ensure_future(self._out.join())
        try:
            done, _ = await asyncio.wait(
                {joined, writer}, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED)
        finally:
            joined.cancel()
        if joined not in done and self._out.qsize():
            log.warning("ACP socket %s drain gave up with %d frame(s) queued",
                        self.cid, self._out.qsize())

    async def stop(self) -> None:
        """Retire the writer task. Idempotent.

        Call this before closing the socket from anywhere other than the
        writer itself: two tasks writing to one ASGI send channel is the
        hazard, and cancelling the only other writer removes it. Pair it with
        ``drain()`` first unless the queue is known to be worthless.
        """
        writer, self._writer = self._writer, None
        if writer is None:
            return
        writer.cancel()
        try:
            await writer
        except asyncio.CancelledError:
            pass


class _Registry:
    """Live sockets, which session each is attached to, and which sessions are
    mid-load.

    Phase 3b populates ``subscribers`` from ``new`` and ``subscribe``; in 3a no
    session can exist, which is why ``subscribe`` has nothing to answer with.
    """

    def __init__(self) -> None:
        self.connections: set[_Connection] = set()
        self.subscribers: dict[str, set[_Connection]] = {}
        # Sessions with a `session/load` in flight, each mapped to the sockets
        # that asked for it while it was running. The invariant the key carries:
        # **while a session is in here, no socket may be attached to it.**
        # `load_session` has to register the session before its round-trip —
        # the agent replays the whole conversation as notifications while the
        # request is outstanding and `record` drops frames for a session with
        # no buffer — and `_emit` broadcasts every one of those notifications.
        # So an attached socket would be handed the replay event by event,
        # SEND_QUEUE_MAXSIZE of them before it is retired, which is exactly
        # what the coalesced `history` frame exists to prevent. Waiters are
        # served that one frame each when the load lands; nothing is lost by
        # waiting, because the frames they did not receive are the ones the
        # buffer they are about to be sent was built from.
        self.loading: dict[str, list[_Connection]] = {}

    def attach(self, conn: _Connection, session_id: str) -> None:
        self.detach(conn)
        conn.session_id = session_id
        self.subscribers.setdefault(session_id, set()).add(conn)
        # A tab opening on a session is a person using it.
        _supervisor.touch_used(session_id)

    def detach(self, conn: _Connection) -> None:
        sid = conn.session_id
        conn.session_id = None
        if sid is None:
            return
        peers = self.subscribers.get(sid)
        if peers is None:
            return
        peers.discard(conn)
        if not peers:
            del self.subscribers[sid]
        # And a tab *closing* is what starts the idle clock. Stamping on the
        # way out is what makes the TTL mean "unattended for this long" rather
        # than "attached this long ago": a session watched for an hour and then
        # abandoned would otherwise be swept on the very next tick.
        _supervisor.touch_used(sid)

    def broadcast(self, session_id: str, frame: dict) -> None:
        """Queue a frame on every socket attached to a session."""
        for conn in tuple(self.subscribers.get(session_id, ())):
            conn.send(frame)


_registry = _Registry()

# Strong references to in-flight background tasks. `asyncio.create_task` only
# keeps a weak one, so a task nobody awaits can be collected mid-await and
# vanish — here that would be a session creation that silently never finishes.
_tasks: set[asyncio.Task] = set()


def _spawn_task(coro) -> None:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _neutral_cwd() -> Path:
    """The directory the agent process itself runs in.

    Deliberately not ``Path.home()``: one process serves N workspaces, so its
    own cwd cannot be meaningful, and ``presence.py``'s process scan reads every
    kiro-cli process's cwd into ``live_cwds``. Pointing it at a real directory
    would light that directory up as "live" in the dashboard for as long as the
    agent runs, which is exactly the false signal this prototype must not add.
    """
    path = CONFIG_DIR / "acp-cwd"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _close_streams(proc: subprocess.Popen) -> None:
    """Close a child's pipes. Never raises.

    Only for a child that failed during ``_spawn``, before any reader thread
    exists: closing ``stdout`` under a thread blocked reading it is a different
    hazard. Without this a spawn that is rejected after ``Popen`` succeeded
    leaks two handles per attempt, and the rejection paths are retried on every
    ``new`` frame.
    """
    for stream in (proc.stdin, proc.stdout):
        if stream is None:
            continue
        try:
            stream.close()
        except Exception:
            pass


def _close_job(job) -> None:
    """Release a job handle. Never raises; a ``None`` job is a no-op.

    Closing the last handle is itself a kill under
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, which is why every teardown path
    ends here — it is the backstop for whatever the explicit kills missed.
    """
    if job is None:
        return
    try:
        job.Close()
    except Exception:
        pass


def _resolve_session_cwd(raw: str | None) -> str:
    """Validate the directory a session will be created against."""
    if not raw:
        return str(_neutral_cwd())
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise BadCwd(f"Unusable session directory: {exc}") from exc
    if not path.is_dir():
        raise BadCwd(f"Not a directory: {path}")
    return str(path)


def _valid_session_id(session_id) -> bool:
    """Whether a client-supplied session id may be used as a path component.

    The same rule ``launcher.py`` applies before an id reaches a command line,
    imported rather than restated so the two cannot drift. ``^[\\w\\-]+$``
    admits no separator and no ``.``, so no form of traversal survives it —
    which is what this id needs before it is joined into ``KIRO_SESSION_DIR``
    and then handed to an agent running trust-all-tools.

    ``fullmatch``, not ``match``: Python's ``$`` also matches immediately
    before a trailing newline, so the shared pattern used with ``match`` — as
    ``launcher.py:134`` uses it — accepts ``"<id>\\n"``. That is the one shape
    the pattern reads as if it excluded.
    """
    return (isinstance(session_id, str)
            and 0 < len(session_id) <= MAX_SESSION_ID_CHARS
            and _SESSION_ID_RE.fullmatch(session_id) is not None)


def _lock_started_at(raw: dict) -> float | None:
    """The lock's own timestamp as a POSIX time, or ``None`` if unreadable."""
    match = _LOCK_TIME_RE.match(str(raw.get("started_at", "")))
    if match is None:
        return None
    try:
        return datetime.strptime(
            match.group(1), "%Y-%m-%dT%H:%M:%S"
        ).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _lock_holder(session_id: str) -> int | None:
    """The pid of the process holding a session's lock, if there is one.

    Blocking — a bounded file read plus a ``psutil`` query — so call it off the
    loop, the way its neighbour ``_stored_session_cwd`` is called.

    A hint and never the gate, per the plan: nothing removes a lock file on a
    hard exit, so the store is full of locks whose pid died months ago and has
    since been recycled onto something unrelated. The authority is the agent's
    own typed refusal, measured at 0.73-0.84 s. This exists to answer in
    roughly no time in the common case, not to be right in every case.

    Every branch that cannot *establish* a holder returns ``None``. A hint may
    only add a refusal; it may never grant one, because the thing it would be
    granting against is the agent's answer.

    Not ``presence.Snapshot.is_live()``: that also requires a provider-name
    match and a start-time skew window, so it reports not-live for sessions the
    agent still refuses.
    """
    if psutil is None:
        # `os.kill(pid, 0)` is not the fallback it looks like on Windows: it
        # calls TerminateProcess, so the liveness probe would kill the process
        # it asked about.
        return None
    try:
        with open(KIRO_SESSION_DIR / f"{session_id}.lock", "rb") as fh:
            prefix = fh.read(LOCK_MAX_BYTES)
    except OSError:
        return None
    try:
        raw = json.loads(prefix.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    pid = raw.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    started = _lock_started_at(raw)
    if started is None:
        return None
    try:
        created = psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None
    if created > started + LOCK_START_SKEW_SECONDS:
        # The pid exists but belongs to something that started long after this
        # lock was written — a recycled pid, not the session's holder.
        return None
    if pid == _supervisor.agent_pid():
        # Our own agent. `session/load` makes it write this lock naming
        # *itself*, so a lock left behind by a load that failed on our side
        # would otherwise refuse every retry for the agent's whole life —
        # telling the operator to exit a process that is PowerAtlas.
        return None
    return pid


def _stored_session_cwd(session_id: str) -> str:
    """The directory a session was created against, from its stored metadata.

    A bounded prefix read rather than a whole-file parse: see
    ``SESSION_JSON_PREFIX_BYTES``. Returns ``""`` whenever the file is missing,
    unreadable, or carries no ``cwd`` in that prefix.
    """
    try:
        with open(KIRO_SESSION_DIR / f"{session_id}.json", "rb") as fh:
            prefix = fh.read(SESSION_JSON_PREFIX_BYTES)
    except OSError:
        return ""
    match = _STORED_CWD_RE.search(prefix.decode("utf-8", "replace"))
    if match is None:
        return ""
    try:
        value = json.loads(match.group(1))
    except ValueError:
        return ""
    return value if isinstance(value, str) else ""


def _load_session_cwd(session_id: str) -> str:
    """The cwd a ``session/load`` runs against. Blocking; call off the loop.

    The session's own directory when it still exists, because that is where a
    prompt after the load would expect its tools to run. The agent's neutral
    cwd otherwise — a workspace that has been moved or deleted does not make
    the conversation unreadable, and refusing the load over it would.
    """
    stored = _stored_session_cwd(session_id)
    if not stored:
        return str(_neutral_cwd())
    try:
        return _resolve_session_cwd(stored)
    except BadCwd:
        log.info("ACP load: stored cwd %r is gone; using the neutral cwd", stored)
        return str(_neutral_cwd())


def _session_limit_message() -> str:
    """Why the cap refused, and what actually frees a slot.

    This message has now been wrong in three directions. It said "close one
    first" while ``close`` still answered ``not_implemented``, and was
    corrected in Phase 5 to name a PowerAtlas restart — which Phase 6's close
    control then made wrong the other way, since a restart is no longer the
    only lever and is by far the more expensive one. The third was subtler and
    is corrected here: "close one from its tab" presumed one tab per session,
    which was true when MAX_SESSIONS was 3 and stopped being true when the rail
    arrived. Followed literally at the shipped cap it is also self-defeating —
    eight sessions in eight tabs is eight sockets, and the ninth is refused by
    MAX_CONNECTIONS, so the page that would explain this cap cannot connect.
    One tab reaches every session, so the remedy names the rail.

    Nothing in the test suite asserts this text is *true*, only that it names a
    remedy and quotes the measured figure, so it is worth re-reading whenever
    the set of controls changes.

    The per-session figure is the final-QA measurement at the shipped default
    of eight concurrent sessions: 24 processes and 1288.6 MiB over eight above
    a 5-process / 531.6 MiB baseline, i.e. 3.0 processes and 161.1 MiB each.
    The ~178 MB it replaces was an earlier eight-session run.
    """
    return (f"At most {MAX_SESSIONS} sessions at once (~3 processes and "
            "~161 MB each). Close one from the session list to free a slot — "
            "open it and press Close; one tab reaches them all. Restarting "
            "PowerAtlas releases every session, and sessions left idle are "
            "reclaimed on their own.")


def _new_session_record(cwd: str) -> dict:
    """The metadata a session carries from the moment it is registered.

    Both timestamps are written **at construction**, in both constructors, and
    that is not tidiness. The sweeper reads ``last_used`` on every tick, and a
    session created but never prompted — the ``_handle_new`` "socket went away"
    case, which also has no subscriber — would otherwise be missing the key
    entirely and be the first thing the sweeper touched.

    ``created`` is wall-clock and only ever rendered. ``last_used`` and
    ``last_activity`` are monotonic and only ever subtracted. Two clocks in one
    record, stated here rather than left to be rediscovered.
    """
    now = time.monotonic()
    return {
        "cwd": cwd,
        "created": time.time(),
        "last_used": now,
        "last_activity": now,
    }


class _Supervisor:
    """The single ``kiro-cli acp`` process, and the JSON-RPC channel to it.

    Lazily spawned on the first session request — never at import and never at
    startup, so a PowerAtlas launch that never opens ``/acp`` pays nothing. The
    idle sweeper amends that by exactly one wakeup a minute: `_sweep_loop`
    starts with the application, but its first act after each sleep is to
    return on an empty `sessions` dict, so no agent is spawned and no work is
    done for a session that does not exist.

    Threading shape: the event loop owns ``_pending``, ``sessions`` and the
    spawn path; one dedicated OS thread owns the blocking read of ``stdout`` and
    touches nothing but ``_post``. Writes are serialized by ``_write_lock`` and
    performed off the loop. That split is why the loop's hundreds of
    milliseconds of synchronous disk I/O during renders cannot stall a stream.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        # The job handle. Held as an attribute for the process's whole lifetime
        # on purpose: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE fires when the *last*
        # handle closes, so letting this be garbage-collected would have the OS
        # kill the agent mid-turn. Only `_spawn` (which takes a fresh one) and
        # `_detach` (which hands it to whoever is disposing of the process) may
        # rebind this.
        self._job = None
        # Set only once `initialize` has answered, and cleared by every path
        # that unbinds the process. Tracked apart from process liveness because
        # the two differ in exactly the case that matters: a process that is
        # running but never handshaken.
        self._ready = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._start_lock: asyncio.Lock | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self.sessions: dict[str, dict] = {}
        # Replay buffers, keyed the same way and cleared by the same paths, so
        # a session's history cannot outlive the session it belongs to —
        # `_handle_subscribe` refuses an unknown session, which would otherwise
        # leave its buffer unreachable and resident for the app's lifetime.
        self.history: dict[str, _History] = {}
        # Sessions with a `session/prompt` in flight. The agent is not asked to
        # arbitrate two concurrent turns on one session: their chunks carry no
        # turn id, so the fan-out would interleave them into one transcript
        # with nothing able to separate them again.
        self.inflight: set[str] = set()
        # Sessions with a `session/close` in flight. Claimed before the first
        # await of `close_session`, so two `close` frames for one session cannot
        # both reach the agent — the second would be refused by an agent that
        # no longer has the session, and reach the page as a failure to close
        # something that is already closed.
        self.closing: set[str] = set()
        # Sessions promised but not yet recorded — see `new_session`. Counted
        # against MAX_SESSIONS alongside `sessions`, because the creation of one
        # spans two awaits and the cap has to hold across them.
        self._reserved = 0

        # ── Sub-agent crews (SUBAGENT_LIST_METHOD) ──
        #
        # Deliberately three dicts parallel to `sessions`/`history`, not entries
        # merged into them: a sub-agent session must never count against
        # `at_capacity()` (Q&A, 2026-08-11 — "exempt them", so a ten-stage
        # fan-out cannot crowd out room for a real session the user asked for)
        # and must never be prompt/close/cancel-able (roadmap's own note: "the
        # correct interaction model is to watch it, not to prompt it"). Folding
        # them into `sessions` would need every one of `at_capacity`,
        # `new_session`'s reservation math, `_handle_prompt`, `_handle_close`
        # and `_handle_cancel` to carry a read-only branch instead of the two or
        # three call sites that actually need one — see `_handle_subscribe`.
        #
        # `crews`: parent session id -> ordered `{child sid: entry}`, insertion
        # order preserved (a plain dict, Python 3.7+) so the bar and the inline
        # transcript card both list sub-agents in the order kiro-cli first
        # reported them. An entry is never removed while its parent session is
        # open — Q&A: "stay, marked done" — only ever updated in place or
        # (past MAX_SUBAGENTS_PER_SESSION, and only among the already-`done`)
        # evicted oldest-first. See `_on_subagent_list`.
        self.crews: dict[str, dict[str, dict]] = {}
        # `subagent_sessions`: child sid -> `{"parent": parent sid}`. Minimal on
        # purpose — this is the membership test `_handle_subscribe`,
        # `_handle_prompt`, `_handle_close` and `_handle_cancel` all consult,
        # and the one place a child's parent is looked up for cleanup. No
        # `cwd`/`created`/`last_used` the way `_new_session_record` carries for
        # a real session: nothing here reads them, because nothing sweeps a
        # sub-agent on its own clock — it is torn down only with its parent
        # (`close_session`) or the whole agent (`_detach`).
        self.subagent_sessions: dict[str, dict] = {}
        # `subagent_history`: child sid -> its own `_History`, exactly like
        # `history` for a real session, and read by the exact same `record`/
        # `_handle_subscribe` — a sub-agent's transcript is the same replay
        # machinery a real session's is, just never counted or swept the same
        # way. Created in `_on_subagent_list` the moment a child sid is first
        # seen, so a `tool_call_chunk`/`agent_message_chunk` arriving after it
        # (the expected order — kiro-cli announces a sub-agent before it talks)
        # is recorded rather than silently dropped the way an unregistered
        # session's frames already are (see the module docstring's isolation
        # note and `_stamp_activity`'s P0-4).
        self.subagent_history: dict[str, _History] = {}

    # -- lifecycle ---------------------------------------------------------

    def alive(self) -> bool:
        """True only for a *handshaken* channel to a running process.

        The handshake half is the load-bearing one. Binding ``_proc`` to a live
        process is not the same as having completed ``initialize``: an
        ``initialize`` that timed out or was refused used to leave a process
        that ``poll()`` still reported as running, so every later
        ``ensure_started`` short-circuited here and skipped the handshake
        permanently — every subsequent ``session/new`` then ran against an
        un-handshaken agent and burned the full request ceiling. Recovery
        needed a PowerAtlas restart.

        The ``poll()`` half stays as a cheap backstop only. The channel's own
        death is what ``_on_agent_death`` reports, per the module docstring;
        this is not the health test.
        """
        proc = self._proc
        return self._ready and proc is not None and proc.poll() is None

    def _publish_live(self) -> None:
        """Tell whoever is listening which sessions this agent holds.

        Call after **every** mutation of ``sessions``. There are five, plus a
        backstop on the sweeper tick — so a sixth added later converges within
        one tick instead of drifting silently, which is the failure mode a
        publish-at-each-site design otherwise has.

        Swallows everything the hook raises. This runs on the paths that create,
        load and close sessions; a consumer that throws must degrade to a stale
        dashboard dot, never to a session that could not be opened.
        """
        hook = sessions_changed_hook
        if hook is None:
            return
        try:
            hook(frozenset(self.sessions), self.agent_pid())
        except Exception:
            log.exception("ACP: publishing the live session set failed")

    def agent_pid(self) -> int | None:
        """The pid of the process this supervisor is bound to, if any.

        Deliberately not gated on ``poll()``: this is read from a worker thread
        (``_lock_holder``) and the only thing it is used for is *suppressing* a
        lock hint. A pid that has been recycled between the agent's death and
        ``_detach`` unbinding it can therefore cost a more specific message and
        nothing else — it can never grant a load that should have been refused.
        """
        proc = self._proc
        return None if proc is None else proc.pid

    def _get_start_lock(self) -> asyncio.Lock:
        # Created on first use rather than in `__init__`: an `asyncio.Lock`
        # binds to whichever loop first awaits it, and this module is imported
        # long before uvicorn creates one.
        if self._start_lock is None:
            self._start_lock = asyncio.Lock()
        return self._start_lock

    async def ensure_started(self) -> None:
        """Spawn and handshake if needed. Safe to call concurrently."""
        async with self._get_start_lock():
            if self.alive():
                return
            if self._proc is not None:
                # Bound but not `alive()`: a previous handshake never finished,
                # or a teardown of it did not take. Either way it is unusable,
                # and spawning a replacement beside it would orphan it for the
                # app's lifetime.
                self._discard("Replaced: the agent never completed its handshake.")
            # Captured here, inside the async path, and never at import:
            # uvicorn builds its loop inside `server.run()` on a non-main
            # thread, so an import-time capture gets a different loop or none —
            # and `call_soon_threadsafe` against the wrong loop is a silent
            # black hole for every agent message.
            self._loop = asyncio.get_running_loop()
            await asyncio.to_thread(self._spawn)
            try:
                result = await self._request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        # Declared explicitly rather than left empty. An agent
                        # that believes we can read files or run terminals will
                        # send `fs/read_text_file` and `terminal/*` requests
                        # this client cannot serve; saying so up front is
                        # cheaper than the catch-all responder having to refuse
                        # them one by one.
                        "clientCapabilities": {
                            "fs": {"readTextFile": False, "writeTextFile": False},
                            "terminal": False,
                        },
                    },
                    timeout=INITIALIZE_TIMEOUT_SECONDS,
                )
            except BaseException as exc:
                # A handshake that fails — timed out, or refused by an agent
                # that self-updated under us — leaves a running process that
                # nothing can use and `poll()` cannot tell apart from a working
                # one. Tear it down here so the next attempt starts from a
                # clean spawn instead of short-circuiting on the wreckage.
                # `BaseException` on purpose: a cancelled `ensure_started` must
                # not leave a bound, un-handshaken process behind either.
                self._discard(f"Handshake failed: {exc}")
                raise
            self._ready = True
            agent_info = (result or {}).get("agentInfo") or {}
            log.info("ACP agent ready: %s (pid %s, protocol %s)",
                     agent_info.get("version", "?"),
                     self._proc.pid if self._proc else "?",
                     (result or {}).get("protocolVersion"))

    def _spawn(self) -> None:
        """Start the agent. Runs off the loop; raises ``AcpError`` on refusal."""
        exe = shutil.which(KIRO_BINARY)
        if not exe:
            raise AgentUnavailable(
                f"'{KIRO_BINARY}' is not on PATH — nothing to connect to.")
        if Path(exe).suffix.lower() in _WRAPPER_SUFFIXES:
            raise AgentUnavailable(
                f"'{exe}' is a shell wrapper. Spawning it with pipes needs "
                "shell=True, which cannot hold clean stdio for JSON-RPC.")
        cwd = _neutral_cwd()

        # Obtained *before* the child exists, so the common failure costs
        # nothing and there is no window in which an unprotected agent runs.
        job = self._create_job()

        try:
            proc = subprocess.Popen(
                [exe, *ACP_ARGS],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                # Never a pipe. Nothing here would drain it, and a child that
                # fills the ~64 KB Windows pipe buffer blocks on write forever —
                # a hang with no error anywhere. The one measured session that
                # produced 0 stderr bytes says nothing about panic backtraces
                # or MCP startup noise.
                stderr=subprocess.DEVNULL,
                cwd=str(cwd),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:
            _close_job(job)
            raise AgentSpawnFailed(f"Could not start the agent: {exc}") from exc

        try:
            handle = win32api.OpenProcess(
                win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                False, proc.pid)
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
        except Exception as exc:
            # The one failure that cannot be shrugged off: the child is already
            # running and is now outside the only guarantee that reaps it on
            # `--stop`, `--restart`, a crash or Task Manager. Leaving it up
            # would be an agent that no death route can reach, for the machine's
            # lifetime. Kill it, then refuse — the refusal reaches the page as a
            # typed `agent_spawn_failed` frame rather than only a log line.
            log.exception("ACP job assignment failed for pid %d; killing it "
                          "rather than leaving it unprotected", proc.pid)
            if proc.poll() is None:
                self._tree_kill(proc)
            _close_streams(proc)
            _close_job(job)
            raise AgentSpawnFailed(
                "The agent started but could not be placed in the Windows job "
                f"object that guarantees its teardown ({exc}). It was killed "
                "rather than left running where nothing could reap it."
            ) from exc

        self._job = job
        self._proc = proc
        self._reader = threading.Thread(
            target=self._reader_loop, args=(proc,),
            name="acp-reader", daemon=True)
        self._reader.start()
        log.info("ACP agent spawned: pid %d, cwd %s, job object held", proc.pid, cwd)

    @staticmethod
    def _create_job():
        """Create the job object that guarantees the agent's teardown.

        Fatal on failure, deliberately. The job — not ``shutdown()`` — is what
        covers `--stop`, `--restart`, a crash and Task Manager, none of which
        run a line of code in this module. An agent spawned without it is an
        agent whose only teardown route is one that those paths never take, so
        the honest outcome is to refuse rather than to spawn something the log
        quietly describes as best-effort.
        """
        if win32job is None:
            raise AgentUnavailable(
                "pywin32 is unavailable, so the agent cannot be placed in a "
                "Windows job object — the only thing that guarantees its whole "
                "process tree dies with PowerAtlas. Refusing to spawn an agent "
                "nothing could reap.")
        job = None
        try:
            job = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation)
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            win32job.SetInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation, info)
        except Exception as exc:
            _close_job(job)
            raise AgentSpawnFailed(
                "Could not create the Windows job object that guarantees the "
                f"agent's teardown ({exc}). Refusing to spawn an agent nothing "
                "could reap.") from exc
        return job

    def shutdown(self) -> None:
        """Kill the agent and its whole tree. Idempotent; never raises.

        The fast path, not the guarantee — the job object is the guarantee, and
        it is what covers `--stop`, `--restart`, a crash and Task Manager, none
        of which reach this function. No graceful protocol shutdown: it kills,
        then waits up to ``KILL_WAIT_SECONDS`` for the tree to actually go, so
        that the one teardown route which can log anything has a post-condition
        rather than only an intent. See ``KILL_WAIT_SECONDS`` for why that wait
        may overrun ``__main__.py``'s 5 s join, and why the overrun is benign.
        """
        self._start_lock = None
        proc, job = self._detach("The agent was shut down.")
        self._dispose(proc, job, "shutdown")

    def _discard(self, reason: str) -> None:
        """Unbind the current process and kill it off the event loop.

        For the loop-side failure paths — a handshake that never completed, a
        channel that closed. Split in two because ``_dispose`` waits seconds for
        a tree to die, which is correct in ``shutdown`` (where blocking the loop
        *is* the point) and wrong on a request path the page is waiting on. The
        kill is not awaited either: teardown must not be contingent on the
        caller surviving long enough to await it.
        """
        proc, job = self._detach(reason)
        if proc is None and job is None:
            return
        threading.Thread(
            target=self._dispose, args=(proc, job, reason),
            name="acp-dispose", daemon=True).start()

    def _detach(self, reason: str):
        """Unbind the process and fail everything waiting on it. Loop-side.

        Returns the ``(proc, job)`` pair for a caller to dispose of. Never
        touches ``_reserved``: each reservation is released by the ``finally``
        of the ``new_session`` that took it, and zeroing the counter here would
        let those releases drive it negative.
        """
        proc, self._proc = self._proc, None
        job, self._job = self._job, None
        self._ready = False
        self.sessions.clear()
        self._publish_live()
        self.history.clear()
        self.inflight.clear()
        self.closing.clear()
        # Every crew belonged to a session that no longer exists on this agent
        # either — the agent that held the sub-agents' own processes is the one
        # that just died.
        self.crews.clear()
        self.subagent_sessions.clear()
        self.subagent_history.clear()
        # Same reason the buffers go: nothing can ever read a bubble whose
        # session no longer exists, and the text in it is agent-authored and
        # unbounded until the cap.
        _bubbles.clear()
        for fut in tuple(self._pending.values()):
            if not fut.done():
                fut.set_exception(AgentDied(reason))
        self._pending.clear()
        return proc, job

    @classmethod
    def _dispose(cls, proc: subprocess.Popen | None, job, reason: str) -> None:
        """Kill a detached tree and release its job handle. Never raises."""
        if proc is not None and proc.poll() is None:
            # Logged because this is the only teardown route that leaves any
            # trace at all: `--stop`, `--restart`, a crash and Task Manager all
            # go through the job object, which by definition runs no code here.
            log.info("ACP teardown: killing agent pid %d and its tree (%s)",
                     proc.pid, reason)
            cls._tree_kill(proc)
        # Released last. While this handle lives the job lives, and closing it
        # is itself a kill — so it doubles as the backstop for anything the
        # tree-kill above missed (a grandchild spawned mid-teardown, an
        # AccessDenied on one branch).
        _close_job(job)

    @staticmethod
    def _tree_kill(proc: subprocess.Popen) -> None:
        """Kill the agent and every descendant. Never raises; always reports.

        Each step is separately guarded, because they fail independently and a
        failure of the cheapest one must not cancel the others. Enumerating the
        children is the step that used to swallow the whole function: an
        `AccessDenied` there jumped straight past ``parent.kill()``, so nothing
        in the tree died and nothing said so — a no-op teardown that read
        exactly like a successful one.

        Taking ``proc.pid`` is only safe because every caller checks
        ``poll() is None`` first: an exited pid can have been recycled onto an
        unrelated process, the hazard `presence.py`'s create-time check exists
        for.
        """
        if psutil is None:
            try:
                proc.kill()
                log.info("ACP tree-kill: killed pid %d (no psutil; children "
                         "left to the job object)", proc.pid)
            except Exception as exc:
                log.warning("ACP tree-kill: could not kill pid %d: %s", proc.pid, exc)
            return

        parent = None
        try:
            parent = psutil.Process(proc.pid)
        except (psutil.Error, OSError) as exc:
            log.warning("ACP tree-kill: pid %d not inspectable (%s)", proc.pid, exc)
        kids = []
        if parent is not None:
            try:
                kids = parent.children(recursive=True)
            except (psutil.Error, OSError) as exc:
                log.warning("ACP tree-kill: could not enumerate the tree under "
                            "pid %d (%s); killing the parent alone", proc.pid, exc)

        # Parent first, so it cannot spawn more children while we work down the
        # list that was captured above.
        targets = ([parent] if parent is not None else []) + list(kids)
        killed = 0
        for victim in targets:
            try:
                victim.kill()
                killed += 1
            except psutil.NoSuchProcess:
                killed += 1
            except (psutil.Error, OSError) as exc:
                log.warning("ACP tree-kill: could not kill pid %s: %s",
                            getattr(victim, "pid", "?"), exc)
        if parent is None:
            # psutil could not even see the parent; the Popen handle still can.
            try:
                proc.kill()
                killed += 1
            except Exception as exc:
                log.warning("ACP tree-kill: fallback kill of pid %d failed: %s",
                            proc.pid, exc)

        if not targets:
            # Nothing psutil could hand to `wait_procs`, so the outcome is
            # genuinely unknown — say that rather than claim a clean tree.
            log.info("ACP tree-kill: killed pid %d, outcome unverified (psutil "
                     "could not see the tree); the job object is the backstop",
                     proc.pid)
            return
        survivors = []
        try:
            _, survivors = psutil.wait_procs(targets, timeout=KILL_WAIT_SECONDS)
        except Exception:
            log.exception("ACP tree-kill: waiting on the tree failed")
        log.info("ACP tree-kill: %d process(es) killed, %d still alive within "
                 "%.0fs%s", killed, len(survivors), KILL_WAIT_SECONDS,
                 "" if not survivors else
                 " — left to the job object: %s" % [p.pid for p in survivors])

    # -- JSON-RPC ----------------------------------------------------------

    async def _request(self, method: str, params: dict,
                       timeout: float = REQUEST_TIMEOUT_SECONDS):
        """Send a request and await its result. Always bounded.

        ``timeout`` carries either a number of seconds or the ``_INACTIVITY``
        sentinel, which asks for the silence-based ceiling instead. The
        signature is unchanged on purpose — see ``_INACTIVITY``.
        """
        loop = self._loop
        if loop is None:
            raise AgentDied("The agent channel is not open.")
        request_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut
        payload = {"jsonrpc": "2.0", "id": request_id,
                   "method": method, "params": params}
        # Read with `is` and read *here*, above the try: the wall-clock arm
        # below formats `{timeout:.0f}` into its message, which a sentinel
        # cannot survive.
        inactivity = timeout is _INACTIVITY
        try:
            await asyncio.to_thread(self._write, payload)
            if inactivity:
                return await self._await_inactivity(
                    fut, method, params.get("sessionId"))
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                raise AgentTimeout(
                    f"The agent did not answer '{method}' within "
                    f"{timeout:.0f}s.")
        finally:
            self._pending.pop(request_id, None)

    async def _await_inactivity(self, fut: asyncio.Future, method: str,
                                session_id):
        """Await a request bounded by agent *silence* rather than wall clock.

        Three properties are load-bearing and none of them is obvious.

        **The future is shielded on every pass.** ``asyncio.wait_for`` cancels
        the future it is given when its timeout expires, so handing it the bare
        pending future would destroy it on the first tick and the real answer
        would arrive to a future nobody holds — dropped by ``_on_response`` as
        "late or unmatched". ``shield`` detaches its callback when the outer
        wait is cancelled, so the callbacks do not accumulate across a
        four-hour turn either.

        **The deadline is seeded locally at send time, not read from
        ``last_activity``.** A session idle for twenty minutes with a tab
        attached is unswept but already "silent" by the shared stamp, so
        seeding from it would kill that session's next prompt on the first tick
        before the agent had any chance to answer.

        **There is a hard stop as well.** See ``PROMPT_ABSOLUTE_MAX_SECONDS``.

        On expiry the turn is cancelled agent-side rather than merely
        abandoned. What that does and does not achieve is measured, not
        assumed: on kiro-cli 2.16.0 the ACP turn really does end (the
        outstanding prompt answers ``cancelled`` in ~9 ms), and the tool's OS
        children really do not die — a shell the agent started keeps running,
        invisible to ``inflight``, to the sweeper and to the per-session memory
        figure, until PowerAtlas exits and its job object takes the whole tree.
        That orphan is a recorded residual of this design, not an oversight.
        """
        started = time.monotonic()
        deadline = started + PROMPT_SILENCE_SECONDS
        hard_stop = started + PROMPT_ABSOLUTE_MAX_SECONDS
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(fut), PROMPT_TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
            meta = self.sessions.get(session_id)
            if meta is None:
                # Closed under us, or `_detach` cleared the whole dict when the
                # agent died. Fall through to a bare `await` so whatever the
                # future already carries — typically the typed `AgentDied`
                # `_detach` set on it — is what surfaces, rather than a timeout
                # this loop invented.
                #
                # That final `await fut` is unbounded, and what bounds it is an
                # invariant held elsewhere: **no path pops a session record out
                # from under a turn in flight without first resolving that
                # turn's pending future.** `inflight` is what enforces it —
                # `_handle_close` refuses a session with `turn_in_progress` and
                # the sweeper's condition 4 skips one, so neither can reach
                # `close_session` while this loop is running. The one popper
                # that can is `_detach`, and it sets `AgentDied` on every
                # pending future *before* clearing `sessions`. Add a route that
                # drops the record without settling the future and this line
                # hangs forever, holding `inflight` — and therefore the
                # session's close and its sweep — with it.
                break
            last = meta.get("last_activity")
            if last is not None:
                deadline = max(deadline, last + PROMPT_SILENCE_SECONDS)
            now = time.monotonic()
            if now <= deadline and now <= hard_stop:
                continue
            capped = now > hard_stop
            log.warning(
                "ACP %s: cancelling session=%s after %.0fs (%s); the agent's "
                "own tool processes are NOT reaped by this",
                method, session_id, now - started,
                "absolute ceiling" if capped else "silence")
            await self._notify("session/cancel", {"sessionId": session_id})
            # Bounded grace. An honoured cancel lands its final frame inside
            # it, and `_handle_prompt` then releases `inflight` behind a turn
            # that has really ended — without it, prompt #2 can interleave with
            # turn #1 in one transcript with no turn id to separate them.
            with contextlib.suppress(asyncio.TimeoutError):
                return await asyncio.wait_for(
                    asyncio.shield(fut), CANCEL_GRACE_SECONDS)
            raise AgentTimeout(
                f"The agent went silent for {PROMPT_SILENCE_SECONDS:.0f}s "
                f"during '{method}'; the turn was cancelled."
                if not capped else
                f"'{method}' ran past the {PROMPT_ABSOLUTE_MAX_SECONDS:.0f}s "
                "ceiling and was cancelled.")
        return await fut

    async def _notify(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification: no id, so no answer is possible.

        Deliberately not ``_request`` with a discarded result. A notification
        carries no id, so the agent has nothing to answer with and a future
        awaiting one would sit until its ceiling expired — on ``session/cancel``
        that would be a Stop button that appears to hang for 90 s while the
        cancellation it asked for has already happened.
        """
        await asyncio.to_thread(self._write, {
            "jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict) -> None:
        """Write one NDJSON line. Serialized; always flushed.

        Both halves matter. The lock is what stops two concurrent requests
        interleaving halves of a line onto one pipe, and the flush is what makes
        the line exist at all — an unflushed request sits in a buffer, invisible
        to the agent, and presents as a hang with no error.
        """
        line = json.dumps(obj, separators=(",", ":"))
        with self._write_lock:
            proc = self._proc
            if proc is None or proc.stdin is None or proc.poll() is not None:
                raise AgentDied("The agent is not running.")
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise AgentDied(f"Lost the agent's stdin: {exc}") from exc

    def _reader_loop(self, proc: subprocess.Popen) -> None:
        """Blocking read of the agent's stdout, on its own OS thread.

        Reads bounded blocks and splits them itself rather than iterating
        lines. The iteration form — and ``readline()``, which is the same thing
        — accumulates until a newline arrives however long that takes, so a
        single runaway line has no ceiling at all. Splitting blocks is what
        makes ``MAX_AGENT_LINE_BYTES`` enforceable: an over-long line is
        abandoned and swallowed up to the next newline instead of buffered.

        The read happens on the underlying binary buffer, which is also what
        makes the cap a byte count rather than a character count. Nothing else
        in this module reads ``proc.stdout`` as text, so the two views of the
        stream never interleave.
        """
        stream = getattr(proc.stdout, "buffer", proc.stdout)
        pending = bytearray()
        # True while the remainder of an over-long line is being discarded.
        # Without it the tail of a rejected line would be parsed as if it were
        # a line of its own, turning one rejection into a run of warnings.
        dropping = False
        try:
            while True:
                # `read1`, not `read`: a buffered `read(n)` loops until it has
                # all n bytes, which would hold every chunk of a streamed
                # answer hostage until 64 KiB of them had accumulated.
                block = stream.read1(READ_BLOCK_BYTES)
                if not block:
                    break
                parts = block.split(b"\n")
                for part in parts[:-1]:
                    if dropping:
                        dropping = False
                    else:
                        pending += part
                        self._on_line(bytes(pending))
                    pending.clear()
                if dropping:
                    continue
                pending += parts[-1]
                if len(pending) > MAX_AGENT_LINE_BYTES:
                    log.error("ACP: discarding an agent line over %d bytes; the "
                              "channel continues at the next newline",
                              MAX_AGENT_LINE_BYTES)
                    dropping = True
                    pending.clear()
        except Exception:
            # Nothing above may take the thread down without a trace: this
            # thread is the only thing reading the agent, so its death is the
            # channel's death, and the `finally` below is what tells anyone.
            log.exception("ACP reader thread failed")
        finally:
            self._post(self._on_agent_death, proc)

    def _on_line(self, raw: bytes) -> None:
        """Parse one complete NDJSON line and hand it to the loop."""
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            # Banner lines and other non-JSON noise are tolerated rather than
            # fatal, but never silent: a protocol change would otherwise read
            # as an agent that says nothing.
            log.warning("ACP: non-JSON line from agent: %.200s", line)
            return
        if isinstance(msg, dict):
            self._post(self._on_message, msg)

    def _post(self, fn, *args) -> None:
        """Hand work to the event loop from the reader thread."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            # The loop is closed. Reachable on shutdown, and also from
            # `__main__.py`'s port-fallback path, which can build a second loop
            # after the first is gone. Dropping the message is correct; dying
            # here would take the thread out before its `finally` runs.
            log.warning("ACP: event loop gone, dropping an agent message")

    # -- loop-side handling ------------------------------------------------

    def _on_message(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            self._on_response(msg)
        elif "id" in msg and "method" in msg:
            self._on_agent_request(msg)
        elif "method" in msg:
            self._on_notification(msg)
        else:
            log.warning("ACP: uninterpretable message from agent: %.200s",
                        json.dumps(msg))

    def _on_response(self, msg: dict) -> None:
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            # A response to a request that already timed out. Expected, and
            # worth a line: it is the difference between "the agent is dead"
            # and "the agent is slower than the ceiling allows".
            log.info("ACP: late or unmatched response id=%r", msg.get("id"))
            return
        if "error" in msg:
            err = msg.get("error") or {}
            # `data` is where kiro-cli 2.16.0 puts the half that identifies the
            # failure. Measured 2026-08-03, a busy `session/load` answers:
            #
            #   {"code": -32603, "message": "Internal error",
            #    "data": "Failed to start session: Session is active in
            #             another process (PID 22264)"}
            #
            # and the pid named is the real holder. `message` alone is
            # "Internal error", which says only that something went wrong.
            #
            # Dropping `data` is why `_IN_USE_MARKER` could never match: the
            # one string it exists to recognise was discarded here, two frames
            # before the code that reads the lock file again and runs psutil to
            # reconstruct the pid the agent had just supplied. The path that
            # consumes a spoken refusal was written for an earlier build that
            # named the holder, went defensive when that stopped, and is
            # reachable again now — see `_load_failure`.
            detail = err.get("data")
            text = str(err.get("message", "agent error"))
            if isinstance(detail, str) and detail.strip():
                # Bounded because this is agent-controlled text on its way to a
                # user-visible message. Generous against the measured string
                # (72 characters) and far short of anything that could crowd a
                # frame.
                text = detail.strip()[:MAX_ERROR_DETAIL_CHARS]
            fut.set_exception(AgentRejected(f"{text} (code {err.get('code')})"))
        else:
            fut.set_result(msg.get("result"))

    def _on_agent_request(self, msg: dict) -> None:
        """Refuse any request from the agent, and say so loudly.

        The catch-all is the point. `session/request_permission`,
        `fs/read_text_file` and `terminal/*` are all plausible and none was
        exercised by the original probe, which never sent a tool-using prompt.
        An unanswered request hangs the turn indistinguishably from the ~6 s
        latency `session/new` already has, so failing fast beats guessing.
        """
        method = msg.get("method")
        log.warning("ACP: refusing unsupported agent request '%s'", method)
        _spawn_task(self._refuse(msg.get("id"), method))

    async def _refuse(self, request_id, method) -> None:
        try:
            await asyncio.to_thread(self._write, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Client does not implement '{method}'.",
                },
            })
        except AcpError as exc:
            # Never silent. This is the write that answers the one request the
            # catch-all exists for; if it does not land, the agent is left
            # hanging on it and the log is the only place that could say so.
            log.warning("ACP: could not deliver the refusal of '%s' (id=%r): %s",
                        method, request_id, exc)

    def _stamp_activity(self, session_id) -> None:
        """Record that the agent has just said *something* about a session.

        Keyed on **"does this notification carry a session id"**, not on a
        method allowlist, and stamped above the branch dispatch rather than
        inside it. Both readings were measured against kiro-cli 2.16.0 rather
        than chosen:

        * ``_kiro.dev/session/update`` exists as a method *distinct* from
          ``session/update``, carries a ``sessionId`` and a
          ``tool_call_chunk``, and falls through this function's dispatch
          entirely. It is genuine agent liveness. A method allowlist naming
          ``session/update`` and ``METADATA_METHOD`` would miss it.
        * At least six ``sessionUpdate`` kinds exist and only three have
          branches here, so a turn emitting nothing but ``agent_thought_chunk``
          or ``plan`` for the silence window would be judged silent and
          cancelled — a working turn killed by its own ceiling.
        * ``_kiro.dev/subagent/list_update`` carries **no** ``sessionId`` at
          all, which is why the null path below is a real case rather than
          defensive padding.

        This advances ``last_activity`` and deliberately **not** ``last_used``.
        They answer opposed questions — "is the agent still working?" versus
        "has nobody used this session?" — and sharing one field would let a
        chatty agent keep its own sessions permanently unsweepable, defeating
        the sweeper with no error anywhere.

        ``time.monotonic`` and not ``time.time``: both readers compare elapsed
        intervals and must not be moved by a clock adjustment. That does put
        two clocks in one record — ``created`` is wall-clock and is only ever
        rendered, never subtracted.
        """
        if not isinstance(session_id, str):
            return
        meta = self.sessions.get(session_id)
        if meta is None:
            # Closed, or detached when the agent died. Never recreate the
            # record: `at_capacity()` would count the resurrected entry against
            # MAX_SESSIONS forever and the sweeper would re-issue terminate for
            # it every tick. `record()` and `_note_context()` model the idiom.
            return
        meta["last_activity"] = time.monotonic()

    def touch_used(self, session_id) -> None:
        """Record that a *person* used this session. Resets the sweeper clock.

        Advanced by a prompt and by a subscriber attaching or detaching —
        never by an agent notification, for the reason ``_stamp_activity``
        gives. Same non-resurrecting write, same monotonic clock.
        """
        if not isinstance(session_id, str):
            return
        meta = self.sessions.get(session_id)
        if meta is None:
            return
        meta["last_used"] = time.monotonic()

    def _on_subagent_list(self, params: dict) -> None:
        """Handle one ``_kiro.dev/subagent/list_update`` notification.

        Carries no ``sessionId`` of its own (measured — see
        ``SUBAGENT_LIST_METHOD``'s docstring and ``_stamp_activity``'s P0-4),
        so the crew it describes has to be attributed some other way. A
        fan-out can only originate from a running turn, so with **exactly
        one** session mid-``session/prompt`` the attribution is unambiguous;
        with zero or more than one, there is no honest answer and this file's
        rule is to say nothing rather than guess (the same rule
        ``METADATA_METHOD``'s neighbouring comment states for a different
        notification) — the crew silently does not appear rather than
        appearing on the wrong session's bar. Two (or more) sessions each
        running their own fan-out at once is the one case this cannot cover;
        it is left as a known gap rather than a guess.
        """
        subs = params.get("subagents")
        if not isinstance(subs, list):
            return
        inflight = self.inflight
        if len(inflight) != 1:
            log.debug("ACP subagent_list: %d session(s) in flight, cannot "
                      "attribute %d entries; dropped", len(inflight), len(subs))
            return
        parent_id = next(iter(inflight))
        crew = self.crews.setdefault(parent_id, {})
        changed = False
        for entry in subs:
            if not isinstance(entry, dict):
                continue
            child_id = _as_text(entry.get("sessionId"))
            if not child_id:
                continue
            existing = crew.get(child_id)
            if existing is not None and existing["done"]:
                # Terminal is sticky (Q&A, 2026-08-11: "stay, marked done") —
                # a crew entry that finished must never un-finish because a
                # stale or reordered notification repeats an earlier status.
                continue
            role = _first_text(entry, _SUBAGENT_ROLE_KEYS)
            task = _first_text(entry, _SUBAGENT_TASK_KEYS)[:MAX_SUBAGENT_TASK_CHARS]
            if not role and not task and existing is None:
                # kiro-cli sometimes announces a slot before it has anything
                # to say about it — corroborated by kirocrew's own
                # `_native_subagent_sync`, which skips exactly this case
                # rather than showing a card with nothing on it. Wait for a
                # later update to name it.
                continue
            status = entry.get("status")
            stype = str(status.get("type") or "").lower() if isinstance(status, dict) else ""
            smsg = str(status.get("message") or "") if isinstance(status, dict) else ""
            done = bool(stype) and stype not in _SUBAGENT_ACTIVE_STATUSES
            # `stype in ("failed", "error")` is unreachable against every
            # vocabulary measured 2026-08-11 against kiro-cli 2.16.2: every
            # terminal entry captured — including a stage whose own command
            # genuinely failed — reported `stype == "terminated"` with no
            # `message` at all. Kept rather than removed: a future kiro-cli
            # build, or a failure mode this app has not exercised (the
            # sub-agent's own session crashing, rather than a tool call
            # erroring inside an otherwise-normal turn), may still use it.
            error = (smsg[:MAX_ERROR_DETAIL_CHARS]
                     if done and stype in ("failed", "error") and smsg
                     else (existing["error"] if existing else ""))
            updated = {
                "role": role or (existing["role"] if existing else ""),
                "task": task or (existing["task"] if existing else ""),
                "status": stype or (existing["status"] if existing else ""),
                "action": existing["action"] if existing else "",
                "done": done,
                "error": error,
                "order": existing["order"] if existing else len(crew),
                "startedAt": existing["startedAt"] if existing else time.time(),
                # Preserve an already-set stoppedAt (e.g. from a cancel cascade
                # that ran before this list update arrived); only stamp now when
                # transitioning to done for the first time.  This is the same
                # rule as ``_mark_crew_done``, applied per-entry here because
                # this function builds the full entry dict from a wire message.
                "stoppedAt": (
                    existing["stoppedAt"]
                    if (existing and existing.get("stoppedAt"))
                    else (time.time() if done else None)
                ),
            }
            if updated != existing:
                changed = True
            crew[child_id] = updated
            if child_id not in self.subagent_sessions:
                self.subagent_sessions[child_id] = {"parent": parent_id}
                self.subagent_history[child_id] = _History()
                changed = True
        if not changed:
            return
        self._evict_finished_subagents(parent_id)
        _emit_subagents_frame(parent_id)

    def _note_subagent_action(self, child_id: str, title: str) -> None:
        """Record a sub-agent's latest tool title as its crew card's action.

        The only source this file has for "what is this sub-agent doing right
        now" — ``SUBAGENT_ACTIVITY_METHOD``'s ``tool_call_chunk`` carries a
        title and nothing more structured. Silent no-op for an id
        ``_on_subagent_list`` never registered, or whose entry already
        finished: the crew card is frozen the instant it is marked done, the
        same rule ``_on_subagent_list`` applies to the fields it owns.
        """
        if not title:
            return
        meta = self.subagent_sessions.get(child_id)
        if meta is None:
            return
        entry = self.crews.get(meta["parent"], {}).get(child_id)
        if entry is None or entry["done"] or entry["action"] == title:
            return
        entry["action"] = title
        _emit_subagents_frame(meta["parent"])

    def _evict_finished_subagents(self, parent_id: str) -> None:
        """Keep one session's remembered crew under MAX_SUBAGENTS_PER_SESSION.

        Evicts the OLDEST *finished* entries first (by arrival order) and
        never a still-running one — sub-agent sessions are exempt from
        MAX_SESSIONS by design (Q&A, 2026-08-11), but exempt must not mean
        unbounded. One fan-out's own stage count sits far under this; only a
        session that has run through many fan-outs over a long life reaches
        it, and even then only its finished stages are ever reclaimed.
        """
        crew = self.crews.get(parent_id)
        if crew is None or len(crew) <= MAX_SUBAGENTS_PER_SESSION:
            return
        finished = sorted(
            (cid for cid, e in crew.items() if e["done"]),
            key=lambda cid: crew[cid]["order"])
        overflow = len(crew) - MAX_SUBAGENTS_PER_SESSION
        for child_id in finished[:overflow]:
            crew.pop(child_id, None)
            self.subagent_sessions.pop(child_id, None)
            self.subagent_history.pop(child_id, None)
            _bubbles.pop(child_id, None)
            frame = _session_closed_frame(child_id)
            for target in tuple(_registry.subscribers.get(child_id, ())):
                target.send(frame)
                _registry.detach(target)

    def _on_notification(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        session_id = params.get("sessionId")
        # Above every branch below, including the fall-through at the end.
        self._stamp_activity(session_id)
        if method == METADATA_METHOD:
            percent = _context_percent(params)
            if percent is not None and isinstance(session_id, str):
                _note_context(session_id, percent)
            return
        if method == SUBAGENT_LIST_METHOD:
            self._on_subagent_list(params)
            return
        if kind in ("agent_message_chunk", "user_message_chunk"):
            # `user_message_chunk` is what makes a loaded conversation a
            # conversation: `session/load` replays both halves of it, and
            # without this arm the transcript would come back as the agent
            # talking to itself. It is not a second source for a live turn —
            # `_handle_prompt` emits the user's own text, and no
            # `user_message_chunk` is emitted during `session/prompt`. First
            # measured on 2.14.2; **re-measured on 2.16.0, 2026-08-03** — zero
            # across a driven turn. A build that started emitting one would render
            # the prompt twice, which is the thing to look for if that appears.
            role = "user" if kind == "user_message_chunk" else "agent"
            content = update.get("content")
            # The nested shape first, a flat `text` field as fallback rather
            # than the other way round: an *empty* nested `content.text` must
            # not shadow a populated flat one. Never observed on either
            # channel — measured 2026-08-11 against kiro-cli 2.16.2, every
            # `agent_message_chunk` captured (parent and child alike) carried
            # the nested object, and none arrived on SUBAGENT_ACTIVITY_METHOD
            # at all (see that constant's comment: it only ever carried
            # `tool_call_chunk` there). The flat fallback was corroborated
            # only against kirocrew's dual-shape handling, never confirmed
            # against this app's own traffic; kept as defensive coverage for
            # a shape that may still exist on a build or channel not yet
            # captured. One `or` covers both without a second branch.
            text = _content_text(content) or _as_text(update.get("text"))
            if role == "user":
                # An image-only turn replays as nothing at all: every block in
                # it is an image, and `_content_text` yields "" for a block
                # with no `text` key. That made `if text` false, which cost two
                # things rather than one — the `chunk` frame, and the
                # `_flush_bubble` below it. Losing the flush is the worse half:
                # in a `session/load` replay it is the *only* thing separating
                # one answer from the next, so the agent's reply either side of
                # an image-only turn merged into a single bubble.
                #
                # Naming the images makes the turn non-empty, and names them
                # exactly as the live path did when the prompt was sent, so a
                # loaded conversation and a replayed one read the same.
                #
                # Deliberately not applied to the agent arm: nothing measured
                # has an agent sending image blocks, and inventing a marker for
                # one would put a label in the transcript that stands for
                # nothing the reader can check.
                text = _with_image_markers(text, _content_image_count(content))
            if text and isinstance(session_id, str):
                if role == "user":
                    # A user chunk closes the agent bubble on the page —
                    # `appendChunk` hands any non-agent role to `addMessage`
                    # and nulls `agentBody`. Live turns reach that boundary
                    # through `_handle_prompt` instead; this arm is the
                    # `session/load` replay, where a whole conversation of
                    # alternating chunks arrives with no turn markers at all
                    # and this is the *only* thing separating one answer from
                    # the next.
                    _flush_bubble(session_id)
                _emit(session_id, envelope(
                    "chunk", {"role": role, "text": text}, session_id))
                if role == "agent":
                    _bubble_append(session_id, text)
            return
        if kind in ("tool_call", "tool_call_update"):
            payload = _tool_payload(update)
            log.info("ACP tool %s: session=%s id=%s status=%s title=%r kind=%s "
                     "input=%.200r", kind, session_id, payload["toolCallId"],
                     payload["status"], payload["title"], payload["kind"],
                     payload["command"])
            if isinstance(session_id, str):
                if kind == "tool_call":
                    # A tool call ends the open agent bubble (`addToolCall`
                    # nulls `agentBody`), so the prose either side of it is
                    # parsed as two documents. `tool_call_update` does *not*:
                    # it rewrites the row its id already opened and leaves the
                    # bubble alone, so flushing on one would split a bubble the
                    # page never split.
                    _flush_bubble(session_id)
                elif not (payload["title"] or payload["kind"] or
                          payload["status"] or payload["command"]):
                    # `tool_call_update` can arrive in two shapes for the same
                    # `toolCallId`: measured 2026-08-11 against a real kiro-cli
                    # 2.16.2 subprocess, every call got an optional
                    # intermediate update carrying only `content` (the tool's
                    # streamed output) — a shape `_tool_payload` does not read
                    # at all — followed by a terminal one with
                    # `status`/`rawOutput`. Forwarding the intermediate one
                    # would emit a `tool_update` with every field blank, which
                    # could flash an already-populated row empty a moment
                    # before the real state lands. Skipped rather than sent:
                    # nothing informative would have reached the page anyway.
                    return
                # Rendered, not only logged. `-a` removes the permission gate
                # and the justification for removing it was a human watching
                # the run; a tool call that reaches nothing but a log file the
                # app does not always write is not something anyone is
                # watching. A `shell` call was observed writing outside its
                # own session's cwd with the operator seeing none of it.
                _emit(session_id, envelope(
                    "tool_call" if kind == "tool_call" else "tool_update",
                    payload, session_id))
            return
        if kind == "tool_call_chunk":
            # A sub-agent's own tool call, on SUBAGENT_ACTIVITY_METHOD rather
            # than plain `session/update` (see that constant's docstring) —
            # `session_id` here is the CHILD's, not the parent's. Narrower
            # payload than the top-level `tool_call` shape: only `toolCallId`/
            # `title` observed, no `kind`/`status`/`rawInput` — but
            # `_tool_payload` already defaults every absent field to `""`, so
            # reusing it unmodified is safe rather than approximate.
            #
            # Gated on `subagent_sessions` membership (unlike the
            # `agent_message_chunk` arm below, which needs no such gate — its
            # dispatch already no-ops for an unregistered id via `record`/
            # `broadcast`): only this arm also mutates crew state
            # (`_note_subagent_action`), and mutating a crew entry for an id
            # `_on_subagent_list` never registered would create one
            # `_evict_finished_subagents` and `close_session` do not know how
            # to find again.
            if isinstance(session_id, str) and session_id in self.subagent_sessions:
                payload = _tool_payload(update)
                _flush_bubble(session_id)
                _emit(session_id, envelope("tool_call", payload, session_id))
                self._note_subagent_action(session_id, payload["title"])
            return
        if kind == "agent_thought_chunk":
            # Never observed: the 2026-08-03 latency benchmark measured zero
            # of these across 1,200 runs on every Claude and Qwen model with
            # every thinking configuration tried (plans/ROADMAP.md). Handled
            # anyway rather than left to the debug fall-through below, on the
            # chance it is model- or config-gated rather than universally
            # absent — the client's "Thinking…" indicator (`acp.html`) already
            # covers the silence this would otherwise fill, so a build that
            # never sends it costs this branch nothing.
            text = _content_text(update.get("content"))
            if text and isinstance(session_id, str):
                # Ends the open bubble for the same reason a tool call does:
                # this is not a continuation of the agent's prose, and mixing
                # it into `agentBody` would parse two documents as one.
                _flush_bubble(session_id)
                _emit(session_id, envelope("thought", {"text": text}, session_id))
            return
        if log.isEnabledFor(logging.DEBUG):
            # Params and not only the method name. This module talks to an
            # undocumented protocol: `_kiro.dev/*` is not in the ACP spec at
            # all, and the context-window branch above exists only because a
            # line like this one showed what those notifications carry. Guarded
            # rather than lazily formatted because the `json.dumps` would
            # otherwise run on every unmatched notification at every log level.
            log.debug("ACP notification %s (%s): %.600s",
                      method, kind or "-", json.dumps(params))

    def _on_agent_death(self, proc: subprocess.Popen) -> None:
        """The reader thread ended: the channel is gone. Runs on the loop."""
        if proc is not self._proc:
            # A reader belonging to a process we already replaced or tore down
            # deliberately. Not news.
            return
        code = proc.poll()
        log.error("ACP agent channel closed (exit code %r); %d session(s) lost",
                  code, len(self.sessions))
        # Releases the job handle as well as the process. The parent is gone but
        # its ~5 MCP grandchildren need not be, and closing the last handle is
        # what kills them — otherwise a dead agent leaves its tree resident
        # until PowerAtlas itself exits.
        self._discard("The agent stopped answering; its channel closed.")
        frame = envelope("agent_died", {
            "exitCode": code,
            "message": "The kiro-cli agent exited. Create a new session to "
                       "start another one.",
        })
        for conn in tuple(_registry.connections):
            conn.send(frame)

    # -- sessions ----------------------------------------------------------

    def at_capacity(self) -> bool:
        """Whether another session would exceed ``MAX_SESSIONS``.

        The cap's one home, so the two frame handlers can consult it *before*
        spending anything on a session that is refused either way — a
        filesystem resolve of a path the client chose, a lock read, an agent
        round-trip. Reservations count alongside recorded sessions, because
        creating one spans two awaits and the cap has to hold across them.
        """
        return len(self.sessions) + self._reserved >= MAX_SESSIONS

    async def new_session(self, cwd: str) -> dict:
        """Create one session, never exceeding ``MAX_SESSIONS``.

        The cap is taken as a *reservation* before the first ``await``, not as a
        reading of ``len(self.sessions)`` that the two awaits below then
        invalidate. Creating a session spans ``ensure_started`` and a
        ``session/new`` round-trip of several seconds; N concurrent ``new``
        frames — which ``_dispatch`` happily turns into N tasks — all used to
        pass a check-then-act test before any of them recorded anything, so N
        sessions were created whatever the cap said. That is not a cosmetic
        overshoot: this cap is the only thing between one socket and memory
        exhaustion at the per-session cost ``plans/ROADMAP.md`` records, and
        every excess session is a permanent artifact in the user's real
        kiro-cli store.

        Incrementing and decrementing without suspending in between is what
        makes the reservation atomic: the event loop cannot interleave another
        ``new_session`` between the check and the increment, nor between
        recording the session and releasing its slot.
        """
        if self.at_capacity():
            raise SessionLimit(_session_limit_message())
        self._reserved += 1
        try:
            await self.ensure_started()
            result = await self._request("session/new", {"cwd": cwd, "mcpServers": []})
            result = result or {}
            session_id = result.get("sessionId")
            if not _valid_session_id(session_id):
                # The agent's id goes through the same gate a client-supplied
                # one does. It is written straight back into ``?sid=``, so a
                # reload after a restart hands it to ``load`` — which joins it
                # into ``KIRO_SESSION_DIR`` and therefore refuses anything this
                # would have admitted, leaving the page holding an id it can
                # never reopen.
                raise AgentRejected(
                    "The agent returned an unusable sessionId: "
                    f"{session_id!r:.200}")
            self.sessions[session_id] = _new_session_record(cwd)
            self._publish_live()
            self.history[session_id] = _History()
        finally:
            # Every path releases the slot, including cancellation: the session
            # it stood for is either recorded above (and counted by `sessions`
            # from now on) or never existed.
            self._reserved -= 1
        log.info("ACP session created: %s (cwd %s); %d live",
                 session_id, cwd, len(self.sessions))
        return {"sessionId": session_id, "cwd": cwd}

    async def load_session(self, session_id: str, cwd: str) -> dict:
        """Adopt a session that exists in the agent's store but not here.

        The session is registered **before** the round-trip rather than after
        it. ``session/load`` is answered by replaying the whole conversation as
        ``session/update`` notifications *while the request is still
        outstanding*, and ``record`` silently drops any frame whose session has
        no buffer — so registering afterwards would return a session whose
        history is empty for exactly the reason the load existed. What stops
        that early registration also handing the replay to a socket is
        ``_registry.loading``, held by ``_handle_load`` across this whole call.

        The reservation is released the instant the session is recorded rather
        than at the end, because from that instant ``sessions`` counts it.
        Holding both counted the loading session twice: measured, with one
        session live, a concurrent ``new`` was refused ``too_many_sessions``
        while only two existed.

        Every failure path unregisters it again, including cancellation: a
        half-loaded session left in ``sessions`` would be counted against
        MAX_SESSIONS and answered by ``subscribe`` with an empty transcript.
        """
        if self.at_capacity():
            raise SessionLimit(_session_limit_message())
        self._reserved += 1
        reserved = True
        try:
            await self.ensure_started()
            if session_id in self.sessions:
                # A concurrent `load` for the same id got here first — or
                # `ensure_started` spawned a replacement and something else
                # populated it. Either way its buffer is the better answer than
                # a second agent-side replay appended to the first, so hand the
                # caller the live record and let it subscribe. Refusing here
                # instead left the loser an error frame it could not act on.
                live = self.sessions[session_id]
                return {"sessionId": session_id, "cwd": live.get("cwd", cwd)}
            self.sessions[session_id] = _new_session_record(cwd)
            self._publish_live()
            self.history[session_id] = _History()
            # Recorded, so the slot it reserved is now counted by `sessions`.
            # Released without suspending in between, which is what makes the
            # handover atomic against another `new_session`'s check.
            self._reserved -= 1
            reserved = False
            try:
                await self._request(
                    "session/load",
                    {"sessionId": session_id, "cwd": cwd, "mcpServers": []})
            except BaseException:
                self.sessions.pop(session_id, None)
                self._publish_live()
                self.history.pop(session_id, None)
                raise
        finally:
            if reserved:
                self._reserved -= 1
        history = self.history.get(session_id)
        log.info("ACP session loaded: %s (cwd %s, %d event(s) replayed); %d live",
                 session_id, cwd, 0 if history is None else len(history),
                 len(self.sessions))
        return {"sessionId": session_id, "cwd": cwd}

    def record(self, session_id: str, frame: dict) -> None:
        """Append a frame to a session's replay buffer, if it still has one.

        Checks ``subagent_history`` as well as ``history`` — the two are
        disjoint (a session id is never in both), so this is one extra dict
        lookup on the common path rather than a second call site to keep in
        sync with the first.
        """
        history = self.history.get(session_id)
        if history is None:
            history = self.subagent_history.get(session_id)
        if history is not None:
            history.append(frame)

    async def prompt(self, session_id: str, text: str,
                     images: list[dict] | None = None) -> dict:
        """Run one turn. Returns the agent's ``{"stopReason": …}``.

        The answer does not come back through here — it arrives as
        ``session/update`` notifications while this is still awaiting, which is
        the whole reason the page sees text progressively. What this returns is
        only the turn's end.
        """
        if session_id not in self.sessions:
            raise AgentRejected("That session no longer exists on this agent.")
        if not self.alive():
            raise AgentDied("The agent is not running.")
        # Both ends of the turn count as use. The start is obvious; the end
        # matters because a turn can legitimately outlast the idle TTL, and
        # stamping only at the start would have the sweeper reclaim a session
        # the instant a long task finished — before the person who left it
        # running could come back to read the answer. Neither stamp is
        # agent-driven: they fire once per prompt a person sent, so no amount
        # of agent chatter can push them.
        self.touch_used(session_id)
        try:
            # The text block first and the images after it, which is the order
            # they were pasted in and the order `[Image N]` numbers them in.
            #
            # `images` defaults to None rather than `[]` so a text-only turn
            # builds the exact single-element array this sent before images
            # existed — the wire shape is asserted verbatim by a test, and a
            # bare `[]` extension would have changed nothing visible while
            # leaving two ways to express the same prompt.
            blocks: list[dict] = [{"type": "text", "text": text}]
            blocks.extend(images or ())
            result = await self._request(
                "session/prompt",
                {"sessionId": session_id, "prompt": blocks},
                timeout=_INACTIVITY,
            )
        finally:
            self.touch_used(session_id)
        return result or {}

    async def cancel(self, session_id: str) -> None:
        """Ask the agent to end the turn in flight on a session.

        Nothing here waits for the turn to end, and nothing here ends it: the
        outstanding ``session/prompt`` is what returns, with
        ``stopReason: "cancelled"``, and ``_handle_prompt``'s own ``finally``
        is what emits the turn boundary the page reads. Ending the turn from
        this side as well would race that task into emitting two.
        """
        if session_id not in self.sessions:
            raise AgentRejected("That session no longer exists on this agent.")
        if not self.alive():
            raise AgentDied("The agent is not running.")
        await self._notify("session/cancel", {"sessionId": session_id})

    async def steer(self, session_id: str, text: str) -> dict:
        """Inject a mid-turn steer message via ``_session/steer``.

        ``_session/steer`` is a JSON-RPC **request** (has ``id``, returns
        ``{"result": {"queued": true}}``). Verified by live probe 2026-08-12
        against kiro-cli 2.16.x: answered in milliseconds; never a
        notification.
        """
        if session_id not in self.sessions:
            raise AgentRejected("That session no longer exists on this agent.")
        if not self.alive():
            raise AgentDied("The agent is not running.")
        return await self._request(
            "_session/steer",
            {"sessionId": session_id, "message": text},
        ) or {}

    async def close_session(self, session_id: str) -> None:
        """Release one session on the agent, and everything it holds here.

        The local state is dropped **only after** the agent has answered. Each
        session costs ~3 processes and the memory ``plans/ROADMAP.md`` records,
        all of it inside the agent rather than here, so dropping our own record
        of one the agent still holds would report a memory saving that did not
        happen — and would leave those processes unreachable for the agent's
        whole life, since nothing else names a session.
        """
        if session_id not in self.sessions:
            raise AgentRejected("That session no longer exists on this agent.")
        if not self.alive():
            raise AgentDied("The agent is not running.")
        await self._request(CLOSE_METHOD, {"sessionId": session_id})
        self.sessions.pop(session_id, None)
        self._publish_live()
        # The ring buffer goes with it. It is keyed by session id and nothing
        # else reaches it, so a buffer left behind here is up to
        # HISTORY_MAX_BYTES resident for the app's lifetime with no path that
        # could ever read or evict it.
        self.history.pop(session_id, None)
        self.inflight.discard(session_id)
        _bubbles.pop(session_id, None)
        # This session's crew, if it ever dispatched a fan-out. A sub-agent's
        # own history/bubble go with it — nothing else can ever name a child
        # sid once its parent's row is what would have shown a "click to open"
        # affordance for it — and any socket still viewing one is told and
        # detached, the same notice and the same pattern `_handle_close` and
        # `_sweep_once` use for a real session's own subscribers.
        for child_id in self.crews.pop(session_id, ()):
            self.subagent_sessions.pop(child_id, None)
            self.subagent_history.pop(child_id, None)
            _bubbles.pop(child_id, None)
            frame = _session_closed_frame(child_id)
            for target in tuple(_registry.subscribers.get(child_id, ())):
                target.send(frame)
                _registry.detach(target)
        log.info("ACP session closed: %s; %d live", session_id, len(self.sessions))


_supervisor = _Supervisor()


def _emit(session_id: str, frame: dict) -> None:
    """Record a frame in a session's history and fan it out to its sockets.

    Recording first is what makes a replay complete: a socket attaching between
    the two halves would otherwise miss the frame in both directions — too late
    for the fan-out, too early for the buffer. Nothing can actually attach
    there, because both halves are synchronous, but the order costs nothing and
    does not depend on that staying true.
    """
    _supervisor.record(session_id, frame)
    _registry.broadcast(session_id, frame)


def _first_text(entry: dict, keys: tuple) -> str:
    """The first non-empty string among ``entry``'s candidate key names.

    Sub-agent list entries use more than one name for the same fact across
    kiro-cli builds — ``role``/``agentName``, ``initialQuery``/``sessionName``
    — corroborated against kirodotdev/kirocrew's ``_native_subagent_sync``,
    which reads both for the same reason.
    """
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _subagents_payload(crew: dict) -> list:
    """A crew dict as the ``subagents`` frame's wire list, arrival-ordered."""
    return [
        {
            "sessionId": child_id,
            "role": entry["role"],
            "task": entry["task"],
            "status": entry["status"],
            "action": entry["action"],
            "done": entry["done"],
            "error": entry["error"],
            "startedAt": entry.get("startedAt"),
            "stoppedAt": entry.get("stoppedAt"),
        }
        for child_id, entry in sorted(crew.items(), key=lambda kv: kv[1]["order"])
    ]


def _emit_subagents_frame(parent_id: str) -> None:
    """Push the current crew snapshot to a session's subscribers.

    Deliberately **not** recorded into the replay buffer via ``_emit`` —
    like ``turnActive``/``contextPercent`` on the ``session`` frame, this is
    current derived state rather than a transcript event. ``_handle_subscribe``
    rebuilds it fresh from ``crews`` on every attach instead, which is both
    cheaper than replaying every incremental update that produced it and more
    current — the last one is always the whole truth, so only the last one is
    worth keeping.
    """
    crew = _supervisor.crews.get(parent_id)
    if not crew:
        return
    _registry.broadcast(parent_id, envelope(
        "subagents", {"subagents": _subagents_payload(crew)}, parent_id))


# session id -> the agent prose accumulated for the **bubble** currently open on
# the page, in the order it was streamed. Not per turn: a bubble is what the
# page's `agentBody` tracks, and it is closed by a tool call as well as by a
# turn boundary. Markdown is therefore parsed per bubble, so a fence the agent
# opened before a tool call and closed after it is two unterminated fences —
# which is correct, because that is exactly what the reader saw.
#
# A plain dict on the event loop. Every writer below runs there: the reader
# thread hands notifications over with `call_soon_threadsafe`, and the two
# handler call sites are coroutines the loop drives. Dropped for a session by
# `_Supervisor.close_session` and for every session by `_Supervisor._detach`,
# which is the only other place a session's buffered state is released.
_bubbles: dict[str, list[str]] = {}


def _bubble_append(session_id: str, text: str) -> None:
    """Record agent prose against the bubble the page currently has open."""
    _bubbles.setdefault(session_id, []).append(text)


def _flush_bubble(session_id: str) -> None:
    """Close the open bubble and emit what its markdown parses to.

    Called immediately **before** every frame that closes a bubble on the page,
    because the client applies a ``rendered`` frame to whatever body is open
    when it arrives. The four boundaries are the ones the page itself uses: a
    ``tool_call``, a user chunk, a turn start, and a turn end. A turn that was
    cancelled or that failed still passes through the last of those, so what
    arrived before the failure is still rendered.

    Emits nothing at all when there is nothing to render, when mistune is
    absent, or when parsing raises — in each case the bubble keeps the plain
    text the chunks already put there, which is the pre-existing behaviour of
    this whole page. A rendering is an upgrade to a transcript that is already
    correct, so no failure here may cost the transcript.
    """
    parts = _bubbles.pop(session_id, None)
    if not parts or _markdown is None:
        return
    text = "".join(parts)
    if not text.strip():
        return
    if len(text) > MAX_BUBBLE_CHARS:
        log.info("ACP markdown: %d chars in one bubble is over the %d cap; "
                 "session=%s stays plain text", len(text), MAX_BUBBLE_CHARS,
                 session_id)
        return
    try:
        tokens = _markdown(text)
    except Exception:
        # Never fatal. This runs inside `_on_notification` and inside
        # `_handle_prompt`'s `finally`; raising in the second would replace a
        # turn's end marker with a traceback and leave the page's Send button
        # disabled for good.
        log.exception("ACP markdown: parsing a bubble failed; session=%s",
                      session_id)
        return
    if not isinstance(tokens, list) or not tokens:
        return
    _emit(session_id, envelope("rendered", {"tokens": tokens}, session_id))


def _note_context(session_id: str, percent: float) -> None:
    """Record and fan out how full a session's context window is.

    Broadcast rather than ``_emit``: this is a *level*, not an event, and the
    only reading worth anything is the latest one. Recording each into the ring
    buffer would spend an eviction on every turn to replay a number that a
    later frame has already superseded — and the buffer would then be the only
    place the page could learn it, which is the one place designed to lose it.
    Kept on the session's own metadata instead, from where ``subscribe`` reads
    it back onto every reconnecting socket.
    """
    meta = _supervisor.sessions.get(session_id)
    if meta is None:
        return
    meta["contextPercent"] = percent
    _registry.broadcast(session_id, envelope(
        "meta", {"contextPercent": percent}, session_id))


def shutdown() -> None:
    """Tear the agent down. Called from ``web.py``'s ``lifespan`` cleanup."""
    _supervisor.shutdown()


async def serve_socket(ws: WebSocket) -> None:
    """Own an accepted ``/ws/acp`` socket for its whole lifetime.

    ``web.py`` validates the token and the origin, accepts, and hands the
    socket here without ever reading a frame's ``type``. Keeping the router
    opaque is what lets later phases add message types without touching it.
    """
    conn = _Connection(ws)
    if len(_registry.connections) >= MAX_CONNECTIONS:
        # Enforced after accept(), not before: the two handshake rejections in
        # web.py are security checks and must stay the first thing that
        # happens on a socket. A policy close also carries a readable reason
        # where a 403 handshake rejection would carry none.
        await ws.send_text(json.dumps(error_frame(
            "too_many_connections",
            f"At most {MAX_CONNECTIONS} /acp sockets may be open at once.")))
        await ws.close(code=1013, reason="too many connections")
        return

    _registry.connections.add(conn)
    conn.start()
    conn.send(envelope("meta", {
        "connected": True,
        "maxMessageBytes": MAX_MESSAGE_BYTES,
        "maxConnections": MAX_CONNECTIONS,
        # The image budget travels rather than being written into the page,
        # because the page has to ration itself against the *same* number this
        # module enforces. A copy in the template would be a second source free
        # to drift, and the direction it would drift in is the bad one: a page
        # believing the cap is higher than it is sends a prompt that is refused
        # after the user has already spent the effort staging it.
        "maxPromptImages": MAX_PROMPT_IMAGES,
        "maxPromptImageBytes": MAX_PROMPT_IMAGE_BYTES,
    }))
    log.info("ACP socket %s open (%d/%d)", conn.cid,
             len(_registry.connections), MAX_CONNECTIONS)

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            raw = message.get("text")
            if raw is None:
                conn.send(error_frame(
                    "binary_unsupported", "Frames must be UTF-8 JSON text."))
                continue
            if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                # Carried as a close code rather than an `error` frame: the
                # frame would be queued behind the writer task while the close
                # is awaited here, so the client would routinely see the close
                # first. 1009 is the standard code for exactly this and its
                # reason string reaches the browser's onclose handler.
                await conn.drain()
                await conn.stop()
                await ws.close(code=1009, reason="message too large")
                break
            try:
                frame = json.loads(raw)
            except ValueError:
                conn.send(error_frame("bad_json", "Frame is not valid JSON."))
                continue
            if not isinstance(frame, dict):
                conn.send(error_frame("bad_envelope", "Frame must be a JSON object."))
                continue
            _dispatch(conn, frame)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        _registry.detach(conn)
        _registry.connections.discard(conn)
        # Drain before stop, in that order, on every exit from this function:
        # `stop()` cancels the writer and whatever is still queued dies with
        # it. That is the tail of a streamed response when the server initiates
        # the close, and it is a no-op costing nothing when the client already
        # went away — the writer's first failed send ends the wait.
        await conn.drain()
        await conn.stop()
        log.info("ACP socket %s closed (%d open)", conn.cid,
                 len(_registry.connections))


def _dispatch(conn: _Connection, frame: dict) -> None:
    """Validate an inbound envelope and route it by ``type``."""
    type_ = frame.get("type")
    session_id = frame.get("sessionId")
    if not isinstance(type_, str) or not type_:
        conn.send(error_frame(
            "bad_envelope", "Frame needs a non-empty string 'type'."))
        return
    if session_id is not None and not isinstance(session_id, str):
        conn.send(error_frame(
            "bad_envelope", "'sessionId' must be a string or null."))
        return
    if not isinstance(frame.get("payload", {}), dict):
        conn.send(error_frame(
            "bad_envelope", "'payload' must be an object.", session_id))
        return
    if type_ not in CLIENT_TYPES:
        conn.send(error_frame(
            "unknown_type", f"Unknown client frame type '{type_}'.", session_id))
        return
    payload = frame.get("payload") or {}
    if type_ == "new":
        _spawn_task(_handle_new(conn, payload))
        return
    if type_ == "subscribe":
        _handle_subscribe(conn, session_id)
        return
    if type_ == "load":
        _spawn_task(_handle_load(conn, session_id))
        return
    if type_ == "prompt":
        _spawn_task(_handle_prompt(conn, session_id, payload))
        return
    if type_ == "cancel":
        _spawn_task(_handle_cancel(conn, session_id))
        return
    if type_ == "steer":
        _spawn_task(_handle_steer(conn, session_id, payload))
        return
    if type_ == "close":
        _spawn_task(_handle_close(conn, session_id))
        return
    # Every member of CLIENT_TYPES is routed above, so reaching here means one
    # was declared and never wired — a server bug, and one that would otherwise
    # present as a control the page draws and the server silently ignores.
    log.error("ACP: client frame type '%s' is declared but not routed", type_)
    conn.send(error_frame(
        "not_implemented",
        f"'{type_}' is a declared frame type this server does not route.",
        session_id))


def _handle_subscribe(conn: _Connection, session_id: str | None) -> None:
    """Attach this socket to an existing session and replay its buffer.

    **This function must not grow an ``await``.** Attaching and queueing the
    replay with nothing suspending in between is atomic against the event loop,
    so no live event can be broadcast between the two — which is exactly what
    would deliver it twice, once in the replay and once live. That property is
    what stands in for an explicit replay cursor; an ``await`` anywhere between
    ``attach`` and the ``history`` frame reintroduces the window and would need
    a real cursor to close it again.

    The replay is **one** frame carrying every event, not one frame per event.
    ``SEND_QUEUE_MAXSIZE`` is 256 and a full queue makes ``_Connection.send``
    retire the socket — so replaying a HISTORY_MAXLEN buffer event by event
    would kill the very socket the replay exists to serve, and would do it
    only for the sessions with enough history to be worth replaying.

    A session mid-``session/load`` is parked rather than attached, for the same
    reason: see ``_defer_until_loaded``.
    """
    if not session_id:
        conn.send(error_frame(
            "bad_envelope", "'subscribe' needs a sessionId."))
        log.warning("ACP subscribe refused: [bad_envelope] no sessionId")
        return
    sub_meta = _supervisor.subagent_sessions.get(session_id)
    if sub_meta is not None:
        # A sub-agent's own session id, not a real one — `_registry.loading`
        # never carries one of these (nothing ever `session/load`s a
        # sub-agent), so that check does not apply here.
        _handle_subagent_subscribe(conn, session_id, sub_meta)
        return
    if session_id in _registry.loading:
        _defer_until_loaded(conn, session_id)
        return
    meta = _supervisor.sessions.get(session_id)
    if meta is None:
        conn.send(error_frame(
            "unknown_session",
            "This server has no such live session. It may belong to an "
            "earlier PowerAtlas process — create a new one.", session_id))
        log.warning("ACP subscribe refused: [unknown_session] session=%s",
                    session_id)
        return
    if session_id in _supervisor.closing:
        # A release is in flight, and `close_session` leaves the session in
        # `sessions` for the whole terminate round-trip. Without this the attach
        # below would stamp `last_used` on a record about to be popped and
        # replay a whole transcript the close tears down a moment later — the
        # broadcast does reach this socket, but only after it has been shown a
        # session that was already gone. Same code and same wording as
        # `_handle_load`'s guard: both entry points refuse a session mid-close.
        conn.send(error_frame(
            "close_in_progress",
            "This session is being released. Wait a moment and load it "
            "again.", session_id))
        log.warning("ACP subscribe refused: [close_in_progress] session=%s",
                    session_id)
        return
    # Below the three refusals rather than above them: each of those costs one
    # small frame and the send queue already bounds them, while the replay is
    # the expensive answer and the one worth rationing. A throttled frame
    # leaves the socket attached to whatever it already was, which for the one
    # shape the page produces — one `subscribe` per socket — is this session.
    now = time.monotonic()
    since = None if conn.replayed_at is None else now - conn.replayed_at
    if since is not None and since < SUBSCRIBE_MIN_INTERVAL_SECONDS:
        conn.send(error_frame(
            "subscribe_throttled",
            "This socket was replayed less than "
            f"{SUBSCRIBE_MIN_INTERVAL_SECONDS:.0f}s ago; the replay was not "
            "rebuilt. Reload the page if the transcript looks wrong.",
            session_id))
        log.warning("ACP subscribe throttled: socket=%s session=%s, %.3fs "
                    "since the last replay", conn.cid, session_id, since)
        return
    conn.replayed_at = now
    _registry.attach(conn, session_id)
    conn.send(envelope("session", {
        "sessionId": session_id,
        "cwd": meta.get("cwd", ""),
        "created": False,
        # The authoritative answer to "is this session still answering",
        # carried on the frame that already exists for it. The page's only
        # other source is a replayed `meta {"turn": "start"}` — a frame the
        # ring buffer is built to evict, so a turn emitting more than
        # HISTORY_MAXLEN chunks would replay without it and leave Send enabled
        # against a session that is still busy.
        "turnActive": session_id in _supervisor.inflight,
        # ``null`` until the session has run a turn: the agent reports context
        # usage per turn and says nothing before the first one. Carried here
        # for the same reason as ``turnActive`` — the live frame that sets it
        # is not recorded in the ring buffer, so a reconnect has no other
        # source for it.
        "contextPercent": meta.get("contextPercent"),
    }, session_id))
    history = _supervisor.history.get(session_id)
    if history is None:
        return
    if history.truncated:
        conn.send(envelope("history_truncated", {
            "message": "Earlier events fell out of the replay buffer; what "
                       "follows is the tail of the conversation.",
        }, session_id))
    conn.send(envelope("history", {"events": history.events()}, session_id))
    log.info("ACP subscribe: session=%s, %d event(s) replayed%s%s",
             session_id, len(history),
             ", truncated" if history.truncated else "",
             ", turn in flight" if session_id in _supervisor.inflight else "")
    crew = _supervisor.crews.get(session_id)
    if crew:
        # A fresh snapshot, not a replay: `subagents` frames are deliberately
        # not recorded into `history` (see `_emit_subagents_frame`), so a
        # reload's only source for "which sub-agents does this session have"
        # is rebuilding it here, the same way `turnActive`/`contextPercent`
        # are rebuilt onto the `session` frame above rather than replayed.
        conn.send(envelope(
            "subagents", {"subagents": _subagents_payload(crew)}, session_id))


def _handle_subagent_subscribe(conn: _Connection, session_id: str,
                                sub_meta: dict) -> None:
    """Attach this socket to a sub-agent's own session, read-only.

    Mirrors ``_handle_subscribe`` against ``subagent_history`` instead of
    ``history``, with the same replay-throttle floor. The ``session`` frame
    carries ``readOnly: true`` and ``parentSessionId`` so the page hides its
    composer and offers a way back, rather than a prompt box the server would
    refuse anyway — see ``_handle_prompt``/``_handle_close``/``_handle_cancel``.

    No ``closing``/reservation checks: a sub-agent session is never itself
    closed, loaded or reserved — only its parent is, and closing the parent
    tears every child down with it (``close_session``), sending this same
    socket a ``session_closed`` at that point exactly as a real session's
    subscriber gets. A subscribe that lands in the brief window before that
    cleanup runs is not a lie — it is correct for the moment it is answered.
    """
    now = time.monotonic()
    since = None if conn.replayed_at is None else now - conn.replayed_at
    if since is not None and since < SUBSCRIBE_MIN_INTERVAL_SECONDS:
        conn.send(error_frame(
            "subscribe_throttled",
            "This socket was replayed less than "
            f"{SUBSCRIBE_MIN_INTERVAL_SECONDS:.0f}s ago; the replay was not "
            "rebuilt. Reload the page if the transcript looks wrong.",
            session_id))
        log.warning("ACP subscribe throttled: socket=%s session=%s, %.3fs "
                    "since the last replay", conn.cid, session_id, since)
        return
    conn.replayed_at = now
    _registry.attach(conn, session_id)
    parent_id = sub_meta["parent"]
    entry = _supervisor.crews.get(parent_id, {}).get(session_id, {})
    conn.send(envelope("session", {
        "sessionId": session_id,
        "cwd": "",
        "created": False,
        "readOnly": True,
        "parentSessionId": parent_id,
        "role": entry.get("role", ""),
        "task": entry.get("task", ""),
        "turnActive": not entry.get("done", False),
        "contextPercent": None,
    }, session_id))
    history = _supervisor.subagent_history.get(session_id)
    if history is None:
        return
    if history.truncated:
        conn.send(envelope("history_truncated", {
            "message": "Earlier events fell out of the replay buffer; what "
                       "follows is the tail of the conversation.",
        }, session_id))
    conn.send(envelope("history", {"events": history.events()}, session_id))
    log.info("ACP subscribe (sub-agent): session=%s parent=%s, %d event(s) "
             "replayed%s", session_id, parent_id, len(history),
             ", truncated" if history.truncated else "")


def _in_use_message(pid: int) -> str:
    return (f"Session is active in another process (PID {pid}). A session can "
            "only be open once — exit that one first.")


def _unattributed_in_use_message() -> str:
    """What is known when the agent refuses and no lock can name a holder.

    ``_lock_holder`` has eight ways to answer ``None`` — psutil missing, the
    lock absent, unreadable, not a JSON object, no pid, an unparseable
    timestamp, a psutil error, a recycled pid — and every one of them used to
    land here on the agent's own word for it, which on kiro-cli 2.14.2 is the
    bare string "Internal error". State the cause that has actually been
    measured and both remedies, without claiming an attribution we do not have.

    **Reachable far less often since 2.16.0** (measured 2026-08-03). That build
    puts the attribution in the error's ``data`` field — ``"Failed to start
    session: Session is active in another process (PID n)"`` — which
    ``_on_response`` now carries into the exception text, so the spoken branch
    in ``_load_failure`` answers first. This stays as the floor for a build
    that says nothing, which is what 2.14.2 did and what the next one may do
    again.

    The sentence names no kiro-cli version, unlike the comments around it. A
    reader of a comment can check which build the observation came from; a user
    reading this on screen cannot, so a version there either reads as "this does
    not apply to me" or claims a re-verification on the build they are actually
    running that nobody has done.
    """
    return ("The agent refused to load this session and gave no reason "
            "(JSON-RPC -32603). A session already open somewhere else has been "
            "seen to look exactly like this, and no lock file here could name "
            "the process holding it. Exit any other kiro-cli that has this "
            "session open and try again; if there is none, restart PowerAtlas, "
            "which releases every session its agent still holds.")


def _load_failure(exc: AcpError, holder: int | None) -> tuple[str, str]:
    """The code and message a failed ``session/load`` reaches the page as.

    Only an ``AgentRejected`` is re-read as an occupied session. The agent
    answering with a JSON-RPC error is the one shape an in-use refusal takes;
    a timeout, a dead agent, a bad cwd or the local session cap say nothing
    about who holds the session, and relabelling those ``session_in_use`` told
    the operator to exit a process that was never there — while hiding the
    failure that did happen.

    ``holder`` is the lock read *again*, after the failure. Measured on
    kiro-cli 2.14.2: a session open elsewhere is refused with a bare
    ``-32603 "Internal error"`` — no pid, and nothing to tell it apart from any
    other internal failure. The lock is the only thing on this machine that can
    still name the process, so the second read is what turns the one cause an
    operator can act on back into a sentence. It also covers a lock taken
    between the pre-flight and the request.

    **That claim no longer holds on the shipping binary, and the direction is
    worth stating.** Re-measured on 2.16.0, 2026-08-03: the refusal now carries
    ``data`` = ``"Failed to start session: Session is active in another process
    (PID 22264)"``, and the pid named was the real holder. So the agent
    identifies the process itself and the lock re-read is no longer the only
    source — it is the fallback. Two builds now bracket the behaviour: 2.14.2
    said nothing, 2.16.0 says everything, so neither branch is dead code and
    the ordering below is what makes the difference invisible to the user.

    The message match handles the speaking build: ``-32603 … "Session is active
    in another process (PID n)"``. It sits below the ``holder`` branch because
    a lock this machine can read is checked against a live process, while the
    agent's sentence is taken on trust. The last branch is what keeps the whole
    path from resting on a third-party file format: a kiro-cli that stopped
    writing locks, or wrote them differently, would otherwise revert every
    in-use refusal to "Internal error".
    """
    if isinstance(exc, AgentRejected):
        text = str(exc)
        if holder is not None:
            return "session_in_use", _in_use_message(holder)
        if _IN_USE_MARKER in text:
            return "session_in_use", text
        if _OPAQUE_REFUSAL_MARKER in text:
            return "session_in_use", _unattributed_in_use_message()
    return exc.code, str(exc)


def _load_pending_frame(session_id: str) -> dict:
    """The frame that tells the page a load is running, and for how long.

    The ceiling travels on the frame rather than being written into the
    template. The page is the only thing that can tell a slow load from a
    wedged one — the agent says nothing at all until it answers — and a
    duration duplicated in the markup drifts from REQUEST_TIMEOUT_SECONDS in
    silence.
    """
    return envelope("meta", {"pending": "load",
                             "timeoutSeconds": REQUEST_TIMEOUT_SECONDS},
                    session_id)


def _defer_until_loaded(conn: _Connection, session_id: str) -> None:
    """Park a socket until the ``session/load`` for its session lands.

    Attaching it now is what a live-looking session invites — ``load_session``
    registers the session before its round-trip, so ``sessions`` already holds
    it — and it hands this socket the agent's replay one frame at a time,
    retiring it at SEND_QUEUE_MAXSIZE. Reproduced: a 1200-event replay with a
    second socket subscribing halfway queued 256 frames and overflowed.

    Suppressing the broadcast for the duration instead would be worse, not
    better: this socket would receive a ``history`` frame of the events so far
    and then never see the rest, because they were recorded but not sent —
    trading a retired socket, which is visible, for a silently truncated
    conversation, which is not. Waiting costs a few seconds and loses nothing:
    the frames not delivered here are precisely the ones the buffer this socket
    is about to be handed is being built from.
    """
    waiters = _registry.loading[session_id]
    if conn not in waiters:
        waiters.append(conn)
    conn.send(_load_pending_frame(session_id))
    log.info("ACP subscribe deferred: session=%s is mid-load, %d socket(s) "
             "waiting on it", session_id, len(waiters))


def _deliver_load(conn: _Connection, waiters: list[_Connection],
                  session_id: str, failure: tuple[str, str] | None) -> None:
    """Answer the socket that asked for the load, and everyone who waited.

    Synchronous, and called with the session already out of
    ``_registry.loading``: attaching a socket and queueing its replay with
    nothing suspending in between is the property ``_handle_subscribe`` rests
    on, extended across every waiter.
    """
    for target in [conn] + [w for w in waiters if w is not conn]:
        if target not in _registry.connections:
            # The tab went away during the load. The session is fine and stays
            # on the supervisor for a later `subscribe`; re-registering a
            # retired socket would leave a subscriber entry behind a dead
            # writer.
            log.info("ACP session %s: a socket waiting on the load is gone",
                     session_id)
            continue
        if failure is not None:
            target.send(error_frame(failure[0], failure[1], session_id))
            continue
        # The replay throttle exists to ration a buffer rebuild a client can
        # ask for freely. This one was paid for with an agent round-trip, and
        # throttling it would discard the entire point of the load — a loaded
        # session that renders nothing.
        target.replayed_at = None
        _handle_subscribe(target, session_id)


async def _handle_load(conn: _Connection, session_id: str | None) -> None:
    """Adopt a session from the agent's store and replay it into this socket.

    The conversation arrives as ``session/update`` notifications while
    ``session/load`` is still outstanding. They are recorded into the session's
    buffer and reach no socket while they arrive: ``_registry.loading`` holds
    the session for the whole of it, and every socket that asks for the session
    in that window is parked rather than attached. Each of them is then served
    the same coalesced ``history`` frame this one gets. Delivering the
    notifications as they arrive would put a whole conversation's worth of
    frames on queues that retire a socket at SEND_QUEUE_MAXSIZE, and would do
    it only for the sessions long enough to be worth loading.

    This is the async half of ``subscribe``, kept out of ``_handle_subscribe``
    because that function's freedom from ``await`` is what stops an event being
    delivered live and in replay both.
    """
    if not _valid_session_id(session_id):
        conn.send(error_frame(
            "bad_session_id",
            "That is not a usable session id: up to "
            f"{MAX_SESSION_ID_CHARS} characters of letters, digits, "
            "underscores and hyphens, and nothing else."))
        log.warning("ACP load refused: [bad_session_id] %.200r", session_id)
        return
    if session_id in _supervisor.subagent_sessions:
        # Already held here read-only — the buffer is the better answer for
        # the same reason the `sessions` branch below redirects to
        # `_handle_subscribe`: a second agent-side `session/load` would
        # duplicate a conversation this process already has, and (unlike that
        # branch) would also spend a real, cap-counted session slot on a
        # sub-agent this surface is never meant to drive interactively.
        _handle_subagent_subscribe(
            conn, session_id, _supervisor.subagent_sessions[session_id])
        return
    if session_id in _registry.loading:
        # A concurrent load owns this session. Waiting for its buffer is the
        # better answer the loser used to be refused outright — an error frame
        # relabelled "exit that one first", against a page whose `loadTried`
        # guard then stopped it retrying.
        _defer_until_loaded(conn, session_id)
        return
    if session_id in _supervisor.closing:
        # A release is in flight. `close_session` leaves the session in
        # `sessions` for the whole terminate round-trip, so without this the
        # branch below would hand this socket a live-looking session and a full
        # replay, and the close would then tell it `session_closed` a moment
        # later — a load that appears to work and immediately unwinds.
        #
        # Unreachable before the sweeper existed and reachable now: a close
        # used to require a subscribed socket pressing Close, and that socket
        # is by definition not the one arriving here. The sweeper closes
        # sessions nobody is watching, which is precisely the state a `load`
        # addresses. Same code and same wording as `_handle_prompt`'s guard.
        conn.send(error_frame(
            "close_in_progress",
            "This session is being released. Wait a moment and load it "
            "again.", session_id))
        log.warning("ACP load refused: [close_in_progress] session=%s",
                    session_id)
        return
    if session_id in _supervisor.sessions:
        # Already live here, so the buffer is the better answer: a second
        # agent-side replay would append the whole conversation to itself.
        _handle_subscribe(conn, session_id)
        return
    # Both gates are below the three cheap answers above and above everything
    # this function spends: `subscribe` has had a replay floor since Phase 4
    # while `load` — which costs strictly more — had none, and the cap was
    # consulted only inside `load_session`, after two thread hops, a registry
    # claim and a pending frame had already been paid for a session that is
    # refused either way.
    now = time.monotonic()
    since = None if conn.loaded_at is None else now - conn.loaded_at
    if since is not None and since < LOAD_MIN_INTERVAL_SECONDS:
        conn.send(error_frame(
            "load_throttled",
            "This socket asked for a load less than "
            f"{LOAD_MIN_INTERVAL_SECONDS:.0f}s ago. Wait for that one to "
            "finish, or reload the page.", session_id))
        log.warning("ACP load throttled: socket=%s session=%s, %.3fs since the "
                    "last load", conn.cid, session_id, since)
        return
    if _supervisor.at_capacity():
        conn.send(error_frame(
            SessionLimit.code, _session_limit_message(), session_id))
        log.warning("ACP load refused: [%s] session=%s at the session cap",
                    SessionLimit.code, session_id)
        return
    conn.loaded_at = now
    # Claimed before the first `await`, which is what makes it a claim: two
    # `load` frames for one session become two tasks, and each task's
    # synchronous prefix runs to completion before the other starts.
    _registry.loading[session_id] = []
    failure: tuple[str, str] | None = None
    try:
        # Every step is inside this ``try``, the pre-flight included. It used to
        # sit outside one, so anything it raised escaped into a spawned task's
        # future and left the socket holding a pending label with no error
        # frame behind it — and with waiters, that would strand them too.
        try:
            holder = await asyncio.to_thread(_lock_holder, session_id)
            if holder is not None:
                failure = ("session_in_use", _in_use_message(holder))
                log.warning(
                    "ACP load refused: [session_in_use] session=%s pid=%d",
                    session_id, holder)
            else:
                # Sockets still attached to a session this process no longer
                # holds: they outlived an agent that died under them. Detaching
                # them before the load is what keeps the replay off their
                # queues, where SEND_QUEUE_MAXSIZE frames would retire them.
                # They re-subscribe on their own next frame.
                for stale in tuple(_registry.subscribers.get(session_id, ())):
                    log.info("ACP load: detaching a socket left over from an "
                             "earlier life of session %s", session_id)
                    _registry.detach(stale)
                # The load spans a spawn on the first one plus the agent's own
                # replay, and the page shows nothing until the `history` frame.
                conn.send(_load_pending_frame(session_id))
                cwd = await asyncio.to_thread(_load_session_cwd, session_id)
                await _supervisor.load_session(session_id, cwd)
                # The replay's own last answer has no boundary behind it: the
                # agent sends the conversation and stops. Without this it would
                # come back as the one bubble on the page still in plain text,
                # and it is the one the reader is looking at. Inside the
                # `loading` claim, so the frame is in the buffer before
                # `_deliver_load` coalesces it into the `history` reply.
                _flush_bubble(session_id)
        except AcpError as exc:
            failure = _load_failure(
                exc, await asyncio.to_thread(_lock_holder, session_id))
            # The code, the message the page is actually given, and the session,
            # in one line. Logging the substituted code beside the original
            # exception produced lines that contradicted themselves, and neither
            # failed-load line named a session at all — alone among this
            # module's refusals.
            log.warning("ACP session/load refused: [%s] session=%s %s%s",
                        failure[0], session_id, failure[1],
                        "" if failure[1] == str(exc) else " (agent: %s)" % exc)
        except Exception:
            log.exception("ACP session/load failed: session=%s", session_id)
            failure = ("internal_error",
                       "Loading the session failed; see orchestrator.log.")
    finally:
        # Released and the answers below queued with nothing suspending in
        # between, so no live event can be broadcast between a socket being
        # attached and being handed the replay that event belongs in.
        waiters = _registry.loading.pop(session_id, [])
    _deliver_load(conn, waiters, session_id, failure)


async def _handle_new(conn: _Connection, payload: dict) -> None:
    """Create a session, reporting every failure as a typed ``error`` frame."""
    raw_cwd = payload.get("cwd")
    if raw_cwd is not None and not isinstance(raw_cwd, str):
        conn.send(error_frame("bad_payload", "'cwd' must be a string."))
        return
    if _supervisor.at_capacity():
        # Above the pending frame and above the resolve, not inside
        # `new_session` where the cap used to be read for the first time. At
        # the cap every `new` frame otherwise bought a filesystem round-trip on
        # a path the client chose, for a session that is refused either way —
        # and claimed to be creating one while doing it.
        conn.send(error_frame(SessionLimit.code, _session_limit_message()))
        log.warning("ACP session/new refused: [%s] at the session cap",
                    SessionLimit.code)
        return
    # `session/new` takes ~1.1 s for the first session of a process and ~0.5 s
    # after on kiro-cli 2.16.0 (it was 5.4 s / 2.5 s on 2.14.x). Faster than it
    # was, still not instant, and a spawn on a cold machine is unbounded —
    # without this the page looks broken for the whole of it.
    conn.send(envelope("meta", {"pending": "new"}))
    try:
        # Off the loop. Both halves of `_resolve_session_cwd` block on the
        # filesystem and `raw_cwd` is whatever the page's directory box holds:
        # a UNC path to an unreachable host measured 42.16 s in a single call,
        # during which uvicorn serves nothing at all — no dashboard, no status
        # polling, no other ACP socket. The sibling `load` path resolves its
        # cwd in a thread for exactly this reason.
        cwd = await asyncio.to_thread(_resolve_session_cwd, raw_cwd)
        info = await _supervisor.new_session(cwd)
    except AcpError as exc:
        log.warning("ACP session/new refused: [%s] %s", exc.code, exc)
        conn.send(error_frame(exc.code, str(exc)))
        return
    except Exception:
        log.exception("ACP session/new failed")
        conn.send(error_frame(
            "internal_error",
            "Creating the session failed; see orchestrator.log."))
        return
    session_id = info["sessionId"]
    if conn not in _registry.connections:
        # The tab closed during the several seconds `session/new` takes. The
        # session itself is fine and survives on the supervisor — a later
        # `subscribe` picks it up — but re-registering a retired socket would
        # leave a subscriber entry behind a dead writer.
        log.info("ACP session %s created after its socket went away", session_id)
        return
    _registry.attach(conn, session_id)
    conn.send(envelope("session", {
        "sessionId": session_id,
        "cwd": info["cwd"],
        "created": True,
    }, session_id))


async def _handle_prompt(conn: _Connection, session_id: str | None,
                         payload: dict) -> None:
    """Run one turn, reporting every failure as a typed ``error`` frame.

    Every check runs before the first ``await``, which is what makes the
    in-flight guard hold: two ``prompt`` frames become two tasks, and each
    task's synchronous prefix runs to completion before the other starts.

    Turn events are emitted to the *session*, not to the socket that asked, so
    a second tab watching the same session sees the same transcript — including
    the prompt it did not send.
    """
    def refuse(code: str, message: str) -> None:
        # Every one of these is a state the page cannot explain on its own —
        # `not_subscribed` in particular is what a reconnect subscribing with
        # the wrong session id looks like from the client side, and it used to
        # leave no trace on the server at all.
        conn.send(error_frame(code, message, session_id))
        log.warning("ACP prompt refused: [%s] session=%s", code, session_id)

    if not session_id:
        conn.send(error_frame("bad_envelope", "'prompt' needs a sessionId."))
        log.warning("ACP prompt refused: [bad_envelope] no sessionId")
        return
    # A missing `prompt` is not a refusal on its own any more: an image-only
    # turn is a real gesture — paste a screenshot, press Enter — and kiro-cli
    # 2.16.0 answers a prompt array with no text block at all (measured
    # 2026-08-04, `stopReason: end_turn`). What is still refused is a prompt
    # carrying neither, and a `prompt` key of the wrong type.
    text = payload.get("prompt")
    if text is None:
        text = ""
    if not isinstance(text, str):
        refuse("bad_payload", "'prompt' must be a string.")
        return
    images, why = _validate_images(payload.get("images"))
    if why:
        refuse("bad_payload", why)
        return
    if not text.strip() and not images:
        refuse("bad_payload", "A prompt needs text, an image, or both.")
        return
    if session_id in _supervisor.subagent_sessions:
        # Checked ahead of the generic `unknown_session` below, which is true
        # of a sub-agent id too (it is never in `sessions`) but says the wrong
        # thing — this is a real, live conversation, just not one this surface
        # may drive. Roadmap's own note: "the correct interaction model is to
        # watch it, not to prompt it alongside the parent."
        refuse("read_only_session", _READ_ONLY_SUBAGENT_MESSAGE)
        return
    if session_id not in _supervisor.sessions:
        refuse("unknown_session",
               "This server has no such live session. It may belong to an "
               "earlier PowerAtlas process — create a new one.")
        return
    if conn.session_id != session_id:
        # A socket that is not attached would start a turn and then receive
        # none of the stream it started, which on the page is indistinguishable
        # from an agent that never answered.
        refuse("not_subscribed", "Subscribe to this session before prompting it.")
        return
    if session_id in _supervisor.closing:
        # The mirror of the `turn_in_progress` guard in `_handle_close`, and it
        # has to be here because that one only bars the second half of the
        # race. A close claims `closing` before its first await and leaves the
        # session in `sessions` until the agent answers, so a prompt arriving
        # in that window starts a turn on a session that is being released:
        # the `session/prompt` future then sits in `_pending` until the
        # inactivity ceiling gives up on it — the exact cost the close guard exists to
        # prevent — while `close_session` discards its `inflight` marker and
        # the close drops the ring buffer and detaches every watcher. The
        # surviving turn's chunks and tool calls then reach neither the page
        # nor the replay, which under `-a` is a turn running ungated tools with
        # nothing watching.
        refuse("close_in_progress",
               "This session is being closed. Create a new one to carry on.")
        return
    if session_id in _supervisor.inflight:
        refuse("turn_in_progress",
               "This session is still answering the previous prompt.")
        return
    _supervisor.inflight.add(session_id)
    log.info("ACP turn start: session=%s (%d chars, %d image(s))",
             session_id, len(text), len(images))
    # What stands for this prompt everywhere it is not the raw bytes: the
    # transcript frame below, and the agent's own text block. One string for
    # both, so the numbering a person reads and the numbering the model reads
    # cannot drift apart.
    spoken = _with_image_markers(text, len(images))

    # Before the user's own chunk, which is the first of the two frames that
    # close the previous bubble on the page. Live turns almost never have
    # anything pending here; a session adopted with `load` does — its last
    # answer arrived with no turn marker behind it, so this is where that
    # bubble finally renders.
    _flush_bubble(session_id)
    # `spoken`, never the image bytes. The frame lands in the replay buffer,
    # which charges every string it can reach at full UTF-8 weight — base64
    # included — so putting the attachments here would spend a 2 MiB
    # conversation on about eight of them.
    _emit(session_id, envelope("chunk", {"role": "user", "text": spoken}, session_id))
    _emit(session_id, envelope("meta", {"turn": "start"}, session_id))
    # Names the state a reload would find if this task never reaches its own
    # end: the turn boundary is what the page derives "still answering" from,
    # so it has to be emitted on the cancellation path too.
    stop_reason = "interrupted"
    try:
        result = await _supervisor.prompt(session_id, spoken, images)
        stop_reason = result.get("stopReason") or "end_turn"
    except AcpError as exc:
        log.warning("ACP session/prompt refused: [%s] %s", exc.code, exc)
        _emit(session_id, error_frame(exc.code, str(exc), session_id))
        stop_reason = "error"
    except Exception:
        log.exception("ACP session/prompt failed")
        _emit(session_id, error_frame(
            "internal_error",
            "The prompt failed; see orchestrator.log.", session_id))
        stop_reason = "error"
    finally:
        _supervisor.inflight.discard(session_id)
        log.info("ACP turn end: session=%s stopReason=%s", session_id, stop_reason)
        # In the `finally` and above the end marker, so the markdown of a turn
        # that was cancelled or that errored is still rendered — `stop_reason`
        # defaults to `interrupted` for exactly the same reason. The end marker
        # is the frame that closes this bubble on the page, so the rendering
        # has to be in front of it.
        _flush_bubble(session_id)
        _emit(session_id, envelope(
            "meta", {"turn": "end", "stopReason": stop_reason}, session_id))


async def _handle_steer(conn: _Connection, session_id: str | None,
                        payload: dict) -> None:
    """Inject a mid-turn message into the running agent turn via
    ``_session/steer``.

    Runs the same pre-flight guards as ``_handle_prompt`` plus an extra
    ``inflight`` check — steering when no turn is active would silently
    queue a request that kiro-cli answers only once the *next* turn starts,
    holding the handler for up to ``REQUEST_TIMEOUT_SECONDS``.
    """
    if not session_id:
        conn.send(error_frame("bad_envelope", "'steer' needs a sessionId."))
        log.warning("ACP steer refused: [%s] session=%s", "bad_envelope", session_id)
        return
    if session_id in _supervisor.subagent_sessions:
        conn.send(error_frame(
            "read_only_session", _READ_ONLY_SUBAGENT_MESSAGE, session_id))
        log.warning("ACP steer refused: [%s] session=%s", "read_only_session", session_id)
        return
    if session_id not in _supervisor.sessions:
        conn.send(error_frame(
            "unknown_session", "This server has no such live session.", session_id))
        log.warning("ACP steer refused: [%s] session=%s", "unknown_session", session_id)
        return
    if conn.session_id != session_id:
        conn.send(error_frame(
            "not_subscribed", "Subscribe to this session first.", session_id))
        log.warning("ACP steer refused: [%s] session=%s", "not_subscribed", session_id)
        return
    if session_id in _supervisor.closing:
        conn.send(error_frame(
            "close_in_progress", "Session is being released.", session_id))
        log.warning("ACP steer refused: [%s] session=%s", "close_in_progress", session_id)
        return
    if session_id not in _supervisor.inflight:
        conn.send(error_frame(
            "no_turn_in_progress",
            "No turn is running — steer is only available during an active turn.",
            session_id))
        log.warning("ACP steer refused: [%s] session=%s", "no_turn_in_progress", session_id)
        return
    raw = payload.get("message")
    if not isinstance(raw, (str, type(None))):
        conn.send(error_frame("bad_payload", "Steer message must be a string.", session_id))
        log.warning("ACP steer refused: [%s] session=%s", "bad_payload", session_id)
        return
    text = (raw or "").strip()
    if not text:
        conn.send(error_frame(
            "bad_payload", "Steer message must not be empty.", session_id))
        log.warning("ACP steer refused: [%s] session=%s", "bad_payload", session_id)
        return
    try:
        result = await _supervisor.steer(session_id, text)
        conn.send(envelope("steer_ack", {"queued": result.get("queued", True)},
                           session_id))
    except AcpError as exc:
        conn.send(error_frame(exc.code, str(exc), session_id))
    except Exception:
        log.exception("ACP _handle_steer: unexpected error")
        conn.send(error_frame(
            "internal_error", "Steer failed unexpectedly.", session_id))


def _mark_crew_done(crew: dict, now: float) -> bool:
    """Mark every non-done crew entry done and stamp ``stoppedAt``.

    Used by the cancel cascade in ``_handle_cancel`` to finalize the whole
    crew locally when kiro-cli stops emitting terminal subagent status after a
    parent cancel.  See ``_on_subagent_list`` for the per-entry stamping rule
    this mirrors: set ``stoppedAt`` to *now* only when transitioning to
    ``done=True`` for the first time and it was not already set.

    Returns ``True`` if any entry was changed, ``False`` otherwise.
    """
    changed = False
    for entry in crew.values():
        if not entry["done"]:
            entry["done"] = True
            if not entry.get("stoppedAt"):
                entry["stoppedAt"] = now
            changed = True
    return changed


async def _handle_cancel(conn: _Connection, session_id: str | None) -> None:
    """Interrupt the turn a session is running.

    Emits nothing about the turn itself. ``session/cancel`` makes the
    outstanding ``session/prompt`` return ``stopReason: "cancelled"``, and the
    task awaiting it is what emits the turn boundary — to the *session*, so
    every attached tab sees the same ending. A second boundary emitted here
    would leave a transcript with two ends to one turn.

    The session survives its cancellation: nothing here touches ``sessions``
    or the ring buffer, so the next prompt runs on the same conversation.
    """
    if not session_id:
        conn.send(error_frame("bad_envelope", "'cancel' needs a sessionId."))
        log.warning("ACP cancel refused: [bad_envelope] no sessionId")
        return
    if session_id in _supervisor.subagent_sessions:
        conn.send(error_frame(
            "read_only_session", _READ_ONLY_SUBAGENT_MESSAGE, session_id))
        log.warning("ACP cancel refused: [read_only_session] session=%s",
                    session_id)
        return
    if session_id not in _supervisor.sessions:
        conn.send(error_frame(
            "unknown_session",
            "This server has no such live session. It may belong to an "
            "earlier PowerAtlas process — create a new one.", session_id))
        log.warning("ACP cancel refused: [unknown_session] session=%s", session_id)
        return
    if conn.session_id != session_id:
        # The same requirement `prompt` carries, for the same reason: the turn
        # this ends belongs to the session's watchers, and a socket that is not
        # one of them is acting on a transcript it cannot see.
        conn.send(error_frame(
            "not_subscribed",
            "Subscribe to this session before cancelling its turn.", session_id))
        log.warning("ACP cancel refused: [not_subscribed] session=%s", session_id)
        return
    if session_id not in _supervisor.inflight:
        # Not an error worth an error frame's noise on the page, but never
        # silent: a Stop that reached a server holding no turn is the shape a
        # lost `meta turn end` takes, and the log is where that is diagnosed.
        log.info("ACP cancel: session=%s is not running a turn", session_id)
        return
    log.info("ACP cancel requested: session=%s", session_id)
    try:
        await _supervisor.cancel(session_id)
    except AcpError as exc:
        log.warning("ACP session/cancel refused: [%s] %s", exc.code, exc)
        conn.send(error_frame(exc.code, str(exc), session_id))
        return
    except Exception:
        log.exception("ACP session/cancel failed: session=%s", session_id)
        conn.send(error_frame(
            "internal_error",
            "Cancelling the turn failed; see orchestrator.log.", session_id))
        return
    # Cancel cascade — kiro-cli never emits terminal subagent status after a
    # parent cancel (verified by live probe 2026-08-12: 11 post-cancel
    # list_update frames, all children still "working"). Mark every non-done
    # crew entry done locally and broadcast so the page clears its crew bar.
    crew = _supervisor.crews.get(session_id)
    if crew:
        now = time.time()
        if _mark_crew_done(crew, now):
            try:
                _emit_subagents_frame(session_id)
            except Exception:
                log.exception("ACP cancel cascade: failed to emit subagents frame")


def _session_closed_frame(session_id: str) -> dict:
    return envelope("session_closed", {
        "sessionId": session_id,
        "message": "This session was closed. Its agent-side processes and its "
                   "replay buffer are gone; create a new session to carry on.",
    }, session_id)


async def _handle_close(conn: _Connection, session_id: str | None) -> None:
    """Release a session on the agent and drop everything it holds here.

    The one control the plan's whole memory budget rests on: §4 and §6 accept
    the per-session cost ``plans/ROADMAP.md`` records on the strength of it
    existing, and §3 calls it "the lever that matters".

    Every check runs before the first ``await``, and the claim on
    ``_supervisor.closing`` is taken there too — two ``close`` frames become
    two tasks, and each task's synchronous prefix runs to completion before the
    other starts.
    """
    def refuse(code: str, message: str) -> None:
        conn.send(error_frame(code, message, session_id))
        log.warning("ACP close refused: [%s] session=%s", code, session_id)

    if not session_id:
        conn.send(error_frame("bad_envelope", "'close' needs a sessionId."))
        log.warning("ACP close refused: [bad_envelope] no sessionId")
        return
    if session_id in _supervisor.subagent_sessions:
        refuse("read_only_session", _READ_ONLY_SUBAGENT_MESSAGE)
        return
    if session_id in _registry.loading:
        # `_Registry.loading` bars attachment for the whole of a `session/load`,
        # and closing under one would have the load's own failure path pop a
        # session this had already removed — and would strand the sockets
        # parked on it, which are answered only when the load lands.
        refuse("session_loading",
               "This session is still being loaded from the agent. Wait for "
               "the conversation to arrive, then close it.")
        return
    if conn.session_id != session_id:
        # Above `_Registry.loading` would be wrong — a loading session has no
        # attached socket by construction, so this would answer every close
        # during a load with the wrong reason — and below the two checks that
        # follow would be worse: a socket that is not watching a session has no
        # business releasing what another tab is holding.
        refuse("not_subscribed", "Subscribe to this session before closing it.")
        return
    if session_id not in _supervisor.sessions:
        # Deliberately **not** `unknown_session`. `subscribe` and `prompt` emit
        # that to mean "this server does not hold it — try adopting it", and
        # the page answers it by sending `load`; reusing it here would have a
        # refused close spawn an agent and re-adopt the session, spending again
        # the memory the Close press existed to free. Frame ordering happens to
        # prevent that today, which is not a design.
        refuse("nothing_to_close",
               "This server has no such live session — there is nothing to "
               "close.")
        return
    if session_id in _supervisor.inflight:
        # Closing under a live turn would leave the `session/prompt` future
        # waiting on a session the agent no longer has until the inactivity
        # ceiling expires — up to PROMPT_SILENCE_SECONDS plus one tick, and up
        # to PROMPT_ABSOLUTE_MAX_SECONDS if the agent keeps talking about a
        # session it no longer holds.
        refuse("turn_in_progress",
               "This session is still answering. Stop the turn first, then "
               "close it.")
        return
    if session_id in _supervisor.closing:
        refuse("close_in_progress", "This session is already being closed.")
        return
    _supervisor.closing.add(session_id)
    try:
        await _supervisor.close_session(session_id)
    except AcpError as exc:
        log.warning("ACP session/close refused: [%s] session=%s %s",
                    exc.code, session_id, exc)
        conn.send(error_frame(exc.code, str(exc), session_id))
        return
    except Exception:
        log.exception("ACP session/close failed: session=%s", session_id)
        conn.send(error_frame(
            "internal_error",
            "Closing the session failed; see orchestrator.log.", session_id))
        return
    finally:
        _supervisor.closing.discard(session_id)
    # Broadcast before detaching, and never through `_emit`: the buffer this
    # would be recorded into has just been dropped, and a second tab watching
    # the same session has to be told too — it is holding a transcript that no
    # longer has a session behind it.
    frame = _session_closed_frame(session_id)
    for target in tuple(_registry.subscribers.get(session_id, ())):
        target.send(frame)
        _registry.detach(target)


# -- the idle sweeper ------------------------------------------------------


# session id -> consecutive failed close attempts. Exists so a session the
# agent will never release logs one traceback rather than one a minute for the
# application's lifetime: measured against kiro-cli with
# ``_kiro.dev/session/terminate`` removed, one stuck session failed 23 times in
# 120 s at a 5 s interval, each with a full traceback, into a log this path does
# not rotate.
#
# **Bounded by construction, and it has to be**: replacing unbounded log growth
# with unbounded memory growth would be no fix at all. `_sweep_once` drops every
# key that is no longer a live session before it does anything else, so the dict
# can only ever hold ids drawn from `_supervisor.sessions` — at most
# MAX_SESSIONS (<= 16) entries. That covers every way a session leaves without
# its close ever succeeding: swept by another path, closed by a user, or dropped
# wholesale by `_detach` when the agent dies.
_sweep_failures: dict[str, int] = {}


def _sweepable(session_id: str, meta: dict, now: float) -> bool:
    """Whether one session may be reclaimed on this tick.

    Six conditions, and each one is a separate way this has already gone wrong
    in review:

    1. **Still registered.** The iteration snapshots ``sessions`` once but
       awaits inside the loop, so by the time session *n* is reached a user
       close may have popped it — and ``close_session`` would then raise
       ``AgentRejected`` and log a WARNING on every pass.
    2. **Idle past the TTL**, measured on ``last_used``.
    3. **No attached subscriber.** A tab watching a session means leave it
       alone whatever its age.
    4. **No turn in flight.**
    5. **No close in flight.**
    6. **No load in flight.** The one the original four missed: a session
       mid-``session/load`` is registered before its round-trip and has zero
       subscribers *by construction*, so it satisfied every other condition and
       would have been terminated mid-load — after which the load's own failure
       path pops an already-removed session and ``_deliver_load`` replays a
       dead one to the sockets parked on it.
    """
    if session_id not in _supervisor.sessions:
        return False
    last_used = meta.get("last_used")
    if last_used is None or now - last_used <= ACP_IDLE_TTL_SECONDS:
        return False
    if _registry.subscribers.get(session_id):
        return False
    if session_id in _supervisor.inflight:
        return False
    if session_id in _supervisor.closing:
        return False
    if session_id in _registry.loading:
        return False
    return True


async def _sweep_once() -> None:
    """One pass over the live sessions. Reclaims what nobody is using.

    What sweeping actually recovers is measured, and it is less than the word
    implies: ``_kiro.dev/session/terminate`` frees the session's own MCP
    processes (~3 processes / ~161 MB on kiro-cli 2.16.0, the final-QA
    eight-session measurement — see ``_session_limit_message``) and removes its
    ``.lock`` within ~0.3 s, and it leaves the ``.json`` and ``.jsonl``
    transcripts intact so the session stays resumable by ``session/load``. It
    does **not** kill a tool subprocess the agent left running — measured
    2026-08-01, a ``pwsh.exe``/``PING.EXE`` pair outlived terminate by the whole
    observation window. Such an orphan can only arise after a turn ended or was
    cancelled, since condition 4 keeps a session with a live turn off this path
    entirely, and it is reaped when PowerAtlas exits and its job object closes.
    Recorded here so the sweeper's claim is not read as more than it is.
    """
    now = time.monotonic()
    # Backstop for `_publish_live`. Every mutation of `sessions` calls it, but
    # that is a property of five call sites rather than of the type, and a
    # sixth added later would drift silently — the symptom being a dashboard
    # dot that is wrong for as long as the agent lives, which is exactly the
    # bug this mechanism exists to fix. Republishing on the tick makes any such
    # miss self-heal within one sweep interval instead of never. Costs one
    # frozenset of a dict that is at most MAX_SESSIONS long.
    _supervisor._publish_live()
    # Forget the failure counts of sessions that are no longer here, whatever
    # took them — this is what keeps `_sweep_failures` bounded by the live
    # session count rather than growing for the application's lifetime.
    for gone in tuple(_sweep_failures):
        if gone not in _supervisor.sessions:
            del _sweep_failures[gone]
    # `close_session` mutates `sessions`, and a live iterator over a dict that
    # changes size raises RuntimeError.
    for session_id, meta in tuple(_supervisor.sessions.items()):
        if not _sweepable(session_id, meta, now):
            continue
        # Claimed in the synchronous prefix, before the first await, exactly as
        # `_handle_close` does. Without it `close_session` leaves the session
        # in `sessions` and out of `closing` for the whole terminate
        # round-trip, so a prompt arriving in that window passes every guard
        # and starts a turn on a session being released — the window
        # `_handle_prompt`'s `close_in_progress` refusal exists to close.
        _supervisor.closing.add(session_id)
        try:
            idle = now - meta.get("last_used", now)
            log.info("ACP sweeper: releasing session %s, idle %.0fs",
                     session_id, idle)
            # Deliberately not shielded — and not because a shield would hold
            # this task. It would not: cancelling the sweeper cancels the
            # shield's *outer* future and `CancelledError` raises here at once
            # (~0.1 ms measured), so the teardown budget is met either way.
            # What a shield would actually buy is an orphan. The inner
            # `close_session` would keep running with nothing awaiting it,
            # issuing a terminate round-trip against an agent that
            # `acp.shutdown()` is killing in the same breath, on a loop that is
            # about to close — a stray task racing the job-object kill and
            # logging into a torn-down world. Letting the cancel reach the
            # close is what keeps teardown to one sequence.
            await _supervisor.close_session(session_id)
            # This session is released; nothing is owed to the next failure.
            _sweep_failures.pop(session_id, None)
        except Exception:
            # WARNING and carry on, never a dead task: if a kiro-cli build
            # drops the private terminate method, the sweeper degrades to
            # memory growth rather than taking itself out on the first tick.
            #
            # The traceback is worth its size exactly once per session. A
            # session that cannot be released fails again on every tick, and at
            # SWEEP_INTERVAL_SECONDS that is one multi-line traceback a minute,
            # per stuck session, forever. So: the whole thing the first time,
            # and one countable line after it, which is what a reader needs to
            # tell "still stuck" from "stuck again for a new reason".
            failures = _sweep_failures[session_id] = (
                _sweep_failures.get(session_id, 0) + 1)
            if failures == 1:
                log.warning("ACP sweeper: releasing session %s failed",
                            session_id, exc_info=True)
            else:
                log.warning(
                    "ACP sweeper: releasing session %s failed again "
                    "(%d consecutive; traceback logged on the first)",
                    session_id, failures)
            continue
        finally:
            _supervisor.closing.discard(session_id)
        # `_handle_close`'s notification half, reproduced rather than shared:
        # its `not_subscribed` guard protects a real case ("a socket not
        # watching a session has no business releasing what another tab
        # holds") and the sweeper has no socket, so relaxing that guard to
        # reach this code would weaken a check for a caller that never needed
        # it. Condition 3 means this loop is normally empty; it is not
        # unreachable, because `_handle_subscribe` can attach during the
        # terminate round-trip above.
        frame = _session_closed_frame(session_id)
        for target in tuple(_registry.subscribers.get(session_id, ())):
            target.send(frame)
            _registry.detach(target)


async def _sweep_loop() -> None:
    """Run ``_sweep_once`` forever. Cancelled by ``lifespan`` at shutdown.

    **Sleeps first, always.** A `continue` placed before the sleep never
    yields, and this task runs on the same loop as every websocket and every
    dashboard render — a tight loop here takes the whole application with it.
    The zero-session guard therefore sits *after* the sleep, which is what
    keeps `_Supervisor`'s "a launch that never opens /acp pays nothing"
    promise true in substance: the task exists, and it costs one wakeup a
    minute and nothing else.
    """
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        if not _supervisor.sessions:
            continue
        try:
            await _sweep_once()
        except Exception:
            # `CancelledError` is a BaseException since 3.8 and is deliberately
            # not caught here: swallowing it would hold teardown for as long as
            # the pass took.
            log.warning("ACP sweeper: pass failed", exc_info=True)


def start_sweeper() -> asyncio.Task:
    """Start the idle sweeper. Called from ``web.py``'s ``lifespan``.

    ACP owns the policy; `web.py` owns only the lifecycle hook, the same split
    `shutdown()` already uses. It cannot live in `shutdown()` on the other
    side, because that function is synchronous and cannot await a task
    cancellation.
    """
    return asyncio.create_task(_sweep_loop())


def apply_config(config) -> None:
    """Rebind this module's tunables from configuration. **Startup only.**

    Called once from ``__main__`` with the ``Config`` that was already loaded
    there, so no route ever pays for a TOML parse to answer `at_capacity()`.
    Rebinding module-level names rather than storing them on ``_Supervisor``
    is what keeps every existing reader — and every test that patches
    ``acp.MAX_SESSIONS`` — working unchanged.

    An out-of-range value is **logged and ignored, not clamped**: the value
    already in force is kept, so `acp_idle_ttl_seconds = 10` leaves the TTL at
    1800.0 rather than snapping it to the 300 bound. Retaining beats snapping
    because a hand-edited number is as likely to be a typo as an intent, and
    the working value is the one the application was known to run with.
    Neither is raised on: this runs inside startup, and refusing to boot over a
    hand-edited number would trade a wrong session cap for no application at
    all. The write path is where a bad value is refused by name.
    """
    global MAX_SESSIONS, ACP_IDLE_TTL_SECONDS, PROMPT_SILENCE_SECONDS
    MAX_SESSIONS = _clamped(config, "acp_max_sessions", MAX_SESSIONS, 1, 16)
    ACP_IDLE_TTL_SECONDS = float(_clamped(
        config, "acp_idle_ttl_seconds", ACP_IDLE_TTL_SECONDS, 300, 86400))
    PROMPT_SILENCE_SECONDS = float(_clamped(
        config, "acp_prompt_silence_seconds", PROMPT_SILENCE_SECONDS,
        60, 7200))
    log.info("ACP config applied: max_sessions=%d idle_ttl=%.0fs "
             "prompt_silence=%.0fs", MAX_SESSIONS, ACP_IDLE_TTL_SECONDS,
             PROMPT_SILENCE_SECONDS)


def _clamped(config, name: str, fallback, low: int, high: int):
    """One configured integer, or the value already in force if it is unusable.

    Returns ``fallback`` unchanged rather than coercing it, so a caller that
    rebound the name to something finer than an integer — the test suite does,
    to avoid burning 900 s of wall clock on one branch — is not quietly
    truncated to zero by a rejected config value.
    """
    value = getattr(config, name, None)
    if isinstance(value, bool) or not isinstance(value, int):
        # `isinstance(True, int)` is True in Python, and `acp_max_sessions =
        # true` in a hand-edited TOML would otherwise become a cap of 1.
        log.warning("ACP config: %s is not an integer (%r); keeping %r",
                    name, value, fallback)
        return fallback
    if not (low <= value <= high):
        log.error("ACP config: %s=%d is outside %d-%d; keeping %r",
                  name, value, low, high, fallback)
        return fallback
    return value
