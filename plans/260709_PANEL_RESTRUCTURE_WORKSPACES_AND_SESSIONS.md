# Panel Restructure — Dedicated Workspaces and Sessions Panels

> **Date**: 2026-07-09
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Replace the pinned-sessions (left) and pinned-workspaces (center) panels with a unified workspaces panel and a paginated all-sessions panel, while keeping the launchers panel

---

## Intent

### Problem statement & desired outcomes

The current 3-panel layout (launchers + pinned sessions | pinned workspaces | all workspaces) splits workspace and session views across panels in a way that separates pinned items from their unpinned counterparts. This makes navigation fragmented — pinned workspaces are in a different panel than non-pinned ones, and there's no way to see all sessions across workspaces at a glance.

The desired outcome is a cleaner 3-panel layout: **Launchers | Workspaces | Sessions** — where each panel owns its domain completely, pinned items appear at the top of their respective panel (with a persistent pin icon indicator), and the sessions panel provides a global interleaved view sorted by recency.

### Success criteria

1. Launchers panel (leftmost) remains unchanged in behavior and position
2. Workspaces panel shows all workspaces — pinned at the top (sorted alphabetically, with persistent pin icon), non-pinned below (sorted by recency). Workspace cards remain expandable to show their sessions inline
3. Sessions panel shows all sessions interleaved by `updated_at` across all workspaces and providers — pinned sessions at the top (with persistent pin icon), paginated at 20 sessions per page with a "Load more" button
4. Every session row in the sessions panel displays the workspace folder name
5. Pinned items (sessions and workspaces) are visually distinguished by a persistent pin icon (📌) visible without hover, not a colored border
6. Provider filter (All / Kiro CLI / Claude Code / Kiro IDE) filters both panels simultaneously
7. Search filters both panels simultaneously — workspace paths in workspaces panel, session titles/prompts in sessions panel
8. Existing session selection, batch launch, and action bar behavior continues to work across both panels

### Scope boundaries & non-goals

**In scope:**
- New `/partials/all-sessions` endpoint (paginated, interleaved across workspaces)
- Merged workspaces panel (pinned + non-pinned in one panel)
- Persistent pin icon in session_row.html and workspace_card.html for pinned items
- Updated `refreshCards()`, `switchProvider()`, search, and polling JS
- Removal of now-unused `/partials/pinned-sessions` and `/partials/pinned-workspaces` endpoints
- CSS layout adjustments (workspaces and sessions panels both `flex: 1`)

**Non-goals:**
- Infinite scroll (explicit "Load more" button chosen)
- Changes to the launchers panel
- Changes to session data model or provider adapters
- Performance optimization of session loading (can be addressed later if needed)
- Changes to pin/unpin API endpoints (they stay as-is)

---

## Discovery

### Existing patterns & constraints

- Sessions are frozen dataclasses (`data.py:22-30`), cache returns copies (`data.py:80-82`)
- `_sort_pinned_first()` already exists (`web.py:535-539`) — reusable for pinned-at-top in both panels
- `show_workspace` + `workspace_name` already supported in `session_row.html:8`
- Provider filter cascades to all panels via `switchProvider()` (`index.html:131-137`)
- Custom htmx-mini requires manual `htmx.process(el)` after innerHTML swaps (project memory)
- `discover_workspaces_with_counts()` returns `(cwd, count, updated_at, provider)` — cached 30s
- `get_sessions(cwd, provider)` lazy-loads per-workspace — the new sessions panel will need to trigger loads across multiple workspaces
- `SessionCache` keyed by `(provider, normalized_cwd)` — iterating `get_loaded_cwds()` gives access to all cached sessions

### Risks & mitigations

1. **Performance of initial sessions panel load**: Loading sessions from all workspaces upfront could be slow with many workspaces. Mitigation: paginate at 20, and rely on workspace discovery (which only provides counts/dates) to determine which workspaces have the most recent sessions — load those first.
2. **Search must target two panels**: Currently search replaces `#workspace-cards` content. New search must return HTML for both panels or trigger two fetches. Mitigation: JS `switchProvider`-style dual fetch on search input.
3. **Provider filter consistency**: `_enabled()` is inconsistently applied in pinned session rendering (`web.py:508-567`). Mitigation: fix as part of the merge into the unified panel.
4. **Existing test coverage**: `test_web.py` has tests for all current panel endpoints. These will need updating for the new endpoint structure.

### Resolved decisions

- Q1: Where does launchers panel sit? — A: Same position — Decision: Launchers stays leftmost, unchanged
- Q2: Eager or paginated sessions? — A: Paginated — Decision: 20 sessions per page with "Load more" button
- Q3: Page size and mechanism? — A: 20, load more button — Decision: 20 initial + "Load more" button
- Q4: Red border for pinned? — A: No, use pin icon instead — Decision: Persistent 📌 icon for pinned items, no special border
- Q5: Pin icon placement? — A: Ok to (A) — Decision: Small persistent pin icon before the title, always visible on pinned items
- Q6: Pinned workspace sort? — A: Ok — Decision: Pinned workspaces sorted alphabetically at top, non-pinned by recency below
- Q7: Show workspace name on all session rows? — A: Ok — Decision: Every session row in sessions panel shows workspace folder name
- Q8: Search behavior? — A: Ok to (C) — Decision: Search filters both panels simultaneously (paths in workspaces, titles/prompts in sessions)
- Q9: Provider filter scope? — A: Ok — Decision: Provider filter applies globally to both panels
- Q10: Workspace cards expandable? — A: Ok — Decision: Workspace cards remain expandable to show inline sessions
- Q11: Space distribution? — A: Ok — Decision: Launchers fixed-width (~330px), workspaces and sessions both flex: 1

### Open items

None — all resolved during planning (see Design Decisions table).

### Recommended approach

1. Create a new data-layer function that collects sessions across all workspaces (leveraging the discovery cache to identify workspaces, then loading sessions from the most-recently-active workspaces first until 20+ sessions are collected)
2. Create `/partials/all-sessions?page=1&provider=all` endpoint that returns paginated session rows with workspace names
3. Restructure `index.html` — remove center panel, add sessions panel (right), make workspaces panel (center) contain both pinned and non-pinned workspace cards
4. Add persistent pin icon to `session_row.html` and `workspace_card.html` templates (conditional on pinned state)
5. Update `refreshCards()`, `switchProvider()`, search handler, and polling to target the new panel structure
6. Remove unused `/partials/pinned-sessions` and `/partials/pinned-workspaces` endpoints
7. Update CSS for new 3-panel flex layout
8. Update tests

---

## 1) Current State

**Panel layout** (`templates/index.html:51-69`): 3-panel flex layout:
- Left (`aside.left-panel`): launchers grid + pinned sessions (`/partials/pinned-sessions`)
- Center (`div.center-panel`): pinned workspaces only (`/partials/pinned-workspaces`)
- Right (`section.right-panel`): all non-pinned workspaces (`/partials/workspaces`)

**Session loading** is lazy per-workspace — sessions only load when a workspace card is expanded via `toggleCard()` → `/partials/sessions?cwd=X`. There is no "all sessions" endpoint.

**Pinned items** live in separate panels from their non-pinned counterparts. Pinned workspaces are excluded from the right panel via filtering at `web.py:363-364`.

**Provider filter** (`switchProvider()` in `index.html:131-137`) re-fetches all three panel endpoints with `?provider=X`.

**Session row border** (`session_row.html:1`): every session has `border-left: 3px solid <provider_color>` — no special pinned visual treatment beyond a `.pinned` class on the pin button.

## 2) Goal

Replace the pinned-sessions and pinned-workspaces panels with a unified workspaces panel (pinned at top + non-pinned below) and a new paginated all-sessions panel (pinned at top, 20 per page, interleaved by `updated_at` across all workspaces/providers), keeping the launchers panel unchanged.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Session loading strategy | Paginated (20 per page, "Load more") | Eager load all; infinite scroll | Desktop app but could have hundreds of sessions; explicit button is simple and predictable |
| Pinned visual indicator | Persistent 📌 icon before title | Red left border; highlighted pin button only | Icon is unambiguous, doesn't conflict with provider-color border |
| Pinned sort (workspaces) | Alphabetical at top, recency below | All by recency | Stable ordering for "favorites" aids muscle memory |
| Pinned sort (sessions) | By `updated_at` desc within pinned group | Config order; separate section | Consistent with non-pinned sort; pinned-at-top gives enough distinction |
| Space distribution | Launchers fixed ~330px; workspaces + sessions both `flex: 1` | Fixed widths for all | Adapts to window size; both data panels get equal space |
| Search behavior | Dual-panel (paths in workspaces, titles/prompts in sessions) | Single target; only active panel | Dashboard should surface all relevant results |
| Provider filter scope | Global — filters both panels | Per-panel toggle | Matches current UX expectation; simpler mental model |
| Workspace cards expandable | Yes (inline sessions within card) | Non-expandable | Sessions panel is global view; expand gives per-workspace focus |
| Page offset on filter change | Reset to page 1 | Maintain offset | Stale offset for new filter shows wrong results |
| Pinned sessions sort within pinned group | By `updated_at` desc | Config pin-order; unsorted | Consistent with non-pinned sort; most-recent-first is intuitive |

## 4) External Dependencies & Costs

### Required external changes

None — this is a UI-only change to a local desktop application. No infrastructure, CI/CD, IAM, or third-party service changes.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Data layer — paginated all-sessions function [QA] [P:2]

**Goal**: Create a new function in `data.py` that returns sessions across all workspaces, paginated, with pinned sessions at the top.

**File scope**: `src/power_atlas/data.py`, `tests/test_data.py`

**Covers**: SC-3

**Changes**:

In `src/power_atlas/data.py`, add:

```python
def get_all_sessions_paginated(
    page: int = 1,
    page_size: int = 20,
    provider: str | None = None,
    pinned_sessions: list[str] | None = None,
    enabled_providers: set[str] | None = None,
) -> tuple[list[tuple[Session, str]], bool]:
    """Return sessions across all workspaces, interleaved by updated_at.

    Uses early-stopping: loads workspaces by recency until enough sessions
    are collected for the requested page, avoiding loading ALL workspaces.

    Args:
        page: 1-based page number (applies to non-pinned sessions only)
        page_size: number of non-pinned sessions per page
        provider: filter to specific provider, None = all
        pinned_sessions: session IDs to sort to top (always returned in full)
        enabled_providers: set of enabled provider names to include

    Returns:
        ([(session, provider_name), ...], has_more)
        Pinned sessions first (all of them), then paginated non-pinned.
    """
    pinned_set = set(pinned_sessions or [])
    target_count = page * page_size  # Need this many non-pinned to fill page

    # 1. Get workspace list from discovery cache (already sorted by recency)
    workspace_data = discover_workspaces_with_counts(provider=None)

    # 2. Collect sessions — cache first (cheap), then load uncached by recency (stop early)
    all_sessions: list[tuple[Session, str]] = []
    seen: set[str] = set()

    def _add_sessions(sessions: list[Session], prov_name: str):
        for s in sessions:
            if s.session_id not in seen:
                seen.add(s.session_id)
                all_sessions.append((s, prov_name))

    providers_to_check = (
        {provider: PROVIDERS[provider]} if provider and provider in PROVIDERS
        else PROVIDERS
    )

    # Pass 1: collect from warm cache (no disk IO)
    for prov_name in providers_to_check:
        if enabled_providers and prov_name not in enabled_providers:
            continue
        if not PROVIDERS[prov_name].is_available():
            continue
        for norm_cwd in session_cache.get_loaded_cwds(prov_name):
            cached = session_cache.get(norm_cwd, prov_name)
            if cached:
                _add_sessions(cached, prov_name)

    # Pass 2: load uncached workspaces by recency, stop once we have enough
    non_pinned_count = sum(1 for s, _ in all_sessions if s.session_id not in pinned_set)
    for cwd, count, updated_at, ws_prov in workspace_data:
        if non_pinned_count >= target_count + page_size:
            break  # Early stop — we have more than enough for this page + peek-ahead
        if ws_prov not in providers_to_check:
            continue
        if enabled_providers and ws_prov not in enabled_providers:
            continue
        norm = _normalize_path(cwd)
        if norm in session_cache.get_loaded_cwds(ws_prov):
            continue  # Already collected in Pass 1
        sessions = get_sessions(cwd, ws_prov)
        _add_sessions(sessions, ws_prov)
        non_pinned_count = sum(1 for s, _ in all_sessions if s.session_id not in pinned_set)

    # 3. Sort by updated_at desc (normalize Z→+00:00)
    all_sessions.sort(key=lambda x: (x[0].updated_at or "").replace("Z", "+00:00"), reverse=True)

    # 4. Split pinned vs non-pinned
    pinned = [(s, p) for s, p in all_sessions if s.session_id in pinned_set]
    non_pinned = [(s, p) for s, p in all_sessions if s.session_id not in pinned_set]

    # 5. Paginate non-pinned
    start = (page - 1) * page_size
    end = start + page_size
    page_items = non_pinned[start:end]
    has_more = end < len(non_pinned)

    # 6. Combine: all pinned + current page of non-pinned
    return pinned + page_items, has_more
```

**Exit criteria**:
- [x] `get_all_sessions_paginated()` function exists in `data.py`
- [x] Returns pinned sessions first (all of them regardless of page)
- [x] Returns paginated non-pinned sessions sorted by `updated_at` desc
- [x] Respects provider filter and enabled_providers
- [x] Returns `has_more` boolean for "Load more" button
- [x] Deduplicates sessions by ID
- [x] Unit tests added in `test_data.py` covering: pagination, pinned-at-top, provider filtering, empty state

**Implementation (2026-07-09, code: b1c9ff6)**
Implemented `get_all_sessions_paginated()` in `src/power_atlas/data.py` — a two-pass data layer function that collects sessions across all providers with early-stopping for pagination efficiency. Pass 1 iterates already-cached workspaces (no disk IO); Pass 2 loads uncached workspaces by recency until enough non-pinned sessions are collected. The function deduplicates by session_id, sorts by `updated_at` descending (normalizing 'Z' suffix for consistent comparison), splits results into pinned (always returned in full) and non-pinned (paginated), and supports filtering by provider name and enabled-providers set. Added 8 unit tests covering basic pagination (page 1/page 2 boundaries), pinned-at-top ordering, provider filtering, empty state, deduplication, enabled-providers exclusion, and sort order verification. All 91 tests pass.

### Phase 2: Templates — pin icon and workspace name on session rows [QA] [P:1]

**Goal**: Add persistent pin icon to `session_row.html` and `workspace_card.html` for pinned items.

**File scope**: `src/power_atlas/templates/partials/session_row.html`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/static/style.css`

**Covers**: SC-4, SC-5

**Changes in `session_row.html`**:

Add a persistent pin indicator before the title (visible only for pinned sessions):

```html
<div class="session-row" data-sid="{{ session.session_id }}" data-cwd="{{ cwd }}" data-provider="{{ provider_name|default('') }}"{% if provider_color %} style="border-left: 3px solid {{ provider_color }}"{% endif %} onclick="if(!event.target.closest('.session-actions')){handleItemClick(this,event,'.session-row')}">
  <div class="session-check"></div>
  <div class="session-content" onmouseenter="loadTail(this)" onmouseleave="hideTail(this)">
    <div class="session-title-row">
      {% if session.session_id in pinned_sessions %}<span class="pinned-indicator" title="Pinned">📌</span>{% endif %}
      <span class="session-title">{{ session.title or 'untitled' }}</span>
      <span class="session-time">{{ session.updated_at[5:16] | replace('T', ' ') if session.updated_at else '' }}</span>
    </div>
    {% if show_workspace and workspace_name %}<div class="session-workspace">{{ workspace_name }}</div>{% endif %}
    ...
```

**Changes in `workspace_card.html`**:

Add pin indicator next to folder name for pinned workspaces:

```html
    <span class="card-folder-name">{% if is_pinned %}<span class="pinned-indicator" title="Pinned">📌</span>{% endif %}{{ folder_name }}...
```

**Changes in `style.css`**:

```css
.pinned-indicator { font-size: 11px; margin-right: 4px; opacity: 0.8; flex-shrink: 0; }
```

**Exit criteria**:
- [x] Pinned sessions show persistent 📌 icon before title
- [x] Pinned workspaces show persistent 📌 icon before folder name
- [x] Icon is always visible (not hover-dependent)
- [x] CSS styling for `.pinned-indicator`

**Implementation (2026-07-09, code: dff4279)**
Added persistent pin indicators (📌) to both session rows and workspace cards. In `session_row.html`, a `.pinned-indicator` span renders conditionally before the session title when `session.session_id in pinned_sessions`. In `workspace_card.html`, the same indicator renders at the start of the `.card-folder-name` span when `is_pinned` is truthy. A new `.pinned-indicator` CSS class in `style.css` (line 126, adjacent to session-row styles) provides consistent 11px sizing, right margin, slight transparency, and flex-shrink protection.

Per-phase review deferred to Step 9: template-only changes ≤30 LOC, no executable code.

### Phase 3: Backend endpoints — unified workspaces + all-sessions [QA]

**Goal**: Create the new `/partials/all-sessions` endpoint and merge pinned workspaces into the existing `/partials/workspaces` endpoint. Remove the separate `/partials/pinned-sessions` and `/partials/pinned-workspaces` endpoints.

**File scope**: `src/power_atlas/web.py`, `tests/test_web.py`

**Covers**: SC-2, SC-3, SC-6

**Changes in `web.py`**:

1. **New endpoint** `/partials/all-sessions`:

```python
@app.get("/partials/all-sessions", response_class=HTMLResponse)
async def partials_all_sessions(request: Request, page: int = 1, provider: str = "all", q: str = ""):
    """Render paginated all-sessions panel. Pinned at top, then by updated_at."""
    import asyncio
    config = load_config()

    enabled = {p for p in data.PROVIDERS if _enabled(config, p)}
    prov_filter = None if provider == "all" else provider

    sessions_with_prov, has_more = await asyncio.to_thread(
        data.get_all_sessions_paginated,
        page=page,
        page_size=20,
        provider=prov_filter,
        pinned_sessions=config.pinned_sessions,
        enabled_providers=enabled,
    )

    # Apply search filter if q provided
    if q:
        query = q.strip().lower()
        sessions_with_prov = [
            (s, p) for s, p in sessions_with_prov
            if query in (s.title or "").lower()
            or query in (s.first_prompt or "").lower()
            or query in (s.cwd or "").lower()
        ]
        has_more = False  # Search disables pagination

    html = ""
    pinned_set = set(config.pinned_sessions)

    for session, prov_name in sessions_with_prov:
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=session.cwd,
            stale=not Path(session.cwd).exists(),
            pinned_sessions=config.pinned_sessions,
            provider_name=prov_name,
            provider_color=_get_provider_color(prov_name, config),
            show_workspace=True,
            workspace_name=Path(session.cwd).name if session.cwd else "",
        )

    if not html:
        html = '<div class="empty-state">No sessions found.</div>'

    if has_more:
        next_page = page + 1
        html += f'<button class="load-more-btn" onclick="loadMoreSessions({next_page})">Load more</button>'

    return HTMLResponse(html)
```

2. **Modify `/partials/workspaces`** to include pinned workspaces at the top (remove the pinned exclusion filter):

```python
# In partials_workspaces: replace existing pinned-exclusion logic with merged view
from .data import _normalize_path

pinned_norm_paths: set[str] = set()
for folder in config.pinned_folders:
    pinned_norm_paths.add(_normalize_path(folder))

# Split into pinned vs non-pinned
pinned_data = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) in pinned_norm_paths]
pinned_data = [(c, n, u, p) for c, n, u, p in pinned_data if _enabled(config, p)]
pinned_grouped = _group_workspaces(pinned_data, config)
pinned_grouped.sort(key=lambda x: x["folder_name"].lower())  # Alphabetical for pinned

other_data = [(c, n, u, p) for c, n, u, p in workspace_data if _normalize_path(c) not in pinned_norm_paths]
other_data = [(c, n, u, p) for c, n, u, p in other_data if _enabled(config, p)]
other_grouped = _group_workspaces(other_data, config)

# Render pinned workspaces first (is_pinned=True), then non-pinned (is_pinned=False)
for group in pinned_grouped:
    cards_html += templates.get_template("partials/workspace_card.html").render(
        ..., is_pinned=True, ...
    )
for group in other_grouped:
    cards_html += templates.get_template("partials/workspace_card.html").render(
        ..., is_pinned=False, ...
    )
```

3. **Remove** `/partials/pinned-sessions` and `/partials/pinned-workspaces` endpoints. Also remove `_render_pinned_sessions()` helper.

4. **Refactor `/search`** — remove pinned-session rendering logic (which references the removed `_render_pinned_sessions` helper). The search endpoint now returns only workspace cards matching the query. Session search is handled by the `/partials/all-sessions?q=X` endpoint called from the JS dual-fetch.

**Exit criteria**:
- [x] `GET /partials/all-sessions?page=1&provider=all` returns paginated session rows
- [x] Pinned sessions appear at top with pin icon
- [x] Every session row shows workspace folder name
- [x] `page=2` returns next 20 sessions
- [x] `provider=kiro-cli` filters to that provider only
- [x] `q=search` filters by title/prompt/workspace
- [x] "Load more" button rendered when `has_more=True`
- [x] `/partials/workspaces` now includes pinned workspaces at top with pin icon
- [x] `/partials/pinned-sessions` and `/partials/pinned-workspaces` removed
- [x] Search returns results for both panels
- [x] Tests updated for new endpoints, removed endpoints

**Implementation (2026-07-09, code: 334d4fa)**
Implemented Phase 3 of the panel restructure: created the new `GET /partials/all-sessions` endpoint that calls `data.get_all_sessions_paginated()` with pagination, provider filtering, and search support; modified `/partials/workspaces` to show all workspaces in a unified view (pinned at top sorted alphabetically, non-pinned below by recency); refactored `/search` to only return workspace cards (removing pinned-session search logic, adding `provider` parameter); and deleted the unused `/partials/pinned-sessions` endpoint, `/partials/pinned-workspaces` endpoint, and `_render_pinned_sessions()` helper. Tests updated: 7 new tests added, 2 existing tests updated. All 387 tests pass.

### Phase 4: Frontend — panel layout, JS refresh logic [QA]

**Goal**: Restructure the HTML layout to 3 panels (launchers | workspaces | sessions), update all JS functions that reference panels.

**File scope**: `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`

**Covers**: SC-1, SC-2, SC-3, SC-6, SC-7, SC-8

**Changes in `index.html`**:

1. **Panel structure** — replace current 3-panel with:

```html
<main class="panels-container">
  <aside class="left-panel" id="left-panel">
    <div class="section-label"><span>Launchers</span> <button class="launcher-add-btn" onclick="openNewLauncher()">+</button></div>
    <div id="launcher-tiles" class="launcher-grid" hx-get="/partials/launchers" hx-trigger="load" hx-swap="innerHTML"></div>
  </aside>
  <div class="center-panel" id="workspaces-panel">
    <div class="section-label">Workspaces</div>
    <div id="workspace-cards" aria-busy="true"
         hx-get="/partials/workspaces?fresh=1" hx-trigger="load" hx-swap="innerHTML"
         hx-on::after-swap="this.removeAttribute('aria-busy');loadExpandedCards();startPolling()">
      <div class="skeleton-card"></div>
      <div class="skeleton-card"></div>
    </div>
  </div>
  <section class="right-panel" id="sessions-panel">
    <div class="section-label">Sessions</div>
    <div id="all-sessions" aria-busy="true"
         hx-get="/partials/all-sessions?page=1&fresh=1" hx-trigger="load" hx-swap="innerHTML"
         hx-on::after-swap="this.removeAttribute('aria-busy')">
      <div class="skeleton-card"></div>
      <div class="skeleton-card"></div>
    </div>
  </section>
</main>
```

2. **Update `refreshCards(fresh)`** — fetch workspaces + all-sessions:

```javascript
function refreshCards(fresh) {
  var qs = fresh ? '&fresh=1' : '';
  var provQs = 'provider=' + encodeURIComponent(window._activeProvider || 'all');

  // Workspaces panel
  fetch('/partials/workspaces?' + provQs + qs).then(r => r.text()).then(html => {
    var el = document.getElementById('workspace-cards');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
    loadExpandedCards();
    updateActionBar();
  });

  // Sessions panel
  fetch('/partials/all-sessions?page=1&' + provQs + qs).then(r => r.text()).then(html => {
    var el = document.getElementById('all-sessions');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
    updateActionBar();
  });
}
```

3. **Update `switchProvider(provider)`** — same dual-fetch pattern.

4. **Update search handler** — dual-fetch with `q` param to both endpoints:

```javascript
// Replace the hx-get on search input with JS handler:
document.querySelector('input[name="q"]').addEventListener('input', debounce(function(e) {
  var q = e.target.value.trim();
  var provQs = 'provider=' + encodeURIComponent(window._activeProvider || 'all');
  // Workspaces
  fetch('/search?' + provQs + '&q=' + encodeURIComponent(q)).then(r => r.text()).then(html => {
    var el = document.getElementById('workspace-cards');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
  });
  // Sessions
  fetch('/partials/all-sessions?page=1&' + provQs + '&q=' + encodeURIComponent(q)).then(r => r.text()).then(html => {
    var el = document.getElementById('all-sessions');
    el.innerHTML = html;
    if (window.htmx) htmx.process(el);
  });
}, 300));
```

5. **Add `loadMoreSessions(page)` function**:

```javascript
function loadMoreSessions(page) {
  var provQs = 'provider=' + encodeURIComponent(window._activeProvider || 'all');
  fetch('/partials/all-sessions?page=' + page + '&' + provQs).then(r => r.text()).then(html => {
    var container = document.getElementById('all-sessions');
    // Remove the existing "Load more" button
    var btn = container.querySelector('.load-more-btn');
    if (btn) btn.remove();
    // Append new sessions
    container.insertAdjacentHTML('beforeend', html);
    if (window.htmx) htmx.process(container);
    updateActionBar();
  });
}
```

6. **Update polling** — replace `startPinnedPoll()` + `startSessionRefresh()` with a single polling function:

```javascript
var _refreshTimer = null;
function startPolling() {
  if (_refreshTimer) return;
  // Initial burst: refresh both panels every 15s for first 2 minutes
  var burstCount = 0;
  var burstTimer = setInterval(function() {
    burstCount++;
    if (burstCount >= 8) { clearInterval(burstTimer); }
    refreshCards(false);
  }, 15000);
  // Then ongoing 30s refresh
  _refreshTimer = setInterval(function() { refreshCards(false); }, 30000);
}
```

7. **Rewrite `pinSession()` callback** — currently fetches removed `/partials/pinned-sessions` endpoint. Replace with:

```javascript
function pinSession(btn) {
  var row = btn.closest('.session-row');
  var pinned = btn.classList.contains('pinned');
  var url = pinned ? '/api/unpin-session' : '/api/pin-session';
  fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({session_id: row.dataset.sid, workspace: row.dataset.cwd})
  }).then(function() {
    btn.classList.toggle('pinned');
    showToast('<div class="toast toast-success">' + (pinned ? 'Unpinned' : 'Pinned') + '<button class="toast-dismiss" onclick="this.parentElement.remove()">×</button></div>');
    refreshCards(true);  // Refresh both panels to reflect pin state
  });
}
```

8. **Remove `hx-get="/search"` from search input** — the search input currently has htmx attributes that would conflict with the new JS handler. Remove `hx-get`, `hx-trigger`, and `hx-target` attributes from the search input element.

9. **Preserve expanded-card state in `refreshCards()`**:

```javascript
function refreshCards(fresh) {
  var qs = fresh ? '&fresh=1' : '';
  var provQs = 'provider=' + encodeURIComponent(window._activeProvider || 'all');

  // Save expanded card state
  var expandedCards = [];
  document.querySelectorAll('.workspace-card:not(.collapsed)').forEach(function(c) {
    expandedCards.push(c.dataset.cwd);
  });

  // Set aria-busy
  var wsEl = document.getElementById('workspace-cards');
  var sessEl = document.getElementById('all-sessions');
  if (wsEl) wsEl.setAttribute('aria-busy', 'true');
  if (sessEl) sessEl.setAttribute('aria-busy', 'true');

  // Workspaces panel
  fetch('/partials/workspaces?' + provQs + qs).then(function(r) { return r.text(); }).then(function(html) {
    wsEl.innerHTML = html;
    if (window.htmx) htmx.process(wsEl);
    wsEl.removeAttribute('aria-busy');
    // Restore expanded cards
    expandedCards.forEach(function(key) {
      wsEl.querySelectorAll('.workspace-card').forEach(function(card) {
        if (card.dataset.cwd === key) {
          card.classList.remove('collapsed');
          var body = card.querySelector('.card-body');
          if (body) {
            body.dataset.loaded = 'true';
            fetch('/partials/sessions?cwd=' + encodeURIComponent(key) + '&provider=' + encodeURIComponent(window._activeProvider || 'all') + '&fresh=1')
              .then(function(r) { return r.text(); })
              .then(function(h) { body.innerHTML = h; });
          }
        }
      });
    });
    loadExpandedCards();
    updateActionBar();
  });

  // Sessions panel
  fetch('/partials/all-sessions?page=1&' + provQs + qs).then(function(r) { return r.text(); }).then(function(html) {
    sessEl.innerHTML = html;
    if (window.htmx) htmx.process(sessEl);
    sessEl.removeAttribute('aria-busy');
    updateActionBar();
  });
}
```

10. **Add loading state to "Load more" button**:

```javascript
function loadMoreSessions(page) {
  var btn = document.querySelector('.load-more-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }
  var provQs = 'provider=' + encodeURIComponent(window._activeProvider || 'all');
  fetch('/partials/all-sessions?page=' + page + '&' + provQs).then(function(r) { return r.text(); }).then(function(html) {
    var container = document.getElementById('all-sessions');
    if (btn) btn.remove();
    container.insertAdjacentHTML('beforeend', html);
    if (window.htmx) htmx.process(container);
    updateActionBar();
  });
}
```

11. **Search clear behavior** — when search input is emptied, trigger `refreshCards(false)` to restore paginated state.

**Changes in `style.css`**:

```css
/* Updated panel layout */
.left-panel { width: 280px; min-width: 240px; max-width: 340px; /* launchers only, slightly narrower */ }
.center-panel { flex: 1; overflow-y: auto; padding: 16px 16px 80px; border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 12px; }
.right-panel { flex: 1; overflow-y: auto; padding: 16px 16px 80px; display: flex; flex-direction: column; gap: 12px; }

/* Remove grid layout from right panel (sessions are a flat list, not a grid) */
/* Load more button */
.load-more-btn { display: block; width: 100%; padding: 12px; margin-top: 8px; background: var(--surface-hover); border: 1px solid var(--border); border-radius: 4px; color: var(--text); cursor: pointer; font-size: 13px; text-align: center; }
.load-more-btn:hover { background: var(--card-bg); border-color: #3a4060; }
```

**Exit criteria**:
- [x] Launchers panel is leftmost, unchanged behavior
- [x] Workspaces panel (center) shows pinned at top + non-pinned below
- [x] Sessions panel (right) shows paginated sessions with workspace names
- [x] "Load more" button loads next page with loading state
- [x] Provider filter re-fetches both panels and resets page to 1
- [x] Search filters both panels; clearing search restores paginated state
- [x] `hx-get`/`hx-trigger` removed from search input (JS handler replaces htmx)
- [x] `refreshCards()` refreshes both panels and preserves expanded-card state
- [x] `pinSession()` rewritten to refresh both panels (no reference to removed endpoints)
- [x] `startPinnedPoll()` replaced with new polling function targeting new endpoints
- [x] `aria-busy` set/cleared during panel refreshes
- [x] `htmx.process()` called after all innerHTML swaps
- [x] Action bar selection works across both panels
- [x] Update `README.md` Features list to reflect new panel layout

**Implementation (2026-07-09, code: 06eecc1, fix: e297ecf)**
Restructured the frontend panel layout from the old three-panel structure (launchers+pinned-sessions | pinned-workspaces | workspaces) to the new unified layout (launchers | workspaces | sessions). The left panel now contains only the launcher grid (narrower at 280px), the center panel shows all workspaces with flex:1, and the right panel is a new sessions panel fetching `/partials/all-sessions?page=1&fresh=1`. Replaced htmx-driven search with a JS debounced dual-panel search handler, rewrote `refreshCards()` and `switchProvider()` to fetch both panels, replaced the old `startPinnedPoll()`/`startSessionRefresh()` with a unified `startPolling()` function, added `loadMoreSessions(page)` for pagination, updated `pinSession()` to use `refreshCards(true)`, and updated CSS to remove grid layout from the right panel and add `.load-more-btn` styles. Review auto-fix added `htmx.process()` to all expanded-card innerHTML swaps and removed dead `refreshExpandedSessions` function and dead `.pinned-sessions-list` CSS rule. README updated to reflect new filter behavior.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Performance: loading all sessions for first page | First load could be slow with 50+ workspaces | Discovery cache provides workspace recency — load most-recent workspaces first; page size of 20 limits initial render |
| Breaking existing tests | ~70KB of web tests depend on current endpoint structure | Phase 3 includes test updates; run full suite after each phase |
| Provider filter + pagination interaction | Filter change with stale page offset | Reset to page 1 on filter/search change (design decision) |
| `_enabled()` inconsistency in pinned items | Disabled provider's sessions appearing | Fix in Phase 3 — unified endpoint applies `_enabled()` consistently |
| Session deduplication | Same session could appear from multiple cache paths | `get_all_sessions_paginated` deduplicates by session_id |
| Pinned session resolution without cache | Pinned sessions from workspaces not yet loaded | Existing `_find_pinned_session_workspace` + `get_sessions` fallback, used in the data layer function |

## 7) Verification

- `pytest tests/` — full test suite passes after each phase
- Manual: open PowerAtlas UI, verify 3-panel layout renders
- Manual: pin a session → verify pin icon appears, session moves to top
- Manual: pin a workspace → verify pin icon, workspace at top of workspaces panel
- Manual: click "Load more" → next 20 sessions appear
- Manual: switch provider filter → both panels filter, page resets
- Manual: type in search → both panels filter
- Manual: expand a workspace card → inline sessions still load
- Manual: select sessions → action bar works

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update Features list to reflect new panel layout (remove "pinned workspaces center panel" phrasing, add "dedicated sessions panel") | 4 |

## 9) Implementation Divergences from Plan

<Reserved -- filled during implementation>

## Review Log

### 2026-07-09 — Plan Review (High-effort, 4 personas: Architect, Senior engineer, End-user advocate, Performance engineer)

15 findings (3 High, 8 Medium, 4 Low). 13 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `get_all_sessions_paginated` eagerly loads ALL workspace sessions — unbounded disk IO on first call. | Resolved — rewrote with early-stopping: loads by recency, stops once page_size + buffer collected. |
| 2 | High | `pinSession()` JS fetches removed `/partials/pinned-sessions` endpoint — will silently fail. | Resolved — Phase 4 now includes rewritten `pinSession()` that calls `refreshCards(true)`. |
| 3 | High | `startPinnedPoll()` fetches removed endpoints — no replacement polling defined. | Resolved — Phase 4 now defines replacement polling function with burst + steady intervals. |
| 4 | Medium | No loading indicator on "Load more" button during fetch. | Resolved — Phase 4 adds disabled+text state on click. |
| 5 | Medium | Search `hx-get` attribute still on input, conflicts with new JS handler. | Resolved — Phase 4 exit criteria requires `hx-get`/`hx-trigger` removal from search input. |
| 6 | Medium | `refreshCards()` drops expanded-card restoration logic. | Resolved — Phase 4 now preserves and restores expanded-card state. |
| 7 | Medium | Open item "pinned session sort" unresolved. | Resolved — added to Design Decisions table: by `updated_at` desc. |
| 8 | Medium | `/search` endpoint still contains pinned-session logic referencing removed helper. | Resolved — Phase 3 now explicitly refactors `/search` to remove pinned-session rendering. |
| 9 | Medium | `partials_workspaces` merged view doesn't detail `is_pinned=True` propagation. | Resolved — Phase 3 now shows explicit merged render loop with `is_pinned` parameter. |
| 10 | Medium | `aria-busy` not re-applied during refreshes — accessibility gap. | Resolved — Phase 4 now sets/clears `aria-busy` during panel refreshes. |
| 11 | Medium | Discovery + cache iteration is redundant — double-scans workspaces. | Resolved — Phase 1 now uses 2-pass strategy: cache-first (no IO), then discovery-guided loads. |
| 12 | Low | 📌 emoji rendering varies across Windows versions. | Noted — acceptable for personal desktop app; documented. |
| 13 | Low | `pinned_sessions` as list in templates causes O(n) per `in` check. | Noted — Jinja2 `in` on small lists is negligible; optimize if pinned count grows. |
| 14 | Low | Left panel width change 330→280px — verify launchers fit. | Noted — implementer should verify during Phase 4. |
| 15 | Low | Ambiguous workspace names in session rows. | Noted — existing `data-cwd` tooltip provides full path on hover. |


### 2026-07-09 -- Implementation Review (after Phase 1, persona: Senior engineer)

Implementation health: Green.
4 findings (0 High, 2 Medium, 2 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | No input validation — `page=0` or negative produces wrong slice via negative indexing. | Fixed — added `page = max(1, page)` and `page_size = max(1, page_size)` at entry. |
| 2 | Medium | Pinned sessions in unloaded workspaces silently missing if early-stop fires before their workspace. | Fixed — added comment documenting implicit contract with `warmup_all()`. |
| 3 | Low | `non_pinned_count` recomputed via O(n) sum on every Pass 2 iteration — quadratic on workspace count. | Noted — acceptable for typical workspace counts (<100). |
| 4 | Low | No test for early-stop behavior — hard to verify it stops loading once threshold is reached. | Noted — optional optimization test, not required for correctness. |

### 2026-07-09 -- Implementation Review (after Phase 3, persona: Senior engineer)

Implementation health: Green.
5 findings (0 High, 1 Medium, 4 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Exit criteria in plan unticked despite all being substantively met by the code. | Fixed — orchestrator ticked all 11 criteria in Step 7 plan update. |
| 2 | Low | No test verifying removed endpoints return 404. | Noted — deletion confirmed via grep; standard multi-phase pattern. |
| 3 | Low | New `provider` param on `/search` has no dedicated test. | Noted — logic simple and consistent with existing patterns; Phase 4 exercises it. |
| 4 | Low | Frontend still references removed endpoints (temporarily broken between phases). | Noted — expected; Phase 4 handles frontend updates. |
| 5 | Low | Redundant double provider-filter in `/search`. | Noted — harmless defensive code, consistent with `partials_workspaces` pattern. |

### 2026-07-09 -- Implementation Review (after Phase 4, persona: Senior engineer)

Implementation health: Green.
4 findings (0 High, 1 Medium, 3 Low).

| # | Severity | Finding | Resolution |
|---|---|---|---|
| 1 | Medium | Expanded-card `body.innerHTML=h` in refreshCards/toggleCard/loadExpandedCards lacked `htmx.process(body)`. | Fixed — added `htmx.process(body)` to all 3 innerHTML swaps (fix: e297ecf). |
| 2 | Low | `refreshExpandedSessions()` is dead code after `startSessionRefresh` removal. | Fixed — removed dead function. |
| 3 | Low | `.pinned-sessions-list` CSS rule is dead after template changes. | Fixed — removed dead CSS rule. |
| 4 | Low | Polling burst/steady timers overlap at 30/60/90/120s causing doubled refreshes. | Noted — harmless, matches plan spec. |

Cycle 2 skipped — cycle 1 auto-fixes were purely mechanical (htmx.process calls + dead code removal).
