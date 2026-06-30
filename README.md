# PowerAtlas

Desktop launcher and dashboard for kiro-cli sessions. System tray icon with a web UI for discovering, resuming, and batch-launching kiro-cli workspaces.

Supports **Windows** and **Linux**.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

On Linux, the system tray icon requires PyGObject and a running notification area:

```bash
# Debian/Ubuntu
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
# Fedora
sudo dnf install python3-gobject libayatana-appindicator-gtk3
```

## Usage

```bash
power-atlas
```

The app starts as a system tray icon. Click to open the dashboard UI.

### Features

- Auto-discovers workspaces from kiro-cli session data
- Resume sessions with one click (opens terminal with `--resume-id`)
- Multi-select and batch launch sessions
- Trust-all-tools toggle applies to all launches
- Pin folders and sessions for quick access
- Search across all workspaces and sessions
- Custom launchers with inline args editing and one-click execution
- Settings page for terminal preference, window mode, autostart
- Platform-aware terminal detection:
  - Windows: Windows Terminal › PowerShell › cmd
  - Linux: kitty › Alacritty › GNOME Terminal › Konsole › xterm

## Configuration

Config stored at:
- Windows: `%LOCALAPPDATA%\power-atlas\config.toml`
- Linux: `~/.config/power-atlas/config.toml`

```toml
trust_all_tools = false
peek_hotkey = "ctrl+shift+z"  # global overlay hotkey (modifier+key format)
terminal_command = ""  # empty = auto-detect (platform-specific)
pinned_folders = []
pinned_sessions = []
```

Linux users need `gir1.2-webkit2-4.1` system package for pywebview. The peek hotkey listener requires X11 (Wayland is not supported).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Assets

Branding assets (tray icon, favicon, banner, app icon) come from the `r3-balanced-master-clean-banner` icon pack. The source zip is stored in `assets-source/` for provenance.
