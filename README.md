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
- Inline provider filter next to search bar — filters workspaces and sessions panels simultaneously
- Workspace tags with configurable colors, unified tag management (add/delete from popover), multi-workspace bulk tag assignment via gear icon during multi-select, tag/time filtering, and hidden workspaces — unified filtering applies to both workspaces and sessions panels with permanent time grouping (Today/Yesterday/This week/Older)
- Resume sessions with one click (opens terminal with `--resume-id`)
- Live session status — sessions currently running in a terminal show a 🟢 Working (active) or 🟡 Waiting (stopped, needs you) dot; a status filter (All / Live / Working / Waiting / Closed) in the Sessions panel narrows both the sessions and workspaces panels. Detected by correlating the session id in running `claude` / `kiro-cli` processes; updates on the normal refresh cycle
- Multi-select and batch launch sessions
- Per-provider settings with default args (e.g. trust-all-tools)
- Pin folders and sessions for quick access
- Quick actions on workspace cards: click folder icon to open in file explorer, hover to reveal terminal button
- Built-in terminal launcher tile opens a shell at selected workspaces or default directory
- Search across all workspaces and sessions
- Custom launchers with inline args editing and one-click execution
- Global launch profiles with configurable Windows Terminal profile and terminal command
- Launch-profile management (gear icon in topbar) for window mode, autostart, and profile switching
- Platform-aware terminal detection:
  - Windows: Windows Terminal › PowerShell › cmd
  - Linux: kitty › Alacritty › GNOME Terminal › Konsole › xterm
- On Windows, provider launches through Windows Terminal use `pwsh -NoExit -Command` to run the provider inside a full PowerShell session, preserving MCP server connections.

## Configuration

Config stored at:
- Windows: `%LOCALAPPDATA%\power-atlas\config.toml`
- Linux: `~/.config/power-atlas/config.toml`

```toml
port = 0  # 0 = random (default), or set e.g. 8080 for a fixed port
peek_hotkey = "ctrl+shift+z"  # global overlay hotkey (modifier+key format)
default_directory = ""  # Global fallback directory for provider launches without workspace selection
pinned_folders = []
pinned_sessions = []
active_launch_profile = "default"

[provider_settings.kiro-cli]
default_args = "-a"  # e.g. trust-all-tools
color = ""
enabled = true
default_directory = ""  # Per-provider override (empty = use global)

[provider_settings.claude-code]
default_args = ""
color = ""
enabled = true
default_directory = ""

[provider_settings.kiro-ide]
default_args = ""
color = ""
enabled = true
default_directory = ""

[[launch_profiles]]
id = "default"
name = "Default"
terminal_command = "wt new-tab --title {title} -p {wt_profile} -d {cwd} -- pwsh -NoExit -Command {pscmd}"
wt_profile = "PowerShell"

[workspace_settings."C:\\path\\to\\project"]
tags = ["frontend", "active"]
color = "#3b82f6"  # explicit card accent color (overrides tag color)

[tag_settings.frontend]
color = "#06b6d4"  # tag color (used as card accent when no explicit workspace color)

[tag_settings.archived]
color = "#64748b"
```

Linux users need `gir1.2-webkit2-4.1` system package for pywebview. The peek hotkey listener requires X11 (Wayland is not supported).

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Assets

Branding assets (tray icon, favicon, banner, app icon) come from the `r3-balanced-master-clean-banner` icon pack. The source zip is stored in `assets-source/` for provenance.
