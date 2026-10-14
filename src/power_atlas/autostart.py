"""Startup/autostart management. Windows: Start Menu shortcut. Linux: XDG autostart .desktop file."""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import CONFIG_DIR
from .interpreter import venv_python

log = logging.getLogger(__name__)


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


def _task_scheduler_task_exists() -> bool:
    """Return True if the 'PowerAtlas Crash Restart' Task Scheduler task is registered."""
    try:
        result = subprocess.run(
            [r"C:\Windows\System32\schtasks.exe", "/query", "/tn", "PowerAtlas Crash Restart"],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def is_enabled() -> bool:
    if sys.platform == "win32":
        return _windows_shortcut_path().exists() or _task_scheduler_task_exists()
    else:
        return _linux_desktop_path().exists()


def enable() -> None:
    """Register power-atlas to start on login."""
    if sys.platform == "win32":
        import html
        import win32com.client

        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(_windows_shortcut_path()))
        shortcut.TargetPath = str(_startup_interpreter(windowed=True))
        shortcut.Arguments = "-m power_atlas"
        shortcut.WorkingDirectory = str(Path.home())
        icon_path = str(Path(__file__).parent / "static" / "poweratlas.ico")
        shortcut.IconLocation = f"{icon_path},0"
        shortcut.save()

        # Register a Task Scheduler task that restarts PowerAtlas after a crash.
        # schtasks requires the XML encoded as UTF-16 LE with BOM.
        interp = html.escape(str(_startup_interpreter(windowed=True)))
        xml_content = (
            '<?xml version="1.0" encoding="UTF-16"?>\n'
            '<Task version="1.2"'
            ' xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
            '  <Principals>\n'
            '    <Principal id="Author">\n'
            '      <LogonType>InteractiveToken</LogonType>\n'
            '      <RunLevel>LeastPrivilege</RunLevel>\n'
            '    </Principal>\n'
            '  </Principals>\n'
            '  <Triggers>\n'
            '    <LogonTrigger>\n'
            '      <Enabled>true</Enabled>\n'
            '    </LogonTrigger>\n'
            '  </Triggers>\n'
            '  <Settings>\n'
            '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
            '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
            '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
            '    <RestartInterval>PT1M</RestartInterval>\n'
            '    <RestartCount>3</RestartCount>\n'
            '  </Settings>\n'
            '  <Actions Context="Author">\n'
            '    <Exec>\n'
            f'      <Command>{interp}</Command>\n'
            '      <Arguments>-m power_atlas --foreground</Arguments>\n'
            '    </Exec>\n'
            '  </Actions>\n'
            '</Task>\n'
        )
        tmp_path_str = None
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-16",
                suffix=".xml",
                dir=str(CONFIG_DIR),
                delete=False,
            ) as f:
                tmp_path_str = f.name
                f.write(xml_content)
            result = subprocess.run(
                [
                    r"C:\Windows\System32\schtasks.exe", "/create",
                    "/tn", "PowerAtlas Crash Restart",
                    "/xml", tmp_path_str,
                    "/f",
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode == 0:
                # Task Scheduler is now the sole launcher; remove the .lnk to
                # avoid the login race (both fire, .lnk wins the single-instance
                # guard, and the task's process exits 0 — RestartInterval never
                # applies to the .lnk-launched process).
                _windows_shortcut_path().unlink(missing_ok=True)
            else:
                log.warning(
                    "Task Scheduler registration failed (returncode %d); "
                    "crash restart disabled, .lnk fallback kept",
                    result.returncode,
                )
        except subprocess.TimeoutExpired:
            log.warning(
                "Task Scheduler setup timed out; crash restart disabled, .lnk fallback kept"
            )
        except Exception as exc:
            log.warning(
                "Task Scheduler setup failed (%s); crash restart disabled, .lnk fallback kept",
                exc,
            )
        finally:
            if tmp_path_str is not None:
                Path(tmp_path_str).unlink(missing_ok=True)
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
        try:
            result = subprocess.run(
                [r"C:\Windows\System32\schtasks.exe", "/delete", "/tn", "PowerAtlas Crash Restart", "/f"],
                capture_output=True,
                timeout=30,
            )
            if result.returncode not in (0, 1):  # 1 = task not found (already gone)
                log.warning(
                    "schtasks /delete returned %d: %s",
                    result.returncode, result.stderr.decode("utf-16-le", errors="replace").strip(),
                )
        except Exception:
            pass  # schtasks not available — ignore
    else:
        _linux_desktop_path().unlink(missing_ok=True)
