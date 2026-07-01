"""Icon extraction for custom launchers.

Extracts icons from binaries at launcher create/edit time and stores them
as PNGs in CONFIG_DIR/icons/<launcher_id>.png. Falls back to bundled
default icons (terminal for CLI tools, generic app for GUI).
"""

import shutil
import sys
from pathlib import Path

from .config import CONFIG_DIR

ICONS_DIR = CONFIG_DIR / "icons"

# Bundled fallback icons are SVG data URIs served inline (no file needed)
_TERMINAL_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>'
)
_APP_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<rect x="2" y="2" width="20" height="20" rx="3"/>'
    '<circle cx="12" cy="12" r="4"/></svg>'
)


def icon_path(launcher_id: str) -> Path:
    """Return the expected icon file path for a launcher."""
    return ICONS_DIR / f"{launcher_id}.png"


def has_icon(launcher_id: str) -> bool:
    """Check if an extracted icon exists for the given launcher."""
    return icon_path(launcher_id).is_file()


def extract_icon(launcher_id: str, command: str, is_terminal: bool) -> bool:
    """Extract icon from binary and save as PNG.

    Returns True if a real icon was extracted, False if falling back to default.
    """
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    target = icon_path(launcher_id)

    # Resolve the binary path (first token of command)
    binary = _resolve_binary(command)
    if binary and binary.suffix.lower() in (".cmd", ".bat"):
        resolved = _resolve_cmd_to_exe(binary)
        if resolved:
            binary = resolved
    if binary and binary.suffix.lower() in (".exe", ".msi") and sys.platform == "win32":
        if _extract_windows_icon(binary, target):
            return True

    # No extraction possible — remove any stale icon so fallback is used
    target.unlink(missing_ok=True)
    return False


def remove_icon(launcher_id: str) -> None:
    """Remove the cached icon for a deleted launcher."""
    icon_path(launcher_id).unlink(missing_ok=True)


def default_icon_svg(is_terminal: bool) -> str:
    """Return the appropriate default SVG icon markup."""
    return _TERMINAL_ICON if is_terminal else _APP_ICON


def _resolve_cmd_to_exe(cmd_path: Path) -> Path | None:
    """Parse a .cmd/.bat shim to find the underlying .exe it wraps.

    Common patterns (e.g. Electron apps installed via npm/scoop):
      - %~dp0..\\App.exe  (relative to shim directory)
      - "%~dp0..\\App.exe"
      - "C:\\Absolute\\Path\\App.exe"
      - C:\\Absolute\\Path\\App.exe
    """
    import re as _re

    try:
        content = cmd_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    cmd_dir = cmd_path.parent

    # Pattern 1: %~dp0-relative paths (Electron/scoop shim pattern)
    # Matches both quoted and unquoted: "%~dp0..\app.exe" or %~dp0..\app.exe
    for match in _re.finditer(r'["\']?%~dp0([^"\s\r\n]+\.exe)["\']?', content, _re.IGNORECASE):
        rel = match.group(1)
        candidate = (cmd_dir / rel).resolve()
        if candidate.is_file():
            return candidate

    # Pattern 2: Quoted absolute paths
    for match in _re.finditer(r'"([A-Za-z]:\\[^"]+\.exe)"', content):
        candidate = Path(match.group(1))
        if candidate.is_file():
            return candidate

    # Pattern 3: Unquoted absolute paths
    for match in _re.finditer(r'(?<!")([A-Za-z]:\\[^\s"]+\.exe)', content):
        candidate = Path(match.group(1))
        if candidate.is_file():
            return candidate

    return None


def _resolve_binary(command: str) -> Path | None:
    """Resolve command string to an actual binary path."""
    if not command:
        return None
    cmd = command.strip().strip('"').strip("'")
    # First, try the entire command as a path (handles paths with spaces)
    path = Path(cmd)
    if path.is_file():
        return path
    # Try the first token (handles "binary.exe --args")
    token = cmd.split()[0]
    path = Path(token)
    if path.is_file():
        return path
    # Try shutil.which for PATH-based commands
    found = shutil.which(token)
    if found:
        return Path(found)
    return None


def _extract_windows_icon(binary: Path, target: Path) -> bool:
    """Extract icon from a Windows PE binary using win32gui + PIL."""
    try:
        import win32gui  # noqa: F401 — Windows-only
        import win32ui
        import win32con
        from PIL import Image

        large, small = win32gui.ExtractIconEx(str(binary), 0)
        if not large:
            # Clean up small icons if any
            for h in small:
                win32gui.DestroyIcon(h)
            return False

        hicon = large[0]
        try:
            # Get icon bitmap info
            info = win32gui.GetIconInfo(hicon)
            hbm_mask = info[3]
            hbm_color = info[4]

            try:
                bmp = win32ui.CreateBitmapFromHandle(hbm_color)
                bmp_info = bmp.GetInfo()
                w = bmp_info["bmWidth"]
                h = bmp_info["bmHeight"]

                # Create device context and select bitmap
                hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
                mem_dc = hdc.CreateCompatibleDC()
                mem_dc.SelectObject(bmp)

                # Read pixel data
                bmp_bits = bmp.GetBitmapBits(True)

                # Create PIL image from BGRA data
                img = Image.frombuffer("RGBA", (w, h), bmp_bits, "raw", "BGRA", 0, 1)
                img.save(str(target), "PNG")

                mem_dc.DeleteDC()
                hdc.DeleteDC()
                return True
            finally:
                win32gui.DeleteObject(hbm_mask)
                win32gui.DeleteObject(hbm_color)
        finally:
            for h_icon in large:
                win32gui.DestroyIcon(h_icon)
            for h_icon in small:
                win32gui.DestroyIcon(h_icon)

    except Exception:
        return False
