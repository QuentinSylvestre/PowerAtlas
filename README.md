# PowerAtlas

Desktop launcher and dashboard for kiro-cli, Claude Code, and Kiro IDE sessions. System tray icon with a web UI for discovering, resuming, batch-launching, and reading the full transcript of any session's conversation — plus, for kiro-cli, a built-in agent surface that creates and drives sessions over ACP without a terminal, optionally reachable from your phone over a NetBird network. See the *Agent sessions* and *Remote access* sections below.

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

### Signing in on this machine

Every page and API route on `http://127.0.0.1:<port>` needs a sign-in cookie. Only the sign-in route
itself and `/static` files are exempt. PowerAtlas signs your browser in for you when you open it from
one of its three doors:

- the tray icon's **Open** item;
- a double-tap of the peek hotkey;
- the peek overlay, whose built-in browser signs in again each time it is shown.

Each door puts a fresh **login code** in the URL it opens. The browser exchanges that code once for a
cookie, `pa_local`, and lands on the dashboard. The code works exactly once and expires after 120
seconds. The cookie is HttpOnly and lasts 90 days.

The tray also has **Copy login link**, for a second browser or any deliberate re-entry. It copies a
fresh login link to the clipboard. The link is single-use and expires after 120 seconds, like any
other login code, so do not bookmark it. To keep a bookmark, sign in first and then bookmark
`http://127.0.0.1:<port>/`. The cookie belongs to the host name `127.0.0.1`, so a bookmark that uses
`localhost` instead does not carry it.

A browser with no valid cookie is not served the page. Opening `http://127.0.0.1:<port>/` directly
shows a short page that says to open PowerAtlas from its tray icon. An open tab whose cookie stops
working (because it expired, or the local secret was rotated) says it is signed out and
points to the tray icon. It offers neither Reconnect nor Reload, because both would resend the same
missing cookie; once the tray has signed the browser back in, reload that tab. A rotation also closes
every open `/acp` connection that was signed in under the old secret, so a tab left open is signed
out at once rather than at its next reconnect (see *Signing out other browsers* under *Remote access*
below).

The cookie is signed with a local secret kept at `%LOCALAPPDATA%\power-atlas\local-secret` (Linux:
`~/.config/power-atlas/local-secret`), created at first start and never served to a browser. Any
program running as your user can read that file, so the cookie keeps out other users, web pages and
other programs that cannot read your profile. It does not stop code already running as you. A remote
device on NetBird is unaffected: it still signs in once at `/remote-auth` with the device secret (see
*Remote access* below).

### Features

- Auto-discovers workspaces from kiro-cli, Claude Code, and Kiro IDE session data
  - Kiro IDE sessions: `%APPDATA%\Kiro\User\globalStorage\...` (Windows) / `~/.config/Kiro/User/globalStorage/...` (Linux)
  - kiro-cli v3 sessions: `~/.kiro/sessions/<workspace-hash>/sess_*/`
- Unified provider-launcher system with extracted icons and configurable colors
- Inline provider filter next to the workspaces rail
- Workspace tags with configurable colors, unified tag management (add/delete from popover), multi-workspace bulk tag assignment via gear icon during multi-select, tag/time filtering, and hidden workspaces — grouped by project, date (Today/Yesterday/This week/Older), or status
- Resume sessions with one click (opens terminal with `--resume-id`)
- Click any session in the dashboard's workspaces rail to open its full transcript in a persistent
  panel next to the rail — works for every provider, reading straight from disk. For a kiro-cli v3
  session that is already running live elsewhere, the panel auto-attaches and streams further output;
  for one that is not, a composer lets you send a prompt to spawn it live, without leaving the
  dashboard for `/acp`. The panel carries the same live-session tools `/acp` does: slash-command and
  skill autocomplete, a context-window usage indicator, a tap-to-copy workspace/session-id widget, a
  debug/transport log panel, Queue and Steer send modes with a Stop button, paste-or-drag image
  attachments, a read-only sub-agent/crew panel for fan-outs, and automatic reconnect with exponential
  backoff if the connection drops. `/acp` remains the only surface reachable from another device — the
  dashboard's panel is loopback-only, like the rest of the dashboard
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
- Live session status — sessions currently running in a terminal show a 🟢 Working (agent executing) or 🟡 Waiting (agent finished, your turn) or 🔴 Errored dot; workspace groups show the highest-priority status dot. A "Group by Status" mode in the workspaces rail's settings popover buckets sessions by status instead of by project or date. Detected by matching the working directory of running `claude` / `kiro-cli` processes to session workspaces; also supports v3 kiro-cli sessions (`messages.jsonl` format). Opt-in toast notifications fire when a session transitions from Working to Waiting or Errored (Windows toast via WinRT, Linux via notify-send)
- Multi-select workspaces and batch-launch a provider or custom launcher across all of them at once
- Per-provider settings with default args (e.g. trust-all-tools)
- Pin folders and sessions for quick access — a pinned row shows a small pin glyph in place of its `⋯`
  trigger while nothing is hovered; hovering swaps it back for the row's real actions, `⋯` included.
  Pin/Unpin itself lives inside that `⋯` menu rather than as a button of its own
- Quick actions on a workspace row, revealed on hover: folder icon opens the workspace in your file
  explorer; terminal icon opens a shell there; a custom-apps dropdown launches any configured launcher
  marked "Use selected workspaces" + "Show in workspace card quick actions" (e.g. an editor) directly in
  that folder; a built-in-AI dropdown starts a new session there with any installed provider. A kiro-cli
  v3 session row's own `⋯` menu can permanently delete just that session, and the workspace row's `⋯`
  menu can delete every kiro-cli session in that workspace at once (optionally the folder too, typed-name
  confirmation required) — the same store and endpoint `/acp`'s own per-session delete uses
- Built-in terminal launcher tile opens a shell at selected workspaces or default directory
- A filter box narrows the workspaces rail to matching names/titles as you type (client-side, over whatever pages are currently loaded — paginate or clear the filter to reach the rest)
- Custom launchers with inline args editing and one-click execution
- Global launch profiles with configurable Windows Terminal profile and terminal command
- Launch-profile management (gear icon in topbar) for window mode, autostart, and profile switching
- **Notify me** (gear icon in topbar, off by default) tells you when an ACP session finishes a turn
  or stops waiting for your approval, so you do not have to keep the page in front of you. Two
  surfaces cover different absences: a desktop toast when no browser tab is attached to the session
  at all, and a browser notification when a tab is attached but backgrounded. A permission request
  notifies either way, because it blocks the turn until you answer. A tab you are actually looking at
  stays quiet. Turns you cancelled yourself are not announced. The toggle takes effect immediately,
  with no restart. Notifications reach the machine running PowerAtlas, **not** a remote browser: the
  remote bind serves plain HTTP, and browsers refuse the notification API outside a secure context,
  so a phone on the NetBird address is hard-denied by the browser and cannot be prompted
- **Agent permissions** (gear icon in topbar) sets the permission mode for ACP sessions PowerAtlas
  creates: **Yolo** (the default; nothing asks, except a short Always blocked list that is refused in
  every mode) or **Manual** (common read-only actions run, everything else asks). **Auto** is shown but
  not yet selectable. **Edit rules…** opens Manual's rule editor. See *Tool permissions* under
  *Agent sessions* below
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
acp_prompt_silence_seconds = 1800  # 60-86400. A turn is cancelled after this much SILENCE from the
                                  # agent — not this much total time — so a long turn that keeps
                                  # streaming is never cut off. A 24-hour absolute ceiling still
                                  # applies, so one chunk per window cannot run forever.

# ACP permission mode. Set from the settings menu's "Agent permissions" section, without a restart.
# A hand edit of the mode (or the rules) takes effect when the next new Default session is started, and
# the dashboard then says the mode changed outside it; task-mode sessions (Spec, Plan, ...) are never
# affected. A hand edit of the base agent takes effect at the next restart, or the next time the mode or
# base agent is saved from the dashboard. See "Tool permissions" under "Agent sessions".
acp_permission_mode = "yolo"                # "yolo" or "manual". Anything else loads as "manual".
acp_permission_base_agent = "kiro_default"  # the kiro-cli agent PowerAtlas's own agent is built from
# acp_permission_rules holds Manual's rules. Edit them with "Edit rules…" under Agent permissions
# rather than by hand: the editor checks every pattern, while a hand-edited table is repaired on load
# (a missing row gets the default rules, an unreadable default asks, an invalid allow pattern is
# dropped, and an invalid block pattern stops the rules from being applied) and the settings menu says
# what it changed. An older config's `acp_permissions_enabled = true` becomes "manual", `false`
# becomes "yolo".

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

`/acp` drives kiro-cli over ACP: one supervised `kiro-cli acp --agent-engine v3` process holds every
session PowerAtlas opens. The left rail lists workspaces with their sessions — ten workspaces and three sessions each by
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
*Delete session* erases that conversation's whole directory from kiro-cli's own store — the transcript,
its metadata, and any subagent task files — after a confirmation, leaving the workspace's shared lock
file untouched, since other sessions in the same workspace still depend on it. This is not the same as *Close*, which only releases
the memory and leaves the conversation resumable. It cannot be undone, there is no trash, and it is
refused for a session PowerAtlas has open (close it first) or one another process is using. The menu is
not shown to mobile browsers; authenticated desktop browsers at the remote address can use it. It is
never shown to a remote viewer who has not authenticated with the device secret. In workspace grouping mode, each workspace group header carries a delete affordance (×) that deletes all of that workspace's sessions at once; a confirmation modal shows the session count, requires typing the folder name to confirm, and offers a separate "Delete folder from filesystem" checkbox (unchecked by default, irreversible) that also removes the directory from disk and cleans up the workspace's `config.toml` entries.

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

**Queue and Steer let you choose a send mode during a running turn.** When a turn is active and the
prompt box has text, the Stop button is replaced by a single full-height action button and a
send-mode selector. Choose **Steer** (the default) or **Queue** from the selector; the button label
tracks your choice and the selection persists across page reloads. **Queue** stores the text and
clears the box — showing a cancellable inline note — then sends it as a normal prompt the moment the
turn ends. **Steer** injects the text mid-turn via `_session/steer`, which kiro-cli processes without
interrupting the turn in progress; the injected text appears as a dimmed band in the transcript and
persists across WebSocket reconnects within the same PowerAtlas session. Pressing Enter during a turn triggers whichever send mode is selected,
consistent with Enter sending a prompt outside a turn. Both discard safely if something unexpected
happens: Queue restores the text if the connection drops or the session changes, and Steer restores
it if the server returns an error. Two floating arrow buttons (↑ / ↓) appear at the bottom-left of the transcript when there are at least two of your messages; they jump to the previous or next user message.

**A finished answer is redrawn as markdown.** While the agent is still streaming, its text shows as it
arrives; once the answer is complete the bubble is rebuilt with headings, lists, emphasis, code blocks
and pipe tables. A table is as wide as it wants to be until the window says otherwise: given the
room it renders full width with every row on one line, and it starts wrapping only when the pane is
narrower than that. When it does wrap, every column gives up width in proportion rather than one column
absorbing all of it. A code block that names its language is
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
never parses markup, which is defense-in-depth against the agent's own output landing on a page you
have open: the transcript holds text the agent wrote, not text you vetted, and never becomes something
executable regardless of what the agent tried to render.

**Tool calls say what happened, not only what ran.** Each row carries an icon for the kind of tool,
a coloured status — pending, in progress, completed, failed — and, for a call that touched a file,
that file's name with the full path on hover. What the call *returned* is shown in two parts,
because the two halves are worth different things. A short summary is kept: a shell call's exit
status and the first lines of its standard error, a read's line and byte count. Below it, *Show
output* opens the rest — the tail of what a command printed. The summary survives a reload of the
page and the body does not, so a row whose output is no longer held says *output not retained
after reload* rather than looking as though the command printed nothing. That split is deliberate:
PowerAtlas keeps a rolling buffer of each session's transcript so a reconnecting page can be
rebuilt, and a build log or a large file read would push the conversation out of it. Tool output is
never treated as markdown — it is bytes a command printed, not prose the agent wrote, and it is
shown verbatim.

An edit row collapses all of that into one control. Collapsed, it shows the name, kind, status, and
a short filename-plus-stat one-liner — enough to tell what a call touched without opening it. One
click on the row's single arrow swaps that one-liner for the full path, the `+n −m` stat again, and
the diff itself — coloured by line and numbered the way kiro-cli's own TUI numbers a file, seeded
from the location the edit reported rather than always starting at 1 — so the same fact is never
shown twice at once. There is no separate hover for the path here and no second toggle for the
summary; both live behind the one control an edit row has.

**A successful edit's diff survives even a session PowerAtlas's own memory has lost.** Loading a
session kiro-cli still has but this process does not — after a restart, or one simply never opened
here before — replays the conversation over the agent protocol, and that replay does not resend a
tool call's raw output. For every other kind of call this is the same *output not retained* gap
above. For a `write` call specifically, PowerAtlas separately reads kiro-cli's own on-disk session
transcript — the same file its own interface reads to redraw a diff after resuming a session — and
uses it to show the real diff again, not just the `+n −m` stat. Only a write kiro-cli actually
completed is shown this way; one that was rejected or cancelled never touched the file and still
shows nothing, correctly.

Three things are worth knowing before leaving a long task running:

- **A turn is bounded by silence, not by duration.** A turn that keeps streaming runs as long as it
  needs; one that goes quiet for `acp_prompt_silence_seconds` (default 30 minutes) is cancelled agent
  side. A 24-hour absolute ceiling applies regardless, so a turn emitting one chunk per window cannot
  hold a session open forever.
- **Idle sessions are reclaimed.** A session with no tab attached, no turn running and no load in
  flight is released locally after `acp_idle_ttl_seconds` (default 30 minutes), without waiting for
  wire-level confirmation from the agent — kiro-cli's v3 protocol offers none to wait for. The
  transcript is left on disk, so the session is resumable afterwards at the cost of one reload — and a
  session with a tab open or a turn running is never swept, however old.
- **Cancelling a turn does not kill what the agent started.** Measured on kiro-cli 2.16.0:
  `session/cancel` stops the ACP turn while leaving any shell subprocess the agent spawned running to
  completion. It is reaped only when PowerAtlas exits.
  So a cancelled build or long-running command keeps consuming CPU and memory that the per-session
  figure above does not include. When a fan-out runs, an inline crew panel appears directly below the spawner tool call in the transcript, listing each sub-agent with its elapsed time; done entries freeze their timer at their actual stop time. Each fan-out produces its own panel. The panel updates live, mid-fan-out, populated as each sub-agent's own tool calls and streamed output arrive — not deferred to turn completion.

Creating a session writes `session.json` and `messages.jsonl` into a per-session `sess_<uuid>/`
directory inside your kiro-cli session store. The workspace's `.lock` file lives one level up, shared
across every session in that workspace rather than owned by this one. Resuming a session without
prompting leaves the transcript byte-identical.

**A session's task mode is chosen when it is created, and only then.** The *New session* picker
carries a task-mode control offering Default, Spec, Quick spec, Bug fix, Plan and Semantic reviewer,
which map to kiro-cli's own `kiro_default`, `spec`, `quick-spec`, `bug-fix`, `plan` and
`semantic_reviewer` agent modes. Default is resolved by the server when the session is created: it
binds the derived agent `poweratlas-acp`, which carries the permission mode below. While that agent
is not in effect, a Default session is refused with the reason and the fix; the other task modes still
start, without PowerAtlas's rules. The control resets to Default each time the picker opens. The mode
is fixed for the life of the session: kiro-cli ignores a different mode on resume, so a session created
in Spec mode stays in Spec mode however it is reopened. The same control is offered when a session is
created from the dashboard's workspace sparkle menu.

**Tool permissions follow the permission mode.** The agent runs kiro-cli's v3 engine without
`--trust-all-tools`; the two are incompatible, and the flag is never passed. What a session may do
without asking is set by the **Agent permissions** section of the settings menu (gear icon in the
topbar). PowerAtlas writes a derived agent, `~/.kiro/agents/poweratlas-acp.md`, in every mode. It is a
copy of the **base agent** (the field below the modes, `kiro_default` unless you change it; a minimal
agent is used if that file does not exist) with one `permissions:` block that PowerAtlas compiles from
the mode. Default sessions bind that agent. The base agent file is never modified, so terminal kiro-cli
sessions keep their own posture.

- **Yolo** (the default). Every action runs without asking, except the Always blocked list below.
  kiro-cli's own built-in rules still apply in every mode and no agent can lift them: it asks before
  writes to `.git`, `.vscode`, `*.code-workspace`, `.kiro/agents` and `.kiro/hooks` (in the workspace
  and in your home folder), and it blocks writes to `.kiroignore`, `.kiro/settings`,
  `~/.kiro/workspace-roots`, `~/.kiro/sandbox-state`, `~/.kiro/web-session`,
  `~/.kiro/powers/installed/*/mcp.json` and `~/.kiro/cloud-cache`.
- **Manual.** The Always blocked list, plus PowerAtlas's default rules. Reading files under the
  session's folder runs without asking, and so do the exact commands `git status`, `git log`,
  `git diff`, `git branch`, `pwd`, `whoami` and `uname`. Everything else asks: other shell commands,
  file writes, reads outside the session's folder, web fetches and searches, MCP tools, sub-agents,
  skills and powers. `git` commands carrying `--output`, `--no-index` or `--ext-diff` are refused.
  Writes to **Protected** items — agent definitions, steering files, skill files and hooks under `.kiro`
  — always ask. A configuration migrated from the old on setting also keeps its old rule that
  refuses writes to `**/.kiro/agents/**` outright. Sub-agents run under the parent session's rules
  (measured 2026-09-24). Nothing asks when a session starts.
- **Auto** is shown but not selectable yet; when it arrives it will decide Manual's prompts itself.

**Manual's rules** are edited with **Edit rules…** under Agent permissions, in any mode; they are used
while Manual is selected. The editor has one row per kind of action: Read files, Write files, Run
commands, Web fetch, Web search, MCP tools, Sub-agents, Skills and Powers. Each row has a default for
anything no pattern matches — **Allow** (run without asking), **Ask** (show a permission prompt) or
**Block** (refuse without asking) — and two pattern lists: **Allow without asking** (not shown when the
default is Allow, since everything already runs) and **Always block**, which wins over everything else
in the row, including the same pattern under Allow without asking. The Always blocked list and the
Protected items apply on top of both lists. `./**` is shown as "the session folder". Patterns are file globs for the two file rows,
exact commands for Run commands (see the limits below; `/` and `\` are different characters, and
patterns are not mirrored for you), site host names such as `example.com` for Web fetch (not full
addresses), `server/tool` for MCP tools, and names for sub-agents, skills and powers, as the
permission prompt shows them. The editor warns, without refusing, when a command pattern names an
interpreter or shell such as `python`, `node`, `pwsh`, `cmd` or `bash` anywhere (equivalent to
allowing everything), starts with a deleting or downloading command such as `rm` or `curl`, or holds
any `*` (which also allows output redirection); when Write files is set to Allow, or a Write files
pattern covers the whole session folder, a drive root or the home folder; when a Web fetch pattern is
typed as an address (`https://example.com/x` never matches; use `example.com`); and when a pattern is
in both lists of a row. Save checks every row and pattern and refuses the whole save when one is not
usable: a pattern must be 1-200 printable characters, must not match everything (a bare `*` or `**`,
or anything else with no literal character besides `*`, `?`, `/`, `\`, `.`, `:` and spaces, such as
`*.*` or `?:/**`; `./**` names the session folder and is fine), and a list holds at most 100
patterns. The refused pattern is marked in its row with the reason. Closing the editor with unsaved
changes asks whether to discard them. The editor does not open while a stored Always block list in
`config.toml` is not a list, since saving from it would drop that list; fix the file first.

The editor also shows the **Protected** items (agent definitions, steering files, skill files and hooks
in any `.kiro` folder), each with a **Block outright** switch that
turns its always-ask into a refusal, and, per item, the links inside it that point elsewhere and are
therefore not covered, with their targets. The Always blocked list is shown read-only. A saved change
applies to sessions created afterwards; if the derived agent cannot be written, the rules are still
saved and a warning says they are not yet in effect.

**Always blocked**, in every mode, is refused silently with kiro-cli's own denial text: reading SSH,
AWS, Azure and gcloud credential folders, kiro-cli's token files and PowerAtlas's own sign-in
secrets; shell commands that mention those names; and file-tool writes to PowerAtlas's derived agent
and to kiro-cli's settings and workspace-roots folders. The settings menu lists every pattern. The
lists include the common Windows 8.3 short spellings (`SSH~1`), but not every alias: a link to one of
those folders, or an unusual short name, is not covered. The shell part catches common accidents, not
a determined command: it also blocks harmless commands such as `ssh -i ~/.ssh/key`.

Four limits of how kiro-cli matches rules, measured 2026-09-24 on kiro-cli 2.24.0, shape all of this.
Command patterns are matched literally and case-sensitively against the whole command, which is why
the default commands are exact: `git log --oneline` asks. A `*` in a command pattern would also allow
an output redirection such as `git status > notes.txt`, which writes that file with no write check.
Write rules cover kiro-cli's file-writing tools only; a shell redirection writes any path unchecked. And
a rule matches a symlink's target, so a link inside a Protected folder that points elsewhere is not
protected: the settings menu counts such links under each Protected item.

A mode change applies to sessions created afterwards; kiro-cli keeps a running session on the rules it
started with. Terminal sessions and the other task modes (Spec, Plan and so on) are not covered.

If the derived agent cannot be written, or the file on disk is not what the current settings compile
to — for example because a file of that name that PowerAtlas did not write is in the way — the menu
shows a "not in effect" badge with the reason, and **new Default sessions are refused** with the cause
and the fix until it is resolved. PowerAtlas never starts a Default session without the Always blocked
list. A file edited by hand is regenerated automatically the next time a session is created. If
`config.toml` is changed while PowerAtlas runs, the next new session picks the change up and the
settings menu says the mode was changed outside the dashboard. Anything that can edit files on this
machine as you can change the mode, including an agent you let run a shell or an interpreter such as
Python; in Manual, allowing an interpreter is equivalent to allowing everything.

When a session does ask, the request renders inline in the transcript and the turn pauses until you
answer, from any tab or after a reload, the same way a clarifying question does. The prompt shows the
full command or tool title, plus what kiro-cli reports about the request: the capability, the resource
(for a write, the file name), the rule that matched, and where that rule came from. When kiro-cli
splits a command, it names the first part that needs asking (`git status && echo x` names `echo x`;
`echo a && echo b` names `echo a`), and that part is shown as **Triggered by**. Other parts of the same
command may need asking too. kiro-cli offers
**Allow** (this once), **Deny** (this once) and **Always deny**; it offers no "always allow".
**Always deny** lasts for the current session only, and the button says so: kiro-cli keeps the rule in
memory, writes nothing to disk, and a new session asks again. Denying
leaves the tool call failed with nothing written. Nothing answers on your behalf: a session nobody is
watching waits at its first such request until the silence timeout above cancels the turn.
`plans/ROADMAP.md`'s item on deciding permission requests by rule for unattended sessions is what would
change that.

**Allow, and always in new sessions…** appears on a prompt that a Manual row raised because nothing
in its Allow without asking list matched. It opens a small field under the prompt, labelled with the
row, holding a pattern you can edit before saving. For a command it is the exact command, or the part
shown as Triggered by, and never a pattern with a wildcard, since `*`, `?` or `[` would also match
output redirection. For a file it is the file's folder, as a full path followed by `/**`. kiro-cli
reports a file inside the session folder by a relative name, and a relative pattern would match in the
folder of every session, so the field joins it to the session folder. The field holds the exact file
instead when its folder is the session folder or a folder above it, a drive root, your home folder or
a folder above that (such as `C:\Users`), and when the path holds a wildcard, a `.` or `..` part, or
a `\\?\` or network prefix. A file rule applies to that folder in every new session, whichever folder
the session opens in. For an MCP tool, a sub-agent or a skill the field holds its name as the prompt
shows it. The same warnings as the rule editor appear as you type, such as the one for an interpreter,
a broad folder for reading or writing, or a relative file pattern. For a split command, the warnings
for the whole command appear too, since the prompt is allowed as a whole. **Save** adds the pattern to
that row's Allow without asking list and then allows this prompt once. The prompt then says "Rule
added — new sessions will not ask", or, when the rule covers only the Triggered by part, that the rest
may still ask. The rule applies to sessions created afterwards; the current session keeps asking,
because kiro-cli reads its rules when a session starts. Saving from a prompt never clears the
dashboard's notice that the settings changed outside it; while that notice is pending, the prompt
points at it after saving. If the prompt was answered elsewhere while you were saving, the rule is
still saved and the prompt says so. If the rule is refused (for example an empty or match-everything
pattern, or a file pattern that goes up a folder with `..`) the reason is shown and the prompt stays
open; if the rule was saved but the derived agent could not be written, the warning is shown with it.
The button does not appear in Yolo (nothing there asks through a row), on Protected prompts or
kiro-cli's own built-in prompts (no row can silence those), in task modes such as Spec or Plan, in a
reopened session that kiro-cli runs under another agent or whose saved agent cannot be read, on a
remote page (rules change only from this computer; a remote request is refused), or for web fetches,
web searches and powers. A web fetch prompt names the site by host, which a pattern saved
from it could be mistaken for an address; add host names in the rule editor instead. Web search and
power prompts have not been measured to match a pattern, so the button is not offered there.

**The agent can ask a clarifying question mid-turn, and the page answers it inline.** When the agent
needs you to choose between options before continuing, the question and its choices render as buttons
directly in the transcript, and the turn stays paused until you click one. Answering (from any tab, or
on reload) marks the request resolved everywhere it's shown; a request answered elsewhere never shows
as still-pending.

**The context-usage bar, steering acknowledgement, and the session title** all show up as the UI
elements you'd expect, fed by the protocol's own notification frames — nothing to configure
separately.

**An MCP connection or authorization failure surfaces inline in the transcript**, as a plain-text
error message attached to the turn, and via the **MCP status indicator** in the session toolbar — labelled "MCP" with connected over active servers (for example "MCP 1/2"). It appears once kiro-cli
reports server status. Red means a server failed to start; amber means one is still connecting or needs
sign-in. Opening it lists each server with its state in words, disabled servers last. A server that
needs sign-in (an OAuth MCP server such as Atlassian) has a **Connect** button: it opens the provider's
sign-in page in the browser of the PC PowerAtlas runs on, because kiro-cli receives the sign-in on that
PC's `localhost`. Finish there, even when you pressed Connect from another device. The sign-in is kept
for PowerAtlas sessions, encrypted for your Windows user (`acp-secrets.bin` in PowerAtlas's config
folder), and survives restarts. It is separate from a terminal kiro-cli sign-in, so each needs its own
once.

**The slash-command palette lists the agent's own catalogue.** Selecting a skill entry works the same
way as typing its name; kiro-default (the palette's own "switch agent" entry, meaningless here because
a session's agent is fixed when it is created, from the task mode and, for Default, the server's
choice between `poweratlas-acp` and `kiro_default`) and three built-in steering documents (`architecture-selection`,
`quick-spec`, `bug-fix`) are not offered — no working trigger exists for those three via anything the
page could click, so they're left out rather than shown broken.

## Remote access (opt-in)

Off by default: with `remote_bind_address` unset, PowerAtlas has exactly one listening socket and it is
loopback. Setting it to this machine's NetBird IP adds a **second** socket on the same port, so the
laptop keeps using `http://127.0.0.1:<port>` while a phone on the same NetBird network can reach the
agent surface. The laptop still signs in through the tray as described under *Signing in on this
machine*: a bare visit to `http://127.0.0.1:<port>` without the sign-in cookie shows the "open
PowerAtlas from its tray icon" page. The two sign-ins are separate. A loopback browser needs the
`pa_local` cookie, and a NetBird device needs the device cookie described below; neither is accepted in
place of the other.

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
discloses), `POST /api/acp/sessions/delete` (authenticated desktop browsers only — the row menu is not
rendered for mobile UA), `/static/*`, and the `/remote-auth` exchange page. The path allowlist is
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

Weigh that against what is behind the cookie. A peer holding a valid cookie can prompt the agent and
press the approve button on every tool request it raises, so they can execute arbitrary commands on
this machine as you. The permission prompt is a gate for the person holding the page, not a defence
against them.

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

**Signing out other browsers on this machine.** The loopback cookie, `pa_local`, has its own secret
and its own revocation. In the settings menu (gear icon), *Browser sign-in* has a **Sign out other
browsers (rotate the local key)** button. The first click arms it and the second, within five seconds,
issues a new local secret: every other browser's cookie stops verifying, their open `/acp`
connections are closed, and each must be reopened from the tray. The browser you pressed it from gets
a fresh cookie in the same response and stays signed in. Use it if a `pa_local` cookie may have leaked,
since it otherwise stays valid for 90 days. The same action is `POST /api/local-secret/rotate`,
loopback-only and only for a browser that is already signed in. If the dashboard itself is out of
reach, the fallback is to quit PowerAtlas, delete `%LOCALAPPDATA%\power-atlas\local-secret` (Linux:
`~/.config/power-atlas/local-secret`) and start it again: a new secret is created at startup, and
every browser signs in again from the tray. The same section warns when the local secret could not be
saved and is held in memory only; every browser then has to sign in again after the next restart.

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
address — and a process that collects it gains full remote access to the agent surface, approve
button included.

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
