"""Entry point: background detach, single-instance guard, uvicorn server, system tray."""

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import threading

import uvicorn

from .config import load_config, CONFIG_DIR

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

_PID_FILE = CONFIG_DIR / "power-atlas.pid"

_mutex_handle = None


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


def _single_instance_guard() -> None:
    """Exit if another instance is already running. Windows: named mutex. Linux: lockfile."""
    global _mutex_handle
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, "PowerAtlasMutex")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            os._exit(0)
    else:
        import fcntl
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = CONFIG_DIR / "power-atlas.lock"
        _mutex_handle = open(lock_path, "w")
        try:
            fcntl.flock(_mutex_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os._exit(0)


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


def _run_foreground() -> None:
    """Run the server + tray in this process (blocking)."""
    import logging
    _migrate_legacy()
    log_path = CONFIG_DIR / "orchestrator.log"
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    log = logging.getLogger("power_atlas")
    log.info("Starting power-atlas (foreground)")

    _single_instance_guard()
    _ensure_display()
    _write_pid()
    config = load_config()

    # Import the real app
    from .web import app

    # Start uvicorn on dynamic port in daemon thread
    uv_config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(uv_config)

    ready_event = threading.Event()
    original_startup = server.startup

    async def _patched_startup(sockets=None):
        await original_startup(sockets=sockets)
        ready_event.set()

    server.startup = _patched_startup

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    ready_event.wait(timeout=10)

    if not ready_event.is_set() or not server.servers:
        print("ERROR: Server failed to start", file=sys.stderr)
        _remove_pid()
        sys.exit(1)

    port = server.servers[0].sockets[0].getsockname()[1]
    server_url = f"http://127.0.0.1:{port}"
    log.info("Server ready at %s", server_url)

    # Warmup pinned workspaces in background (non-blocking)
    from .peek import create_peek
    from .tray import run_tray, restart_requested, set_peek_stop_callback
    import threading as _threading
    from .data import warmup_pinned

    peek = create_peek(server_url, config.peek_hotkey)

    if peek:
        set_peek_stop_callback(peek.stop)

        if sys.platform != "win32":
            # Linux: pywebview needs main thread (GTK).
            # Pystray on background daemon thread.
            tray_thread = _threading.Thread(target=run_tray, args=(server_url, config), daemon=True)
            tray_thread.start()
            _threading.Thread(target=warmup_pinned, args=(config.pinned_folders,), daemon=True).start()
            peek.start(on_main_thread=True)  # blocks until peek.stop() is called
        else:
            # Windows: pywebview on background thread, pystray on main.
            peek.start(on_main_thread=False)
            _threading.Thread(target=warmup_pinned, args=(config.pinned_folders,), daemon=True).start()
            run_tray(server_url, config)  # blocks until tray quit
    else:
        # No peek available — original path
        _threading.Thread(target=warmup_pinned, args=(config.pinned_folders,), daemon=True).start()
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

    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="power-atlas")
    parser.add_argument(
        "-f", "--foreground", action="store_true",
        help="Run in this terminal instead of detaching to the background",
    )
    parser.add_argument(
        "--stop", action="store_true",
        help="Stop the running PowerAtlas instance",
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="Restart the running PowerAtlas instance",
    )
    args = parser.parse_args()

    if args.stop:
        _stop_running()
        return

    if args.restart:
        _stop_running()
        import time
        time.sleep(0.5)
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
