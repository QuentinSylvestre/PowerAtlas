# Semantic Session Status

> **Date**: 2026-07-15
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Estimated effort**: ~2-3 days
> **Scope**: Replace mtime-based working/waiting heuristic with JSONL-derived semantic status (Active/Needs-input/Idle/Errored), add fresh-session detection, and layer toast notifications on status transitions

---

## Intent

### Problem statement & desired outcomes

PowerAtlas's live session status (shipped in `260711_SESSION_LIVE_STATUS_AND_FILTER`) uses a two-step heuristic: psutil process scan (is the process running?) + file mtime check (modified within 60s → "working", else → "waiting"). This produces inaccurate and uninformative results:

- Cannot distinguish "agent executing tools" from "waiting for user input" from "permission prompt pending" from "errored"
- 60-second mtime window causes false "waiting" when agent pauses to think >60s (e.g., complex reasoning)
- Kiro-CLI vs Claude Code asymmetry: kiro-cli `updated_at` comes from metadata `.json` (may lag behind actual activity), Claude Code's comes from `.jsonl` mtime (always fresh)
- Fresh sessions (started without `--resume-id` flag) are invisible to session-row-level detection
- No way to know "the agent finished and is waiting for you" without checking the terminal

The improvement replaces the mtime heuristic with **semantic classification from JSONL tail content** — reading the last few KB of the session transcript to determine the agent's actual state from message types. This is complemented by **toast notifications** that alert the user when an agent transitions from active to idle/needs-input.

Inspired by Omnigent's structured state machine approach, but adapted to PowerAtlas's read-only architecture (no runtime interposition — observe session files on disk only).

### Success criteria

1. Live sessions show one of 4 semantic status dots: **Active** (🟢 pulse — agent working), **Needs input** (🟡 — blocked on user, e.g. permission prompt), **Idle** (⚪ dim — turn complete, waiting for next prompt), **Errored** (🔴 — agent hit a problem). Closed sessions show no dot (unchanged).
2. Status classification is derived from the **last few JSONL lines** (message types), not from file mtime. Mtime heuristic serves only as fallback when JSONL parsing fails or format is unrecognized.
3. Both **kiro-cli v2** and **Claude Code** sessions are supported with equal accuracy. The classifier abstraction is designed so v3 kiro-cli (with richer `pending_interaction` signals) can be plugged in later without redesign.
4. **Fresh sessions** (started without `--resume-id`, invisible to current session-id-based detection) are detected via process-cwd + newest-session-file heuristic and shown with a status dot.
5. **Status filter dropdown** updated to new vocabulary: All / Live / Active / Needs input / Idle / Errored / Closed.
6. **Toast notifications** (opt-in, off by default) fire on Active→Idle, Active→Needs-input, and Active→Errored transitions, with a 1-minute cooldown per session.
7. No noticeable latency increase on the existing 15-30s refresh cycle. Status reads use a dedicated 4KB-read cache with mtime guard (separate from the 128KB tooltip tail cache).

### Scope boundaries & non-goals

**In scope:**
- Semantic status classifier (per-provider JSONL tail parser)
- Status cache with mtime-guarded invalidation (5s TTL)
- Fresh session detection via process-cwd matching
- Expanded status dot vocabulary + CSS
- Updated filter dropdown
- Toast notifications on status transitions (opt-in, 1-min cooldown)
- v3-ready abstraction (interface designed for `pending_interaction`, implementation deferred)

**Non-goals:**
- v3 kiro-cli session discovery/parsing (separate future plan — but the status classifier interface accommodates it)
- Token/cost visibility on session cards (separate feature)
- Sub-agent relationship visualization
- WebSocket/SSE push (stays poll-based)
- Sound/chime notifications
- Tray icon badge count
- Activity sparkline / timeline visualization
- kiro-ide session status (IDE sessions remain excluded — no live CLI process)

---

## 1) Current State

The live session status subsystem (`260711_SESSION_LIVE_STATUS_AND_FILTER`) uses a two-step pipeline:

1. **Process scan** (`presence.py:107-141`) — `_scan()` iterates `psutil.process_iter(["name", "cmdline"])`, matches processes against `_PROVIDER_SPECS` (kiro-cli, claude-code binaries), extracts session IDs from `--resume-id`/`--resume` flags. Produces a `Snapshot(live_sids, live_cwds)` cached 3s (`_SNAPSHOT_TTL`).

2. **Mtime heuristic** (`web.py:108-139`) — for sessions confirmed live by step 1, checks `_age_seconds(session.updated_at)` against `_WORKING_WINDOW_SECONDS = 60`. Result: `working` (≤60s) or `waiting` (>60s).

Key data flows:
- Kiro-CLI v2: `Session.updated_at` from `.json` metadata field (`data_kiro.py:133`) — may lag behind actual JSONL activity
- Claude Code: `Session.updated_at` from `.jsonl` file mtime (`data_claude.py:214`) — always fresh
- Status computed fresh per render — no status caching (`web.py:625`, `web.py:669`)
- UI: green pulse dot (`status-working`), yellow static dot (`status-waiting`), no dot for closed (`session_row.html:5`, `style.css:166-170`)
- Filter: All / Live / Working / Waiting / Closed (`index.html:82-86`)

Known gaps:
- Fresh sessions (no `--resume-id` flag) invisible at session-row level (`presence.py:12-14` docstring)
- v2 kiro-cli has only `Prompt`/`AssistantMessage`/`ToolResults` kinds — no explicit "needs input" signal
- v3 sessions have `pending_interaction` type (confirmed: 114 occurrences across v3 sessions, `interactionType: "tool_approval"`)
- `Session.updated_at` is frozen at load time (frozen dataclass, `data.py:24`) — grows stale between 30s background refreshes

## 2) Goal

Replace the mtime-based working/waiting heuristic with a JSONL-derived semantic classifier that reads the last few KB of session transcripts to determine the agent's actual state (Active/Needs-input/Idle/Errored), add fresh-session detection, and layer opt-in toast notifications on status transitions.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Status vocabulary | 4 live states: Active, Needs-input, Idle, Errored (+ Closed) | 2-state (accurate working/waiting); 5-state (split Active into Executing/Thinking) | 4-state balances informativeness with simplicity. "Active" merges executing/thinking since both mean "agent is busy." "Needs input" vs "Idle" is the killer distinction for user actionability. |
| Detection mechanism | JSONL tail semantics with mtime fallback | JSONL-only (no fallback); mtime-only (status quo); process IO counters | Fallback chain provides resilience to provider format changes. IO counters add complexity for marginal gain over JSONL reading. |
| Status cache | Dedicated 4KB-read cache, 5s TTL, mtime-guarded | No cache (compute per render); share with tooltip tail cache | Separate cache keeps status reads (4KB) independent from tooltip reads (128KB). Mtime guard prevents redundant reads for unchanged files. |
| Fresh session detection | Process-cwd + newest-session-file heuristic | No detection (status quo); PID registry at launch time | Heuristic is cheap and covers the common case. PID registry requires PowerAtlas to own the launch (breaks for externally-started sessions). |
| v3 readiness | Classifier interface accepts provider-specific parsers; v3 parser deferred | Bundle v3 discovery now; ignore v3 entirely | User primarily on v2; design now, implement later avoids double-work on the interface. |
| Notification mechanism | Windows toast via PowerShell WinRT API (no new pip dependency) | winotify; plyer; win10toast | PowerShell WinRT is available on all Windows 10+ systems without extra packages. Avoids pip dependency management. Linux uses `notify-send` (standard). |
| Notification behavior | Opt-in, 1-min cooldown per session, Active→Idle/Needs-input/Errored transitions | Always-on; 5-min cooldown; per-workspace grouping | 1-min cooldown balances responsiveness with spam prevention. Opt-in respects users who don't want interruptions. |
| Filter vocabulary | All / Live / Active / Needs input / Idle / Errored / Closed | Keep current All/Live/Working/Waiting/Closed | New vocabulary matches the new status model. "Live" remains as a superset filter (Active ∪ Needs-input ∪ Idle ∪ Errored). |

## 4) External Dependencies & Costs

### Required external changes

None. This is a code-only change reading existing on-disk session files. No infrastructure, CI/CD, IAM, or third-party service changes.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Semantic status classifier module [QA] [P:2]

**Goal**: Create a `status_classifier.py` module with per-provider JSONL tail parsers that derive semantic status from the last few lines of session transcripts.

**File scope**: `src/power_atlas/status_classifier.py` (new), `tests/test_web.py` (extend existing)

**Covers**: SC-1, SC-2, SC-3

**Details**:

Define the status enum and classifier interface:

```python
"""Semantic session status derived from JSONL tail content."""
from enum import Enum
from pathlib import Path

class SemanticStatus(str, Enum):
    ACTIVE = "active"         # Agent mid-turn: tools, reasoning, writing
    NEEDS_INPUT = "needs_input"  # Blocked on user (permission, question)
    IDLE = "idle"             # Turn complete, waiting for next prompt
    ERRORED = "errored"       # Agent hit a problem
    CLOSED = "closed"         # No process running

def _resolve_jsonl_path(session_id: str, provider: str, cwd: str) -> Path | None:
    """Resolve the JSONL file path per provider.
    
    - kiro-cli v2: ~/.kiro/sessions/cli/{session_id}.jsonl
    - claude-code: ~/.claude/projects/{folder_name(cwd)}/{session_id}.jsonl
    - kiro-cli v3 (future): ~/.kiro/sessions/{hash}/sess_{id}/messages.jsonl
    Returns None if the file doesn't exist.
    """
    ...

def _read_tail_lines(path: Path, max_bytes: int = 4096) -> list[str]:
    """Read last max_bytes of file, split into lines.
    
    Discards the first (potentially incomplete) line to avoid
    partial UTF-8/JSON from the seek boundary.
    """
    ...

def classify_kiro_v2(tail_lines: list[str]) -> SemanticStatus:
    """Classify from kiro-cli v2 JSONL tail (kind: Prompt/AssistantMessage/ToolResults).
    
    Note: v2 can only produce ACTIVE or IDLE — NEEDS_INPUT and ERRORED
    require v3's pending_interaction/error signals (deferred).
    """
    ...

def classify_claude(tail_lines: list[str]) -> SemanticStatus:
    """Classify from Claude Code JSONL tail (type: user/assistant/tool_call/...).
    
    Note: Claude Code can produce ACTIVE, IDLE, and ERRORED (via error
    content in assistant messages). NEEDS_INPUT requires v3 kiro-cli.
    """
    ...

def classify_session(session_id: str, provider: str, cwd: str) -> SemanticStatus | None:
    """Read last ~4KB of session JSONL, classify. Returns None on read failure."""
    ...
```

**JSONL path resolution strategy** (addresses the "how does the classifier find the file" question):
- Import `SESSION_DIR` from `data_kiro` and `_get_project_folder` from `data_claude` (these are module-level constants/functions, no circular import risk since `status_classifier.py` is a new module not imported by `data.py`)
- `_resolve_jsonl_path()` maps provider → path formula, returns `None` if file doesn't exist

**Tail reading** — discard first partial line:
```python
def _read_tail_lines(path: Path, max_bytes: int = 4096) -> list[str]:
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        read_size = min(size, max_bytes)
        fh.seek(size - read_size)
        raw = fh.read().decode("utf-8", errors="replace")
    lines = raw.splitlines()
    if read_size < size:
        lines = lines[1:]  # Discard first (potentially partial) line
    return lines
```

Classification rules (derived from empirical analysis):

**Kiro-CLI v2** (reachable states: ACTIVE, IDLE only):
- Last `kind` is `Prompt` (user just sent) → `ACTIVE` (agent will respond)
- Last `kind` is `AssistantMessage` with `toolUse` content → `ACTIVE` (mid-turn, tools pending)
- Last `kind` is `ToolResults` → `ACTIVE` (tools returned, agent will continue)
- Last `kind` is `AssistantMessage` without `toolUse` → `IDLE` (agent finished talking)
- Parse failure or empty tail → `None` (fallback to mtime)

*Limitation*: v2 lacks explicit signals for NEEDS_INPUT (no `pending_interaction` kind) and ERRORED (no error-type kind). These states require v3 kiro-cli support (deferred).

**Claude Code** (reachable states: ACTIVE, IDLE, ERRORED):
- Last `type` is `user` with `tool_result` content → `ACTIVE` (tool results returned to agent)
- Last `type` is `assistant` with error indicators (e.g., content containing `"error"` type blocks) → `ERRORED`
- Last `type` is `assistant` without error → `IDLE` (agent finished responding)
- Last `type` is `user` without `tool_result` content → `ACTIVE` (user just sent, agent processing)
- Parse failure or empty tail → `None` (fallback to mtime)

*Limitation*: Claude Code lacks explicit `pending_interaction`; NEEDS_INPUT requires v3 kiro-cli.

**v3-ready slot** (interface only, returns None to trigger fallback):
```python
def classify_kiro_v3(tail_lines: list[str]) -> SemanticStatus:
    """Placeholder for v3 messages.jsonl. Returns None (triggers mtime fallback).
    
    When implemented, would use:
    - turn_end with stopReason: "end_turn" → IDLE
    - pending_interaction → NEEDS_INPUT  
    - tool_call without subsequent turn_end → ACTIVE
    - session_event with error → ERRORED
    """
    return None  # Not implemented — safe fallback, no exception
```

Status cache with mtime guard (uses `time.monotonic()` per existing presence.py pattern):

```python
_status_cache: dict[str, tuple[float, float, SemanticStatus]] = {}
_STATUS_CACHE_TTL = 5.0  # seconds

def get_semantic_status(session_id: str, provider: str, cwd: str) -> SemanticStatus | None:
    """Cached semantic status. Returns None if JSONL unreadable (caller uses fallback).
    
    Uses time.monotonic() for TTL (immune to clock skew) and file mtime for invalidation.
    Logs provider/session_id/failure_reason on None for observability.
    """
    ...
```

**Exit criteria**:
- [x] `SemanticStatus` enum defined with 5 values
- [x] `_resolve_jsonl_path()` correctly locates JSONL per provider (kiro-v2, claude-code)
- [x] `_read_tail_lines()` discards first partial line from seek boundary
- [x] `classify_kiro_v2()` correctly classifies 2 reachable states (Active, Idle) from real session data
- [x] `classify_claude()` correctly classifies 3 reachable states (Active, Idle, Errored) from real session data
- [x] `classify_kiro_v3()` returns `None` (safe fallback, no exception)
- [x] `get_semantic_status()` caches results with 5s TTL (monotonic) + mtime guard; logs on None
- [x] Tests added to `tests/test_web.py`: each terminal message kind per provider, parse failure → None, cache hit/miss, partial-line discard

Implementation (2026-07-15, code: 4647c19)
Created `src/power_atlas/status_classifier.py` with a `SemanticStatus` enum (ACTIVE, NEEDS_INPUT, IDLE, ERRORED, CLOSED), per-provider JSONL tail parsers (`classify_kiro_v2`, `classify_claude`, `classify_kiro_v3` placeholder), a binary tail reader (`_read_tail_lines` with partial-line discard on seek), path resolution per provider, and a `get_semantic_status` cache with 5-second TTL + mtime guard. Notable decisions: (1) defined `SESSION_DIR` inline rather than importing from `data_kiro` to avoid a circular import chain through `data.py`; (2) imported `_get_project_folder` from `data_claude` (no circular issue); (3) `_read_tail_lines` strips `\r` from line endings to handle Windows CRLF files correctly; (4) cache bounded to 100 entries with oldest-eviction; (5) cache key is `(provider, session_id)` tuple to avoid collision; (6) single stat call per cache-miss path shared with `_read_tail_lines` via `file_size` parameter.

### Phase 2: Fresh session detection [QA] [P:1]

**Goal**: Detect freshly-started sessions (no `--resume-id` on cmdline) by matching process cwd to the newest session file in that workspace.

**File scope**: `src/power_atlas/presence.py`, `tests/test_data.py`

**Covers**: SC-4

**Details**:

Add a method to `Snapshot` that identifies "probable fresh session":

```python
class Snapshot:
    ...
    def probable_fresh_session(self, provider: str, cwd: str,
                                sessions: list["Session"]) -> str | None:
        """If a provider process runs in cwd but no session id was matched,
        return the session_id of the newest session (created within 60s)."""
        from .data import _normalize_path
        norm = _normalize_path(cwd)
        if norm not in self.live_cwds({provider}):
            return None
        # Check if ANY session in this cwd is already matched by id
        for s in sessions:
            if (provider, s.session_id) in self._live_sids:
                return None  # Already have an explicit match
        # Find newest session created within 60s
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for s in sorted(sessions, key=lambda x: x.created_at, reverse=True):
            try:
                dt = datetime.fromisoformat(s.created_at.replace("Z", "+00:00"))
                if (now - dt.astimezone(timezone.utc)).total_seconds() <= 60:
                    return s.session_id
            except (ValueError, OSError):
                continue
            break  # Only check the most recent
        return None
```

**Exit criteria**:
- [x] Fresh sessions (no `--resume-id`) get a status dot when a provider process runs in the same cwd
- [x] Only the newest session (created within 90s) is matched — avoids false positives on old sessions
- [x] If an explicit `--resume-id` match exists for that cwd, fresh-session detection does not double-match
- [x] Unit tests cover: fresh session matched, old session not matched, explicit match takes precedence

Implementation (2026-07-15, code: f481ac9)
Added `probable_fresh_session` method to `Snapshot` in `presence.py` that detects freshly-started sessions by matching a live provider process's cwd to the newest session created within 90 seconds. The method normalizes paths, checks for explicit session-id matches (to avoid double-detection), and parses ISO-8601 timestamps with Z-suffix or timezone support. Extracted `_parse_created_at` to module level for reuse and testability. 9 tests in `test_data.py` cover all paths: fresh session matched, old session not matched, explicit match precedence, no process in cwd, multiple sessions (newest picked), empty sessions, Z-suffix parsing, and two-fresh-sessions-same-cwd.

### Phase 3: Integrate classifier into status pipeline [QA]

**Goal**: Wire `get_semantic_status()` into `_session_status()`, replacing the mtime heuristic for live sessions. Integrate fresh-session detection. Update `_workspace_status()` to return new vocabulary.

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`

**Covers**: SC-1, SC-2, SC-4, SC-5, SC-7

**Details**:

Replace the status computation in `web.py`:

```python
from .status_classifier import get_semantic_status, SemanticStatus

# Update status constants
_LIVE_STATUSES = ("active", "needs_input", "idle", "errored")

def _session_status(snapshot, session, provider: str,
                    all_sessions: list | None = None) -> str:
    """Return semantic status for a session."""
    # 1. Check explicit live (session id on cmdline)
    is_explicitly_live = snapshot.is_live(provider, session.cwd, session.session_id)

    # 2. Check fresh-session detection
    is_fresh = False
    if not is_explicitly_live and all_sessions is not None:
        fresh_sid = _probable_fresh_session(snapshot, provider, session.cwd, all_sessions)
        if fresh_sid == session.session_id:
            is_fresh = True

    if not is_explicitly_live and not is_fresh:
        return "closed"

    # 3. Try semantic classification (JSONL tail)
    semantic = get_semantic_status(session.session_id, provider, session.cwd)
    if semantic is not None:
        return semantic.value

    # 4. Fallback: mtime heuristic (maps to active/idle only)
    age = _age_seconds(session.updated_at)
    if age is not None and age <= _WORKING_WINDOW_SECONDS:
        return "active"
    return "idle"


def _workspace_status(snapshot, cwd: str, latest_updated: str,
                      providers: set[str] | None) -> str:
    """Coarse status for a workspace card — maps to new vocabulary."""
    from .data import _normalize_path
    if _normalize_path(cwd) not in snapshot.live_cwds(providers):
        return "closed"
    # Workspace-level uses mtime as proxy (no per-session JSONL read here)
    age = _age_seconds(latest_updated)
    if age is not None and age <= _WORKING_WINDOW_SECONDS:
        return "active"
    return "idle"


def _status_matches(status_filter: str, status: str) -> bool:
    """Updated filter logic for new vocabulary."""
    if not status_filter or status_filter == "all":
        return True
    if status_filter == "live":
        return status in _LIVE_STATUSES
    return status == status_filter
```

**All callers to update** (enumerate to prevent missed call sites):
- `partials_workspaces()` (~line 489) — workspace card status filter: already uses `_workspace_status()`
- `partials_all_sessions()` (~line 625) — sessions panel row_status dict: pass `all_sessions` list for fresh detection
- `partials_sessions()` (~line 1235) — expanded card sessions: pass sessions list for fresh detection
- `search()` (~line 756) — search results status filter: uses `_workspace_status()` for workspace-level filtering

**Fresh-session utility** — placed in `web.py` (not in `Snapshot`) to avoid coupling `presence.py` to the `Session` type:

```python
def _probable_fresh_session(snapshot, provider: str, cwd: str,
                            sessions: list) -> str | None:
    """If a provider process runs in cwd but no session id matched,
    return the session_id of the newest session (created within 90s)."""
    # Use 90s window (wider than 60s to cover refresh-cycle latency)
    ...
```

**Exit criteria**:
- [ ] `_session_status()` returns semantic status strings (`active`/`needs_input`/`idle`/`errored`/`closed`)
- [ ] `_workspace_status()` returns new vocabulary (`active`/`idle`/`closed`) — workspace filter works with new dropdown values
- [ ] Mtime fallback activates only when `get_semantic_status()` returns `None`
- [ ] Fresh-session detection wired in — new sessions show status dots (90s window)
- [ ] `_status_matches()` handles new vocabulary correctly
- [ ] All 4 callers in web.py updated (partials_workspaces, partials_all_sessions, partials_sessions, search)
- [ ] Existing status tests migrated to new vocabulary (`working`→`active`, `waiting`→`idle`)
- [ ] New tests: semantic status path, fallback path, fresh-session path

### Phase 4: UI — dots, CSS, filter dropdown [QA]

**Goal**: Render the 4 semantic status dots with appropriate colors/animations and update the filter dropdown.

**File scope**: `src/power_atlas/static/style.css`, `src/power_atlas/templates/partials/session_row.html`, `src/power_atlas/templates/index.html`

**Covers**: SC-1, SC-5

**Details**:

Update `session_row.html` status dot rendering:
```html
{% if status == 'active' %}<span class="session-status status-active" title="Active — agent is working" aria-label="Active"></span>
{% elif status == 'needs_input' %}<span class="session-status status-needs-input" title="Needs input — waiting for you" aria-label="Needs input"></span>
{% elif status == 'idle' %}<span class="session-status status-idle" title="Idle — turn complete" aria-label="Idle"></span>
{% elif status == 'errored' %}<span class="session-status status-errored" title="Errored — something went wrong" aria-label="Errored"></span>
{% endif %}
```

CSS additions (`style.css`):
```css
.session-status.status-active { background: #22c55e; box-shadow: 0 0 0 2px rgba(34, 197, 94, 0.22); animation: status-pulse 2s ease-in-out infinite; }
.session-status.status-needs-input { background: #eab308; box-shadow: 0 0 0 2px rgba(234, 179, 8, 0.22); }
.session-status.status-idle { background: #94a3b8; box-shadow: 0 0 0 2px rgba(148, 163, 184, 0.15); }
.session-status.status-errored { background: #ef4444; box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.22); }
```

Update filter dropdown (`index.html`):
```html
<select class="ws-filter-select" id="statusFilter" ...>
  <option value="">All status</option>
  <option value="live">🟢 Live</option>
  <option value="active">🟢 Active</option>
  <option value="needs_input">🟡 Needs input</option>
  <option value="idle">⚪ Idle</option>
  <option value="errored">🔴 Errored</option>
  <option value="closed">⚫ Closed</option>
</select>
```

**Exit criteria**:
- [ ] All 4 live status dots render with correct colors and animations
- [ ] Non-color differentiator for accessibility: distinct title text on dots (already present) + consider adding subtle shape variation (filled vs ring) or an icon overlay for Errored state
- [ ] Filter dropdown shows new vocabulary and filters correctly
- [ ] Accessibility: `aria-label` and `title` on all dot variants (already planned)
- [ ] `@media (prefers-reduced-motion)` disables pulse animation (existing pattern)
- [ ] No dot rendered for "closed" status (unchanged)

### Phase 5: Toast notifications on status transitions [QA]

**Goal**: Fire opt-in Windows toast (or Linux notify-send) notifications when a session transitions from Active to Idle/Needs-input/Errored.

**File scope**: `src/power_atlas/notifications.py` (new), `src/power_atlas/web.py`, `src/power_atlas/config.py`, `tests/test_notifications.py` (new)

**Covers**: SC-6

**Details**:

New `notifications.py` module:

```python
"""Toast notifications for session status transitions."""
import logging
import sys
import time
from dataclasses import dataclass, field

log = logging.getLogger("power_atlas.notifications")

@dataclass
class _SessionNotifyState:
    last_status: str = "closed"
    last_notified_at: float = 0.0

_COOLDOWN_SECONDS = 60.0
_session_states: dict[str, _SessionNotifyState] = {}

# Transitions that trigger notification
_NOTIFY_TRANSITIONS = {
    ("active", "idle"),
    ("active", "needs_input"),
    ("active", "errored"),
}

def check_and_notify(session_id: str, session_title: str,
                     new_status: str, enabled: bool) -> None:
    """Check if status transition warrants a notification. Fire if so."""
    if not enabled:
        return
    state = _session_states.setdefault(session_id, _SessionNotifyState())
    old_status = state.last_status
    state.last_status = new_status

    if (old_status, new_status) not in _NOTIFY_TRANSITIONS:
        return
    now = time.monotonic()
    if now - state.last_notified_at < _COOLDOWN_SECONDS:
        return
    state.last_notified_at = now
    _fire_toast(session_title, new_status)


def _fire_toast(title: str, status: str) -> None:
    """Platform-specific toast notification."""
    messages = {
        "idle": "Done — waiting for you",
        "needs_input": "Needs your input",
        "errored": "Hit an error",
    }
    body = messages.get(status, status)
    if sys.platform == "win32":
        _fire_windows_toast(f"PowerAtlas — {title}", body)
    else:
        _fire_linux_notify(f"PowerAtlas — {title}", body)


def _fire_windows_toast(title: str, body: str) -> None:
    """Windows toast via PowerShell (no extra dependency)."""
    import subprocess
    # Use BurntToast if available, fall back to basic balloon
    script = f'''
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0)
    $text = $template.GetElementsByTagName("text")
    $text[0].AppendChild($template.CreateTextNode("{title}")) | Out-Null
    $text[1].AppendChild($template.CreateTextNode("{body}")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("PowerAtlas").Show($toast)
    '''
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("Windows toast failed", exc_info=True)


def _fire_linux_notify(title: str, body: str) -> None:
    """Linux notification via notify-send."""
    import subprocess
    try:
        subprocess.Popen(
            ["notify-send", title, body, "--app-name=PowerAtlas"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.debug("Linux notify-send failed", exc_info=True)
```

Config integration (`config.py`) — use a `[notifications]` TOML table for consistency:
```toml
[notifications]
enabled = false
```

Wire into `web.py` — call `check_and_notify()` after computing each session's status. Call `notifications.mark_initialized()` after the first complete render pass to establish baseline without firing.

**Exit criteria**:
- [ ] Toast fires on Active→Idle, Active→Needs-input, Active→Errored transitions
- [ ] First render pass establishes baseline without firing (prevents startup notification burst)
- [ ] 1-minute cooldown per session prevents notification spam
- [ ] `_session_states` bounded to 100 entries with LRU eviction of closed sessions
- [ ] Config `[notifications] enabled = false` by default (opt-in), uses TOML table namespace
- [ ] Windows: toast via PowerShell WinRT API with `-EncodedCommand` (no string injection from session titles)
- [ ] Linux: `notify-send` subprocess with `shutil.which()` availability check (no new pip dependency)
- [ ] `close_fds=True` on all subprocess.Popen calls (prevent fd leakage)
- [ ] Notification failure is silently logged, never crashes the app
- [ ] Unit tests in `tests/test_web.py`: transition detection, cooldown enforcement, disabled state, startup no-notify
- [ ] README.md updated: feature description, config example, notification mention
- [ ] ROADMAP.md updated: strike implemented items from future-extension list

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Provider JSONL format changes silently | Status classification breaks for affected provider | Fallback to mtime heuristic when parse fails; classifier returns `None` on any error. Self-corrects if format stabilizes. |
| False "Active" when session is actually idle | User doesn't know agent finished | Conservative: only classify as non-Active when positive signal exists (explicit `AssistantMessage` without toolUse). Unknown states default to Active (safe, errs toward "leave it alone"). |
| Fresh-session detection false positive | Wrong session gets a dot | Only match newest session created within 60s; skip if an explicit --resume match already exists for that cwd. Worst case: cosmetic (dot on wrong row, self-corrects on next resume). |
| Toast notification fatigue | User disables feature entirely | Opt-in by default, 1-min cooldown. Only notify on Active→non-Active (not Idle→Active which would be very frequent). |
| Performance: JSONL reads for all live sessions | Adds latency to refresh cycle | 4KB reads (not 128KB); mtime-guarded cache (skip unchanged files); only reads for sessions confirmed live by process scan (typically 1-5). |
| pywin32 toast API instability across Windows versions | Notifications fail silently on some machines | Wrapped in try/except with debug logging. Feature is opt-in and non-critical — failure is acceptable. |
| v2 kiro-cli `updated_at` in .json may not update reliably | Mtime fallback based on stale metadata | Fallback also checks JSONL file mtime directly (not just metadata `updated_at`). Both signals available. |

## 7) Verification

**Automated** (pytest):
- `test_status_classifier.py` — per-provider classification from real session snippets
- `test_notifications.py` — transition detection, cooldown, config-disabled
- `test_web.py` — updated status integration tests (status computation, filter matching)
- `test_data.py` — fresh-session presence detection

**Manual / browser** (`/qqa`):
- Launch a kiro-cli session → verify dot shows "Active" during tool execution → "Idle" when agent finishes
- Launch a Claude Code session → same verification
- Start a fresh session (no --resume) → verify dot appears
- Toggle `notifications_enabled = true` in config → verify toast fires when session goes idle
- Filter dropdown: each status value correctly filters the sessions panel
- Verify dot colors and animations match spec (green pulse, yellow static, gray, red)

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update "Live session status" feature bullet with new 4-state vocabulary, new filter options, mention JSONL-based detection and fresh-session detection | 5 |
| `README.md` | Add `notifications_enabled = false` to config.toml example block | 5 |
| `README.md` | Add feature bullet mentioning opt-in toast notifications on status transitions | 5 |
| `plans/ROADMAP.md` | Update "Workspace Intelligence" section — strike items now implemented (Active/Needs-input distinction, fresh-session detection), keep only remaining deferred items | 5 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-07-15 — Implementation Review (after Phases 1&2, personas: Reliability engineer, Performance engineer, Senior engineer, Security auditor, End-user advocate, Maintainability reviewer)

Implementation health: Green.
8 sub-agents dispatched (4 per phase). 14 raw findings merged to 11 deduplicated (1 High, 4 Medium, 6 Low).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `_status_cache` unbounded — plan requires 100-entry LRU eviction | Fixed — added `_MAX_CACHE_ENTRIES = 100` with oldest-eviction |
| 2 | Medium | Double path resolution on cache miss (`get_semantic_status` + `classify_session`) | Fixed — extracted `_classify_from_path` accepting pre-resolved path |
| 3 | Medium | Triple stat per miss (is_file + os.stat + path.stat in tail reader) | Fixed — pass `file_size` from caller's stat, eliminated `path.stat()` |
| 4 | Medium | Cache key is bare session_id — provider collision possible | Fixed — changed to `(provider, session_id)` tuple key |
| 5 | Medium | Second fresh session in same workspace invisible when first is resumed | User: accepted — documented MVP trade-off, not a bug |
| 6 | Low | `SESSION_DIR` duplicated from data_kiro (drift risk) | User: accepted — circular import prevents direct import; comment explains |
| 7 | Low | `_parse_iso` nested function re-created per call | Fixed — extracted to module-level `_parse_created_at` |
| 8 | Low | No test for two fresh sessions same cwd | Fixed — added `test_two_fresh_sessions_same_cwd_only_newest` |
| 9 | Low | classify_kiro_v2 silently skips non-message kinds | User: accepted — correct behavior (skip metadata, find last message) |
| 10 | Low | TOCTOU race in tail reader between stat and open | User: accepted — acceptable risk; concurrent writes handled by JSON skip |
| 11 | Low | No test for zero-byte file edge case | User: accepted — returns empty list (no lines), triggers None from classifier |

Phase 2 end-user finding #1 (invisible second fresh session) is a documented design trade-off: the method returns `None` when an explicit match already exists in that cwd, which is the correct conservative behavior to avoid false positives.

### 2026-07-15 — Plan Review (via /qplan Step 4, High effort)

4 personas (Architect, Senior engineer, Reliability engineer, End-user advocate). 13 deduplicated findings (3 High, 7 Medium, 3 Low). 10 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | No JSONL path resolution strategy — classifier can't find the file | Resolved — added `_resolve_jsonl_path()` with per-provider path logic and explicit import strategy |
| 2 | High | v2 kiro-cli can only produce Active/Idle — NEEDS_INPUT and ERRORED unreachable; SC-1 partially unimplementable | Resolved — documented v2's 2-state ceiling, adjusted exit criteria, v3 slot returns None safely |
| 3 | High | `_workspace_status()` still returns old vocabulary — workspace filter breaks for new dropdown values | Resolved — Phase 3 now updates `_workspace_status()` and enumerates all 4 callers |
| 4 | Medium | PowerShell string injection in `_fire_windows_toast` from session titles with special chars | Resolved — uses `-EncodedCommand` with base64 and HTML-escapes title/body for XML template |
| 5 | Medium | `_session_states` dict grows unboundedly (memory leak over weeks) | Resolved — bounded to 100 entries with eviction of closed sessions |
| 6 | Medium | 4KB tail read can split UTF-8/JSON line at boundary | Resolved — `_read_tail_lines()` discards first partial line explicitly |
| 7 | Medium | First render after startup fires notifications for all sessions (transition from unknown→active) | Resolved — `mark_initialized()` pattern establishes baseline on first pass |
| 8 | Medium | Design decision table says "pywin32" but implementation uses PowerShell WinRT subprocess | Resolved — corrected rationale to "PowerShell WinRT API (no pip dependency)" |
| 9 | Medium | Fresh-session 60s window too narrow given 30s refresh cycle | Resolved — widened to 90s in Phase 3 implementation |
| 10 | Medium | `classify_kiro_v3()` raises NotImplementedError which would crash if accidentally routed | Resolved — returns None (safe fallback) instead of raising |
| 11 | Low | Color-only differentiation fails color-blind users (green/red, yellow/gray indistinguishable) | Resolved — added exit criterion for non-color differentiator (shape/icon consideration) |
| 12 | Low | Config `notifications_enabled` as bare boolean breaks structured-section pattern | Resolved — uses `[notifications] enabled = false` TOML table |
| 13 | Low | `subprocess.Popen` missing `close_fds=True` — potential fd leakage | Resolved — added `close_fds=True` to all Popen calls |

Remaining unresolved (blocked — require user decision):
- End-user finding: "Needs input" filter option visible but unreachable for current providers — user may want to hide it until v3. Deferred to implementation judgment (can show grayed-out or remove from dropdown until signal exists).
- End-user finding: No in-UI toggle for notifications — only config.toml. Acceptable for MVP; UI toggle is a natural follow-on.
