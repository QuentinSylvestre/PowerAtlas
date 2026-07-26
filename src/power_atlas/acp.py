"""Browser-facing half of the ACP prototype, and the agent process behind it.

This module owns everything that happens on a ``/ws/acp`` socket once
``web.py`` has accepted it — envelope parsing, the connection and session
registry, the outbound fan-out machinery — and, since Phase 3b, the supervised
``kiro-cli acp`` subprocess it all talks to: spawn, the JSON-RPC handshake,
``session/new``, and teardown. Phase 4 adds ``session/prompt``, the
``agent_message_chunk`` and tool-call fan-out behind it, and the per-session
ring buffer that a reload replays. ``cancel``/``close`` still answer a typed
``not_implemented``; they arrive in Phase 6.

Isolation boundary — this module imports exactly one name from the rest of
``power_atlas``: ``config.CONFIG_DIR``, to place the agent's neutral cwd where
every other PowerAtlas artifact lives. Two caches elsewhere in the package are
plain unlocked ``OrderedDict``s, safe only because every current caller runs on
the event loop — and this module now runs an OS reader thread that does not.
Neither of the two modules holding them is imported here, and neither is
reachable from ``config``, which imports nothing from the package at all. So
the property is still held by the import graph rather than by discipline; it is
just no longer stated as "imports nothing". The plan's exit criterion greps
this file for those two module names, which is why they are described here
rather than spelled.

Wire contract, identical in both directions::

    {"type": <str>, "sessionId": <str|null>, "payload": <object>}

Client to server: ``subscribe`` (attach this socket to a session and replay its
buffer), ``new`` (create a session against a cwd), ``prompt``, ``cancel``,
``close``.

Server to client: ``session`` (id and metadata after ``new``/``subscribe``),
``chunk``, ``tool_call``, ``tool_update``, ``meta``, ``error``, ``agent_died``,
``history_truncated``, ``history`` (the whole replay, coalesced into one
frame). ``envelope`` refuses any type not in ``SERVER_TYPES``.

Session identity survives a reload because the page carries ``?sid=…`` and
re-sends ``subscribe`` on connect.

Everything below the registry runs against one lazily spawned process holding N
sessions. Its health is judged from the JSON-RPC channel alone — never from an
exit code and never from ``stderr``, which is ``DEVNULL`` here: the agent has
been observed dying with exit 0 and no stderr at all, and an undrained pipe
deadlocks the child once ~64 KB accumulates in it.
"""

import asyncio
import collections
import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from .config import CONFIG_DIR

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

CLIENT_TYPES = frozenset({"subscribe", "new", "prompt", "cancel", "close"})
SERVER_TYPES = frozenset({
    "session", "chunk", "tool_call", "tool_update", "meta", "error",
    "agent_died", "history_truncated", "history",
})

# The largest legitimate client frame is a `prompt` payload: prose a human
# typed or pasted into the page. 256 KiB is far more of that than anyone
# sends, and two orders of magnitude below uvicorn's 16 MiB `ws_max_size`
# default. Note what this cap is and is not: uvicorn has already decoded the
# frame by the time we see it, so this rejects oversized frames at the
# protocol layer rather than at the transport. Lowering the transport ceiling
# means passing `ws_max_size` to the uvicorn.Config calls in __main__.py,
# which is outside this phase's file scope.
MAX_MESSAGE_BYTES = 256 * 1024

# A single-user local UI: one tab in practice, two while comparing, plus
# sockets that linger for a moment either side of a reload. Eight leaves room
# for that while bounding what a local page can pin — one send queue now, and
# from Phase 3b one fan-out target per socket on every agent event.
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

# How much of a tool call's input the page is shown. Under `-a` there is no
# permission gate, so what the operator can read here is the only account of
# what ran — a command clipped to a shell's first token would be worse than
# not rendering it. 4000 characters is far above any command yet observed and
# still bounded, because this string is agent-authored, is recorded in the
# replay buffer, and is rendered into the DOM. A clipped command says so on
# the page rather than looking complete.
MAX_TOOL_INPUT_CHARS = 4000

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

# ACP protocol version, confirmed against kiro-cli 2.14.1 on this machine:
# `initialize` answers `{"protocolVersion": 1, ...}` in ~1.1 s.
PROTOCOL_VERSION = 1

# Each live session costs roughly five processes and ~306 MB of MCP servers, and
# nothing in this prototype sweeps idle ones. Three is about as much as a
# developer machine should carry while a browser and an IDE are also running.
MAX_SESSIONS = 3

# Wall-clock ceilings on JSON-RPC requests. Every pending future carries one:
# an agent that has stopped answering is otherwise indistinguishable from one
# that is merely slow, and `session/new` is genuinely slow — measured at 5.84 s
# with an earlier spike at ~3.2 s, so the ceiling has to be far above both.
INITIALIZE_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 90.0

# A prompt is the one request whose duration is the model's, not the channel's.
# The measured trivial turn took ~24 s end to end, and a turn that runs tools
# under `-a` is minutes rather than seconds — so the ordinary ceiling would
# abandon working turns. Ten minutes still bounds it, which is the point: a
# request with no ceiling makes a dead agent indistinguishable from a slow one.
PROMPT_TIMEOUT_SECONDS = 600.0

# How long the tree-kill fast path waits for the tree to actually go, so that
# teardown has a post-condition rather than only an intent.
#
# It is *not* bounded inside `__main__.py`'s 5 s server-thread join, and the
# arithmetic says so: uvicorn's 0.1 s shutdown poll, plus the fixed 0.1 s sleep
# in `Server.shutdown`, plus up to DRAIN_TIMEOUT_SECONDS of socket drain, plus
# this, comes to ~5.2 s worst case. Typical is ~0.3-0.5 s, because a killed
# tree is gone long before the ceiling. The overrun is benign for exactly one
# reason: `os._exit(0)` closes the job handle on the way out and the OS kills
# whatever the fast path had not finished killing — which is why a spawn that
# cannot obtain a job object refuses to spawn at all rather than degrading.
KILL_WAIT_SECONDS = 3.0

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

    Measured against kiro-cli 2.14.2: ``agent_message_chunk`` carries a single
    ``{"type": "text", "text": …}`` object, not the list the spec's content
    blocks suggest elsewhere. Both are accepted because only one of them is
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
    """
    return 128 + _string_bytes(frame.get("payload") or {})


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


class _Connection:
    """One browser socket: an outbound queue drained by a single writer task.

    Sends are queued rather than awaited so that Phase 3b's fan-out never
    blocks the agent dispatch path on the slowest attached tab.
    """

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.session_id: str | None = None
        # Each entry is ``(weight, frame)``: the weight is computed once, on
        # the way in, so the writer can release it again without a second walk
        # of the frame it has just serialized.
        self._out: asyncio.Queue = asyncio.Queue(maxsize=SEND_QUEUE_MAXSIZE)
        self._queued_bytes = 0
        self._writer: asyncio.Task | None = None
        self._overflowed = False

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
                    await self.ws.send_text(json.dumps(frame))
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
            log.debug("ACP socket writer stopped: peer gone")
        except Exception:
            # Anything else is a bug in the frames we build. Do not leave a
            # registered socket with a dead writer behind it: it would hold one
            # of MAX_CONNECTIONS slots and swallow every outbound frame in
            # silence, which is indistinguishable from a hung agent.
            log.exception("ACP socket writer failed; retiring the socket")
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
        log.info("ACP socket retired by writer (%s); %d open",
                 close_reason or "peer gone", len(_registry.connections))

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
            log.warning("ACP socket drain gave up with %d frame(s) queued",
                        self._out.qsize())

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
    """Live sockets, and which session each is attached to.

    Phase 3b populates ``subscribers`` from ``new`` and ``subscribe``; in 3a no
    session can exist, which is why ``subscribe`` has nothing to answer with.
    """

    def __init__(self) -> None:
        self.connections: set[_Connection] = set()
        self.subscribers: dict[str, set[_Connection]] = {}

    def attach(self, conn: _Connection, session_id: str) -> None:
        self.detach(conn)
        conn.session_id = session_id
        self.subscribers.setdefault(session_id, set()).add(conn)

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


class _Supervisor:
    """The single ``kiro-cli acp`` process, and the JSON-RPC channel to it.

    Lazily spawned on the first session request — never at import and never at
    startup, so a PowerAtlas launch that never opens ``/acp`` pays nothing.

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
        # Sessions promised but not yet recorded — see `new_session`. Counted
        # against MAX_SESSIONS alongside `sessions`, because the creation of one
        # spans two awaits and the cap has to hold across them.
        self._reserved = 0
        self.agent_info: dict = {}

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
            self.agent_info = (result or {}).get("agentInfo") or {}
            log.info("ACP agent ready: %s (pid %s, protocol %s)",
                     self.agent_info.get("version", "?"),
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
        self.agent_info = {}
        self.sessions.clear()
        self.history.clear()
        self.inflight.clear()
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
        """Send a request and await its result. Always bounded."""
        loop = self._loop
        if loop is None:
            raise AgentDied("The agent channel is not open.")
        request_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = loop.create_future()
        self._pending[request_id] = fut
        payload = {"jsonrpc": "2.0", "id": request_id,
                   "method": method, "params": params}
        try:
            await asyncio.to_thread(self._write, payload)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise AgentTimeout(
                f"The agent did not answer '{method}' within {timeout:.0f}s.")
        finally:
            self._pending.pop(request_id, None)

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
            fut.set_exception(AgentRejected(
                f"{err.get('message', 'agent error')} (code {err.get('code')})"))
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

    def _on_notification(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        session_id = params.get("sessionId")
        if kind == "agent_message_chunk":
            text = _content_text(update.get("content"))
            if text and isinstance(session_id, str):
                _emit(session_id, envelope(
                    "chunk", {"role": "agent", "text": text}, session_id))
            return
        if kind in ("tool_call", "tool_call_update"):
            payload = _tool_payload(update)
            log.info("ACP tool %s: session=%s id=%s status=%s title=%r kind=%s "
                     "input=%.200r", kind, session_id, payload["toolCallId"],
                     payload["status"], payload["title"], payload["kind"],
                     payload["command"])
            if isinstance(session_id, str):
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
        log.debug("ACP notification %s (%s)", method, kind or "-")

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

    async def new_session(self, cwd: str) -> dict:
        """Create one session, never exceeding ``MAX_SESSIONS``.

        The cap is taken as a *reservation* before the first ``await``, not as a
        reading of ``len(self.sessions)`` that the two awaits below then
        invalidate. Creating a session spans ``ensure_started`` and a
        ``session/new`` round-trip measured at 5.84 s; N concurrent ``new``
        frames — which ``_dispatch`` happily turns into N tasks — all used to
        pass a check-then-act test before any of them recorded anything, so N
        sessions were created whatever the cap said. That is not a cosmetic
        overshoot: this cap is the only thing between one socket and memory
        exhaustion at ~306 MB a session, and every excess session is a permanent
        artifact in the user's real kiro-cli store.

        Incrementing and decrementing without suspending in between is what
        makes the reservation atomic: the event loop cannot interleave another
        ``new_session`` between the check and the increment, nor between
        recording the session and releasing its slot.
        """
        if len(self.sessions) + self._reserved >= MAX_SESSIONS:
            raise SessionLimit(
                f"At most {MAX_SESSIONS} sessions at once "
                f"(~306 MB each); close one first.")
        self._reserved += 1
        try:
            await self.ensure_started()
            result = await self._request("session/new", {"cwd": cwd, "mcpServers": []})
            result = result or {}
            session_id = result.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                raise AgentRejected("The agent returned no sessionId.")
            self.sessions[session_id] = {
                "cwd": cwd,
                "created": time.time(),
                "models": result.get("models") or {},
                "modes": result.get("modes") or {},
            }
            self.history[session_id] = _History()
        finally:
            # Every path releases the slot, including cancellation: the session
            # it stood for is either recorded above (and counted by `sessions`
            # from now on) or never existed.
            self._reserved -= 1
        log.info("ACP session created: %s (cwd %s); %d live",
                 session_id, cwd, len(self.sessions))
        return {"sessionId": session_id, "cwd": cwd}

    def record(self, session_id: str, frame: dict) -> None:
        """Append a frame to a session's replay buffer, if it still has one."""
        history = self.history.get(session_id)
        if history is not None:
            history.append(frame)

    async def prompt(self, session_id: str, text: str) -> dict:
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
        result = await self._request(
            "session/prompt",
            {"sessionId": session_id,
             "prompt": [{"type": "text", "text": text}]},
            timeout=PROMPT_TIMEOUT_SECONDS,
        )
        return result or {}


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
    }))
    log.info("ACP socket open (%d/%d)", len(_registry.connections), MAX_CONNECTIONS)

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
        log.info("ACP socket closed (%d open)", len(_registry.connections))


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
    if type_ == "prompt":
        _spawn_task(_handle_prompt(conn, session_id, payload))
        return
    # `cancel` and `close` arrive in Phase 6. Answering with a typed error
    # keeps an unimplemented type a protocol event the page can render, rather
    # than a dropped frame or a traceback that takes the socket down with it.
    conn.send(error_frame(
        "not_implemented",
        f"'{type_}' is not implemented yet — it arrives with session close "
        "and cancel.",
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
    """
    if not session_id:
        conn.send(error_frame(
            "bad_envelope", "'subscribe' needs a sessionId."))
        log.warning("ACP subscribe refused: [bad_envelope] no sessionId")
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


async def _handle_new(conn: _Connection, payload: dict) -> None:
    """Create a session, reporting every failure as a typed ``error`` frame."""
    raw_cwd = payload.get("cwd")
    if raw_cwd is not None and not isinstance(raw_cwd, str):
        conn.send(error_frame("bad_payload", "'cwd' must be a string."))
        return
    # `session/new` was measured at 5.84 s and the spawn adds ~1 s on the first
    # one. Without this the page looks broken for the whole of it.
    conn.send(envelope("meta", {"pending": "new"}))
    try:
        cwd = _resolve_session_cwd(raw_cwd)
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
    text = payload.get("prompt")
    if not isinstance(text, str) or not text.strip():
        refuse("bad_payload", "'prompt' must be a non-empty string.")
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
    if session_id in _supervisor.inflight:
        refuse("turn_in_progress",
               "This session is still answering the previous prompt.")
        return
    _supervisor.inflight.add(session_id)
    log.info("ACP turn start: session=%s (%d chars)", session_id, len(text))

    _emit(session_id, envelope("chunk", {"role": "user", "text": text}, session_id))
    _emit(session_id, envelope("meta", {"turn": "start"}, session_id))
    # Names the state a reload would find if this task never reaches its own
    # end: the turn boundary is what the page derives "still answering" from,
    # so it has to be emitted on the cancellation path too.
    stop_reason = "interrupted"
    try:
        result = await _supervisor.prompt(session_id, text)
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
        _emit(session_id, envelope(
            "meta", {"turn": "end", "stopReason": stop_reason}, session_id))
