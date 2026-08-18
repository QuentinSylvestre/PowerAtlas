"""Entry point: background detach, single-instance guard, uvicorn server, system tray."""

import argparse
import ctypes
import faulthandler
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import uvicorn

from .config import (load_config, load_remote_secret, CONFIG_DIR,
                     REMOTE_SECRET_MIN_LEN, REMOTE_SECRET_PATH)
from .interpreter import ensure_project_interpreter

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

_PID_FILE = CONFIG_DIR / "power-atlas.pid"

# `orchestrator.log` is unbounded under a plain `FileHandler`, and two sources
# write to it without a natural end: Phase 2's idle sweeper emits a line per
# tick per stuck session for as long as that session stays stuck, and an
# unauthenticated remote peer drives a WARNING per refused `/remote-auth`
# attempt. The developer's own file had already reached ~10 MB before either of
# those existed. A log that grows until the disk does is a availability bug the
# app inflicts on its host.
#
# 10 MiB x 3 backups = 40 MiB worst case. The size is chosen so one file is
# about what the app produced over its whole life so far, which makes a single
# file roughly a "recent history" unit rather than an arbitrary slice; three
# backups then keep enough context to look back past the incident that made
# someone open the log, while capping the footprint at something no user will
# notice next to the workspaces this app indexes.
#
# `crash.log` is deliberately NOT rotated: `faulthandler` writes to a raw
# descriptor held for the process lifetime (see `_enable_crash_handler`), so a
# rename underneath it would send the next dump to an unlinked inode. It grows
# only on a native crash, which is rare enough that unbounded is honest there.
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUP_COUNT = 3


def _build_log_handler(log_path) -> logging.Handler:
    """The single log handler, size-bounded.

    Its own function so a test can bind a real file and drive a real rollover.
    Asserting the handler class against ``__main__.py``'s source text would pass
    just as happily against a ``RotatingFileHandler`` constructed with no
    ``maxBytes``, which is a plain ``FileHandler`` wearing the right name — it
    never rolls over at all.

    Nothing tails or byte-offsets into this file: ``tray.py`` hands the path to
    ``os.startfile``, which opens whatever is at ``orchestrator.log`` at that
    moment, and that is always the newest segment. ``crash.log`` is a separate
    file on a raw descriptor and is untouched by this.

    On Windows a rollover renames the live file, which fails while another
    process holds it open — a log viewer, typically. ``logging`` routes that
    through ``handleError``, so the effect is a skipped rotation and a file that
    keeps growing until the next attempt succeeds, never a lost record or a
    crashed app.
    """
    return logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8")


# The size bound above caps the *file*; it does not cap the *rate*, and one
# source below the application can saturate it at will.
#
# Measured 2026-07-31 against a real NetBird peer and reproduced identically
# from loopback: a `POST /remote-auth` carrying `Content-Length: 10` followed
# by 200,000 unread bytes leaves h11 in MUST_CLOSE, so uvicorn's attempt to
# send its own 400 raises `LocalProtocolError`. The *application* is correct
# throughout — only ten bytes reach `request.stream()` and it logs one clean
# WARNING — but the layer beneath it writes ~35 lines / ~2,970 bytes per
# request (measured over 12 requests on loopback, 2026-08-01).
#
# **Where those bytes actually come from decides where the filter goes**, and
# the answer is not the obvious one. Attributing the same run per logger:
#
#     uvicorn.error   24 records   1,872 bytes   ( 5%)  two short WARNINGs
#     asyncio         12 records  33,732 bytes   (95%)  the whole traceback
#
# The `LocalProtocolError` escapes uvicorn's own handler and surfaces through
# `asyncio`'s default exception handler, so the expensive part — the traceback
# — is logged by `asyncio` and reaches `orchestrator.log` by propagation to the
# root handler installed in `_run_foreground`. Filtering `uvicorn.error` alone
# would bound 5% of the amplification and read like a fix.
#
# `web._claim_throttle_warning` does not cover any of it. That bounds the
# *application's* WARNING to one per lockout window per peer; these records are
# emitted beneath the app and were confirmed still firing on iterations that
# already returned 429. Combined with the rotation above this is an
# anti-forensics primitive rather than mere noise: ~3,500 such requests fill a
# segment and ~14,000 roll every backup off the end, so an attacker can
# deliberately erase the 403s and 429s recording their own secret-guessing run.
_PROTOCOL_ERROR_LOGGERS = ("uvicorn.error", "asyncio")

# 60 s. Long enough that the residual is a bound rather than a slowdown: one
# full record plus one summary per key per window, and two keys in the measured
# flood, is ~3 KB/min in place of ~3 KB/request. That turns "roll the whole
# 40 MiB in the minutes it takes to send 14,000 requests" into ~10 days of
# uninterrupted flooding which is itself visible in the log throughout. Short
# enough to stay a per-minute heartbeat: an ongoing attack keeps writing
# evidence of itself, and a genuinely new incident surfaces within a minute.
_PROTOCOL_ERROR_WINDOW_SECONDS = 60.0

# The hard ceiling on distinct keys held at once. Expiry alone already bounds
# the dict to "keys seen in the last window", which here is a handful; this
# exists for the case where the key is *not* drawn from a closed set — a record
# whose message carries an address or a counter would otherwise mint a fresh
# key every time. That failure mode is safe (nothing collapses, everything is
# logged) only because it cannot also become unbounded memory. Replacing
# unbounded log growth with unbounded memory growth would be no fix at all,
# which is the same reason `acp._sweep_failures` prunes to the live session set.
_PROTOCOL_ERROR_MAX_KEYS = 64


class _RepeatedRecordFilter(logging.Filter):
    """Collapse repeated identical error records to one per window.

    Threshold is one: the first record of a window passes in full, every
    identical one after it is counted instead of written, and when the window
    closes a single line states how many were suppressed. One is the right
    threshold because the first record already carries the whole diagnostic
    payload — the traceback — and a second copy of it adds nothing a reader can
    use.

    **Suppression is never silent.** The point of the change is that the log
    stays trustworthy evidence, so a dropped record always survives as a count.

    Identity is ``(logger, level, message-or-nothing, exception type, raise
    site)``, and the middle term is the load-bearing one. `asyncio`'s default
    exception handler composes its `msg` out of the transport, protocol and
    handle *reprs*, every one of which carries a memory address — so keying on
    the message would mint a key per request and collapse nothing, which is
    exactly the flood being fixed. When a record carries an exception, the
    message is therefore dropped from the key and the exception's type plus the
    innermost frame of its traceback (file, line) stand in as identity: both are
    address-free, both are stable across repetitions of one failure, and both
    differ the moment the failure genuinely differs. Records without an
    exception keep the `msg` *template* (not `record.args`, which would carry
    the per-request peer address and defeat the collapse the same way).

    The message is still *shown* — the first record's own text is kept for the
    summary line — so normalising the key costs nothing in readability.

    Records below WARNING pass through untouched and are never counted, so
    uvicorn's startup and shutdown lines, which share `uvicorn.error`, cannot be
    swallowed however often they repeat. They do pass through the expiry sweep,
    so they also serve as flush points for a pending summary.
    """

    def __init__(self, window_seconds: float = _PROTOCOL_ERROR_WINDOW_SECONDS,
                 max_keys: int = _PROTOCOL_ERROR_MAX_KEYS,
                 clock=time.monotonic) -> None:
        super().__init__()
        self._window = window_seconds
        self._max_keys = max_keys
        self._clock = clock
        # Both the event loop thread and the tray/sweeper threads log here.
        self._lock = threading.Lock()
        # key -> [window opened at, suppressed count, logger name, levelno,
        #         human-readable detail for the summary].
        # Insertion-ordered, which is also window-start order, so evicting the
        # first entry evicts the window closest to expiring anyway.
        self._windows: dict[tuple, list] = {}

    @staticmethod
    def _key(record: logging.LogRecord) -> tuple:
        exc_info = record.exc_info
        if isinstance(exc_info, tuple) and exc_info and exc_info[0] is not None:
            exc_name = getattr(exc_info[0], "__name__", str(exc_info[0]))
            # The innermost frame — where it was actually raised. Walking the
            # traceback is O(depth) and touches no formatting, unlike the
            # rendered text this exists to avoid producing.
            origin = ()
            tb = exc_info[2]
            while tb is not None:
                origin = (tb.tb_frame.f_code.co_filename, tb.tb_lineno)
                tb = tb.tb_next
            return (record.name, record.levelno, "", exc_name, origin)
        return (record.name, record.levelno, str(record.msg), "", ())

    @staticmethod
    def _detail(record: logging.LogRecord, key: tuple) -> str:
        """What the summary shows. Built once, from the window's first record."""
        try:
            first_line = record.getMessage().splitlines()[0]
        except Exception:
            first_line = str(record.msg)
        detail = repr(first_line[:120])
        if key[3]:
            where = f" at {os.path.basename(key[4][0])}:{key[4][1]}" if key[4] else ""
            detail += f" [{key[3]}{where}]"
        return detail

    def filter(self, record: logging.LogRecord) -> bool:
        # A summary this filter emitted itself. Checked first so that handing
        # it back to `Logger.handle` cannot recurse or be counted.
        if getattr(record, "_pa_repeat_summary", False):
            return True
        now = self._clock()
        counted = record.levelno >= logging.WARNING
        key = self._key(record) if counted else None
        with self._lock:
            summaries = self._close_expired(now)
            if not counted:
                allow = True
            elif key in self._windows:
                self._windows[key][1] += 1
                allow = False
            else:
                self._windows[key] = [now, 0, record.name, record.levelno,
                                      self._detail(record, key)]
                summaries.extend(self._evict_overflow())
                allow = True
        # Emitted outside the lock: `Logger.handle` takes the handler locks, and
        # taking those under this one would invert the order against any other
        # thread logging through the same handler.
        for name, levelno, text in summaries:
            self._emit_summary(name, levelno, text)
        return allow

    def _close_expired(self, now: float) -> list:
        out = []
        for key in tuple(self._windows):
            opened, count, name, levelno, detail = self._windows[key]
            if now - opened < self._window:
                continue
            del self._windows[key]
            if count:
                out.append(self._summary(detail, count, now - opened, name, levelno))
        return out

    def _evict_overflow(self) -> list:
        out = []
        while len(self._windows) > self._max_keys:
            key = next(iter(self._windows))
            opened, count, name, levelno, detail = self._windows.pop(key)
            if count:
                out.append(self._summary(
                    detail, count, self._clock() - opened, name, levelno))
        return out

    @staticmethod
    def _summary(detail: str, count: int, elapsed: float,
                 name: str, levelno: int) -> tuple:
        return (name, levelno,
                f"{count} further identical {logging.getLevelName(levelno)} "
                f"record(s) suppressed over {elapsed:.0f}s: {detail}")

    def _emit_summary(self, name: str, levelno: int, text: str) -> None:
        record = logging.LogRecord(name, levelno, __file__, 0, text, None, None)
        record._pa_repeat_summary = True
        logging.getLogger(name).handle(record)

    def flush(self) -> None:
        """Emit every pending summary now, whatever its window says.

        `uvicorn.Config(log_level="warning")` leaves these loggers silent in
        normal operation, so the sweep on the next record cannot be relied on
        to arrive: after a flood stops, its final count would sit unwritten.
        Called on the shutdown path so the last window is always accounted for.
        """
        with self._lock:
            now = self._clock()
            summaries = [self._summary(detail, count, now - opened, name, levelno)
                         for (opened, count, name, levelno, detail)
                         in self._windows.values() if count]
            self._windows.clear()
        for name, levelno, text in summaries:
            self._emit_summary(name, levelno, text)


def _install_repeat_filter(
        logger_names=_PROTOCOL_ERROR_LOGGERS) -> "_RepeatedRecordFilter":
    """Attach the collapse filter to every logger that carries the flood, once.

    On the loggers rather than on the root handler, so `power_atlas`'s own
    records — which have their own throttle in `web._claim_throttle_warning` —
    are untouched. `asyncio` is not optional here: it carries 95% of the
    measured bytes, and filtering `uvicorn.error` alone would leave the
    traceback, and therefore the amplification, in place.

    One filter instance shared across both loggers, so the key cap and the
    shutdown flush are single. Filters run only on the logger a record was
    emitted through, never again on ancestors during propagation, so sharing
    cannot double-count; the logger name is part of the key regardless.

    Safe to call before `uvicorn.Config` is constructed: `Config.__init__` runs
    `dictConfig` over `uvicorn.config.LOGGING_CONFIG`, which replaces
    `uvicorn.error`'s handlers but leaves its filters alone.
    """
    filt = None
    for name in logger_names:
        for existing in logging.getLogger(name).filters:
            if isinstance(existing, _RepeatedRecordFilter):
                filt = existing
                break
        if filt is not None:
            break
    if filt is None:
        filt = _RepeatedRecordFilter()
    for name in logger_names:
        logger = logging.getLogger(name)
        if not any(isinstance(f, _RepeatedRecordFilter) for f in logger.filters):
            logger.addFilter(filt)
    return filt


# The transport ceiling on an inbound WebSocket frame. uvicorn has decoded the
# whole frame before the application sees it, so `/ws/acp`'s own 256 KiB cap
# (``acp.MAX_MESSAGE_BYTES``) refuses frames the server has already buffered in
# full — 16 MiB of one, at uvicorn's default. A megabyte bounds that while
# staying above the application cap, which keeps the typed 1009 refusal the one
# a client actually meets. A literal rather than an import from ``acp``: that
# module is imported under a guard precisely because it may fail to load, and
# the launcher has to start the server either way.
WS_MAX_SIZE_BYTES = 1024 * 1024

_mutex_handle = None


def _bind(host: str, port: int) -> socket.socket:
    """Create one listening socket.

    Do **not** replace this with ``uvicorn.Config.bind_socket``. That helper
    sets ``SO_REUSEADDR``, which on Windows lets a *different local process*
    bind the identical ``127.0.0.1:<port>`` and hijack connections to a surface
    that serves ``_ACP_TOKEN`` and fronts ``kiro-cli acp -a``. It also sets
    ``set_inheritable(True)``, handing the listener to every child process the
    app spawns — and this app spawns terminals and agents.

    ``SO_EXCLUSIVEADDRUSE`` is the Windows opposite of ``SO_REUSEADDR``: it
    makes a second bind to the same address fail. It does not exist on POSIX,
    where the default ``SO_REUSEADDR``-off behaviour is already exclusive.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(family, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.set_inheritable(False)
        s.bind((host, port))
        s.listen()
    except BaseException:
        s.close()
        raise
    return s


def _bind_remote_socket(log, config, socks: list, port: int) -> bool:
    """Append the remote listener to ``socks``, or explain why not.

    Never raises. PowerAtlas autostarts at login and NetBird's interface may
    not be up yet, which raises ``OSError`` (Windows ``WinError 10049``); the
    existing port-in-use retry does not cover that, so unhandled this would
    make the app **exit 1** rather than degrade to loopback-only.

    The secret is checked **before** the socket is created, not at request
    time. Otherwise the remote surface is bound and accepting while
    authentication is structurally impossible — a listener on a network of 17
    peers with no way to say no.

    ``web``'s two startup setters are called only after the bind succeeds, so
    the Host allowlist never widens for an address nothing is listening on and
    the process holds no secret it cannot use.

    ``port`` is always ``config.port`` in practice: ``_choose_sockets`` does not
    call this at all once the loopback bind has fallen back to an OS-assigned
    port, because a remote listener on a port that changes every restart is
    unreachable by the bookmark D25 assumes and is an exposed listener nobody
    can find on purpose.
    """
    address = (config.remote_bind_address or "").strip()
    if not address:
        return False
    secret = load_remote_secret()
    if not secret:
        log.error("Remote bind to %s skipped: %s is missing, empty or shorter "
                  "than %d characters. Remote access stays disabled.",
                  address, REMOTE_SECRET_PATH, REMOTE_SECRET_MIN_LEN)
        return False
    try:
        socks.append(_bind(address, port))
    except OSError as exc:
        log.error("Remote bind to %s:%d failed (%s); loopback only",
                  address, port, exc)
        return False
    from .web import set_remote_host, set_remote_secret
    set_remote_host(address)
    set_remote_secret(secret)
    log.info("Remote access enabled on %s:%d", address, port)
    return True


def _choose_sockets(log, config, desired_port: int) -> tuple[list, int]:
    """Bind every listener and report the port ``server_url`` must name.

    Extracted from ``main`` so the returned port can be asserted against real
    bound sockets rather than against the source text. The invariant is narrow
    and easy to break silently: **the returned port is the port of
    ``socks[0]``, the loopback listener** — never the remote one, and never
    whichever socket uvicorn happens to register first. Appending
    ``port = socks[-1].getsockname()[1]`` after the remote bind leaves every
    source-text assertion in this suite intact while making ``server_url`` name
    a port nothing listens on at ``127.0.0.1``, so tray and peek open a dead
    URL. Only a test holding real sockets can see that.

    Raises ``OSError`` when no loopback listener can be created at all, having
    first logged **which** binds were attempted — this is the only frame that
    knows whether the random fallback ran, so the caller turns the exception
    into a clean exit without restating it. The remote bind never raises — it
    degrades to loopback-only by design (D27), and is skipped entirely when the
    loopback bind fell back to an OS-assigned port.
    """
    # Both listeners are created here and handed to ONE `uvicorn.Server` via
    # `run(sockets=…)` (D23). `uvicorn.Config(host=)` takes a single address, so
    # two addresses meant either `0.0.0.0` — a listener on every network this
    # laptop ever joins — or two `Server` instances, which would run lifespan
    # twice: two background-refresh loops, two sweepers racing on the same
    # sessions, and `acp.shutdown()` called twice.
    #
    # Loopback is MANDATORY and keeps its port-in-use fallback. Only the remote
    # bind may degrade: a remote-only listener with no loopback is a state the
    # whole model assumes cannot exist.
    fell_back = False
    try:
        socks = [_bind("127.0.0.1", desired_port)]
    except OSError as exc:
        if desired_port <= 0:
            # No fallback exists on this path: the OS was already asked for any
            # free port and had none to give. Saying so here is what lets the
            # caller stop claiming a fallback it cannot see was never tried.
            log.error("Loopback bind on an OS-assigned port failed: %s", exc)
            raise
        log.warning("Port %d unavailable (%s), falling back to random port",
                    desired_port, exc)
        try:
            socks = [_bind("127.0.0.1", 0)]
        except OSError as fallback_exc:
            log.error("Loopback bind failed on port %d and on the random "
                      "fallback: %s", desired_port, fallback_exc)
            raise
        fell_back = True
    # Read from the loopback socket explicitly, never from `server.servers[0]`:
    # with two sockets, index 0 is merely whichever one uvicorn happened to
    # register first, and this value becomes the URL tray and peek open.
    loopback_sock = socks[0]
    port = loopback_sock.getsockname()[1]

    # A fallback port is a *new* port on every restart. D25's premise is that a
    # phone holds a bookmarked `http://<address>:<port>/…`, so a remote listener
    # on an OS-assigned port is unreachable by the only means anyone was ever
    # going to reach it by — while still being a listener on a 17-peer network,
    # in front of `kiro-cli acp -a`. Binding it buys nothing and exposes a
    # surface no legitimate user can find on purpose, so it is not bound at all.
    #
    # This deliberately does not fall back to `config.port` for the remote
    # socket: both listeners share one port by design (D23/D25), and a remote
    # socket on a port the loopback one could not take would make `server_url`
    # and the remote URL disagree — the exact confusion `_choose_sockets`
    # exists to prevent.
    if fell_back and (config.remote_bind_address or "").strip():
        log.warning("Remote bind to %s skipped: loopback fell back to port %d, "
                    "so the remote listener would be on a port that changes "
                    "every restart and no bookmarked URL can reach. Free port "
                    "%d and restart to re-enable remote access.",
                    config.remote_bind_address, port, config.port)
    else:
        _bind_remote_socket(log, config, socks, port)

    log.info("Listening on %s",
             [s.getsockname()[:2] for s in socks])
    return socks, port


def _write_pid() -> None:
    """Write current PID to file for stop/restart commands."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    """Remove PID file on shutdown."""
    try:
        _PID_FILE.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_pid() -> int | None:
    """Read PID of running instance, or None if not running."""
    # Try PID file first
    try:
        pid = int(_PID_FILE.read_text().strip())
        if _pid_alive(pid):
            return pid
    except (OSError, ValueError):
        pass
    # Fallback: scan /proc for running power_atlas process (Linux only)
    if sys.platform != "win32":
        import glob as _glob
        for proc_dir in _glob.glob("/proc/[0-9]*/cmdline"):
            try:
                with open(proc_dir, "rb") as f:
                    cmdline = f.read()
                if b"power_atlas" in cmdline and b"--foreground" in cmdline:
                    pid = int(proc_dir.split("/")[2])
                    if pid != os.getpid():
                        return pid
            except (OSError, ValueError):
                continue
    return None


def _stop_running() -> bool:
    """Stop the running instance. Returns True if a process was stopped."""
    pid = _read_pid()
    if pid is None:
        print("PowerAtlas is not running.")
        return False
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 0)
            kernel32.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGTERM)
    _remove_pid()
    print(f"PowerAtlas stopped (pid {pid}).")
    return True


def _exit_immediately(code: int) -> None:
    """Exit without unwinding, flushing stdio first.

    ``os._exit`` is deliberate — uvicorn and pystray leave threads that a normal
    exit would wait on — but it also discards buffered output. Python
    line-buffers stdout only when it is a console, so under a pipe every message
    printed just before the exit is lost. ``pythonw`` has no stdio at all and
    leaves the streams as None.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    os._exit(code)


def _single_instance_guard() -> None:
    """Exit if another instance is already running. Windows: named mutex. Linux: lockfile."""
    global _mutex_handle
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, "PowerAtlasMutex")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            print("PowerAtlas is already running.")
            _exit_immediately(0)
    else:
        import fcntl
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = CONFIG_DIR / "power-atlas.lock"
        _mutex_handle = open(lock_path, "w")
        try:
            fcntl.flock(_mutex_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("PowerAtlas is already running.")
            _exit_immediately(0)


def _release_mutex() -> None:
    global _mutex_handle
    if _mutex_handle:
        if sys.platform == "win32":
            ctypes.WinDLL("kernel32").CloseHandle(_mutex_handle)
        else:
            import fcntl
            fcntl.flock(_mutex_handle, fcntl.LOCK_UN)
            _mutex_handle.close()
        _mutex_handle = None


def _relaunch_detached() -> None:
    """Re-exec ourselves as a detached background process, then exit."""
    cmd = [sys.executable, "-m", "power_atlas", "--foreground"]
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    print("PowerAtlas started in the background. Quit from the tray icon.")
    print("Run with --foreground to keep it attached to this terminal.")


def _migrate_legacy() -> None:
    """One-time migration from kiro-orchestrator to power-atlas. Windows only."""
    if sys.platform != "win32":
        return
    import shutil
    from pathlib import Path
    localappdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    old_config = Path(localappdata) / "kiro-orchestrator"
    new_config = Path(localappdata) / "power-atlas"
    if old_config.exists() and not new_config.exists():
        try:
            shutil.copytree(old_config, new_config)
        except OSError:
            shutil.rmtree(new_config, ignore_errors=True)
            return
        print(f"Migrated settings from {old_config} to {new_config}")
    # Clean up old autostart shortcut
    appdata = os.environ.get("APPDATA", "")
    old_shortcut = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Kiro Orchestrator.lnk"
    if old_shortcut.exists():
        try:
            old_shortcut.unlink()
        except OSError:
            return
        try:
            from .autostart import enable
            enable()
        except Exception:
            print("Warning: could not re-create autostart shortcut after migration")


def _ensure_display() -> None:
    """Ensure DISPLAY or WAYLAND_DISPLAY is set. Probe for running display
    servers if the env var is missing; abort if none found.
    """
    if sys.platform == "win32":
        return
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return
    # Probe for X11 socket — /tmp/.X11-unix/X<N> exists when Xorg is running
    import glob
    x_sockets = glob.glob("/tmp/.X11-unix/X*")
    if x_sockets:
        sock_name = os.path.basename(x_sockets[0])  # "X0"
        display_num = sock_name[1:]  # "0"
        os.environ["DISPLAY"] = f":{display_num}"
        return
    # Check for Wayland socket in XDG_RUNTIME_DIR
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if os.path.isdir(xdg_runtime):
        for entry in os.listdir(xdg_runtime):
            if entry.startswith("wayland-") and not entry.endswith(".lock"):
                os.environ["WAYLAND_DISPLAY"] = entry
                return
    print("ERROR: No display server found (DISPLAY and WAYLAND_DISPLAY unset, "
          "no X11/Wayland sockets detected). PowerAtlas requires a desktop session.",
          file=sys.stderr)
    sys.exit(1)


_crash_log = None  # module-level: faulthandler writes to this file's descriptor for the process lifetime


def _enable_crash_handler() -> None:
    """Dump every thread's Python traceback to ``crash.log`` on a native crash.

    A hard crash otherwise leaves only a minidump, which names the faulting
    machine instruction but not the Python code that got there — the 2026-07-28
    access violation (the GC dereferencing a tuple element whose ``ob_type`` was
    NULL) cost a dump parse to reach a subsystem-level guess.

    Two constraints shape this. The file must be explicit: ``faulthandler``
    defaults to ``sys.stderr``, which is None under ``pythonw``, the very
    configuration that crashes at login. And the handle must outlive this
    function — the handler writes to the raw descriptor, so letting the object
    be collected would close it. It is a separate file from ``orchestrator.log``
    because ``logging.FileHandler`` holds its own buffered handle on that one and
    the descriptor-level writes would land mid-line.
    """
    global _crash_log
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _crash_log = open(CONFIG_DIR / "crash.log", "a", encoding="utf-8")
        _crash_log.write(f"\n=== pid {os.getpid()} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        _crash_log.flush()
        faulthandler.enable(file=_crash_log, all_threads=True)
    except Exception as e:
        logging.getLogger("power_atlas").warning("Crash handler unavailable: %s", e)


def _run_foreground() -> None:
    """Run the server + tray in this process (blocking)."""
    _migrate_legacy()
    log_path = CONFIG_DIR / "orchestrator.log"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[_build_log_handler(log_path)],
    )
    repeat_filter = _install_repeat_filter()
    log = logging.getLogger("power_atlas")
    _enable_crash_handler()
    log.info("Starting power-atlas (foreground)")

    _single_instance_guard()
    _ensure_display()
    _write_pid()
    config = load_config()

    # Import the real app
    from .web import acp as acp_module, app

    # ACP's tunables, read from configuration exactly once and pushed in as
    # module-level names. `at_capacity()` runs on the event loop and
    # `load_config()` is an uncached whole-file TOML parse, so reading them
    # per call would reproduce the stall `_handle_new` already threads out to
    # avoid. Reusing `web`'s guarded import rather than importing `acp` here
    # keeps the "an ACP import failure disables /acp, it does not stop the
    # app" property in one place.
    if acp_module is not None:
        acp_module.apply_config(config)

    # The same "read once at startup" values, snapshotted so the settings panel
    # can tell what this process is running from what is merely on disk.
    # Deliberately outside the `acp_module` guard above: three of the six keys
    # are ACP's, but `port`, `peek_hotkey` and `remote_bind_address` are not,
    # and an ACP import failure must not leave the panel unable to say what is
    # in force for the other three. Before the server binds, so nothing can
    # serve `/api/settings` ahead of the snapshot.
    from .web import set_startup_config
    set_startup_config(config)

    # Determine port: 0 = random, >0 = attempt static with random fallback
    desired_port = config.port

    def _make_patched_startup(srv, evt):
        """Factory to create a patched startup coroutine for a given server instance."""
        orig = srv.startup
        async def _patched(sockets=None):
            await orig(sockets=sockets)
            evt.set()
        return _patched

    try:
        socks, port = _choose_sockets(log, config, desired_port)
    except OSError:
        # `_choose_sockets` has already logged which binds it attempted and why
        # the last one failed. This line claimed "on port %d and on the random
        # fallback" unconditionally, which is false on the `port = 0` path —
        # that path raises before any fallback is attempted, because there is
        # none to attempt. The distinction lives where it is known; here we log
        # only the consequence.
        log.error("No loopback listener could be bound; exiting")
        print("ERROR: Server failed to start", file=sys.stderr)
        _remove_pid()
        sys.exit(1)

    uv_config = uvicorn.Config(app, host="127.0.0.1", port=port,
                               log_level="warning",
                               ws_max_size=WS_MAX_SIZE_BYTES,
                               # `ProxyHeadersMiddleware` OVERWRITES
                               # `scope["client"]` from `X-Forwarded-For` for
                               # any peer in `forwarded_allow_ips`, and
                               # `proxy_headers` defaults to True. With
                               # `FORWARDED_ALLOW_IPS=*` in the environment,
                               # a remote peer could then declare itself
                               # loopback and skip both the path allowlist and
                               # the cookie — the exact class of bug D26 exists
                               # to close. PowerAtlas is never behind a proxy.
                               proxy_headers=False,
                               # A ceiling on simultaneously-open connections,
                               # so an unauthenticated remote peer cannot open
                               # arbitrarily many at once. It is the process
                               # sibling of `_REMOTE_AUTH_MAX_BODY`: that caps
                               # one request, this caps how many can be in
                               # flight. 128 cannot starve normal use — a
                               # single dashboard holds one websocket plus a
                               # handful of short-lived fetches, and the
                               # remote client the same, so real usage sits in
                               # the low tens even with several devices open.
                               # uvicorn answers 503 past the ceiling rather
                               # than queueing, which is the right failure: a
                               # refused connection is recoverable, an
                               # exhausted event loop is not.
                               limit_concurrency=128)
    server = uvicorn.Server(uv_config)
    ready_event = threading.Event()
    server.startup = _make_patched_startup(server, ready_event)

    # `run()` blocks, so the thread + ready_event scaffolding has to stay or
    # neither the tray nor peek ever starts.
    server_thread = threading.Thread(target=server.run,
                                     kwargs={"sockets": socks}, daemon=True)
    server_thread.start()
    ready_event.wait(timeout=10)

    if not ready_event.is_set() or not server.servers:
        log.error("Server failed to start on port %d", port)
        print("ERROR: Server failed to start", file=sys.stderr)
        _remove_pid()
        sys.exit(1)

    server_url = f"http://127.0.0.1:{port}"
    log.info("Server ready at %s", server_url)

    # Warmup pinned workspaces in background (non-blocking)
    from .peek import create_peek
    from .tray import run_tray, restart_requested, set_peek_stop_callback, trigger_restart
    from .data import warmup_all
    from .web import set_restart_callback
    set_restart_callback(trigger_restart)

    peek = create_peek(server_url, config.peek_hotkey)

    if peek:
        set_peek_stop_callback(peek.stop)

        # pywebview requires the main thread on all platforms
        # (Windows EdgeChromium + Linux GTK both enforce this).
        # Pystray runs on a background daemon thread.
        tray_thread = threading.Thread(target=run_tray, args=(server_url, config), daemon=True)
        tray_thread.start()
        threading.Thread(target=warmup_all, args=(config.pinned_folders, config.pinned_sessions), daemon=True).start()
        peek.start(on_main_thread=True)  # blocks until peek.stop() is called
    else:
        # No peek available — original path (pystray on main thread)
        threading.Thread(target=warmup_all, args=(config.pinned_folders, config.pinned_sessions), daemon=True).start()
        run_tray(server_url, config)

    # Shutdown sequence
    if peek:
        peek.stop()  # no-op if already stopped by tray callback

    server.should_exit = True
    server_thread.join(timeout=5)

    should_restart = restart_requested()

    _remove_pid()
    _release_mutex()
    # Ahead of the logging teardown below, or the last window's count is
    # written to a closed handler and lost — the one case the
    # sweep-on-next-record cannot reach, since `log_level="warning"` leaves
    # this logger silent once a flood stops.
    repeat_filter.flush()
    logging.shutdown()

    if should_restart:
        _relaunch_detached()

    _exit_immediately(0)


def main() -> None:
    # Before anything reads a config or takes the single-instance mutex: every
    # entry point converges on the checkout's venv, so the app can never run on
    # a different dependency stack than the one the suite verifies.
    ensure_project_interpreter()

    parser = argparse.ArgumentParser(prog="power-atlas")
    parser.add_argument(
        "-f", "--foreground", action="store_true",
        help="Run in this terminal instead of detaching to the background",
    )
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--stop", action="store_true",
        help="Stop the running PowerAtlas instance",
    )
    action_group.add_argument(
        "--restart", action="store_true",
        help="Restart the running PowerAtlas instance",
    )
    args = parser.parse_args()

    if args.stop:
        _stop_running()
        return

    if args.restart:
        old_pid = _read_pid()
        _stop_running()
        deadline = time.monotonic() + 5.0
        while old_pid and _pid_alive(old_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if old_pid and _pid_alive(old_pid):
            print("Old instance still running after 5s; not restarting.", file=sys.stderr)
            return
        # Fall through to start a new instance
        _single_instance_guard()
        _relaunch_detached()
        return

    if args.foreground:
        _run_foreground()
    else:
        _single_instance_guard()
        _relaunch_detached()


if __name__ == "__main__":
    main()
