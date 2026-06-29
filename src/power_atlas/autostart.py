"""Windows startup shortcut management."""

import os
import sys
from pathlib import Path

_appdata = os.environ.get("APPDATA", "")
if not _appdata:
    _appdata = str(Path.home() / "AppData" / "Roaming")
STARTUP_DIR = Path(_appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
SHORTCUT_NAME = "PowerAtlas.lnk"


def _shortcut_path() -> Path:
    return STARTUP_DIR / SHORTCUT_NAME


def is_enabled() -> bool:
    return _shortcut_path().exists()


def enable() -> None:
    """Create startup shortcut via WScript.Shell COM."""
    import win32com.client

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(_shortcut_path()))
    shortcut.TargetPath = str(Path(sys.executable).parent / "pythonw.exe")
    shortcut.Arguments = "-m power_atlas"
    shortcut.WorkingDirectory = str(Path.home())
    shortcut.save()


def disable() -> None:
    _shortcut_path().unlink(missing_ok=True)
