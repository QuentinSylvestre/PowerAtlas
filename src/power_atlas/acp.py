"""Browser-facing half of the ACP prototype: wire protocol and socket registry.

Phase 3a scaffolding. This module owns everything that happens on a ``/ws/acp``
socket once ``web.py`` has accepted it — envelope parsing, the connection and
session registry, and the outbound fan-out machinery. The supervised
``kiro-cli acp`` subprocess, the JSON-RPC handshake, ``session/new`` and
teardown land in Phase 3b; nothing here spawns a process yet, so every client
frame is answered with a typed ``not_implemented`` error.

Isolation boundary — this module imports nothing else from ``power_atlas``.
Two caches in the package are plain unlocked ``OrderedDict``s that are safe
only because every current caller runs on the event loop, and Phase 3b adds an
OS reader thread that would not. Importing nothing is what keeps that true by
construction rather than by discipline.

Wire contract, identical in both directions::

    {"type": <str>, "sessionId": <str|null>, "payload": <object>}

Client to server: ``subscribe`` (attach this socket to a session and replay its
buffer), ``new`` (create a session against a cwd), ``prompt``, ``cancel``,
``close``.

Server to client: ``session`` (id and metadata after ``new``/``subscribe``),
``chunk``, ``tool_call``, ``tool_update``, ``meta``, ``error``, ``agent_died``,
``history_truncated``.

Session identity survives a reload because the page carries ``?sid=…`` and
re-sends ``subscribe`` on connect.
"""

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger("power_atlas.acp")

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
    "agent_died", "history_truncated",
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

# Per-socket outbound queue depth. Bounded so that one browser tab that has
# stopped reading drops its own socket instead of growing the server's memory
# while Phase 3b streams chunks at it.
SEND_QUEUE_MAXSIZE = 256

# How long a server-initiated close waits for the outbound queue to reach the
# wire before giving up on it. Bounded because the peer this is waiting on may
# already be dead, and shutdown must not hang on it; two seconds is far longer
# than a loopback socket needs and short enough that a wedged tab does not
# stall the close path.
DRAIN_TIMEOUT_SECONDS = 2.0


def envelope(type_: str, payload: dict | None = None,
             session_id: str | None = None) -> dict:
    """Build a wire frame. The only place the envelope shape is written."""
    return {"type": type_, "sessionId": session_id, "payload": payload or {}}


def error_frame(code: str, message: str, session_id: str | None = None) -> dict:
    """Build a typed ``error`` frame.

    ``code`` is for the client to branch on and stays stable; ``message`` is
    for a human reading the page's log and may be reworded freely.
    """
    return envelope("error", {"code": code, "message": message}, session_id)


class _Connection:
    """One browser socket: an outbound queue drained by a single writer task.

    Sends are queued rather than awaited so that Phase 3b's fan-out never
    blocks the agent dispatch path on the slowest attached tab.
    """

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.session_id: str | None = None
        self._out: asyncio.Queue = asyncio.Queue(maxsize=SEND_QUEUE_MAXSIZE)
        self._writer: asyncio.Task | None = None
        self._overflowed = False

    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop())

    def send(self, frame: dict) -> None:
        """Queue a frame for delivery. Never blocks and never raises."""
        try:
            self._out.put_nowait(frame)
        except asyncio.QueueFull:
            # A socket that has not drained SEND_QUEUE_MAXSIZE frames is not
            # coming back. Let the writer close it rather than keep buffering.
            self._overflowed = True

    async def _write_loop(self) -> None:
        # Names the server-side fault that ended the writer, and is empty for a
        # routine peer-gone exit. It selects the close *code and log level*, not
        # whether a close happens: `_retire` closes on every path it is reached
        # by. Gating the close on this string is what left a live socket behind
        # a dead writer on the routine arm.
        close_reason = ""
        try:
            while True:
                frame = await self._out.get()
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
    # Every client type is recognised; none can be served until Phase 3b brings
    # up the agent. Answering with a typed error keeps an unimplemented type a
    # protocol event the page can render, rather than a dropped frame or a
    # traceback that takes the socket down with it.
    conn.send(error_frame(
        "not_implemented",
        f"'{type_}' arrives in phase 3b, with the supervised kiro-cli process.",
        session_id))
