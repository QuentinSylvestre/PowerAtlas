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

    Raises ``OSError`` when no loopback listener can be created at all; the
    caller turns that into a clean exit. The remote bind never raises — it
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
            log.error("Loopback bind on an OS-assigned port failed: %s", exc)
            raise
        log.warning("Port %d unavailable (%s), falling back to random port",
                    desired_port, exc)
        socks = [_bind("127.0.0.1", 0)]
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
    except OSError as exc:
        log.error("Loopback bind failed on port %d and on the random "
                  "fallback: %s", desired_port, exc)
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
    from .tray import run_tray, restart_requested, set_peek_stop_callback
    from .data import warmup_all

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
