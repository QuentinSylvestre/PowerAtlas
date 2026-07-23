# Background Refresh and Accelerated Polling

> **Date**: 2026-07-23
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Estimated effort**: ~1-2 days
> **Scope**: Eliminate "Loading..." flicker on expanded workspace cards during refresh; accelerate session/status updates for active workspaces

---

## Intent

### Problem statement & desired outcomes

Expanded workspace cards flash "Loading..." text for 5-10 seconds on every periodic refresh (15s burst / 30s steady). This happens because `refreshCards()` replaces the entire `#workspace-cards` container via `innerHTML`, and the server always renders cards with `sessions=[]` (sessions are lazy-loaded separately). The user sees populated sessions vanish, "Loading..." appear, then sessions repopulate — a jarring experience for cards they're actively watching.

Additionally, the uniform 30s polling rate means status changes (agent finished, error occurred) take up to 30s to reflect in the UI, regardless of whether the workspace has an actively-running process. Key workspaces with running agents should feel snappier.

**Desired outcomes:**
- Expanded workspace cards never show "Loading..." during periodic background refresh — existing session content remains visible while fresh data loads behind the scenes
- Status dots (working/waiting/errored) update within 5 seconds for workspaces with active processes
- Session content for active workspaces refreshes every 10s (vs 30s today)
- Workspace panel ordering remains correct even for expanded cards (DOM relocation when position changes)

### Success criteria

- SC-1: An expanded workspace card with visible session rows never flashes "Loading..." during a periodic refresh cycle — content stays visible while the background fetch completes, then updates in-place.
- SC-2: Status dots on workspace cards and session rows for active workspaces (with a running kiro-cli/claude process) update within 5 seconds of a state transition (working→waiting, working→errored).
- SC-3: Session rows within expanded cards of active workspaces refresh content (new sessions, title changes) every 10 seconds without destroying existing DOM content.
- SC-4: When an expanded card's time-group position changes (e.g., "Yesterday" → "Today" due to new activity), the card is relocated to the correct position in the workspace panel without collapsing or losing session content.
- SC-5: The right-panel (all-sessions) status dots update at the same 5s cadence for sessions in active workspaces.
- SC-6: Scroll position and selection state in both panels are preserved during the fast status poll and the 10s session content refresh.
- SC-7: The 30s full workspace discovery cycle continues to handle structural changes (new workspaces appearing, workspaces being removed, ordering of collapsed cards).

### Scope boundaries & non-goals

**In scope:**
- New lightweight `/api/session-status` endpoint returning per-session and per-workspace status as JSON
- Client-side 5s status-only poll that updates dots in-place (CSS class changes, no HTML re-render)
- Client-side 10s session content refresh for expanded cards in active workspaces (cache-aware, no `fresh=1`)
- Refactored `refreshCards()` to preserve expanded card DOM nodes during full workspace panel refresh
- DOM relocation logic to move expanded cards to correct time-group position when ordering changes
- Maintaining `htmx.process()` contract for any new DOM insertions

**Non-goals:**
- SSE/WebSocket infrastructure (staying with polling)
- Changing the 30s workspace discovery cadence
- Changing the server-side `refresh_stale_entries()` 30s background task
- Accelerating refresh for workspaces without active processes
- Preserving session-level selections across the 30s full refresh (existing limitation, orthogonal)

---

## Discovery

### Existing patterns & constraints

- `refreshCards()` (index.html:169-222) does full innerHTML replacement of `#workspace-cards` and `#all-sessions`
- `/partials/workspaces` (web.py:506-660) always renders with `sessions=[]` — sessions are lazy-loaded via `/partials/sessions`
- `workspace_card.html:34-38` shows "Loading..." when sessions is empty (always the case from workspace endpoint)
- Pin/unpin uses optimistic DOM manipulation without full refresh (precedent for in-place updates)
- `loadMoreSessions` uses `insertAdjacentHTML` (precedent for non-destructive DOM updates)
- Custom htmx-mini requires `htmx.process(el)` after every innerHTML swap (project memory)
- `SessionCache.get()` returns copies, not references (data.py:82) — safe for concurrent access
- `presence.get_snapshot()` has 3s TTL; `status_classifier` has 5s TTL — floor for meaningful status updates
- `_background_refresh()` runs `refresh_stale_entries()` every 30s, updating SessionCache when file mtime changes
- Currently `refreshCards(false)` still sends `fresh=1` to `/partials/sessions` for expanded cards (index.html:213), bypassing cache and hitting disk

### Risks & mitigations

1. **DOM relocation handler preservation** — Moving a DOM subtree via `removeChild`/`insertBefore` preserves event listeners per spec. Low risk but should be verified with Playwright.
2. **Race between 5s status poll and 30s full refresh** — If both fire simultaneously, full refresh may overwrite dots. Mitigation: next 5s poll self-corrects within 5s. Acceptable.
3. **Expanded card identity matching** — Cards identified by `CSS.escape(cwd)` selector. Windows paths with backslashes require CSS escaping. Existing pattern works (already in use).
4. **30s latency for new session discovery in 10s content poll** — Cache-aware fetch won't see new sessions until `refresh_stale_entries()` updates the cache. Mitigated by 5s status dots providing immediate visual activity feedback.

### Resolved decisions

- Q1: Approach for preventing flicker — A: Option B (in-place DOM updates for expanded cards, skip innerHTML destruction) — Decision: Expanded cards are excluded from innerHTML replacement; their content is fetched in background and swapped only when response arrives
- Q2: Fast-poll interval for status — A: Separate lightweight status poll at 5s — Decision: New `/api/session-status` endpoint polled at 5s, updates dots in-place via CSS class changes
- Q3: Scope of accelerated session refresh — A: Workspaces with active processes (presence-based), not UI-state-based — Decision: `live_cwds()` from presence snapshot determines which workspaces get fast polling
- Q4: Right panel update strategy — A: Hybrid (C) — lightweight status update at 5s + full innerHTML at 30s — Decision: Status dots update in-place at 5s; structural changes (new sessions, ordering) on 30s cycle
- Q5: Status poll interval — A: 5s (aligned with classifier TTL) — Decision: 5s status poll
- Q6: Expanded card ordering — A: DOM relocation (B) — Decision: After innerHTML for collapsed cards, expanded card DOM nodes are relocated to correct position in the time-group ordering
- Q7: Status endpoint contract — A: Server returns both per-session and per-workspace aggregated status — Decision: Endpoint returns `{sessions: {id: status}, workspaces: {cwd: status}}`
- Q8: Accelerated session content interval — A: 10s for active workspaces — Decision: 10s cache-aware session content refresh for expanded cards in active workspaces
- Q9: Cache strategy for 10s session refresh — A: Cache-aware (no fresh=1) — Decision: Reads from SessionCache updated by background `refresh_stale_entries()`; avoids disk IO on fast path

### Open items

None — all design decisions resolved.

### Recommended approach

Three-tier polling architecture:

1. **5s lightweight status poll** — New `/api/session-status` endpoint. Client sends list of visible session IDs (or active workspace cwds). Server returns JSON with per-session status + per-workspace aggregated status. Client updates status dot CSS classes in-place on existing DOM elements (both panels). No HTML rendering server-side.

2. **10s session content refresh** (active workspaces only) — For expanded cards whose workspace has a running process, fetch `/partials/sessions?cwd=...` (cache-aware, no `fresh=1`). Replace card-body innerHTML only when response arrives (never destroy existing content before new content is ready). Also update matching rows in right panel if visible.

3. **30s full workspace refresh** (existing, refined) — `refreshCards()` refactored: before innerHTML replacement of `#workspace-cards`, detach expanded card DOM nodes. After innerHTML, relocate preserved nodes to their correct position in the new DOM structure (matching time-group). Collapsed cards get full re-render as today. Right panel gets full innerHTML replacement.

The `/api/session-status` endpoint leverages existing `presence.get_snapshot()` + `_session_status()` + `_workspace_status()` logic — no new backend computation, just a JSON serialization of what's already computed for HTML rendering.

### QA environment

- PowerAtlas startable locally: `python -m power_atlas` from the project venv
- Web UI accessible at `http://127.0.0.1:<port>` (port shown at startup or in config)
- Playwright MCP available for browser verification
- Active sessions can be simulated by running `kiro-cli chat -a` in a workspace
- Status transitions observable by starting/stopping kiro-cli processes


---

## 1) Current State

The refresh pipeline has a full-destruction cycle that causes visible flicker:

1. `refreshCards()` (index.html:169-222) captures expanded/selected card cwds, then does `el.innerHTML = html` on `#workspace-cards`
2. `/partials/workspaces` (web.py:506-660) renders ALL workspace cards with `sessions=[]` — every card-body contains `<div class="loading-sessions">Loading...</div>` (workspace_card.html:37)
3. After innerHTML, JS re-expands previously-expanded cards and fires `/partials/sessions?...&fresh=1` for each (index.html:196-215)
4. Sessions repopulate 5-10s later when the fetch completes
5. Result: visible "Loading..." flash on every 15s/30s poll cycle for any expanded card

Polling runs at two fixed cadences (index.html:261):
- Burst: 8 × 15s (first 2 minutes)
- Steady: 30s indefinitely

No differential updates exist — status changes, new sessions, and ordering all rely on the same destructive full-panel replacement.

Presence detection (`presence.py:222-229`) already identifies active workspaces via `get_snapshot().live_cwds()` with a 3s TTL. Status classification (`status_classifier.py`) has a 5s TTL per session. These caches are the floor for meaningful accelerated polling.

## 2) Goal

Implement a three-tier polling architecture that eliminates flicker on expanded cards and delivers near-real-time status updates for active workspaces:
- **5s**: lightweight status-only JSON poll → in-place CSS dot updates
- **10s**: session content refresh for active workspaces → background HTML swap (never destroy before replace)
- **30s**: full workspace discovery → DOM relocation for expanded cards, innerHTML for collapsed cards

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Flicker prevention strategy | Preserve expanded card DOM nodes; skip innerHTML destruction for them | Pre-include sessions in workspace response; full-panel virtual DOM diff | Avoids coupling session loading to workspace endpoint; simpler than virtual DOM |
| Status update mechanism | New `/api/session-status` JSON endpoint at 5s | Faster full refresh; SSE push | Lightweight (no HTML render), no infrastructure change, aligned with classifier TTL |
| Active workspace detection | `presence.get_snapshot().live_cwds()` (process in workspace) | Expanded-card state; manual user marking | Presence is the objective signal; UI state is transient |
| Expanded card ordering | DOM relocation via `removeChild`/`insertBefore` | Let ordering drift; full re-render | Correct ordering without content destruction; standard DOM API preserves handlers |
| Session content fast-poll cache strategy | Cache-aware (no `fresh=1`); reads from SessionCache updated by 30s background task | Always fresh (disk IO); event-driven invalidation | Avoids 10s disk reads; 5s status dots give immediate feedback; structural latency ≤30s acceptable |
| Right panel fast updates | Status dots updated in-place at 5s; full innerHTML stays at 30s | Accelerated full innerHTML; per-row targeted updates | Cheap, preserves scroll/selection during fast cycle |

## 4) External Dependencies & Costs

### Required external changes

None — this is a code-only UI/polling change with no infrastructure, CI/CD, IAM, or third-party service dependencies.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Lightweight status endpoint [QA] [P:2]

**Goal**: Create `/api/session-status` — a JSON endpoint that returns per-session and per-workspace aggregated status for a given set of active workspaces. This is the backbone of the 5s fast poll. Must be side-effect-free (no notifications) and avoid disk IO on the hot path.

**File scope**: `src/power_atlas/web.py`

**Covers**: SC-2, SC-5

**Changes**:

```python
@app.post("/api/session-status")
async def api_session_status(request: Request):
    """Return live status for requested workspaces. POST to avoid URL length limits.
    
    Body JSON: {"cwds": ["C:\\path1", "C:\\path2"]}
    Only send cwds of workspaces with known active processes (client-side filtering).
    
    Returns JSON:
        {
            "sessions": {"<session_id>": "<status>", ...},
            "workspaces": {"<cwd>": "<status>", ...},
            "active_cwds": ["<cwd>", ...]  // cwds with live processes (for client-side state)
        }
    """
    import asyncio
    body = await request.json()
    cwd_list = body.get("cwds", [])
    
    snap = await asyncio.to_thread(presence.get_snapshot)
    config = load_config()
    
    session_statuses: dict[str, str] = {}
    workspace_statuses: dict[str, str] = {}
    
    # Only process providers with presence detection (exclude kiro-ide)
    live_providers = {"kiro-cli", "claude-code"}
    prov_names = None  # for _workspace_status
    
    for cwd in cwd_list:
        # Workspace-level status (uses existing optimized aggregation)
        ws_status = _workspace_status(snap, cwd, prov_names)
        workspace_statuses[cwd] = ws_status or ""
        
        # Per-session: only for cwds with a live process (short-circuit)
        from .data import _normalize_path
        norm_cwd = _normalize_path(cwd)
        if norm_cwd not in snap.live_cwds(live_providers):
            continue  # No running process — all sessions are "closed", skip iteration
        
        # Only iterate providers with presence detection
        for prov_name in live_providers:
            sessions = data.session_cache.get(cwd, prov_name)
            if not sessions:
                continue
            for session in sessions:
                # Use _session_status WITHOUT notification side-effect
                sess_status = _session_status(snap, session, prov_name, False)
                if sess_status and sess_status != "closed":
                    session_statuses[session.session_id] = sess_status
    
    # Return active cwds so client can maintain its own state
    active_cwds = list(snap.live_cwds(live_providers))
    
    return {"sessions": session_statuses, "workspaces": workspace_statuses,
            "active_cwds": active_cwds}
```

Additionally, refactor `_session_status()` to accept a `notify` parameter (default `True` for backward compat):

```python
def _session_status(snap, session, provider, notify=True):
    # ... existing logic ...
    # Only call notifications.check_and_notify() when notify=True
    if notify:
        notifications.check_and_notify(session.session_id, status)
    return status
```

**Exit criteria**:
- [x] `POST /api/session-status` with JSON body `{"cwds": [...]}` returns correct status map
- [x] Endpoint does NOT trigger notification side-effects (no toasts from status poll)
- [x] Endpoint skips kiro-ide provider entirely
- [x] Endpoint short-circuits for cwds without a live process (no per-session iteration)
- [x] Response includes `active_cwds` list for client-side state tracking
- [x] Endpoint responds in <100ms for typical workloads (1-3 active workspaces)
- [x] Tests added for the endpoint

Implementation (2026-07-23, code: 0885428)
Added POST /api/session-status endpoint to web.py that accepts {"cwds": [...]} and returns per-session and per-workspace status maps plus an active_cwds list. The endpoint uses the cached presence snapshot (3s TTL) to short-circuit inactive cwds immediately, processes only kiro-cli and claude-code providers, and calls _session_status() with notify=False to suppress notification side-effects. Refactored _session_status() to accept a keyword-only notify parameter (default True for backward compat). Added try-except per cwd for fault isolation. Added 6 tests covering empty input, inactive cwd short-circuit, active status with no-notification verification, kiro-ide exclusion, mixed active/inactive cwds, and missing key handling.

### Phase 2: Refactor `refreshCards()` to preserve expanded cards [QA] [P:1]

**Goal**: Modify `refreshCards()` so expanded workspace cards are never destroyed during the 30s full refresh. Their card-body content is preserved while card-header metadata is refreshed from the server response. DOM relocation maintains correct ordering.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/web.py` (pass `time_group` to template)

**Covers**: SC-1, SC-4, SC-6, SC-7

**Changes to `refreshCards()` in index.html**:

```javascript
// Utility: build a card selector from cwd (DRY helper — addresses finding #15)
function _cardSelector(cwd) {
  return '.workspace-card[data-cwd="' + CSS.escape(cwd) + '"]';
}

function refreshCards(fresh) {
  var qs = fresh ? '&fresh=1' : '';
  var expandedCwds = [];
  var selectedCwds = [];
  var expandedNodes = {};  // cwd -> {node, scrollTop}
  
  // Capture state
  document.querySelectorAll('.workspace-card:not(.collapsed)').forEach(function(c) {
    var cwd = c.dataset.cwd;
    expandedCwds.push(cwd);
    var body = c.querySelector('.card-body');
    expandedNodes[cwd] = {node: c, scrollTop: body ? body.scrollTop : 0};
  });
  document.querySelectorAll('.workspace-card.selected').forEach(function(c) {
    selectedCwds.push(c.dataset.cwd);
  });
  
  var provQs = _buildWorkspaceQs();
  var wp = document.getElementById('workspace-cards');
  var sp = document.getElementById('all-sessions');
  if(wp) wp.setAttribute('aria-busy', 'true');
  if(sp) sp.setAttribute('aria-busy', 'true');
  
  // NOTE: Do NOT detach expanded nodes before fetch — keep DOM intact to avoid
  // event/interaction loss during the network round-trip (addresses finding #3 from perf review).
  // The swap happens atomically after response arrives.
  
  fetch('/partials/workspaces?' + provQs + qs).then(function(r) {
    return r.text();
  }).then(function(html) {
    var el = document.getElementById('workspace-cards');
    
    // Parse the response into a temporary container (not live DOM)
    var temp = document.createElement('div');
    temp.innerHTML = html;
    
    // For each expanded card: find its placeholder in the new HTML,
    // update header metadata from placeholder, then replace placeholder with preserved node
    expandedCwds.forEach(function(cwd) {
      var preserved = expandedNodes[cwd];
      if(!preserved) return;
      var placeholder = temp.querySelector(_cardSelector(cwd));
      if(placeholder) {
        // Update card-header metadata from server's fresh render (session count, last-active, status dot, etc.)
        var freshHeader = placeholder.querySelector('.card-header');
        var oldHeader = preserved.node.querySelector('.card-header');
        if(freshHeader && oldHeader) {
          // Update count
          var freshCount = freshHeader.querySelector('.card-count');
          var oldCount = oldHeader.querySelector('.card-count');
          if(freshCount && oldCount) oldCount.textContent = freshCount.textContent;
          // Update last-active
          var freshActive = freshHeader.querySelector('.card-last-active');
          var oldActive = oldHeader.querySelector('.card-last-active');
          if(freshActive && oldActive) oldActive.textContent = freshActive.textContent;
          // Update workspace status dot
          var freshDot = freshHeader.querySelector('.ws-status');
          var oldDot = oldHeader.querySelector('.ws-status');
          if(oldDot) oldDot.remove();
          if(freshDot) {
            var lastActive = oldHeader.querySelector('.card-last-active');
            if(lastActive) oldHeader.insertBefore(freshDot.cloneNode(true), lastActive);
            else oldHeader.appendChild(freshDot.cloneNode(true));
          }
        }
        // Update data attributes for positioning
        preserved.node.dataset.timeGroup = placeholder.dataset.timeGroup || '';
        preserved.node.dataset.sortKey = placeholder.dataset.sortKey || '';
        // Replace placeholder with preserved node in the temp container
        placeholder.parentNode.replaceChild(preserved.node, placeholder);
      }
      // If placeholder not found: card filtered out — don't re-insert
    });
    
    // Now swap the entire prepared DOM into the live container
    el.innerHTML = '';
    while(temp.firstChild) el.appendChild(temp.firstChild);
    if(window.htmx) htmx.process(el);
    
    el.removeAttribute('aria-busy');
    
    // Restore scroll positions for expanded card-bodies
    expandedCwds.forEach(function(cwd) {
      var info = expandedNodes[cwd];
      if(info && info.scrollTop) {
        var card = el.querySelector(_cardSelector(cwd));
        if(card) {
          var body = card.querySelector('.card-body');
          if(body) body.scrollTop = info.scrollTop;
        }
      }
    });
    
    // Re-apply selection state
    selectedCwds.forEach(function(cwd) {
      var card = el.querySelector(_cardSelector(cwd));
      if(card) card.classList.add('selected');
    });
    
    // loadExpandedCards() for any NEW cards that are expanded (htmx trigger, etc.)
    // but NOT for preserved cards (which already have content)
    loadExpandedCards();
    updateActionBar();
    
    // If this was a visibility-change refresh (fresh=true), also re-fetch session
    // content for expanded cards to ensure freshness after tab return
    if(fresh) {
      var provider = window._activeProvider || 'all';
      expandedCwds.forEach(function(cwd) {
        var card = el.querySelector(_cardSelector(cwd));
        if(!card || card.classList.contains('collapsed')) return;
        var body = card.querySelector('.card-body');
        if(body) {
          fetch('/partials/sessions?cwd=' + encodeURIComponent(cwd) +
                '&provider=' + encodeURIComponent(provider) + _statusQs() + '&fresh=1').then(function(r) {
            return r.text();
          }).then(function(h) {
            body.innerHTML = h;
            if(window.htmx) htmx.process(body);
          });
        }
      });
    }
  }).catch(function() {
    if(wp) wp.removeAttribute('aria-busy');
  });
  
  // Sessions panel: full innerHTML (unchanged for 30s cycle)
  var sessQs = _buildWorkspaceQs();
  fetch('/partials/all-sessions?page=1&' + sessQs + qs).then(function(r) {
    return r.text();
  }).then(function(html) {
    var el = document.getElementById('all-sessions');
    el.innerHTML = html;
    if(window.htmx) htmx.process(el);
    el.removeAttribute('aria-busy');
    updateActionBar();
  }).catch(function() {
    if(sp) sp.removeAttribute('aria-busy');
  });
}
```

**Changes to workspace_card.html** — add positioning attributes:

```html
<div class="workspace-card{% if stale %} stale{% endif %}{% if providers|length > 1 %} multi-provider{% endif %} collapsed"
     data-cwd="{{ cwd }}"
     data-time-group="{{ time_group|default('') }}"
     data-sort-key="{{ last_updated|default('') }}">
```

**Changes to web.py** — pass `time_group` to template in `partials_workspaces()`:

In the time-grouped rendering loop, pass the `key` (e.g., "today", "yesterday") to each card template:

```python
cards_html += templates.get_template("partials/workspace_card.html").render(
    ..., time_group=key, ...
)
```

For pinned cards, pass `time_group="pinned"`.

**Exit criteria**:
- [x] Expanding a workspace card, waiting for sessions to load, then triggering a refresh — sessions remain visible throughout, no "Loading..." flash
- [x] Card-header metadata (session count, last-active, status dot) updates from server on each 30s refresh
- [x] After refresh, expanded cards appear in correct time-group position
- [x] Collapsed cards still render correctly via innerHTML replacement
- [x] Card selections are preserved across refresh
- [x] Scroll position within expanded card-bodies is preserved
- [x] On `visibilitychange` (tab return), expanded cards re-fetch session content with `fresh=1`
- [x] On fetch error, DOM remains intact (no detachment before response)
- [x] `htmx.process()` called on new content
- [x] `data-time-group` and `data-sort-key` attributes added to workspace card template
- [x] `time_group` passed from `partials_workspaces()` to card template

Implementation (2026-07-23, code: 1382985)
Refactored refreshCards() in index.html to preserve expanded workspace card DOM nodes across periodic 30s refreshes. The new implementation parses the server response into a temporary off-DOM container, updates only card-header metadata (session count, last-active, status dot) from the fresh render while preserving the live card-body with loaded sessions, then swaps the entire prepared container into #workspace-cards. Added _cardSelector() DRY helper, focus preservation on refresh (captures activeElement and restores focus to the card header after swap), tooltip cleanup before fresh-session innerHTML swap, scroll/selection state restoration, and clarifying comments. Added data-time-group and data-sort-key attributes to workspace_card.html template, and passed time_group from partials_workspaces() to both pinned and time-grouped render calls.

### Phase 3: Client-side 5s status poll [QA]

**Goal**: Implement a 5s polling loop that calls `/api/session-status`, receives JSON, and updates status dot CSS classes in-place on both panels — no HTML re-render. Poll pauses when tab is hidden.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`

**Covers**: SC-2, SC-5, SC-6

**Changes to index.html** — new status polling with visibility lifecycle:

```javascript
var _statusTimer = null;
var _statusFetching = false;
// Client-side active workspace state (maintained from server responses)
window._activeCwds = [];

function startStatusPoll() {
  if(_statusTimer) return;
  _statusTimer = setInterval(pollStatus, 5000);
}

function stopStatusPoll() {
  if(_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; }
}

function pollStatus() {
  // Guard against concurrent calls
  if(_statusFetching) return;
  
  // Only send cwds that are likely active (from last known state)
  // On first call, send all visible cwds; thereafter use server's active_cwds
  var cwds = window._activeCwds.length > 0 ? window._activeCwds : [];
  if(!cwds.length) {
    // Fallback: send all visible workspace cwds on first poll
    document.querySelectorAll('.workspace-card').forEach(function(c) {
      cwds.push(c.dataset.cwd);
    });
  }
  if(!cwds.length) return;
  
  _statusFetching = true;
  fetch('/api/session-status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({cwds: cwds})
  }).then(function(r) {
    return r.json();
  }).then(function(data) {
    // Update client-side active state from server response
    window._activeCwds = data.active_cwds || [];
    
    // Update workspace-level status dots (in-place class change, no DOM churn)
    Object.keys(data.workspaces).forEach(function(cwd) {
      var card = document.querySelector(_cardSelector(cwd));
      if(!card) return;
      _updateWorkspaceStatusDot(card, data.workspaces[cwd]);
    });
    
    // Update session-level status dots
    Object.keys(data.sessions).forEach(function(sid) {
      document.querySelectorAll('.session-row[data-sid="' + sid + '"]').forEach(function(row) {
        _updateSessionStatusDot(row, data.sessions[sid]);
      });
    });
  }).catch(function() { /* silent — retry next cycle */ 
  }).finally(function() { _statusFetching = false; });
}

function _updateWorkspaceStatusDot(card, newStatus) {
  var header = card.querySelector('.card-header');
  if(!header) return;
  var dot = header.querySelector('.ws-status');
  if(dot) {
    if(!newStatus) { dot.remove(); return; }
    // Update class in-place (no remove/recreate)
    dot.className = 'ws-status status-' + newStatus;
    dot.title = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
  } else if(newStatus) {
    dot = document.createElement('span');
    dot.className = 'ws-status status-' + newStatus;
    dot.title = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
    var lastActive = header.querySelector('.card-last-active');
    if(lastActive) header.insertBefore(dot, lastActive);
    else header.appendChild(dot);
  }
}

function _updateSessionStatusDot(row, newStatus) {
  var dot = row.querySelector('.session-status');
  if(dot) {
    if(!newStatus || newStatus === 'closed') { dot.remove(); return; }
    dot.className = 'session-status status-' + newStatus;
    dot.title = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
  } else if(newStatus && newStatus !== 'closed') {
    dot = document.createElement('span');
    dot.className = 'session-status status-' + newStatus;
    dot.title = newStatus.charAt(0).toUpperCase() + newStatus.slice(1);
    var titleEl = row.querySelector('.session-title');
    if(titleEl) titleEl.parentNode.insertBefore(dot, titleEl);
  }
}
```

Update the `visibilitychange` handler to manage timer lifecycle:

```javascript
document.addEventListener('visibilitychange', function() {
  if(document.hidden) {
    stopStatusPoll();
    stopActiveSessionPoll();  // Phase 4
  } else {
    refreshSettings();
    refreshCards(true);
    initWorkspaceFilters();
    startStatusPoll();
    startActiveSessionPoll();  // Phase 4
  }
});
```

Call `startStatusPoll()` alongside `startPolling()` in the `hx-on::after-swap` attribute.

**Exit criteria**:
- [ ] Status dots update within 5s of a process state change (start/stop a kiro-cli session)
- [ ] Both workspace cards and session rows reflect status changes
- [ ] No DOM destruction during status poll — only class/attribute changes (no remove/recreate churn)
- [ ] Scroll position preserved during status poll
- [ ] Selection state preserved during status poll
- [ ] No errors in console when workspace cards are filtered/removed between polls
- [ ] Status poll stops when tab is hidden, resumes on focus
- [ ] Concurrent poll calls guarded (in-flight flag)
- [ ] Client only sends active-workspace cwds after first response (not all visible cwds)

### Phase 4: Accelerated 10s session content refresh [QA]

**Goal**: For expanded cards in active workspaces (presence-detected via `_activeCwds` from status poll), refresh session content every 10s using cache-aware fetches. Replace card-body content only when the new HTML arrives.

**File scope**: `src/power_atlas/templates/index.html`

**Covers**: SC-3, SC-6

**Changes to index.html** — new `pollActiveSessions()` function:

```javascript
var _activeSessionTimer = null;

function startActiveSessionPoll() {
  if(_activeSessionTimer) return;
  _activeSessionTimer = setInterval(pollActiveSessions, 10000);
}

function stopActiveSessionPoll() {
  if(_activeSessionTimer) { clearInterval(_activeSessionTimer); _activeSessionTimer = null; }
}

function pollActiveSessions() {
  // Skip if a full refresh is in flight (dedup guard)
  var wp = document.getElementById('workspace-cards');
  if(wp && wp.getAttribute('aria-busy') === 'true') return;
  
  // Use _activeCwds from status poll responses (server-authoritative, not DOM-state)
  var activeCwds = window._activeCwds || [];
  if(!activeCwds.length) return;
  
  var provider = window._activeProvider || 'all';
  
  // Find expanded cards that are in active workspaces
  var expandedActive = [];
  activeCwds.forEach(function(cwd) {
    var card = document.querySelector(_cardSelector(cwd));
    if(card && !card.classList.contains('collapsed')) {
      expandedActive.push(cwd);
    }
  });
  
  if(!expandedActive.length) return;
  
  // Stagger requests with 200ms offset to avoid lock contention
  expandedActive.forEach(function(cwd, i) {
    setTimeout(function() {
      // Cache-aware fetch (no fresh=1, no status filter — avoid filter drift)
      fetch('/partials/sessions?cwd=' + encodeURIComponent(cwd) +
            '&provider=' + encodeURIComponent(provider)).then(function(r) {
        return r.text();
      }).then(function(html) {
        var card = document.querySelector(_cardSelector(cwd));
        if(!card || card.classList.contains('collapsed')) return;
        var body = card.querySelector('.card-body');
        if(body) {
          var scrollTop = body.scrollTop;
          body.innerHTML = html;
          if(window.htmx) htmx.process(body);
          body.scrollTop = scrollTop;  // Preserve scroll within card-body
        }
      });
    }, i * 200);
  });
}
```

Call `startActiveSessionPoll()` alongside `startStatusPoll()` in initialization.

**Exit criteria**:
- [ ] Expanded cards in active workspaces show updated session content within 10s of changes
- [ ] No `fresh=1` in the 10s poll requests (cache-aware)
- [ ] No status filter parameter in the 10s poll requests (avoids filter drift)
- [ ] Inactive expanded cards are NOT polled at 10s
- [ ] Uses `_activeCwds` from status poll (server-authoritative), not DOM dot presence
- [ ] `htmx.process()` called after session content swap
- [ ] Scroll position within card-body preserved
- [ ] Requests staggered with 200ms offset
- [ ] Poll skipped when `aria-busy` indicates a full refresh is in flight
- [ ] Poll stops when tab is hidden, resumes on focus

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| DOM relocation drops event handlers | Expanded cards become non-interactive after 30s refresh | Standard DOM API (`replaceChild`) preserves handlers; verify with Playwright test |
| Race between 5s status poll and 30s full refresh | Status dots briefly stale after full refresh | Self-corrects within 5s on next poll; acceptable |
| `CSS.escape(cwd)` fails on edge-case paths | Card relocation fails, card lost | Existing pattern already works; error handling re-attaches cards |
| 10s session poll fires for many active workspaces simultaneously | Server load spike | Presence typically shows 1-2 active workspaces; stagger requests if needed in future |
| Session content flashes on 10s swap | Brief visual disruption | `innerHTML` swap is atomic (parse→replace is synchronous); no intermediate state visible |
| Status endpoint called with many cwds | Slow response | Endpoint uses cached snapshot (3s TTL) + cached sessions; no disk IO; bounded by visible cards |

## 7) Verification

**Automated**:
- `pytest tests/test_web.py` — existing tests pass; new endpoint tests added in Phase 1
- No existing test assertions about "Loading..." text in expanded cards (the test at line 209 tests initial card render, which still shows Loading for collapsed cards)

**Manual / Playwright**:
- Start PowerAtlas, expand a workspace card, wait for sessions to load
- Observe 30s refresh cycle — sessions must NOT flash "Loading..."
- Start `kiro-cli chat -a` in a workspace — status dot should appear within 5s
- Stop the kiro-cli process — dot should disappear within 5s
- Verify right panel dots also update at 5s cadence
- Verify expanded card in active workspace shows new session rows within 10s of creating a new session
- Verify scroll position preserved during all fast polls

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | No update needed — no user-visible CLI/config surface change | N/A |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-07-23 — Plan Review (via /qplan)

High-effort review (4 personas: Architect, Senior engineer, Performance engineer, Reliability engineer). 15 findings (3 High, 6 Medium, 6 Low). All auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Status endpoint calls `_session_status()` which triggers notifications and disk IO on 5s hot path | Fixed — added `notify=False` param; short-circuit for inactive cwds; skip kiro-ide |
| 2 | High | Preserved expanded cards lose metadata freshness (count, last-active, dots) after relocation | Fixed — Phase 2 now updates card-header metadata from server placeholder before discarding |
| 3 | High | `visibilitychange` → `refreshCards(true)` no longer re-fetches sessions for expanded cards | Fixed — Phase 2 adds explicit `fresh=1` session re-fetch for expanded cards when `fresh` is true |
| 4 | Medium | No `clearInterval` for 5s/10s timers on tab hidden | Fixed — added `stopStatusPoll()`/`stopActiveSessionPoll()` in visibility handler |
| 5 | Medium | `pollStatus()` sends ALL visible cwds; URL length risk with comma delimiter | Fixed — switched to POST with JSON body; client sends only `_activeCwds` after first response |
| 6 | Medium | `pollActiveSessions()` uses DOM dot presence (flickers after 30s refresh removes dots) | Fixed — uses `window._activeCwds` from status poll response (server-authoritative) |
| 7 | Medium | No dedup guard when 10s and 30s timers fire simultaneously | Fixed — `pollActiveSessions()` skips when `aria-busy` indicates full refresh in flight |
| 8 | Medium | Phase 4 appends `_statusQs()` creating filter drift between polls | Fixed — removed `_statusQs()` from 10s poll; shows all sessions, 30s handles filter |
| 9 | Medium | Inactive expanded cards never get sessions re-fetched after Phase 4 removes old logic | Fixed — Phase 2 `loadExpandedCards()` still runs for safety; `fresh=true` path re-fetches all |
| 10 | Low | `_updateStatusDot` removes/recreates instead of updating className | Fixed — unified to in-place className update pattern |
| 11 | Low | kiro-ide included in provider loop despite no presence detection | Fixed — endpoint filters to `{"kiro-cli", "claude-code"}` only |
| 12 | Low | No debounce against concurrent `pollStatus()` calls | Fixed — added `_statusFetching` in-flight guard |
| 13 | Low | Error recovery appends cards at end instead of original position | Fixed — Phase 2 no longer detaches before fetch; DOM stays intact on error |
| 14 | Low | Scroll position not preserved within card-body on 10s swap | Fixed — capture/restore `body.scrollTop` in both Phase 2 and Phase 4 |
| 15 | Low | Three identical `CSS.escape(cwd)` patterns should be DRY | Fixed — extracted `_cardSelector(cwd)` helper in Phase 2 |

### 2026-07-23 -- Implementation Review (after Phases 1&2, personas: Senior engineer, Reliability engineer, End-user advocate)

Implementation health: Green.
11 findings (0 High, 5 Medium, 6 Low).
QA verification: PASS (2 surfaces verified — endpoint JSON response + expanded card DOM preservation).

| # | Severity | Finding (one line) | Resolution |
|---|---|---|---|
| 1 | Medium | No try-except around per-cwd loop in status endpoint | Fixed — wrapped loop body in try-except with log.debug fallback |
| 2 | Medium | Focus not preserved during refresh (activeElement lost after DOM swap) | Fixed — capture/restore focus to card header by cwd |
| 3 | Medium | Session tooltip visible during fresh path destroyed without cleanup | Fixed — clear tooltip slots before innerHTML swap |
| 4 | Medium | loadExpandedCards() behavior for filtered-then-reappearing cards undocumented | Fixed — added clarifying comment |
| 5 | Medium | _cardSelector CSS.escape safety undocumented | Fixed — added comment noting filesystem path safety |
| 6 | Low | Test assertion loose (workspace status `in` tuple instead of `==`) | Fixed — tightened to `== "waiting"` |
| 7 | Low | No test for missing 'cwds' key in request body | Fixed — added test_missing_cwds_key_returns_empty |
| 8 | Low | `notify` parameter positional-capable, not keyword-only | Fixed — made keyword-only with `*` separator |
| 9 | Low | `data-sort-key` populated but never consumed client-side | Orchestrator: proposed-accept — intentional for future Phase 4 use |
| 10 | Low | `aria-busy` without visible loading indicator for AT users | Orchestrator: proposed-accept — aria-live region enhancement beyond scope |
| 11 | Low | O(n) DOM operations for 50+ expanded cards could cause frame drop | Orchestrator: proposed-accept — acceptable for typical 5-15 card scenario |
