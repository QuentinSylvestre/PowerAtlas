"""Browser-facing half of the ACP prototype, and the agent process behind it.

This module owns everything that happens on a ``/ws/acp`` socket once
``web.py`` has accepted it — envelope parsing, the connection and session
registry, the outbound fan-out machinery — and, since Phase 3b, the supervised
``kiro-cli acp`` subprocess it all talks to: spawn, the JSON-RPC handshake,
``session/new``, and teardown. ``prompt``/``cancel``/``close`` still answer a
typed ``not_implemented``; they arrive in Phases 4 and 6.

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
``history_truncated``.

Session identity survives a reload because the page carries ``?sid=…`` and
re-sends ``subscribe`` on connect.

Everything below the registry runs against one lazily spawned process holding N
sessions. Its health is judged from the JSON-RPC channel alone — never from an
exit code and never from ``stderr``, which is ``DEVNULL`` here: the agent has
been observed dying with exit 0 and no stderr at all, and an undrained pipe
deadlocks the child once ~64 KB accumulates in it.
"""

import asyncio
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
    log.warning("pywin32 unavailable — no ACP job object; teardown is best-effort: %s", _e)

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

# How long the tree-kill fast path waits for the tree to actually go. Bounded
# well inside `__main__.py`'s 5 s server-thread join: teardown that overruns it
# is teardown that did not happen, since the process exits regardless.
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
        # `shutdown` (which deliberately releases it) may rebind this.
        self._job = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._start_lock: asyncio.Lock | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self.sessions: dict[str, dict] = {}
        self.agent_info: dict = {}

    # -- lifecycle ---------------------------------------------------------

    def alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

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
            # Captured here, inside the async path, and never at import:
            # uvicorn builds its loop inside `server.run()` on a non-main
            # thread, so an import-time capture gets a different loop or none —
            # and `call_soon_threadsafe` against the wrong loop is a silent
            # black hole for every agent message.
            self._loop = asyncio.get_running_loop()
            await asyncio.to_thread(self._spawn)
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    # Declared explicitly rather than left empty. An agent that
                    # believes we can read files or run terminals will send
                    # `fs/read_text_file` and `terminal/*` requests this client
                    # cannot serve; saying so up front is cheaper than the
                    # catch-all responder having to refuse them one by one.
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                },
                timeout=INITIALIZE_TIMEOUT_SECONDS,
            )
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

        job = None
        if win32job is not None:
            try:
                job = win32job.CreateJobObject(None, "")
                info = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation)
                info["BasicLimitInformation"]["LimitFlags"] |= (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, info)
            except Exception:
                log.exception("ACP job object setup failed; teardown is best-effort")
                job = None

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
            raise AgentSpawnFailed(f"Could not start the agent: {exc}") from exc

        if job is not None:
            try:
                handle = win32api.OpenProcess(
                    win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                    False, proc.pid)
                try:
                    win32job.AssignProcessToJobObject(job, handle)
                finally:
                    win32api.CloseHandle(handle)
            except Exception:
                log.exception("ACP job assignment failed; teardown is best-effort")
                job = None

        self._job = job
        self._proc = proc
        self._reader = threading.Thread(
            target=self._reader_loop, args=(proc,),
            name="acp-reader", daemon=True)
        self._reader.start()
        log.info("ACP agent spawned: pid %d, cwd %s, job=%s",
                 proc.pid, cwd, "yes" if job is not None else "NO")

    def shutdown(self) -> None:
        """Kill the agent and its whole tree. Idempotent; never raises.

        The fast path, not the guarantee — the job object is the guarantee, and
        it is what covers `--stop`, `--restart`, a crash and Task Manager, none
        of which reach this function. Kills only: no graceful protocol
        shutdown and no waiting on the agent, because anything slower than the
        5 s server-thread join in ``__main__.py`` simply does not run.
        """
        proc, self._proc = self._proc, None
        job, self._job = self._job, None
        self.sessions.clear()
        self._start_lock = None
        for fut in tuple(self._pending.values()):
            if not fut.done():
                fut.set_exception(AgentDied("The agent was shut down."))
        self._pending.clear()

        if proc is not None and proc.poll() is None:
            # Logged because this is the only teardown route that leaves any
            # trace at all: `--stop`, `--restart`, a crash and Task Manager all
            # go through the job object, which by definition runs no code here.
            log.info("ACP teardown: killing agent pid %d and its tree", proc.pid)
            self._tree_kill(proc)
        # Released last. While this handle lives the job lives, and closing it
        # is itself a kill — so it doubles as the backstop for anything the
        # tree-kill above missed (a grandchild spawned mid-teardown, an
        # AccessDenied on one branch).
        if job is not None:
            try:
                job.Close()
            except Exception:
                pass

    @staticmethod
    def _tree_kill(proc: subprocess.Popen) -> None:
        if psutil is None:
            try:
                proc.kill()
            except Exception:
                pass
            return
        try:
            parent = psutil.Process(proc.pid)
            kids = parent.children(recursive=True)
            # Parent first, so it cannot spawn more children while we work down
            # the list. The `poll()` guard above is what makes taking this pid
            # safe at all: an already-exited pid can have been recycled onto an
            # unrelated process, which is the exact hazard `presence.py`'s
            # create-time check defends against.
            parent.kill()
            for child in kids:
                try:
                    child.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs([parent, *kids], timeout=KILL_WAIT_SECONDS)
        except psutil.Error:
            pass
        except Exception:
            log.exception("ACP tree-kill failed")

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
        """Blocking read of the agent's stdout, on its own OS thread."""
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    # Banner lines and other non-JSON noise are tolerated
                    # rather than fatal, but never silent: a protocol change
                    # would otherwise read as an agent that says nothing.
                    log.warning("ACP: non-JSON line from agent: %.200s", line)
                    continue
                if isinstance(msg, dict):
                    self._post(self._on_message, msg)
        except Exception:
            # Nothing above may take the thread down without a trace: this
            # thread is the only thing reading the agent, so its death is the
            # channel's death, and the `finally` below is what tells anyone.
            log.exception("ACP reader thread failed")
        finally:
            self._post(self._on_agent_death, proc)

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
        except AcpError:
            pass

    def _on_notification(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind in ("tool_call", "tool_call_update"):
            # Logged from this phase, not from Phase 6 where tool rendering
            # lands. Execution capability arrives with `-a` *now*; without this
            # line three phases would run commands with no record anywhere.
            log.info("ACP tool %s: session=%s id=%s status=%s title=%r kind=%s",
                     kind, params.get("sessionId"), update.get("toolCallId"),
                     update.get("status"), update.get("title"),
                     update.get("kind"))
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
        self._proc = None
        self.sessions.clear()
        for fut in tuple(self._pending.values()):
            if not fut.done():
                fut.set_exception(AgentDied(
                    "The agent stopped answering; its channel closed."))
        self._pending.clear()
        frame = envelope("agent_died", {
            "exitCode": code,
            "message": "The kiro-cli agent exited. Create a new session to "
                       "start another one.",
        })
        for conn in tuple(_registry.connections):
            conn.send(frame)

    # -- sessions ----------------------------------------------------------

    async def new_session(self, cwd: str) -> dict:
        if len(self.sessions) >= MAX_SESSIONS:
            raise SessionLimit(
                f"At most {MAX_SESSIONS} sessions at once "
                f"(~306 MB each); close one first.")
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
        log.info("ACP session created: %s (cwd %s); %d live",
                 session_id, cwd, len(self.sessions))
        return {"sessionId": session_id, "cwd": cwd}


_supervisor = _Supervisor()


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
    # `prompt` and `cancel` arrive with streaming in Phase 4/6, `close` in
    # Phase 6. Answering with a typed error keeps an unimplemented type a
    # protocol event the page can render, rather than a dropped frame or a
    # traceback that takes the socket down with it.
    conn.send(error_frame(
        "not_implemented",
        f"'{type_}' is not part of phase 3b — spawn, handshake and "
        "session/new only.",
        session_id))


def _handle_subscribe(conn: _Connection, session_id: str | None) -> None:
    """Attach this socket to an existing session.

    Replay of the session's event buffer is Phase 4's; there is no buffer yet
    because nothing streams. Attaching is still worth doing now: the page sends
    ``subscribe`` on every reload that carries ``?sid=``, and answering that
    with an error would make a reload look like a lost session when the session
    is in fact fine.
    """
    if not session_id:
        conn.send(error_frame(
            "bad_envelope", "'subscribe' needs a sessionId."))
        return
    meta = _supervisor.sessions.get(session_id)
    if meta is None:
        conn.send(error_frame(
            "unknown_session",
            "This server has no such live session. It may belong to an "
            "earlier PowerAtlas process — create a new one.", session_id))
        return
    _registry.attach(conn, session_id)
    conn.send(envelope("session", {
        "sessionId": session_id,
        "cwd": meta.get("cwd", ""),
        "created": False,
    }, session_id))


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
