# PowerAtlas

Desktop launcher and dashboard for kiro-cli sessions. System tray icon with a web UI for discovering, resuming, and batch-launching kiro-cli workspaces.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

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

## Configuration

Config stored at `%LOCALAPPDATA%\power-atlas\config.toml`:

```toml
trust_all_tools = false
use_pywebview = true
terminal_command = ""  # empty = auto-detect (wt > pwsh > cmd)
pinned_folders = []
pinned_sessions = []
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Assets

Branding assets (tray icon, favicon, banner, app icon) come from the `r3-balanced-master-clean-banner` icon pack. The source zip is stored in `assets-source/` for provenance.
