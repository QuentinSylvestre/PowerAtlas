"""System tray icon and menu."""

import os
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw, ImageFont

from .config import Config, CONFIG_DIR, load_config, save_config

_shutdown_event = threading.Event()


def _create_icon() -> Image.Image:
    img = Image.new("RGBA", (16, 16), (60, 120, 220, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 11)
    except OSError:
        font = ImageFont.load_default()
    draw.text((3, 1), "K", fill="white", font=font)
    return img


def run_tray(server_url: str, config: Config) -> None:
    """Run pystray on the calling thread (blocks). Opens browser for UI."""

    def on_open(icon, item):
        webbrowser.open(server_url)

    def on_trust(icon, item):
        fresh = load_config()
        fresh.trust_all_tools = not fresh.trust_all_tools
        save_config(fresh)
        config.trust_all_tools = fresh.trust_all_tools

    def on_settings(icon, item):
        webbrowser.open(f"{server_url}/settings")

    def on_logs(icon, item):
        log_path = CONFIG_DIR / "orchestrator.log"
        if log_path.exists():
            os.startfile(str(log_path))

    def on_quit(icon, item):
        _shutdown_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open", on_open, default=True),
        pystray.MenuItem("Trust All Tools", on_trust, checked=lambda item: config.trust_all_tools),
        pystray.MenuItem("Settings", on_settings),
        pystray.MenuItem("Logs", on_logs),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("kiro-orchestrator", _create_icon(), "Kiro Orchestrator", menu)
    icon.run()


def get_shutdown_event() -> threading.Event:
    return _shutdown_event
