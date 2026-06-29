"""System tray icon and menu."""

import os
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw, ImageFont

from .config import Config, CONFIG_DIR, load_config, save_config

_shutdown_event = threading.Event()
_restart_requested = False
_icon_instance = None


def _create_icon() -> Image.Image:
    img = Image.new("RGBA", (16, 16), (60, 120, 220, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 11)
    except OSError:
        font = ImageFont.load_default()
    draw.text((3, 1), "P", fill="white", font=font)
    return img


def run_tray(server_url: str, config: Config) -> None:
    """Run pystray on the calling thread (blocks). Opens browser for UI."""

    def on_open(icon, item):
        import threading as _t
        from .data import warmup_pinned
        from .config import load_config as _load_config
        _t.Thread(target=warmup_pinned, args=(_load_config().pinned_folders,), daemon=True).start()
        webbrowser.open(server_url)

    def on_trust(icon, item):
        fresh = load_config()
        fresh.trust_all_tools = not fresh.trust_all_tools
        save_config(fresh)
        config.trust_all_tools = fresh.trust_all_tools

    def on_logs(icon, item):
        log_path = CONFIG_DIR / "orchestrator.log"
        if log_path.exists():
            os.startfile(str(log_path))

    def on_quit(icon, item):
        _shutdown_event.set()
        icon.stop()

    def on_restart(icon, item):
        global _restart_requested
        _restart_requested = True
        _shutdown_event.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open", on_open, default=True),
        pystray.MenuItem("Trust All Tools", on_trust, checked=lambda item: config.trust_all_tools),
        pystray.MenuItem("Logs", on_logs),
        pystray.MenuItem("Restart", on_restart),
        pystray.MenuItem("Quit", on_quit),
    )
    icon = pystray.Icon("power-atlas", _create_icon(), "PowerAtlas", menu)
    global _icon_instance
    _icon_instance = icon
    icon.run()


def get_shutdown_event() -> threading.Event:
    return _shutdown_event


def restart_requested() -> bool:
    return _restart_requested
