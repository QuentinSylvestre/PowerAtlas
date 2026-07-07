# PowerAtlas

Desktop launcher and dashboard for kiro-cli, Claude Code, and Kiro IDE sessions. System tray icon with a web UI for discovering, resuming, and batch-launching AI coding assistant workspaces.

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

- Auto-discovers workspaces from kiro-cli, Claude Code, and Kiro IDE session data
  - Kiro IDE sessions: `%APPDATA%\Kiro\User\globalStorage\...` (Windows) / `~/.config/Kiro/User/globalStorage/...` (Linux)
- Unified provider-launcher system with extracted icons and configurable colors
- Inline provider filter next to search bar — filters workspace cards, pinned workspaces, and pinned sessions globally
- Resume sessions with one click (opens terminal with `--resume-id`)
- Multi-select and batch launch sessions
- Per-provider settings with default args (e.g. trust-all-tools)
- Pin folders and sessions for quick access
- Search across all workspaces and sessions
- Custom launchers with inline args editing and one-click execution
- Global launch profiles with configurable Windows Terminal profile, MCP-safe settings, and terminal command
- Launch-profile management (gear icon in topbar) for window mode, autostart, and profile switching
- Platform-aware terminal detection:
  - Windows: Windows Terminal › PowerShell › cmd
  - Linux: kitty › Alacritty › GNOME Terminal › Konsole › xterm
- On Windows, MCP-safe mode launches Kiro CLI and Claude Code through a PowerShell-compatible Windows Terminal profile tab: PowerAtlas opens a normal PowerShell tab and types the provider command into the prompt, preserving MCP server connections. If the helper fails, it falls back to a direct Windows Terminal tab launch (with a visible warning). MCP-safe settings are configurable per launch profile.

## Configuration

Config stored at:
- Windows: `%LOCALAPPDATA%\power-atlas\config.toml`
- Linux: `~/.config/power-atlas/config.toml`

```toml
port = 0  # 0 = random (default), or set e.g. 8080 for a fixed port
peek_hotkey = "ctrl+shift+z"  # global overlay hotkey (modifier+key format)
pinned_folders = []
pinned_sessions = []
active_launch_profile = "default"

[provider_settings.kiro-cli]
default_args = "-a"  # e.g. trust-all-tools
color = ""
enabled = true

[provider_settings.claude-code]
default_args = ""
color = ""
enabled = true

[provider_settings.kiro-ide]
default_args = ""
color = ""
enabled = true

[[launch_profiles]]
id = "default"
name = "Default"
terminal_command = ""  # empty = auto-detect
wt_profile = "PowerShell"
shell_process_name = "pwsh.exe"
helper_runner = "pwsh"
attach_timeout_ms = 4500
helper_timeout_ms = 8000
mcp_safe_enabled = true
```

Linux users need `gir1.2-webkit2-4.1` system package for pywebview. The peek hotkey listener requires X11 (Wayland is not supported).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Assets

Branding assets (tray icon, favicon, banner, app icon) come from the `r3-balanced-master-clean-banner` icon pack. The source zip is stored in `assets-source/` for provenance.
