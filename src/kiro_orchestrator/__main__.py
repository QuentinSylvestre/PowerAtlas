"""Entry point: background detach, single-instance guard, uvicorn server, system tray."""

import argparse
import ctypes
import json
import subprocess
import sys
import threading

import uvicorn

from .config import load_config
from .tray import run_tray

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000


def _single_instance_guard() -> None:
    """Exit if another instance is already running (Windows named mutex)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "KiroOrchestratorMutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)


def _relaunch_detached() -> None:
    """Re-exec ourselves as a detached background process, then exit."""
    cmd = [sys.executable, "-m", "kiro_orchestrator", "--foreground"]
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
    print("Kiro Orchestrator started in the background. Quit from the tray icon.")
    print("Run with --foreground to keep it attached to this terminal.")


def _run_foreground() -> None:
    """Run the server + tray in this process (blocking)."""
    import logging

    from .config import CONFIG_DIR
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
    log = logging.getLogger("kiro_orchestrator")
    log.info("Starting kiro-orchestrator (foreground)")

    _single_instance_guard()
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
        sys.exit(1)

    port = server.servers[0].sockets[0].getsockname()[1]
    server_url = f"http://127.0.0.1:{port}"
    log.info("Server ready at %s", server_url)

    # Tray blocks on main thread; on quit, shutdown
    run_tray(server_url, config)

    server.should_exit = True
    server_thread.join(timeout=5)
    sys.exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(prog="kiro-orchestrator")
    parser.add_argument(
        "-f", "--foreground", action="store_true",
        help="Run in this terminal instead of detaching to the background",
    )
    args = parser.parse_args()

    if args.foreground:
        _run_foreground()
    else:
        _single_instance_guard()
        _relaunch_detached()


if __name__ == "__main__":
    main()
