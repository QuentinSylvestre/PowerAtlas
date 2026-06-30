"""Peek window: hotkey-held native overlay showing the dashboard."""

import logging
import threading

log = logging.getLogger("power_atlas.peek")

_AVAILABLE = True
_IMPORT_ERROR = ""
try:
    import webview
    from pynput import keyboard
except ImportError as e:
    _AVAILABLE = False
    _IMPORT_ERROR = str(e)


def is_available() -> bool:
    """Return True if peek dependencies are importable."""
    return _AVAILABLE


class PeekWindow:
    """Manages the pywebview overlay window and pynput hotkey listener."""

    def __init__(self, server_url: str, hotkey: str = "ctrl+shift+z"):
        if not _AVAILABLE:
            raise RuntimeError(f"Peek unavailable: {_IMPORT_ERROR}")
        self._server_url = server_url
        self._hotkey = hotkey
        self._window: webview.Window | None = None
        self._visible = False
        self._listener: keyboard.Listener | None = None
        self._trigger_keys = self._parse_hotkey(hotkey)
        self._pressed_keys: set = set()
        self._triggered = False  # True after full combo pressed
        self._webview_ready = threading.Event()
        self._webview_ok = False  # Set True only when webview is confirmed ready

    def start(self, on_main_thread: bool = False) -> None:
        """Start the peek window and hotkey listener.

        Args:
            on_main_thread: If True, webview.start() is called on the
                current thread (blocks). If False, starts on a new thread.
        """
        if on_main_thread:
            self._start_listener()
            self._webview_ok = True  # On main thread, webview.start() blocks until ready
            self._run_webview()  # blocks
        else:
            t = threading.Thread(target=self._run_webview, daemon=True)
            t.start()
            self._webview_ready.wait(timeout=10)
            if not self._webview_ready.is_set():
                log.warning("Peek webview did not become ready within 10s — peek may be non-functional")
            else:
                self._webview_ok = True
            self._start_listener()

    def stop(self) -> None:
        """Stop the hotkey listener and destroy the window. Final — call only at process exit."""
        if self._listener:
            self._listener.stop()
            self._listener = None
        if self._window:
            self._window.destroy()
            self._window = None

    def _run_webview(self) -> None:
        """Create and run the pywebview window."""
        self._window = webview.create_window(
            "PowerAtlas",
            self._server_url,
            frameless=True,
            on_top=True,
            hidden=True,
            fullscreen=True,
        )
        webview.start(func=self._on_webview_ready, debug=False)

    def _on_webview_ready(self) -> None:
        """Called when webview is ready."""
        log.info("Peek webview ready")
        self._webview_ready.set()

    def _start_listener(self) -> None:
        """Start the pynput keyboard listener."""
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            self._listener.daemon = True
            self._listener.start()
            log.info("Peek hotkey listener started (hotkey: %s)", self._hotkey)
        except Exception as e:
            log.warning("Failed to start hotkey listener: %s", e)
            self._listener = None

    def _show(self) -> None:
        win = self._window  # local capture for thread safety
        if win and not self._visible and self._webview_ok:
            self._visible = True
            log.debug("Peek show")
            win.show()
            win.evaluate_js("if(typeof doRefresh==='function') doRefresh()")

    def _hide(self) -> None:
        win = self._window  # local capture for thread safety
        if win and self._visible:
            self._visible = False
            log.debug("Peek hide")
            win.hide()

    def _on_press(self, key) -> None:
        """Track pressed keys, show on full combo. Escape is a fallback dismiss.
        
        Thread safety: pynput guarantees sequential callback dispatch on a single
        listener thread — no concurrent press/release delivery. The compound
        check-then-act here is safe without explicit synchronization.
        """
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed_keys.add(normalized)
        # Escape key fallback: always dismiss if visible
        if self._triggered and normalized == "esc":
            self._triggered = False
            self._pressed_keys.clear()
            self._hide()
            return
        if not self._triggered and self._trigger_keys.issubset(self._pressed_keys):
            self._triggered = True
            self._show()

    def _on_release(self, key) -> None:
        """Hide on modifier release after trigger."""
        normalized = self._normalize_key(key)
        if normalized:
            self._pressed_keys.discard(normalized)
        if self._triggered:
            # Hide when either modifier is released
            modifiers = {k for k in self._trigger_keys if k in ("ctrl", "shift", "alt")}
            if not modifiers.issubset(self._pressed_keys):
                self._triggered = False
                self._hide()

    @staticmethod
    def _parse_hotkey(hotkey: str) -> set[str]:
        """Parse 'ctrl+shift+z' into {'ctrl', 'shift', 'z'}."""
        return {part.strip().lower() for part in hotkey.split("+") if part.strip()}
    @staticmethod
    def _normalize_key(key) -> str | None:
        """Normalize a pynput key to a string."""
        if hasattr(key, "char") and key.char:
            ch = key.char
            # When Ctrl is held, character keys report control codes (0x01-0x1a).
            # Map them back to letters: 0x01='a', 0x02='b', ..., 0x1a='z'.
            if len(ch) == 1 and 1 <= ord(ch) <= 26:
                return chr(ord(ch) + ord('a') - 1)
            return ch.lower()
        if hasattr(key, "name"):
            name = key.name.lower()
            if name in ("ctrl_l", "ctrl_r", "ctrl"):
                return "ctrl"
            if name in ("shift_l", "shift_r", "shift"):
                return "shift"
            if name in ("alt_l", "alt_r", "alt_gr", "alt"):
                return "alt"
            return name
        return None


def create_peek(server_url: str, hotkey: str = "ctrl+shift+z") -> PeekWindow | None:
    """Factory: create PeekWindow if available, else log warning and return None."""
    if not is_available():
        log.warning("Peek window disabled: %s", _IMPORT_ERROR)
        return None
    _KNOWN_MODIFIERS = {"ctrl", "shift", "alt"}
    parts = {p.strip().lower() for p in hotkey.split("+") if p.strip()}
    modifiers = parts & _KNOWN_MODIFIERS
    non_modifiers = parts - _KNOWN_MODIFIERS
    if not modifiers or not non_modifiers:
        log.warning("Invalid peek_hotkey '%s' (need modifier+key). Falling back to ctrl+shift+z", hotkey)
        hotkey = "ctrl+shift+z"
    try:
        return PeekWindow(server_url, hotkey)
    except Exception as e:
        log.warning("Peek window disabled: %s", e)
        return None
