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
drill-down below 768 px. Reach it from the **ACP** button beside the dashboard's logo, from the *open in
ACP* action on any kiro-cli session row, or by opening `/acp` directly.

### Features

- Auto-discovers workspaces from kiro-cli, Claude Code, and Kiro IDE session data
  - Kiro IDE sessions: `%APPDATA%\Kiro\User\globalStorage\...` (Windows) / `~/.config/Kiro/User/globalStorage/...` (Linux)
- Unified provider-launcher system with extracted icons and configurable colors
- Inline provider filter next to search bar — filters workspaces and sessions panels simultaneously
- Workspace tags with configurable colors, unified tag management (add/delete from popover), multi-workspace bulk tag assignment via gear icon during multi-select, tag/time filtering, and hidden workspaces — unified filtering applies to both workspaces and sessions panels with permanent time grouping (Today/Yesterday/This week/Older)
- Resume sessions with one click (opens terminal with `--resume-id`)
- Drive kiro-cli sessions from the browser at `/acp`, with no terminal — create a session or resume an
  exited one over ACP, stream the agent's output, cancel a turn, close the session, queue a prompt for
  after the current turn, or steer the agent mid-turn. Paste a screenshot
  straight into the prompt with Ctrl+V, or drag one onto it. Creating asks which
  workspace first; each row's `⋯` menu can permanently delete a session from the kiro-cli store, from
  this machine only. A workspace-grouped session browser lists what is resumable and greys out sessions
  another process currently holds. See *Agent sessions* below
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
acp_max_sessions = 8  # 1-16. Concurrent kiro-cli ACP sessions. Each costs ~3 processes and ~161 MB
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

Reached from the **ACP** button beside the logo in the dashboard topbar, or by opening `/acp` directly.
`/acp` carries the mirror of that button — **Main dashboard**, in the same corner — so the two surfaces
switch between each other from the same place. The dashboard is loopback-only, so a phone reaching `/acp`
over remote access sees the logo there but no dashboard button; the link would only ever have produced a
403.

`/acp` drives kiro-cli over ACP: one supervised `kiro-cli acp` process holds every session PowerAtlas
opens. The left rail lists workspaces with their sessions — ten workspaces and three sessions each by
default, each axis paging independently — and marks every visible row *available*, *held by PowerAtlas*,
or *locked* by another process. Selecting a row resumes that session and replays its history; sessions
whose workspace directory no longer exists are marked so, since they cannot be resumed usefully.
If the connection drops, the page reconnects automatically with exponential backoff (1 s, 2 s, … up to 30 s); the Reconnect button remains available for immediate retry.

**The rail groups by workspace, by day, or by status.** The sliders button beside *Refresh* switches between them and
remembers the choice. Grouped by day the rail shows every session across every workspace, newest first,
cut into *Today*, *Yesterday* and dated groups — by **your** clock, not the machine's, so the grouping is
right on a phone in another timezone. Status groups every session into Working / Waiting / Errored / Available / Locked buckets, showing only occupied buckets — useful when you want to see all active sessions at a glance. In each mode each group shows three sessions and offers the rest, and
any group can be collapsed by its heading. Timestamps show only what distinguishes them — a clock for
today, a day for this year. Hovering a row states all three of its facts at once, in any grouping
mode: `[{workspace}]: {session title} - {date & time}`, so the project a row came from, a title too long
for the rail's width and the full timestamp are one hover away. On a desktop the rail's right edge drags
to resize it, between 220 px and half the window, and that width is remembered too; arrow keys move it
when the handle has focus.

Sessions in the /acp rail show a colour-coded status dot: blue (agent working), amber (waiting for you), red (error), white (idle), or static green (turn finished while you weren't looking).

**Creating a session asks where first.** *New session* — in the rail and in the conversation toolbar —
opens a picker offering the agent's own scratch folder, for general local work that lights up no
workspace in the dashboard, or any workspace that already has kiro-cli sessions, with a filter box.
Workspaces whose folder is no longer on this machine are left out and counted, because a session cannot
be created in a directory that does not exist. If you already have a session open, the picker says so —
it keeps one of the `acp_max_sessions` slots until closed or reclaimed — and offers to close it first;
that offer is off by default, so leaving a long turn running while you start another session still
works. At the session limit both create controls are disabled and say why, rather than failing after
the press.

**Deleting a session is possible, from this machine only.** Each rail row carries a `⋯` menu whose
*Delete session* erases that conversation from kiro-cli's own store — the transcript, its metadata, its
lock and its task files — after a confirmation. This is not the same as *Close*, which only releases
the memory and leaves the conversation resumable. It cannot be undone, there is no trash, and it is
refused for a session PowerAtlas has open (close it first) or one another process is using. The menu is
not shown at all to a remote viewer and its endpoint is not on the remote path allowlist, so deletion
is reachable only from the machine running PowerAtlas.

**Images go in with Ctrl+V.** Paste a screenshot into the prompt box, or drag an image file onto it, and
it is staged as a thumbnail above the box before anything is sent — each one labelled *Image 1*, *Image 2*
and removable with the `×` beside it. An image on its own is a whole prompt: paste and press Enter without
typing anything. Because a screenshot is far larger than the frame this page sends, each one is scaled to
at most 1568 px on its long edge and re-encoded — WebP where the browser can, JPEG otherwise — until it
fits the budget the server advertises, and refused with the reason if it still will not. Up to four images
go with one prompt, within about 176 KB between them once decoded; both numbers come from the server, so
they are the same ones it enforces.

The conversation itself shows `[Image 1]` where the picture went, and so does the copy the agent reads —
so *"compare image 1 with image 2"* names something it can see. A `[Image N]` marker is inserted at the cursor in the prompt box, showing where the image falls within your text. The thumbnails live above the prompt box
and only until the turn starts; the bytes go to the agent and are deliberately never written into the
transcript, which is what keeps a reload from replaying megabytes and what stops a few screenshots
evicting the conversation behind them. A reloaded transcript therefore shows `[Image 1]` rather than the
picture. The prompt box also grows as you type, up to a limit, then scrolls.

**Queue and Steer let you act during a running turn.** When a turn is active and the prompt box has
text, the Stop button is replaced by two stacked half-height buttons. **Queue** stores the text and
clears the box — showing a cancellable inline note — then sends it as a normal prompt the moment the
turn ends. **Steer** injects the text mid-turn via `_session/steer`, which kiro-cli processes without
interrupting the turn in progress; a brief confirmation appears when the injection is accepted. Both
discard safely if something unexpected happens: Queue restores the text if the connection drops or the
session changes, and Steer restores it if the server returns an error. Two floating arrow buttons (↑ / ↓) appear at the bottom-left of the transcript when there are at least two of your messages; they jump to the previous or next user message.

**A finished answer is redrawn as markdown.** While the agent is still streaming, its text shows as it
arrives; once the answer is complete the bubble is rebuilt with headings, lists, emphasis, code blocks
and pipe tables. A table is as wide as it wants to be until the window says otherwise: given the
room it renders full width with every row on one line, and it starts wrapping only when the pane is
narrower than that. When it does wrap, every column gives up width in proportion rather than one column
absorbing all of it. (The dashboard's session tooltip keeps its own tables on one line and scrolls
instead — it holds names and counts, where this holds prose.) A code block that names its language is
syntax-highlighted and carries that name as a small label above it. Both come from the fence the
agent wrote, so a block opened without a language — or indented rather than fenced — has neither.
The highlighter is Prism, vendored under `static/` rather than fetched from a CDN so that code
blocks still colour with no network, and used through its tokeniser rather than its HTML renderer,
which is what keeps the rule below true of code blocks too. Colour is never allowed to cost the
code: a language the bundle carries no grammar for renders plainly, as does a snippet past 20,000
characters, and so does every block if the highlighter fails to load at all. The label is drawn by
the stylesheet rather than added to the block, so copying a snippet gets the code and not the
language name. Raw HTML and any image the *agent* writes into its markdown are dropped rather than shown
— an image there is a URL the page would fetch on the agent's say-so, which is a different thing from a
picture you attached yourself (see below) — and a link is clickable only when its URL is `http(s)`;
anything else stays as plain text. The page builds every one of those elements itself and
never parses markup, which is what stops an agent running with every tool pre-approved from putting
something executable on a page you have open.

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
  figure above does not include. The crew bar shows each sub-agent's elapsed time; done entries freeze their timer at their actual stop time.

Creating a session writes a permanent `.json`, `.jsonl` and `.lock` into your kiro-cli session store,
as any kiro-cli session does. Resuming one without prompting leaves the transcript byte-identical.

## Remote access (opt-in)

Off by default: with `remote_bind_address` unset, PowerAtlas has exactly one listening socket and it is
loopback. Setting it to this machine's NetBird IP adds a **second** socket on the same port, so the
laptop keeps using `http://127.0.0.1:<port>` unchanged while a phone on the same NetBird network can
reach the agent surface.

**Enabling it — one save, one restart.** Open the topbar's **Remote** button to reach the *Remote
access* panel, type this machine's NetBird IP literal into **Bind address**, and press **Save**. That
one request sets `remote_bind_address` *and* issues the device secret if none exists, and it refuses
the whole write if the secret cannot be created — so the surface cannot become reachable without also
becoming authenticable. The panel re-reads itself on success, so the URL to open and the secret to
paste are on screen straight away. Restart PowerAtlas once and the second socket is bound.

The address is accepted only alongside a fixed non-zero `port`: with `port = 0` the OS assigns a
number per bind call, so the two sockets would land on different ports and a phone cannot bookmark an
ephemeral one anyway. Set **Port** to *Static* in the topbar first. If you forget, the save is
refused and the panel shows the server's own sentence saying so — as it does for a wildcard,
loopback, multicast, bracketed, zone-id'd, non-canonical or non-literal address, so the refusal
always names what to type instead.

Clearing **Bind address** and saving turns remote access off from the next launch. The device secret
is kept, so devices already enrolled work again if you turn it back on.

Authentication needs that device secret, kept at `%LOCALAPPDATA%\power-atlas\remote-secret` (Linux:
`~/.config/power-atlas/remote-secret`) — never in `config.toml`, and never served over the remote
surface it authenticates. **If no secret exists the remote socket is deliberately not bound at all**,
with the reason logged: a listener that nothing can authenticate against is worse than no listener.

*Setting `remote_bind_address` in `config.toml` by hand still works, and it is the one route that
issues no secret* — it leaves you with an address that is refused a listener for exactly the reason
above, visible only in the log. To recover, open the panel: the field already holds the address you
typed, so pressing **Save** issues the missing secret and touches nothing else. Do **not** reach for
**Rotate device secret** to create a first one. Rotation is the revocation control described below,
and it signs out every enrolled device at once.

On the device, open `http://<netbird-ip>:<port>/remote-auth`, enter the secret once, and it is
exchanged for a device cookie valid for 90 days that survives a PowerAtlas restart. Failed attempts are
logged with the peer address and backed off per peer.

Two degradations are deliberate and silent apart from the log: if the NetBird interface is not up yet
at login the remote bind is skipped and the app still starts on loopback, and if the configured `port`
was already taken — so loopback fell back to a random one — the remote bind is skipped too, rather than
exposing a listener on a port that changes every restart.

**What is reachable remotely**, and nothing else: `/acp`, its WebSocket `/ws/acp`, the read-only session
listing `GET /api/acp/sessions`, the workspace list `GET /api/acp/workspaces` that the create picker
reads (paths and session counts, no session content — a strict subset of what the listing already
discloses), `/static/*`, and the `/remote-auth` exchange page. **Session deletion is deliberately not on
that list**: `POST /api/acp/sessions/delete` is refused from the remote address, and `/acp` does not
render the row menu for a remote viewer. The path allowlist is
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
cookie can execute arbitrary commands on this machine as you.

**It also reads out the name of everything you have worked on.** `GET /api/acp/sessions` is paged, and
paging is all there is: `group_page` walks the workspaces and `session_page` walks the sessions inside
each one, with no ceiling other than the data running out. On this machine `group_total` is 61 with
`has_more: true`, so a peer that keeps asking enumerates **every workspace path and every session title
on the machine** — not the 10 workspaces by 3 sessions the page happens to show first. That first page
alone carried an employer name, four client and project names, the directory layout of the whole
machine, a colleague's first name and the subject line of an email. Session titles are the raw text of
the first prompt whenever the store has no title of its own (267 of 1,210 sessions here, 22.1%) — that
proportion is how often the fallback fires, not a bound on what is reachable, because the peer can page
to all 1,210. The same route also answers `mode=recent`, one flat newest-first walk used when the rail
groups sessions by day; it reaches nothing the paged form does not, but it reaches it with a single
cursor instead of two nested ones, so collecting the lot is less work than the paragraph above implies.

This is stated rather than capped: the listing is what makes the phone usable, and truncating it would
break the feature rather than fix the exposure. **The decision it asks of you is whether the machine's
project names are things you are willing to publish to every peer holding a device cookie.** On a
personal machine that is usually yes. On a work laptop — client names, internal hostnames, an employer's
directory conventions — read it as a disclosure and decide deliberately; if the answer is no, leave
`remote_bind_address` unset, which is the default.

**Revoking a device.** There is exactly one mechanism: rotate the secret, from the *Remote access*
panel or `POST /api/remote-access/rotate` (loopback-only, so a peer holding a stolen cookie cannot
re-key the surface around you). Rotation issues a new secret and invalidates **every** device cookie at
once — there is no per-device revocation, so each remaining device must re-enter the new secret.

**Turning it off right now, without a restart.** The *Remote access* panel carries a **Stop remote
access now** button. Pressing it makes every request arriving from a remote address refused
immediately — the same 403 an unlisted path gets — while loopback is untouched, so the dashboard you
pressed it from keeps working. **It does not close the socket**: the port stays bound until PowerAtlas
restarts, so a phone sees a refusal rather than a connection error. That trade was chosen deliberately;
closing the listener would need a restart, which is the thing the control exists to avoid.

Nothing is written to `config.toml`. This is process state, so a restart brings the surface back
according to `remote_bind_address` — use **Bind address** above it, and a restart, for a change that
sticks. **Resume remote access** puts it back immediately and asks for confirmation first. The switch
is `POST /api/remote-access/stop`, loopback-only by the same default-deny allowlist the other two
remote-access routes rely on, so a peer can neither resume a surface you stopped nor stop one you are
using. Only an exact `{"stopped": false}` resumes — any other body stops, because the ambiguous
direction here is the one that refuses.

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
