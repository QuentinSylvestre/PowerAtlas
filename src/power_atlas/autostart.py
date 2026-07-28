"""Startup/autostart management. Windows: Start Menu shortcut. Linux: XDG autostart .desktop file."""

import os
import sys
from pathlib import Path

from .interpreter import venv_python


def _startup_interpreter(*, windowed: bool = False) -> Path:
    """The interpreter the login entry should invoke.

    Resolved from the checkout rather than from ``sys.executable`` so that
    toggling autostart never records whichever interpreter happened to be
    running the toggle.
    """
    resolved = venv_python(windowed=windowed)
    if resolved is not None:
        return resolved
    if windowed and sys.platform == "win32":
        return Path(sys.executable).parent / "pythonw.exe"
    return Path(sys.executable)


def _windows_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        appdata = str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "PowerAtlas.lnk"


def _linux_desktop_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    return config_home / "autostart" / "power-atlas.desktop"


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _windows_shortcut_path().exists()
    else:
        return _linux_desktop_path().exists()


def enable() -> None:
    """Register power-atlas to start on login."""
    if sys.platform == "win32":
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(_windows_shortcut_path()))
        shortcut.TargetPath = str(_startup_interpreter(windowed=True))
        shortcut.Arguments = "-m power_atlas"
        shortcut.WorkingDirectory = str(Path.home())
        icon_path = str(Path(__file__).parent / "static" / "poweratlas.ico")
        shortcut.IconLocation = f"{icon_path},0"
        shortcut.save()
    else:
        desktop_path = _linux_desktop_path()
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        desktop_path.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=PowerAtlas\n"
            f"Exec={_startup_interpreter()} -m power_atlas\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )


def disable() -> None:
    if sys.platform == "win32":
        _windows_shortcut_path().unlink(missing_ok=True)
    else:
        _linux_desktop_path().unlink(missing_ok=True)
