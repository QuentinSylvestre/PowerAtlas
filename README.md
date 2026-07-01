# PowerAtlas

Desktop launcher and dashboard for kiro-cli and Claude Code sessions. System tray icon with a web UI for discovering, resuming, and batch-launching AI coding assistant workspaces.

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

- Auto-discovers workspaces from kiro-cli and Claude Code session data
- Resume sessions with one click (opens terminal with `--resume-id`)
- Multi-select and batch launch sessions
- Per-provider settings with default args (e.g. trust-all-tools)
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
peek_hotkey = "ctrl+shift+z"  # global overlay hotkey (modifier+key format)
terminal_command = ""  # empty = auto-detect (platform-specific)
pinned_folders = []
pinned_sessions = []

[provider_settings.kiro-cli]
default_args = "-a"  # e.g. trust-all-tools
color = ""
enabled = true

[provider_settings.claude-code]
default_args = ""
color = ""
enabled = true
```

Linux users need `gir1.2-webkit2-4.1` system package for pywebview. The peek hotkey listener requires X11 (Wayland is not supported).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Assets

Branding assets (tray icon, favicon, banner, app icon) come from the `r3-balanced-master-clean-banner` icon pack. The source zip is stored in `assets-source/` for provenance.
