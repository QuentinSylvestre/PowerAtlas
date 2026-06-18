"""Entry point: single-instance guard, uvicorn server, system tray."""

import ctypes
import sys
import threading

import uvicorn
from fastapi import FastAPI

from .config import load_config
from .tray import run_tray

# Placeholder app until Phase 3 provides web.py
_app = FastAPI()


@_app.get("/health")
def _health():
    return {"ok": True}


def _single_instance_guard() -> None:
    """Exit if another instance is already running (Windows named mutex)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "KiroOrchestratorMutex")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        sys.exit(0)


def main() -> None:
    _single_instance_guard()
    config = load_config()

    # Start uvicorn on dynamic port in daemon thread
    uv_config = uvicorn.Config(_app, host="127.0.0.1", port=0, log_level="warning")
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

    # Get actual bound port
    port = server.servers[0].sockets[0].getsockname()[1]
    server_url = f"http://127.0.0.1:{port}"

    # Tray blocks on main thread; on quit, shutdown uvicorn
    run_tray(server_url, config)

    # After tray exits
    server.should_exit = True
    server_thread.join(timeout=5)
    sys.exit(0)


if __name__ == "__main__":
    main()
