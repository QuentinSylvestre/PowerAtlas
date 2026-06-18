"""System tray icon and menu."""

import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw, ImageFont

from .config import Config, save_config

_webview_window = None
_webview_lock = threading.Lock()
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


def _open_ui(server_url: str, config: Config):
    global _webview_window
    if config.use_pywebview:
        try:
            import webview

            with _webview_lock:
                if _webview_window is None:
                    _webview_window = webview.create_window(
                        "Kiro Orchestrator", server_url, width=900, height=700
                    )
                    _webview_window.events.closing += _on_window_closing
                    threading.Thread(target=webview.start, daemon=True).start()
                else:
                    _webview_window.show()
            return
        except Exception:
            pass
    webbrowser.open(server_url)


def _on_window_closing():
    """Hide window instead of destroying — minimize to tray."""
    global _webview_window
    with _webview_lock:
        if _webview_window:
            _webview_window.hide()
    return False  # Prevent destruction


def _on_quit(icon: pystray.Icon):
    global _webview_window
    _shutdown_event.set()
    with _webview_lock:
        if _webview_window:
            try:
                _webview_window.destroy()
            except Exception:
                pass
            _webview_window = None
    icon.stop()


def run_tray(server_url: str, config: Config) -> None:
    """Run pystray on the calling thread (blocks)."""

    def on_open(icon, item):
        _open_ui(server_url, config)

    def on_trust(icon, item):
        config.trust_all_tools = not config.trust_all_tools
        save_config(config)

    def on_settings(icon, item):
        webbrowser.open(f"{server_url}/settings")

    def on_quit_click(icon, item):
        _on_quit(icon)

    menu = pystray.Menu(
        pystray.MenuItem("Open", on_open, default=True),
        pystray.MenuItem("Trust All Tools", on_trust, checked=lambda item: config.trust_all_tools),
        pystray.MenuItem("Settings", on_settings),
        pystray.MenuItem("Quit", on_quit_click),
    )
    icon = pystray.Icon("kiro-orchestrator", _create_icon(), "Kiro Orchestrator", menu)
    icon.run()


def get_shutdown_event() -> threading.Event:
    return _shutdown_event
