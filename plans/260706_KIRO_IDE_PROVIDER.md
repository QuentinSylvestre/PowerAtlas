# Kiro IDE Provider

> **Date**: 2026-07-06
> **Status**: In Progress  <!-- Status lifecycle: Exploring → Draft → In Progress → Complete -->
> **Estimated effort**: 1-2 days
> **Scope**: Add Kiro IDE as a third session provider in PowerAtlas

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently discovers and manages sessions from two providers: kiro-cli (terminal-based) and Claude Code (terminal-based). Kiro IDE — a GUI-based IDE — also stores session data locally but is invisible to PowerAtlas. Users who work across all three tools have no unified view of their Kiro IDE sessions alongside their CLI sessions.

The goal is to add Kiro IDE as a first-class provider: session discovery, browsing, workspace filtering, and launching (open workspace in Kiro IDE). This follows the established provider adapter pattern and integrates with the recently-shipped merged multi-provider workspace cards.

### Success criteria

1. **SC1 — Session discovery**: Kiro IDE sessions are discovered from `%APPDATA%\Kiro\User\globalStorage\kiro.kiroagent\workspace-sessions\` (Windows) and `~/.config/Kiro/User/globalStorage/kiro.kiroagent/workspace-sessions/` (Linux). Workspace folders (base64-encoded paths) are decoded and mapped to real paths.

2. **SC2 — Adapter interface compliance**: New `data_kiro_ide.py` module implements the full provider adapter interface: `is_available()`, `discover_workspaces()`, `load_sessions()`, `refresh_stale_entries_for_cwd()`, `get_session_tail()`, `get_first_prompt()`, plus the new `find_session_workspace()` method.

3. **SC3 — UI integration**: Kiro IDE appears in the provider filter tabs, workspace card badges/gradients, session row borders, and launcher grid. Display name "Kiro IDE", badge "I", default color `#8b5cf6`.

4. **SC4 — Non-terminal launch**: "New session" and "Resume" buttons for Kiro IDE open the workspace in Kiro IDE via `kiro <path>` as a detached GUI process (no terminal wrapper). Resume is functionally identical to "new" (opens workspace, user finds session in IDE history).

5. **SC5 — Generic dispatch refactor**: Hardcoded provider if/else branches in `data.py` (`get_session_tail`, `get_first_prompt`) and `launcher.py` (CLI args) are refactored to generic dispatch. New `find_session_workspace(session_id)` adapter method replaces hardcoded pinned-session resolution in `warmup_all()` and `_render_pinned_sessions()`.

6. **SC6 — Provider-level terminal flag**: `launch_session()` respects a per-provider `terminal` flag. Terminal providers get the existing terminal-wrapper flow; non-terminal providers (Kiro IDE) get direct `subprocess.Popen` with `DETACHED_PROCESS`.

7. **SC7 — Validated parsing**: Session parsing logic is validated against real Kiro IDE session files on disk to confirm format assumptions (base64 folder encoding, sessions.json schema, per-session history JSON structure).

8. **SC8 — Tests updated**: Existing tests are extended to cover the third provider (mock data, filter tests, launch tests). No new test files.

### Scope boundaries & non-goals

**In scope**: New adapter module, provider registration, generic dispatch refactor, non-terminal launch, UI integration (filter/badges/buttons/icons), search integration, pinned session resolution, validation against real data, test updates.

**Non-goals**: Kiro IDE session resume by ID (no CLI support). kiro-cli v3 session support (separate roadmap item). Auto-removing the existing "Kiro IDE" custom launcher from user config. Linux Kiro IDE testing (verified on Windows only). Session content editing or deletion.

---

## Discovery

### Existing patterns & constraints

- Provider registry at `data.py:50-53` maps name → module. Each module exports 6 functions (now 7 with `find_session_workspace`).
- `SessionCache` uses compound `(provider, norm_cwd)` keys — already supports arbitrary provider strings (`data.py:62-100`).
- Workspace grouping (`web.py:63-96`) is provider-agnostic — merges any provider's tuples by normalized path.
- Visual identity dicts (`web.py:19-34`) need entries for the new provider.
- `launcher.py:110-119` has hardcoded if/else for CLI args per provider.
- `data.py:228-237` dispatches `get_session_tail`/`get_first_prompt` with explicit if/else (not polymorphic).
- `data.py:220-281` `warmup_all()` has provider-specific fallback branches for pinned session resolution.
- `web.py:565-597` `_render_pinned_sessions()` and `web.py:406-432` search route have hardcoded kiro-cli/claude-code scans.
- Icon extraction (`web.py:899+`) uses `launcher._PROVIDER_BINARY` to resolve binary for icon extraction — `kiro` binary will work here.
- Provider filter auto-hides when <2 providers available; at 3 it shows naturally.
- Config `provider_settings` is open-ended dict — no schema changes needed.
- AGENTS.md: update existing tests, no new test files unless requested.
- Project MEMORY: cache getters must return copies; custom htmx-mini requires `process()` after innerHTML swaps.

### Risks & mitigations

- **Session format mismatch**: Steering docs describe the format but real files may differ. Mitigation: SC7 requires validation against real files before finalizing parsing logic.
- **Base64 decode edge cases**: Workspace paths with special characters may produce non-standard base64. Mitigation: test decode against all existing workspace folders in the real data directory.
- **`kiro` binary not on PATH**: Some users may have Kiro IDE installed but not the CLI shim. Mitigation: `is_available()` checks data directory existence (not binary availability); launch gracefully errors if `shutil.which("kiro")` fails.
- **Gradient border with 3 providers**: Template currently handles 2-color gradient. With 3, need a 3-way split. Mitigation: extend gradient logic to handle N providers.

### Resolved decisions

- Q1: Launch behavior for Kiro IDE — A: Hybrid (option 3) — Decision: "Open in Kiro IDE" button opens workspace; no session resume capability.
- Q3: Binary name — A: `kiro` on PATH, confirmed working — Decision: `_PROVIDER_BINARY["kiro-ide"] = "kiro"`, launched as detached process without terminal.
- Q4: Default color — A: User set `#8b5cf6` — Decision: `PROVIDER_COLORS["kiro-ide"] = "#8b5cf6"`.
- Q5: Custom launcher coexistence — A: ok — Decision: No auto-migration; user removes custom launcher manually after first-class provider works.
- Q6: Resume button behavior — A: ok (option 1) — Decision: Resume button opens workspace in Kiro IDE (same as "new session" action).
- Q7: Adapter signature for tail/prompt — A: ok — Decision: `get_session_tail(session_id, cwd, max_lines)` and `get_first_prompt(session_id, cwd)` take `cwd`; refactor dispatch to generic.
- Q8: Hardcoded dispatch refactor — A: ok — Decision: Add `find_session_workspace(session_id)` to adapter interface; refactor `warmup_all()`, `_render_pinned_sessions()`, search to iterate all providers generically.
- Q9: Session content extraction semantics — A: yes, validate on real files — Decision: Same semantics as other providers (first user message, last user message, last assistant tail), validated against real session data.
- Q10: Launch mechanism — A: ok — Decision: Provider-level `terminal` flag in registry; `launch_session()` branches on it for GUI vs CLI providers.

### Open items

- Exact `sessions.json` schema to be confirmed empirically during validation step (SC7).
- Whether base64 decode produces the workspace path with or without a trailing separator — verify against real folders.
- 3-provider gradient border rendering approach (CSS detail, resolved during implementation).

### Assumptions (unconfirmed)

- Kiro IDE session format matches steering docs (`sessions.json` index + `<sessionId>.json` history files with `history[].message.{role, content}`). Validated in SC7.
- Base64 decode uses URL-safe alphabet with `- → +`, `_ → /`, `? → =` substitution. Validated in SC7.
- No session-resume CLI exists for Kiro IDE (open workspace is the only launch action).

### Recommended approach

1. Create `data_kiro_ide.py` adapter with full interface compliance, including base64 workspace path decoding and session JSON parsing.
2. Validate parsing against real Kiro IDE session files on disk.
3. Register in `PROVIDERS` dict and add visual identity entries across `web.py` and `launcher.py`.
4. Refactor hardcoded dispatch in `data.py` (`get_session_tail`, `get_first_prompt`) to generic provider method calls.
5. Add `find_session_workspace()` to all adapters; refactor `warmup_all()`, `_render_pinned_sessions()`, and search to use it.
6. Add provider-level `terminal` flag; update `launch_session()` to support non-terminal launch for GUI providers.
7. Handle 3-provider gradient border in template/CSS.
8. Update existing tests to cover third provider.

---

## 1) Current State

- **Provider registry** (`data.py:50-53`): Two providers registered (`kiro-cli`, `claude-code`). Each module exports `is_available()`, `discover_workspaces()`, `load_sessions()`, `refresh_stale_entries_for_cwd()`, `get_session_tail()`, `get_first_prompt()`.
- **Dispatch** (`data.py:228-237`): `get_session_tail()` and `get_first_prompt()` use hardcoded `if provider == "claude-code"` else kiro-cli fallback. Not polymorphic.
- **Pinned session resolution** (`data.py:241-281`, `web.py:565-597`): Two hardcoded fallback branches per provider for finding which workspace a pinned session belongs to.
- **Launch** (`launcher.py:110-119`): Hardcoded if/else builds CLI args. All launches open a terminal.
- **Visual identity** (`web.py:19-34`, `launcher.py:60-67`): Dicts map provider name to color, display name, badge, binary, etc.
- **Gradient border** (`workspace_card.html:4`): Only handles 1-2 providers (uses `linear-gradient(to bottom, color1 50%, color2 50%)`).
- **Search** (`web.py:406-432`): Pinned session search only scans kiro-cli metadata.
- **Kiro IDE sessions on disk** (empirically confirmed):
  - Location: `%APPDATA%\Kiro\User\globalStorage\kiro.kiroagent\workspace-sessions\`
  - Folder names: URL-safe base64-encoded workspace paths (`-`→`+`, `_`→`/`, `?`→`=`)
  - `sessions.json`: JSON array of `{sessionId, title, dateCreated (Unix ms string), workspaceDirectory}`
  - `<sessionId>.json`: `{history: [{message: {role, content, id}, contextItems, ...}], title, sessionId, workspaceDirectory, ...}`
  - User `content`: array of `{type: "text", text: "..."}` blocks
  - Assistant `content`: plain string
  - Decoded paths are lowercase with backslashes (Windows): `c:\users\...\project`

## 2) Goal

Add Kiro IDE as a first-class provider with session discovery, browsing, filtering, and workspace launching, while refactoring hardcoded provider dispatch into a generic pattern that scales to N providers.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Provider name | `"kiro-ide"` | `"kiro"`, `"kiro-desktop"` | Kebab-case consistent with `kiro-cli` and `claude-code`; avoids confusion with `kiro-cli` |
| Launch mechanism | Direct subprocess (no terminal) | Terminal wrapper, no launch at all | Kiro IDE is a GUI app; confirmed `kiro <path>` works |
| Resume behavior | Opens workspace (same as "new") | Disable resume button, no-op | User can find session in IDE history; suppressing buttons makes provider feel second-class |
| Dispatch refactor | All providers accept `(session_id, cwd, ...)` uniformly | Add third elif branch | Scales to N providers without modification; kiro-cli adapts by ignoring cwd |
| Pinned resolution | Generic `find_session_workspace(session_id)` per module | Add third hardcoded branch | Same rationale — generic scales |
| Terminal flag | Per-provider boolean in registry dict | Separate launch function, config-level flag | Minimal change to existing code; mirrors custom launcher `terminal` field |
| Color | `#8b5cf6` (violet) | Various — user chose this | Distinct from kiro-cli (`#7138cc`) and claude-code (`#c2590f`) |
| Gradient border | CSS handles N providers via even-split gradient stops | Max 2 providers, hide border for 3+ | Gracefully handles future providers too |

## 4) External Dependencies & Costs

### Required external changes

None — this is a code-only change. No infrastructure, CI/CD, IAM, or third-party service changes required.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Adapter module + validation [QA] [P:2]

**Goal**: Create `data_kiro_ide.py` with full adapter interface, validate against real session data.

**File scope**: `src/power_atlas/data_kiro_ide.py` (new), `tests/test_data.py`

**Details**:

```python
# data_kiro_ide.py — key structure

import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .data import Session, _FileInfo, _normalize_path, _cap_text


def _sessions_dir() -> Path:
    """Platform-appropriate Kiro IDE workspace-sessions directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", ""))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    return base / "Kiro" / "User" / "globalStorage" / "kiro.kiroagent" / "workspace-sessions"


SESSIONS_DIR = _sessions_dir()


def _decode_folder_name(encoded: str) -> str:
    """Decode URL-safe base64 folder name to workspace path."""
    fixed = encoded.replace('-', '+').replace('_', '/').replace('?', '=')
    return base64.b64decode(fixed).decode("utf-8")


def _encode_path(path: str) -> str:
    """Encode workspace path to URL-safe base64 folder name."""
    encoded = base64.b64encode(path.encode("utf-8")).decode("ascii")
    return encoded.replace('+', '-').replace('/', '_').replace('=', '?')


def is_available() -> bool:
    """Return True if Kiro IDE session data exists on disk."""
    return SESSIONS_DIR.is_dir()


def discover_workspaces() -> list[tuple[str, int, str]]:
    """Discover workspaces from Kiro IDE session folders."""
    # Iterate workspace folders, decode names, read sessions.json for count + latest date
    ...


def load_sessions(cwd: str) -> tuple[list[Session], dict[str, _FileInfo]]:
    """Load sessions for a workspace. Find matching folder, read sessions.json."""
    ...


def refresh_stale_entries_for_cwd(norm_cwd: str, old_stats: dict[str, _FileInfo]) -> bool:
    """Check if sessions.json or session files have changed."""
    ...


def get_session_tail(session_id: str, cwd: str, max_lines: int = 15) -> list[str]:
    """Extract last N assistant messages from a session file."""
    # Read <sessionId>.json, iterate history backwards, collect role=="assistant" content strings
    ...


def get_first_prompt(session_id: str, cwd: str) -> str:
    """Extract first user message from a session file."""
    # Read <sessionId>.json, find first role=="user", concatenate content[].text blocks
    ...


def find_session_workspace(session_id: str) -> str | None:
    """Find which workspace a session belongs to by scanning sessions.json files."""
    ...
```

**Key parsing details**:
- `discover_workspaces()`: iterate folders, decode each name (wrap in try/except for `binascii.Error`/`UnicodeDecodeError` — skip malformed), read `sessions.json` array, count entries, compute `updated_at` from `max(int(dateCreated))` converted to ISO — **guard against empty array** (`if not sessions_list: continue`, fall back to folder mtime).
- `load_sessions()`: find workspace folder by encoding the `cwd` to base64. If not found, try scanning all folders for matching `workspaceDirectory` (using `_normalize_path` for comparison). Read `sessions.json` for session list. For each session, **wrap in try/except (OSError, json.JSONDecodeError, UnicodeDecodeError): continue** — session files may not exist or be corrupted. Only read `<sid>.json` head/tail for prompts.
- `get_session_tail()`: read full `<sid>.json`, iterate `history` in reverse, collect strings from `role=="assistant"` entries. **Handle both string content (normal) and array content (defensive).**
- `get_first_prompt()`: read `<sid>.json`, find first `role=="user"` entry, concatenate `content[].text` blocks.
- **Content format**: user messages have `content: [{type:"text", text:"..."}]` (array), assistant messages have `content: "..."` (string). Parse both defensively.
- **Error isolation**: every per-session file read must be wrapped in its own try/except — matching the pattern in `data_kiro.py:84-127` and `data_claude.py:157-204`.
- `find_session_workspace()`: scan all workspace folders' `sessions.json` for matching `sessionId`. For 32 folders with small JSON arrays, this is negligible. **Optimization**: build a `{session_id: folder_path}` reverse index during `discover_workspaces()` and cache it with the workspace results.

**Validation step**: After implementing, run against real data directory and verify:
1. All 32 workspace folders decode to valid paths
2. Session counts match `sessions.json` arrays
3. `first_prompt` and `last_reply_tail` extraction produces sensible text

**Exit criteria**:
- [x] `data_kiro_ide.py` implements all 7 adapter functions
- [x] Base64 decode handles all existing workspace folders without error
- [x] `first_prompt` extraction tested on 3+ real sessions produces correct text
- [x] `get_session_tail` tested on real sessions returns assistant message strings
- [x] Tests in `test_data.py` added for `TestKiroIdeIsAvailable`, `TestKiroIdeDiscoverWorkspaces`, `TestKiroIdLoadSessions`, `TestKiroIdeFindSessionWorkspace`

Implementation (2026-07-06, code: 8957221)
Created `src/power_atlas/data_kiro_ide.py` implementing all 7 adapter functions (is_available, discover_workspaces, load_sessions, refresh_stale_entries_for_cwd, get_session_tail, get_first_prompt, find_session_workspace) following the established provider adapter pattern. The module reads Kiro IDE workspace-sessions from the platform-appropriate APPDATA directory, uses `workspaceDirectory` from `sessions.json` as the canonical path (not base64 decode), implements TTL-cached reverse index for find_session_workspace, and isolates errors per-file. Added 30 tests across 8 test classes to the existing tests/test_data.py file. Validated against 29 real workspaces on disk.

### Phase 2: Generic dispatch refactor + registration [QA] [P:1]

**Goal**: Refactor hardcoded provider dispatch to generic; register Kiro IDE in all provider dicts.

**File scope**: `src/power_atlas/data.py`, `src/power_atlas/launcher.py`, `src/power_atlas/web.py`, `tests/test_data.py`, `tests/test_launcher.py`, `tests/test_web.py`

**Details**:

1. **Uniform adapter signature for tail/prompt** (`data.py`):

```python
# data.py — refactored dispatch (replaces hardcoded if/else at lines 228-237)

def get_session_tail(session_id: str, provider: str = "kiro-cli", cwd: str = "", max_lines: int = 15) -> list[str]:
    """Extract last N assistant messages. Dispatches to provider module."""
    mod = PROVIDERS.get(provider)
    if mod is None:
        return []
    return mod.get_session_tail(session_id, cwd, max_lines)


def get_first_prompt(session_id: str, provider: str = "kiro-cli", cwd: str = "") -> str:
    """Extract first_prompt. Dispatches to provider module."""
    mod = PROVIDERS.get(provider)
    if mod is None:
        return ""
    return mod.get_first_prompt(session_id, cwd)
```

Update `data_kiro.py` signatures to accept (and ignore) `cwd` — **positional order matters**:
```python
# MUST match: (session_id, cwd, max_lines) positionally for generic dispatch
def get_session_tail(session_id: str, cwd: str = "", max_lines: int = 15) -> list[str]:
    # existing implementation unchanged — cwd ignored
    ...

def get_first_prompt(session_id: str, cwd: str = "") -> str:
    # existing implementation unchanged — cwd ignored
    ...
```

Note: `data_claude.py` already conforms to this signature — only `data_kiro.py` needs the `cwd` parameter added as the second positional argument.

2. **`find_session_workspace()` + generic pinned resolution** (`data.py`):

```python
# Add to data_kiro.py, data_claude.py, data_kiro_ide.py
def find_session_workspace(session_id: str) -> str | None:
    """Find which workspace owns this session. Returns cwd or None."""
    ...

# data.py — replace hardcoded branches in warmup_all()
def _find_pinned_session_workspace(session_id: str) -> tuple[str, str] | None:
    """Find (cwd, provider) for a pinned session across all providers."""
    for prov_name, mod in PROVIDERS.items():
        if not mod.is_available():
            continue
        cwd = mod.find_session_workspace(session_id)
        if cwd:
            return (cwd, prov_name)
    return None
```

3. **Provider registry additions** (`data.py:49-53`):

```python
from . import data_kiro, data_claude, data_kiro_ide  # noqa: E402

PROVIDERS: dict[str, object] = {
    "kiro-cli": data_kiro,
    "claude-code": data_claude,
    "kiro-ide": data_kiro_ide,
}
```

4. **Visual identity** (`web.py:19-34`):

```python
PROVIDER_COLORS = {
    "kiro-cli": "#7138cc",
    "claude-code": "#c2590f",
    "kiro-ide": "#8b5cf6",
}
PROVIDER_DISPLAY_NAMES = {
    "kiro-cli": "Kiro CLI",
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
}
PROVIDER_BADGES = {
    "kiro-cli": "K",
    "claude-code": "C",
    "kiro-ide": "I",
}
_PROVIDER_BINARY_DISPLAY = {
    "kiro-cli": "kiro-cli chat",
    "claude-code": "claude",
    "kiro-ide": "kiro",
}
```

5. **Launcher registry** (`launcher.py`):

```python
_PROVIDER_DISPLAY = {
    "kiro-cli": "Kiro CLI",
    "claude-code": "Claude Code",
    "kiro-ide": "Kiro IDE",
}

_PROVIDER_BINARY = {
    "kiro-cli": "kiro-cli",
    "claude-code": "claude",
    "kiro-ide": "kiro",
}

# New: per-provider terminal flag
_PROVIDER_TERMINAL = {
    "kiro-cli": True,
    "claude-code": True,
    "kiro-ide": False,
}
```

6. **Non-terminal launch** (`launcher.py:launch_session`):

```python
def launch_session(...) -> LaunchResult:
    binary = _PROVIDER_BINARY.get(provider, provider)
    display = _PROVIDER_DISPLAY.get(provider, provider)
    use_terminal = _PROVIDER_TERMINAL.get(provider, True)

    if not shutil.which(binary):
        return LaunchResult(False, session_id, cwd,
            error=f"'{binary}' not found on PATH. Install {display} or check your PATH.")

    if not Path(cwd).exists():
        return LaunchResult(False, session_id, cwd, error=f"Folder not found: {cwd}")

    if session_id and not _SESSION_ID_RE.match(session_id):
        return LaunchResult(False, session_id, cwd, error="Invalid session ID format")

    # Build provider-specific args
    cli_args = _build_provider_args(provider, binary, session_id)
    if default_args:
        cli_args += shlex.split(default_args)

    if not use_terminal:
        # GUI provider: launch directly as detached process
        try:
            cmd = cli_args + [cwd]  # kiro <path>
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen(cmd, **kwargs)
            return LaunchResult(True, session_id, cwd)
        except OSError as e:
            return LaunchResult(False, session_id, cwd, error=str(e))

    # Terminal provider: existing flow
    terminal = detect_terminal(terminal_override)
    ...


def _build_provider_args(provider: str, binary: str, session_id: str | None) -> list[str]:
    """Build CLI args per provider."""
    if provider == "claude-code":
        args = [binary]
        if session_id:
            args += ["--resume", session_id]
    elif provider == "kiro-ide":
        args = [binary]
        # No session resume — session_id ignored
    else:
        # Default: kiro-cli pattern
        args = [binary, "chat"]
        if session_id:
            args += ["--resume-id", session_id]
    return args
```

7. **Generic search fallback** (`web.py:search` route): Replace hardcoded kiro-cli scan with iteration over all providers using `find_session_workspace`.

8. **Generic `_render_pinned_sessions` fallback** (`web.py`): Replace hardcoded per-provider scan blocks with the generic `_find_pinned_session_workspace()`.

9. **Empty state message** (`web.py:357`):

```python
empty_msgs = {
    "claude-code": "No Claude Code sessions found — start one with <code>claude</code> to see it here.",
    "kiro-cli": "No Kiro CLI sessions found — start one with <code>kiro-cli</code> to see it here.",
    "kiro-ide": "No Kiro IDE sessions found — open a folder in Kiro IDE and start a conversation to see it here.",
}
```

10. **Resume button differentiation for non-terminal providers** (`session_row.html`):

For providers where resume opens the workspace (not the session), update the button's `title` and `aria-label` attributes:
```html
{% if provider_name == "kiro-ide" %}
<button class="row-btn primary terminal-btn" onclick="resumeSession(this)" aria-label="Open in Kiro IDE" title="Open workspace in Kiro IDE">
{% else %}
<button class="row-btn primary terminal-btn" onclick="resumeSession(this)" aria-label="Resume" title="Resume session">
{% endif %}
```

This ensures users understand the action is "open workspace" not "resume conversation" for GUI providers. The `aria-label` fix addresses accessibility.

11. **`launch_batch()` and `api_launch()`** already pass provider through to `launch_session()` — no changes needed. The `_PROVIDER_TERMINAL` flag is resolved internally.

**Exit criteria**:
- [x] `get_session_tail` / `get_first_prompt` dispatch generically through `PROVIDERS[provider]`
- [x] `data_kiro.py` signature updated to accept `cwd` (ignored)
- [x] `find_session_workspace()` implemented in all three adapters
- [x] `warmup_all()` uses generic `_find_pinned_session_workspace()` — no hardcoded provider branches
- [x] `_render_pinned_sessions()` uses generic resolution — no hardcoded branches
- [x] `search()` route scans all providers for pinned session title matches
- [x] `launch_session()` respects `_PROVIDER_TERMINAL` flag
- [x] Non-terminal launch works for `kiro-ide` (verified manually)
- [x] Existing tests updated for new signatures; no regressions
- [x] Update `README.md` with Kiro IDE provider mention

Implementation (2026-07-06, code: e3091b9)
Refactored hardcoded if/else provider dispatch in `data.py` to a generic `PROVIDERS` dict lookup for `get_session_tail` and `get_first_prompt`, registered `data_kiro_ide` as the third provider. Added `find_session_workspace()` to both `data_kiro.py` and `data_claude.py`, and a generic `_find_pinned_session_workspace()` helper that iterates all providers. Replaced hardcoded per-provider pinned-session scan blocks in `warmup_all()` and `_render_pinned_sessions()` with the generic helper. Updated `data_kiro.py` function signatures to accept an ignored `cwd` parameter for interface uniformity. Added Kiro IDE entries to all visual identity dicts in `web.py` and the kiro-ide empty-state message. In `launcher.py`, added `_PROVIDER_TERMINAL` dict, a `_build_provider_args()` helper, and refactored `launch_session()` to launch non-terminal providers directly via `subprocess.Popen` with `DETACHED_PROCESS`/`start_new_session` flags. Updated search route, README, and tests (264 passed, 1 skipped).

### Phase 3: UI/CSS for 3+ provider gradient + resume button UX [QA]

**Goal**: Extend workspace card gradient border to handle N providers (widen bar for 3+), differentiate resume button for non-terminal providers, verify UI renders correctly.

**File scope**: `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/templates/partials/session_row.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Details**:

Update gradient logic in `workspace_card.html` to compute even stops for N providers:

```html
<span class="provider-gradient" style="background: {% if providers|length == 1 %}{{ providers[0].color }}{% else %}linear-gradient(to bottom, {% for p in providers %}{{ p.color }} {{ (loop.index0 * 100 // providers|length) }}%, {{ p.color }} {{ (loop.index * 100 // providers|length) }}%{% if not loop.last %}, {% endif %}{% endfor %}){% endif %}"></span>
```

**Gradient bar width**: widen from 3px to 4px for 3+ provider cards to ensure each color segment is visible:
```css
.workspace-card.multi-provider .provider-gradient {
    width: 4px;
}
```

This produces:
- 1 provider: solid color, 3px
- 2 providers: `linear-gradient(to bottom, color1 0%, color1 50%, color2 50%, color2 100%)`, 3px
- 3 providers: `linear-gradient(to bottom, color1 0%, color1 33%, color2 33%, color2 66%, color3 66%, color3 100%)`, 4px

**Resume button differentiation**: Update `session_row.html` to show "Open in Kiro IDE" title/aria-label for non-terminal providers instead of "Resume" (per Phase 2 item 10).

**Exit criteria**:
- [x] Workspace cards with 1, 2, and 3 providers render correct gradient
- [x] 3-provider gradient is visually distinguishable (4px width, ~1.3px per color)
- [x] Resume button shows "Open workspace in Kiro IDE" tooltip for kiro-ide sessions
- [x] Resume button shows "Resume session" tooltip for kiro-cli/claude-code sessions
- [x] CSS renders cleanly — no visual glitches on card borders
- [x] Test in `test_web.py` verifies 3-provider card HTML includes all three colors

Implementation (2026-07-06, code: 8f136e4)
Extended workspace_card.html gradient template to compute even-split gradient stops for N providers using Jinja loop math. Added `gradient-3plus` CSS class that widens the bar from 3px to 4px for 3+ providers. Updated session_row.html resume button with provider-aware title and aria-label attributes — "Open workspace in Kiro IDE" for non-terminal providers, "Resume session" for CLI providers. Added 3 tests: gradient rendering, kiro-ide tooltip, terminal-provider tooltip.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Session JSON format changes in future Kiro IDE versions | Sessions stop loading | `is_available()` checks dir exists; `load_sessions()` wraps parsing in try/except per session |
| Large workspace-sessions dir (32+ folders) impacts discovery time | Slow workspace discovery | Same O(n) scan as kiro-cli/claude-code; 32 folders is trivial. Cache with 30s TTL handles it. |
| Base64 decode fails on edge-case paths | Workspace not discovered | try/except per folder; log warning, skip malformed entries |
| Kiro IDE not installed (no data dir) | Provider doesn't appear | `is_available()` returns False; filter hides it; no errors |
| `kiro` binary not on PATH | Launch fails gracefully | `shutil.which` check returns helpful error message |
| Refactored dispatch breaks existing providers | Regression | kiro-cli/claude-code adapters get `cwd` param added (ignored); existing tests verify no regression |

## 7) Verification

- `pytest tests/` — all existing + new tests pass
- Manual: run PowerAtlas, verify Kiro IDE workspaces appear in "All" tab
- Manual: switch to "Kiro IDE" filter tab — only Kiro IDE workspaces visible
- Manual: expand a workspace card with Kiro IDE sessions — sessions listed with violet border
- Manual: hover a session row — tooltip shows first prompt and last reply tail
- Manual: click "New session" button for Kiro IDE — Kiro IDE opens at that workspace
- Manual: confirm existing kiro-cli and claude-code functionality unchanged

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add Kiro IDE to supported providers list, mention discovery location | 2 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

---

## Review Log

### 2026-07-06 — Plan Creation (via /qplan, high effort)

4 personas (Architect, Senior engineer, Reliability engineer, End-user advocate). 11 findings across personas (3 High, 5 Medium, 3 Low after dedup). All High and Medium auto-resolved.

| # | Severity | Finding (one line) | Status (one line) |
|---|---|---|---|
| 1 | High | `data_kiro.py` signature change creates positional arg mismatch — `cwd` passed as `max_lines` | Resolved — explicit positional spec added to Phase 2 |
| 2 | High | Resume button is deceptive for Kiro IDE (implies session continuity) | Resolved — Phase 2/3 adds "Open in Kiro IDE" tooltip + aria-label differentiation |
| 3 | High | No tooltip explaining behavioral difference between IDE resume and CLI resume | Resolved — merged into finding #2's fix |
| 4 | Medium | `find_session_workspace()` is O(N*M) for pinned sessions; no performance spec | Resolved — Phase 1 specifies reverse index built during discover, O(1) per lookup |
| 5 | Medium | Empty `sessions.json` array causes `max()` ValueError | Resolved — Phase 1 details specify explicit empty-array guard |
| 6 | Medium | Pseudocode bodies lack per-session try/except error isolation | Resolved — Phase 1 details now mandate error isolation matching existing adapters |
| 7 | Medium | 3-provider gradient on 3px bar is illegible (1px per color) | Resolved — Phase 3 widens to 4px for 3+ providers |
| 8 | Medium | Empty state message uses ambiguous "Kiro" instead of "Kiro IDE" | Resolved — message updated to "open a folder in Kiro IDE" |
| 9 | Low | `_build_provider_args` still has if/else — "scales to N providers" claim is misleading | Noted — pragmatically correct (3 providers have genuinely different arg patterns); rationale updated |
| 10 | Low | Line references in Current State section are slightly inaccurate | Noted — non-blocking; implementer should use search, not line numbers |
| 11 | Low | Binary name collision risk (`kiro` vs `kiro-cli`) | Noted — empirically confirmed as distinct binaries; no collision |
