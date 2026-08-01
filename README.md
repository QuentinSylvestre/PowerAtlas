# PowerAtlas

Desktop launcher and dashboard for kiro-cli, Claude Code, and Kiro IDE sessions. System tray icon with a web UI for discovering, resuming, and batch-launching AI coding assistant workspaces — plus, for kiro-cli, a built-in agent surface that creates and drives sessions over ACP without a terminal, optionally reachable from your phone over a NetBird network. See the *Agent sessions* and *Remote access* sections below.

Supports **Windows** and **Linux**.

## Installation

PowerAtlas runs on a virtualenv inside the checkout, never on a global interpreter — the app and the
test suite share one dependency stack, so a green suite is evidence about the app that actually runs.
Create the venv and install into it:

```bash
python -m venv .venv-PowerAtlas
.venv-PowerAtlas\Scripts\python -m pip install -e .   # Windows
.venv-PowerAtlas/bin/python -m pip install -e .       # Linux
```

Requires Python 3.11+.

Started with any other interpreter, PowerAtlas re-launches itself on the checkout's venv, so the
autostart entry, the tray's Restart action and a plain `python -m power_atlas` all converge on it.
A venv directory named `.venv` is also recognised; when several `.venv*` directories exist and none
matches either name, PowerAtlas declines to guess and stays on the interpreter it was given.

On Linux, the system tray icon requires PyGObject and a running notification area:

```bash
# Debian/Ubuntu
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
# Fedora
sudo dnf install python3-gobject libayatana-appindicator-gtk3
```

## Usage

```bash
.venv-PowerAtlas\Scripts\power-atlas    # Windows
.venv-PowerAtlas/bin/power-atlas        # Linux
```

The console script is installed into the venv, so a bare `power-atlas` works only while the venv is
active. To call it from any shell, put a wrapper on your `PATH` that invokes the venv interpreter
rather than adding the venv's `Scripts`/`bin` directory itself — that directory also carries `pip`,
`pytest` and `ruff`, which would then shadow the system copies. On Windows, a `power-atlas.cmd`:

```cmd
@echo off
"<checkout>\.venv-PowerAtlas\Scripts\python.exe" -m power_atlas %*
```

The app starts as a system tray icon. Click to open the dashboard UI. The kiro-cli agent surface lives
at `/acp` — a workspace-grouped session browser beside a conversation pane, two panes on a desktop and a
drill-down below 768 px. Reach it from the dashboard's *open in ACP* action on any kiro-cli session row,
or by opening `/acp` directly.

### Features

- Auto-discovers workspaces from kiro-cli, Claude Code, and Kiro IDE session data
  - Kiro IDE sessions: `%APPDATA%\Kiro\User\globalStorage\...` (Windows) / `~/.config/Kiro/User/globalStorage/...` (Linux)
- Unified provider-launcher system with extracted icons and configurable colors
- Inline provider filter next to search bar — filters workspaces and sessions panels simultaneously
- Workspace tags with configurable colors, unified tag management (add/delete from popover), multi-workspace bulk tag assignment via gear icon during multi-select, tag/time filtering, and hidden workspaces — unified filtering applies to both workspaces and sessions panels with permanent time grouping (Today/Yesterday/This week/Older)
- Resume sessions with one click (opens terminal with `--resume-id`)
- Drive kiro-cli sessions from the browser at `/acp`, with no terminal — create a session or resume an
  exited one over ACP, stream the agent's output, cancel a turn, close the session. A workspace-grouped
  session browser lists what is resumable and greys out sessions another process currently holds. See
  *Agent sessions* below
- Optional remote access over NetBird — off by default. When enabled, `/acp` and its listing endpoint
  are reachable from your own devices behind a device secret, while the dashboard, launchers and
  settings stay loopback-only. See *Remote access* below
- Live session status — sessions currently running in a terminal show a 🟢 Working (agent executing) or 🟡 Waiting (agent finished, your turn) or 🔴 Errored dot; workspace cards show the highest-priority status dot. A status filter (All / Working / Waiting / Errored) in the Sessions panel narrows both panels. Detected by matching the working directory of running `claude` / `kiro-cli` processes to session workspaces; also supports v3 kiro-cli sessions (`messages.jsonl` format). Opt-in toast notifications fire when a session transitions from Working to Waiting or Errored (Windows toast via WinRT, Linux via notify-send)
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
port = 0  # 0 = random (default), or set e.g. 8080 for a fixed port.
          # A fixed non-zero port is REQUIRED when remote_bind_address is set: a phone cannot
          # bookmark an OS-assigned port, and with 0 the two listeners would be given different
          # numbers. The combination is rejected with a named error rather than half-applied.
peek_hotkey = "ctrl+shift+z"  # global overlay hotkey (modifier+key format)
default_directory = ""  # Global fallback directory for provider launches without workspace selection
pinned_folders = []
pinned_sessions = []
active_launch_profile = "default"

remote_bind_address = ""  # "" = loopback only (the default; a version bump never starts listening
                          # on its own). Set to this machine's NetBird IP literal — not a hostname,
                          # not 0.0.0.0 — to also listen there. Needs a fixed port above and a
                          # restart. Read the "Remote access" section before enabling it.

# ACP session limits. All three are read once at startup, so a change needs a restart; the settings
# panel says so rather than pretending a live effect.
acp_max_sessions = 8  # 1-16. Concurrent kiro-cli ACP sessions. Each costs ~3 processes and ~178 MB
                      # (measured on kiro-cli 2.16.0), so 8 is roughly 1.5 GB at the cap.
acp_idle_ttl_seconds = 1800  # 300-86400. A session with no attached tab, no running turn and no
                             # in-flight load is terminated after this long idle, on a 60 s sweep.
                             # Its transcript stays on disk, so it can be resumed later.
acp_prompt_silence_seconds = 900  # 60-7200. A turn is cancelled after this much SILENCE from the
                                  # agent — not this much total time — so a long turn that keeps
                                  # streaming is never cut off. A 4-hour absolute ceiling still
                                  # applies, so one chunk per window cannot run forever.

[provider_settings.kiro-cli]
default_args = "-a"  # e.g. trust-all-tools.
                     # The `/acp` agent is always started with `-a`, independently of this setting,
                     # so a session PowerAtlas drives executes tools without asking. Treat anything
                     # that can reach `/acp` as able to run commands as you — which is what the
                     # device secret in "Remote access" below exists to prevent.
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

[notifications]
enabled = false  # opt-in: fire OS toast on Working→Waiting/Errored transitions
```

Linux users need `gir1.2-webkit2-4.1` system package for pywebview. The peek hotkey listener requires X11 (Wayland is not supported).

## Agent sessions (`/acp`)

`/acp` drives kiro-cli over ACP: one supervised `kiro-cli acp` process holds every session PowerAtlas
opens. The left rail lists workspaces with their sessions — ten workspaces and three sessions each by
default, each axis paging independently — and marks every visible row *available*, *held by PowerAtlas*,
or *locked* by another process. Selecting a row resumes that session and replays its history; sessions
whose workspace directory no longer exists are marked so, since they cannot be resumed usefully.

Three things are worth knowing before leaving a long task running:

- **A turn is bounded by silence, not by duration.** A turn that keeps streaming runs as long as it
  needs; one that goes quiet for `acp_prompt_silence_seconds` (default 15 minutes) is cancelled agent
  side. A 4-hour absolute ceiling applies regardless, so a turn emitting one chunk per window cannot
  hold a session open forever.
- **Idle sessions are reclaimed.** A session with no tab attached, no turn running and no load in
  flight is terminated after `acp_idle_ttl_seconds` (default 30 minutes) and its lock removed. The
  transcript is left on disk, so the session is resumable afterwards at the cost of one reload — and a
  session with a tab open or a turn running is never swept, however old.
- **Cancelling a turn does not kill what the agent started.** Measured on kiro-cli 2.16.0: both
  `session/cancel` and the session terminate the sweeper uses stop the ACP turn while leaving any
  shell subprocess the agent spawned running to completion. It is reaped only when PowerAtlas exits.
  So a cancelled build or long-running command keeps consuming CPU and memory that the per-session
  figure above does not include.

Creating a session writes a permanent `.json`, `.jsonl` and `.lock` into your kiro-cli session store,
as any kiro-cli session does. Resuming one without prompting leaves the transcript byte-identical.

## Remote access (opt-in)

Off by default: with `remote_bind_address` unset, PowerAtlas has exactly one listening socket and it is
loopback. Setting it to this machine's NetBird IP adds a **second** socket on the same port, so the
laptop keeps using `http://127.0.0.1:<port>` unchanged while a phone on the same NetBird network can
reach the agent surface.

**Enabling it.** In `config.toml`, set a fixed non-zero `port` and set `remote_bind_address` to this
machine's NetBird IP literal, then restart.

Authentication needs a device secret, kept at `%LOCALAPPDATA%\power-atlas\remote-secret` (Linux:
`~/.config/power-atlas/remote-secret`) — never in `config.toml`, and never served over the remote
surface it authenticates. **If no secret exists the remote socket is deliberately not bound at all**,
with the reason logged: a listener that nothing can authenticate against is worse than no listener.
Create one from the topbar's *Remote access* panel with **Rotate device secret**, then restart once
more. That panel then shows the URL to open and the secret to paste.

On the device, open `http://<netbird-ip>:<port>/remote-auth`, enter the secret once, and it is
exchanged for a device cookie valid for 90 days that survives a PowerAtlas restart. Failed attempts are
logged with the peer address and backed off per peer.

Two degradations are deliberate and silent apart from the log: if the NetBird interface is not up yet
at login the remote bind is skipped and the app still starts on loopback, and if the configured `port`
was already taken — so loopback fell back to a random one — the remote bind is skipped too, rather than
exposing a listener on a port that changes every restart.

**What is reachable remotely**, and nothing else: `/acp`, its WebSocket `/ws/acp`, the read-only session
listing `GET /api/acp/sessions`, `/static/*`, and the `/remote-auth` exchange page. The path allowlist is
default-deny, so any route added later is loopback-only until deliberately listed. The dashboard `/`,
`/api/launchers` (which carries custom-launcher environment variables in cleartext) and `/api/settings`
are refused from the remote address with a 403 before routing.

**The security model as shipped.** The device cookie is the **sole** authorization layer. The design
called for two independent layers — a NetBird access policy restricting this host to your own devices,
plus the cookie — but measurement on 2026-07-31 found no such policy in force: all 17 peers on the
account, including machines belonging to other people, sit in this host's network map and can reach the
port. Every one of them is stopped by the cookie and by nothing else. Creating a restricting policy in
the NetBird console re-establishes the second layer at any time and takes about five minutes; **nothing
in the implementation depends on its absence**, so it is worth doing.

Weigh that against what is behind the cookie. `/acp` runs its agent with `-a`, so a peer holding a valid
cookie can execute arbitrary commands on this machine as you. It also sees **every workspace path and
every session title on the machine** through the listing endpoint — and a session's title is the raw
text of its first prompt for roughly a fifth of sessions, which in practice includes filesystem paths
and URLs.

**Revoking a device.** There is exactly one mechanism: rotate the secret, from the *Remote access*
panel or `POST /api/remote-access/rotate` (loopback-only, so a peer holding a stolen cookie cannot
re-key the surface around you). Rotation issues a new secret and invalidates **every** device cookie at
once — there is no per-device revocation, so each remaining device must re-enter the new secret.

**One operational rule, because the design does not mitigate it.** Do not bind other services to
`0.0.0.0` on this machine while the remote bind is enabled. Cookies are scoped to a host, not a port, so
the device transmits its PowerAtlas cookie to *any* service listening on any port of the NetBird
address — and a process that collects it gains full remote access to a `-a` agent.

There is no TLS, deliberately: WireGuard already encrypts the NetBird transport, and adding TLS inside
it would buy encryption rather than authorization. That reasoning holds only while NetBird is the sole
remote interface — binding a real LAN address would reopen it.

## Development

Install the dev extras into the same venv the app runs on:

```bash
.venv-PowerAtlas\Scripts\python -m pip install -e ".[dev]"   # Windows
.venv-PowerAtlas/bin/python -m pip install -e ".[dev]"       # Linux
.venv-PowerAtlas/bin/python -m pytest
```

## Assets

Branding assets (tray icon, favicon, banner, app icon) come from the `r3-balanced-master-clean-banner` icon pack. The source zip is stored in `assets-source/` for provenance.
