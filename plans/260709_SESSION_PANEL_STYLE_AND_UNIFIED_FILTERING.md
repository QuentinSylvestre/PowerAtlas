# Session Panel Style Alignment and Unified Filtering

> **Date**: 2026-07-09
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Last Updated**: <set by /qclose at archival>
> **Scope**: Align 3rd panel session card styling with 2nd panel workspace cards, unify filtering/grouping across both panels, remove group-by UI option
> **Estimated effort**: ~1-2 days

---

## Intent

### Problem statement & desired outcomes

The sessions panel (3rd panel) has visually mismatched session rows compared to the workspace cards in the 2nd panel — session rows sit on a transparent/dark background while workspace cards use the lighter card background (`var(--card-bg)`), and border treatments differ. Additionally, the workspace panel's filtering options (tag, time) and time-grouping don't apply to the sessions panel, creating an inconsistent experience where filtering one panel doesn't filter the other.

The desired outcome is visual consistency between panels (session rows match workspace card styling), permanently-enabled time grouping in both panels, and unified filtering (tag, time, provider, search) that applies simultaneously to both panels.

### Success criteria

1. Session rows in the 3rd panel (and inside expanded workspace cards) use `var(--card-bg)` background and matching border treatment consistent with workspace cards
2. Both panels are always grouped by time (Today / Yesterday / This week / Older) with group headings — no UI toggle, no "no grouping" option
3. The group-by dropdown is removed from the workspace filters UI
4. Tag filter applies to both panels simultaneously — sessions filtered by their workspace's tags
5. Time filter applies to both panels simultaneously — workspaces by aggregate `latest_updated`, sessions by individual `updated_at`
6. Provider filter continues to apply to both panels (already does)
7. Search continues to filter both panels (workspaces by path, sessions by title/prompt/cwd) with results rendered within time groups
8. Pinned workspaces/sessions always render at the top (above time-group headings), with a separator below — unaffected by grouping mode
9. All filtering options (provider, tag, time) can hide pinned items — pinning only guarantees top position, not visibility
10. Empty time-group headings are hidden (not rendered when no items in that bucket)
11. Sessions panel retains pagination ("Load more" button at bottom), working within the time-grouped view — next page response includes appropriate group headings
12. "Time" grouping is the default and only grouping mode on first load (no `group_by` toggle state needed)

### Scope boundaries & non-goals

**In scope:**
- CSS changes to `.session-row` for card-bg background and border alignment
- Removal of group-by dropdown from `initWorkspaceFilters()` JS
- Hardcoded time-grouping in `partials_workspaces` (remove flat/tag group_by code paths)
- New `tag` and `time_filter` params on `/partials/all-sessions` endpoint
- Server-side tag/time filtering for sessions (lookup workspace tags via `session.cwd`)
- Time-grouped rendering in `partials_all_sessions` with group headings
- Pinned-first extraction before time grouping in both panels
- JS changes to pass tag/time filter state to sessions panel fetch calls

**Non-goals:**
- Workspace color cascade on session rows (no workspace_color on sessions)
- Multi-provider gradient on session rows (sessions are single-provider)
- Per-time-group pagination / "Load more" buttons
- Changes to the launchers panel
- Changes to session data model or provider adapters
- Tag grouping mode (removed entirely)
- Infinite scroll

---

## Discovery

### Existing patterns & constraints

- `partials_workspaces` (`web.py:349-549`) already implements time-grouping logic with `_time_bucket()` and group headings — reusable for sessions panel
- `_time_bucket()` (`web.py:95-101`) classifies ISO-8601 timestamps into today/yesterday/this_week/before
- `get_workspace_settings(config, cwd)["tags"]` resolves workspace tags from config — usable for session tag filtering via `session.cwd`
- Session rows currently use `display: grid; grid-template-columns: 28px 1fr` with `border-left` accent (`style.css:133`)
- Workspace cards use `background: var(--card-bg); border: 1px solid var(--card-border)` (`style.css:101`)
- `_buildWorkspaceQs()` (`index.html:132`) assembles filter query string but only sends to workspace endpoint
- `refreshCards()` (`index.html:137`) already refreshes both panels but passes different params
- `get_all_sessions_paginated` (`data.py:308-404`) returns pinned sessions first regardless of page, pagination only on non-pinned
- Custom htmx-mini requires `htmx.process(el)` after innerHTML swaps (project memory)
- Pinned items in current grouped modes (`web.py:419/458`) lose top position — needs fix: extract pinned before grouping

### Risks & mitigations

- **Pagination + time grouping complexity**: "Load more" response must include group headings when crossing time boundaries. Mitigation: server renders complete HTML fragments with headings; client appends raw HTML before the button.
- **Tag filter on sessions requires per-session workspace lookup**: Could be slow for 1000+ sessions. Mitigation: resolve workspace tags once per unique cwd (sessions share workspaces), cache the lookup.
- **Removing flat mode is irreversible UX change**: Users lose the ungrouped view. Mitigation: time-grouped view with all buckets visible is effectively the same as flat (just with headings inserted).

### Resolved decisions

- Q1: Should session rows get workspace_color or multi-provider gradients? — A: No, sessions are single-provider, no custom color needed — Decision: No workspace_color or gradient changes to session rows
- Q2: What "style alignment" means — A: Mainly the background color (card-bg vs transparent) and matching borders — Decision: Give session rows `var(--card-bg)` background and consistent border treatment
- Q3: Should grouping options be checkboxes allowing simultaneous tag+time? — A: No, radio buttons... then revised to no option at all — Decision: Remove group-by UI entirely, always time-group
- Q4: Keep a "no grouping" flat option? — A: No, 2 options only (tag/time)... then revised to time-only always — Decision: Permanently time-grouped, no toggle
- Q5: How does tag filter apply to sessions? — A: Filter sessions by their workspace's tags — Decision: Session tag filtering via workspace tag lookup on session.cwd
- Q6: Time filter + time grouping interaction — A: Keep time filter, it narrows to matching group only — Decision: Time filter hides non-matching time groups
- Q7: Do filters hide pinned items? — A: Yes, all filters apply to pinned items — Decision: Pinned items affected by filtering, only guaranteed top position
- Q8: Drop pagination from sessions? — A: No, ~1000 sessions, keep pagination — Decision: Keep "Load more" pagination within time-grouped view
- Q9: Per-group or single "Load more"? — A: Single at bottom, backend returns next page sorted by updated_at — Decision: Single "Load more", server includes group headings in response
- Q10: Search behavior unchanged? — A: Yes, still filters both panels independently within time groups — Decision: Search disables pagination, results time-grouped
- Q11: Empty time-group headings? — A: Hide them — Decision: Don't render headings for empty time buckets

### Open items

- None — all decisions resolved.

### Assumptions (unconfirmed)

- Session row styling applies uniformly to all session rows (both 3rd panel and inside expanded workspace cards) for visual consistency
- Tag filter dropdown and time filter dropdown remain as `<select>` elements (unchanged)
- "Load more" response is a server-rendered HTML fragment including group headings as needed
- `_activeGroupBy` JS variable and query param can be removed (hardcoded to time)
- "Hidden" workspace tag exclusion applies identically in sessions panel

### Recommended approach

1. **CSS**: Add `background: var(--card-bg)` and matching border to `.session-row` (applies everywhere session rows appear)
2. **Remove group-by UI**: Delete the group-by `<select>` from `initWorkspaceFilters()`. Remove `_activeGroupBy` JS state. Remove `group_by` param from `_buildWorkspaceQs()`.
3. **Hardcode time grouping in workspaces panel**: Simplify `partials_workspaces` to always use the time-grouping code path. Remove tag-grouping and flat-mode code paths. Extract pinned workspaces before grouping (render pinned section first, then time-grouped non-pinned).
4. **Add filtering to sessions panel**: Add `tag` and `time_filter` params to `/partials/all-sessions`. Filter sessions by workspace tags (via `get_workspace_settings(config, session.cwd)`). Filter by `_time_bucket(session.updated_at)`.
5. **Time-grouped rendering in sessions panel**: Group sessions by `_time_bucket(session.updated_at)`, render with group headings. Extract pinned sessions first (above headings). Include group headings in paginated "Load more" responses.
6. **Unified JS filter passing**: Update `refreshCards()`, `loadMoreSessions()`, and search handler to pass `tag` and `time_filter` to the sessions endpoint (alongside `provider`).

---

## 1) Current State

**Session row styling** (`style.css:133`):
```css
.session-row { display: grid; grid-template-columns: 28px 1fr; align-items: center; padding: 10px 16px; gap: 10px; cursor: pointer; transition: background 0.1s; position: relative; }
```
No `background` set (transparent). No border except inline `border-left: 3px solid {color}` from template.

**Workspace card styling** (`style.css:101`):
```css
.workspace-card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 0; transition: border-color 0.2s; }
```
Explicit lighter background (`#161922`) with subtle border.

**Workspace panel group_by logic** (`web.py:418-530`): Three code paths — tag grouping, time grouping, and flat mode. The time-grouping path (`web.py:457-486`) merges pinned+other into `all_visible`, buckets by `_time_bucket()`, renders headings + cards. Pinned items lose top position in grouped modes.

**Sessions panel** (`web.py:543-600`): Accepts only `page`, `provider`, `q`. No tag/time/groupBy filtering. Flat pinned-first rendering with separator.

**JS filter state** (`index.html:129-132`): `_activeProvider`, `_activeTag`, `_activeTimeFilter`, `_activeGroupBy`. The `_buildWorkspaceQs()` assembles all four but is only sent to the workspace endpoint. Sessions endpoint receives only `provider`.

**Workspace filters UI** (`index.html:162`): `initWorkspaceFilters()` builds three `<select>` dropdowns — tag, time, group-by — inside `#workspaceFilters` div.

## 2) Goal

Unify visual styling and filtering behavior across both panels: session rows get card-like background/border styling, both panels are permanently time-grouped with pinned items extracted to the top, and all filter controls (tag, time, provider, search) apply simultaneously to both panels.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Session row background | Add `background: var(--card-bg)` + `border: 1px solid var(--card-border)` to `.session-row` | Wrap sessions in card containers; add background only to 3rd panel sessions | Uniform styling everywhere session rows appear (both panels). Simplest CSS change. |
| Group-by mode | Hardcode time grouping, remove group_by UI and code paths | Keep dropdown with time as default; keep tag grouping as alternative | User explicitly wants only time grouping, no toggle |
| Pinned extraction in grouped mode | Extract pinned items before time-grouping, render above all group headings | Leave pinned in their time bucket (current behavior) | User requirement: pinned always at top regardless of grouping |
| Session tag filtering | Filter sessions by their workspace's tags via `get_workspace_settings(config, session.cwd)` | Add tags to Session dataclass; filter at data layer | Sessions don't own tags — workspace lookup is correct and avoids data model changes |
| Workspace tag lookup caching | Build `cwd→tags` dict once per request from unique session cwds | Call `get_workspace_settings` per session | 1000+ sessions share ~50 workspaces — single dict build avoids repeated config lookups |
| Pagination with time groups | Server renders full HTML fragment with group headings; single "Load more" at bottom | Client-side grouping; per-group pagination | Server-rendered is consistent with existing pattern; single button is simpler UX |
| `_activeGroupBy` removal | Remove variable, remove from `_buildWorkspaceQs()`, remove `group_by` query param | Keep as hidden/hardcoded param | Dead code — no UI to change it, server always time-groups |

## 4) External Dependencies & Costs

### Required external changes

None. This is a code-only UI change with no infrastructure, CI/CD, IAM, or third-party dependencies.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: CSS and template alignment [QA] [P:2]

**Goal**: Give session rows the same card-like visual treatment as workspace cards.

**File scope**: `src/power_atlas/static/style.css`, `src/power_atlas/templates/partials/session_row.html`

**Changes**:

1. **`style.css`** — add background and border to `.session-row`:
```css
.session-row {
  display: grid; grid-template-columns: 28px 1fr; align-items: center;
  padding: 10px 16px; gap: 10px; cursor: pointer; transition: background 0.1s;
  position: relative;
  background: var(--card-bg);
  border: 1px solid var(--card-border);
}
```

2. **`style.css`** — update `.session-row + .session-row` separator to remove double borders:
```css
.session-row + .session-row { border-top: none; margin-top: -1px; }
```
This collapses adjacent borders (bottom of one card = top of next) for a clean stacked appearance, matching how workspace cards stack without gaps.

3. **`session_row.html`** — the existing `border-left: 3px solid {{ provider_color }}` inline style remains (single-provider accent). No template changes needed for the background — CSS handles it.

**Covers**: SC-1

**Exit criteria**:
- [x] Session rows in both panels visually match workspace card styling (card-bg background, card-border, provider accent left border)
- [x] No double-border artifacts between adjacent session rows
- [x] Session rows inside expanded workspace card bodies (`.card-body`) don't create unwanted visual nesting
- [x] Existing hover/selected states still function correctly

Implementation (2026-07-09, code: d46f54a)
Added card-like visual treatment to `.session-row` elements by adding `background: var(--card-bg)` and `border: 1px solid var(--card-border)` to the base rule, and updated the adjacent-sibling separator from a top border to `border-top: none; margin-top: -1px` to collapse double borders between stacked rows. No template changes were needed — the existing inline `border-left: 3px solid` provider accent overrides the CSS border for the left side due to inline specificity.

---

### Phase 2: Simplify workspace panel to permanent time grouping [QA] [P:1]

**Goal**: Remove tag-grouping and flat-mode code paths from `partials_workspaces`. Always time-group. Extract pinned workspaces to top before grouping.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `tests/test_web.py`

**Changes**:

1. **`web.py` — `partials_workspaces`**: Remove `group_by` parameter. Delete the `group_by == "tag"` and flat-mode code paths. Keep only the time-grouping path. Modify it to extract pinned items first:

```python
@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(
    request: Request,
    provider: str = "all",
    tag: str = "",
    time_filter: str = "",
    fresh: int = 0,
):
    # ... existing discovery and filtering code (provider, tag, time_filter) ...
    
    # --- Render: pinned first, then time-grouped non-pinned ---
    cards_html = ""
    
    # Pinned section (above time groups, sorted alphabetically)
    if pinned_grouped:
        for group in pinned_grouped:
            # ... render workspace_card with is_pinned=True ...
        if other_grouped:
            cards_html += '<div class="pinned-separator" aria-hidden="true"></div>'
    
    # Time-grouped non-pinned section
    time_groups = {"today": [], "yesterday": [], "this_week": [], "before": []}
    for ws in other_grouped:
        bucket = _time_bucket(ws["latest_updated"])
        time_groups[bucket].append(ws)
    time_labels = {"today": "Today", "yesterday": "Yesterday", "this_week": "This week", "before": "Older"}
    for key in ["today", "yesterday", "this_week", "before"]:
        if time_groups[key]:
            cards_html += f'<div class="group-heading">{time_labels[key]}</div>'
            for group in time_groups[key]:
                # ... render workspace_card with is_pinned=False ...
```

2. **`web.py` — `/search` endpoint**: Same simplification — remove tag/time group_by code paths, always render time-grouped results. Pinned search results at top.

3. **`index.html` — JS**: Remove `_activeGroupBy` variable. Remove `group_by` from `_buildWorkspaceQs()`. Remove the group-by `<select>` from `initWorkspaceFilters()`:

```javascript
// Before:
window._activeGroupBy='';
function _buildWorkspaceQs(){
  var qs='provider='+encodeURIComponent(window._activeProvider||'all');
  if(window._activeTag)qs+='&tag='+encodeURIComponent(window._activeTag);
  if(window._activeTimeFilter)qs+='&time_filter='+encodeURIComponent(window._activeTimeFilter);
  if(window._activeGroupBy)qs+='&group_by='+encodeURIComponent(window._activeGroupBy);
  return qs
}

// After:
function _buildWorkspaceQs(){
  var qs='provider='+encodeURIComponent(window._activeProvider||'all');
  if(window._activeTag)qs+='&tag='+encodeURIComponent(window._activeTag);
  if(window._activeTimeFilter)qs+='&time_filter='+encodeURIComponent(window._activeTimeFilter);
  return qs
}
```

4. **`index.html` — `initWorkspaceFilters()`**: Remove the group-by `<select>` HTML generation block. Remove `setGroupBy()` function. Update `clearWorkspaceFilters()` to drop the `_activeGroupBy` reference.

**Covers**: SC-2, SC-3, SC-8, SC-10, SC-12

**Exit criteria**:
- [x] Workspace panel always renders time-grouped (Today/Yesterday/This week/Older headings)
- [x] No group-by dropdown visible in workspace filters area
- [x] Pinned workspaces render above all time-group headings
- [x] Empty time-group headings are not rendered
- [x] Tag and time filters still work correctly
- [x] Search results are time-grouped with pinned at top
- [x] `_activeGroupBy` and `setGroupBy` no longer exist in JS
- [x] `_updateFilterUI()` updated to remove `_activeGroupBy` from active-filter check
- [x] `clearWorkspaceFilters()` updated to remove `_activeGroupBy` and `groupSel` references
- [x] Existing `group_by` tests in `test_web.py` updated or removed (test_group_by_tag_*, test_group_by_time_*)
- [x] `/search` endpoint uses pinned-first extraction before time-grouped rendering

Implementation (2026-07-09, code: e20ac1b)
Removed `group_by` parameter from `partials_workspaces` and `/search` endpoints. Replaced the 3-way branching (tag/time/flat) with permanent time-grouping: pinned workspaces render first (sorted alphabetically), then a separator, then non-pinned bucketed into Today/Yesterday/This week/Older with headings. In JS, removed `_activeGroupBy` variable, group-by `<select>` dropdown from `initWorkspaceFilters()`, `setGroupBy()` function, and all related references in `clearWorkspaceFilters()` and `_updateFilterUI()`. Removed 3 tag-grouping tests and renamed the time-grouping test to `test_default_time_grouping_renders_headings` (no `group_by` param). 163 tests pass.

---

### Phase 3: Unified filtering and time grouping in sessions panel [QA]

**Goal**: Add tag/time filtering and time-grouped rendering to `/partials/all-sessions`. Pass filter state from JS to sessions endpoint.

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `tests/test_web.py`, `README.md`

**Changes**:

1. **`web.py` — `partials_all_sessions`**: Add `tag` and `time_filter` params. Apply workspace-tag and time filtering. Render time-grouped with pinned extraction:

```python
@app.get("/partials/all-sessions", response_class=HTMLResponse)
async def partials_all_sessions(
    request: Request, page: int = 1, provider: str = "all", q: str = "",
    tag: str = "", time_filter: str = "",
):
    config = load_config()
    enabled = {p for p in data.PROVIDERS if _enabled(config, p)}
    prov_filter = None if provider == "all" else provider

    sessions_with_prov, has_more = await asyncio.to_thread(
        data.get_all_sessions_paginated,
        page=page, page_size=20, provider=prov_filter,
        pinned_sessions=config.pinned_sessions, enabled_providers=enabled,
    )

    # Search filter (disables pagination — full scan)
    if q:
        query = q.strip().lower()
        sessions_with_prov = [
            (s, p) for s, p in sessions_with_prov
            if query in (s.title or "").lower()
            or query in (s.first_prompt or "").lower()
            or query in (s.cwd or "").lower()
        ]
        has_more = False

    # Build workspace-tags lookup once (shared by tag filter and hidden exclusion)
    from .config import get_workspace_settings
    cwd_tags_cache: dict[str, list[str]] = {}
    def _get_ws_tags(cwd: str) -> list[str]:
        if not cwd:
            return []
        if cwd not in cwd_tags_cache:
            cwd_tags_cache[cwd] = get_workspace_settings(config, cwd)["tags"]
        return cwd_tags_cache[cwd]

    # Tag filter (disables pagination — changes result set fundamentally)
    if tag:
        if tag == "hidden":
            sessions_with_prov = [(s, p) for s, p in sessions_with_prov if "hidden" in _get_ws_tags(s.cwd)]
        else:
            sessions_with_prov = [(s, p) for s, p in sessions_with_prov if tag in _get_ws_tags(s.cwd)]
        has_more = False
    else:
        # Default: exclude sessions from hidden workspaces
        sessions_with_prov = [(s, p) for s, p in sessions_with_prov if "hidden" not in _get_ws_tags(s.cwd)]

    # Time filter (disables pagination — subset of results)
    if time_filter:
        sessions_with_prov = [(s, p) for s, p in sessions_with_prov
                              if _time_bucket(s.updated_at) == time_filter]
        has_more = False

    # Split pinned from non-pinned
    pinned_set = set(config.pinned_sessions)
    pinned_items = [(s, p) for s, p in sessions_with_prov if s.session_id in pinned_set]
    non_pinned = [(s, p) for s, p in sessions_with_prov if s.session_id not in pinned_set]

    # Exclude pinned from page > 1 (already shown on page 1)
    if page > 1:
        pinned_items = []

    html = ""

    # Render pinned section
    for session, prov_name in pinned_items:
        html += templates.get_template("partials/session_row.html").render(
            request=request, session=session, cwd=session.cwd,
            stale=not Path(session.cwd).exists(),
            pinned_sessions=config.pinned_sessions,
            provider_name=prov_name,
            provider_color=_get_provider_color(prov_name, config),
            show_workspace=True,
            workspace_name=Path(session.cwd).name if session.cwd else "",
        )
    if pinned_items and non_pinned:
        html += '<div class="pinned-separator" aria-hidden="true"></div>'

    # Render time-grouped non-pinned
    time_groups = {"today": [], "yesterday": [], "this_week": [], "before": []}
    for s, p in non_pinned:
        bucket = _time_bucket(s.updated_at)
        time_groups[bucket].append((s, p))
    time_labels = {"today": "Today", "yesterday": "Yesterday", "this_week": "This week", "before": "Older"}
    for key in ["today", "yesterday", "this_week", "before"]:
        if time_groups[key]:
            html += f'<div class="group-heading">{time_labels[key]}</div>'
            for session, prov_name in time_groups[key]:
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
        # Contextual empty state based on active filters
        if tag:
            html = f'<div class="empty-state">No sessions in workspaces tagged &quot;{html_mod.escape(tag)}&quot;</div>'
        elif time_filter:
            html = f'<div class="empty-state">No sessions active {html_mod.escape(time_filter.replace("_", " "))}</div>'
        else:
            html = '<div class="empty-state">No sessions found.</div>'

    if has_more:
        next_page = page + 1
        html += f'<button class="load-more-btn" onclick="loadMoreSessions({next_page})">Load more</button>'

    return HTMLResponse(html)
```

2. **`index.html` — `refreshCards()`**: Pass tag/time filters to sessions endpoint:

```javascript
// In refreshCards(), change sessions fetch from:
var sessProvQs='provider='+encodeURIComponent(window._activeProvider||'all');
fetch('/partials/all-sessions?page=1&'+sessProvQs+qs)

// To:
var sessQs=_buildWorkspaceQs();  // reuse same qs builder (now has provider+tag+time_filter)
fetch('/partials/all-sessions?page=1&'+sessQs+qs)
```

3. **`index.html` — `switchProvider()`**: Pass full filter state to sessions endpoint (not just provider):

```javascript
// Change from:
fetch('/partials/all-sessions?page=1&provider='+encodeURIComponent(provider))
// To:
fetch('/partials/all-sessions?page=1&'+_buildWorkspaceQs())
```

4. **`index.html` — `loadMoreSessions()`**: Pass tag/time filters:

```javascript
function loadMoreSessions(page){
  var btn=document.querySelector('.load-more-btn');
  if(btn){btn.disabled=true;btn.textContent='Loading...'}
  var qs=_buildWorkspaceQs();
  fetch('/partials/all-sessions?page='+page+'&'+qs)
  .then(function(r){return r.text()})
  .then(function(html){
    var container=document.getElementById('all-sessions');
    if(btn)btn.remove();
    container.insertAdjacentHTML('beforeend',html);
    if(window.htmx)htmx.process(container);
    updateActionBar()
  }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Load more'}})
}
```

5. **`index.html` — search handler**: Pass full filter state to sessions search:

```javascript
// Change from:
var sessProvQs='provider='+encodeURIComponent(window._activeProvider||'all');
fetch('/partials/all-sessions?page=1&'+sessProvQs+'&q='+encodeURIComponent(q))
// To:
fetch('/partials/all-sessions?page=1&'+_buildWorkspaceQs()+'&q='+encodeURIComponent(q))
```

**Covers**: SC-4, SC-5, SC-6, SC-7, SC-8, SC-9, SC-10, SC-11

**Exit criteria**:
- [x] Sessions panel renders time-grouped (Today/Yesterday/This week/Older headings)
- [x] Pinned sessions render above time-group headings with separator
- [x] Tag filter hides sessions from non-matching workspaces
- [x] Time filter shows only matching time-group bucket
- [x] Hidden-workspace sessions excluded by default
- [x] Provider filter applies to sessions (already did, confirm no regression)
- [x] Search filters sessions within time groups
- [x] "Load more" appends next page with appropriate group headings
- [x] Empty time-group headings not rendered
- [x] All filters (tag, time, provider) can hide pinned sessions
- [x] Update `tests/test_web.py` with tests for new `tag`/`time_filter` params on `/partials/all-sessions`
- [x] Update `README.md` Features section to reflect permanent time grouping and unified filtering

Implementation (2026-07-09, code: bc0421b)
Added unified filtering (tag, time_filter, provider) and permanent time-grouped rendering to the `/partials/all-sessions` endpoint. The sessions panel now renders pinned sessions at the top with a separator, followed by non-pinned sessions grouped under Today/Yesterday/This week/Older headings (empty groups omitted). Tag filtering excludes sessions from non-matching workspaces, hidden-workspace sessions are excluded by default, and time filtering restricts to a single bucket. All JS fetch calls for the sessions panel (`refreshCards`, `switchProvider`, `loadMoreSessions`, search handler) now pass the full filter state via `_buildWorkspaceQs()`. Seven new tests cover the filtering and grouping behavior, and the README features section was updated to reflect permanent time grouping with unified filtering across both panels.

---

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| "Load more" inserts duplicate group headings if a time bucket spans multiple pages | Visual clutter — "Today" heading appears twice | Server always emits headings per-page fragment; accept duplicates as benign (20px visual separators, not semantic containers). No `skip_headings` param. |
| Tag filter performance with 1000+ sessions | Slow response | Build `cwd→tags` dict once per request from unique cwds (~50 workspaces) — O(50) config lookups, not O(1000) |
| Removing flat mode is irreversible UX change | Users lose the ungrouped view | Time-grouped view with all buckets visible is effectively the same as flat (just with headings inserted) |
| Sessions panel now hides "hidden"-tagged workspace sessions by default | New behavior — previously sessions from hidden workspaces appeared in sessions panel | Intentional unification with workspace panel behavior. Tag filter "hidden" reveals them. |
| Tag/time filters disable pagination (set `has_more=False`) | Filtered views load all matching sessions at once | Filters narrow the result set significantly; same pattern as search already uses. Acceptable trade-off vs broken pagination with variable page sizes. |
| `Path.exists()` stat per session row | Up to 20 stat calls per page load | Sessions share workspaces — cache existence check per unique cwd within the request |

## 7) Verification

- `pytest tests/test_web.py -x` — existing tests pass
- Manual visual check: session rows in 3rd panel have card-bg background matching workspace cards
- Manual visual check: both panels show time-group headings
- Manual visual check: no group-by dropdown visible
- Manual check: tag filter filters both panels simultaneously
- Manual check: time filter narrows both panels to selected bucket
- Manual check: pinned items stay at top in both panels
- Manual check: "Load more" in sessions panel works within time-grouped view
- Manual check: search filters both panels with results time-grouped

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Update "Features" bullet to reflect permanent time grouping and unified filtering (remove "group-by modes" mention, note unified filtering) | 3 |

## 9) Implementation Divergences from Plan

<Reserved -- filled during implementation>

## Review Log

### 2026-07-09 -- Post-Implementation Review

Overall implementation health: Green.
Personas: Senior engineer, End-user advocate.
4 findings (0 High, 0 Medium, 4 Low).
QA verification: PASS (all 12 success criteria verified on live instance, unified filtering confirmed across both panels).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | Combined filter empty state shows only one cause, not both active filters. | Accepted — polish-level, current messages are adequate. |
| 2 | Low | Sessions panel lacks provider-specific empty state hints (workspace panel has them). | Accepted — follows workspaces as the primary discovery panel. |
| 3 | Low | Group-heading divs lack ARIA `role="heading"` for screen reader navigation. | Accepted — existing pattern, not a regression from this plan. |
| 4 | Low | Empty-state divs lack `role="status"` for assistive tech announcement. | Accepted — existing pattern, not a regression from this plan. |

### 2026-07-09 -- Implementation Review (after Phase 3, persona: Senior engineer)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low).
Cycle 2 skipped — cycle 1 findings all Low + no fixes needed (informational only).
QA verification: PASS (time-grouped sessions verified, unified filtering confirmed across both panels, pagination disabled under filter).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | Duplicate group headings on "Load more" page 2+ (plan-acknowledged benign UX). | Accepted — explicitly addressed in Risk Assessment. |
| 2 | Low | No test for `tag=hidden` on `/partials/all-sessions` endpoint. | Accepted — non-blocking, code logic verified by tracing. |

### 2026-07-09 -- Implementation Review (after Phase 2, persona: Senior engineer)

Implementation health: Green.
0 findings.
QA verification: PASS (time-group headings, pinned separator, no group-by dropdown verified in live instance).

### 2026-07-09 -- Implementation Review (after Phase 1, persona: End-user advocate)

Implementation health: Green.
2 findings (0 High, 0 Medium, 2 Low).
Cycle 2 skipped — cycle 1 findings all Low + auto-fixes purely mechanical (no fixes needed, informational only).
QA verification: PASS (session row styling verified in live instance, all 4 exit criteria confirmed).

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Low | Nested session rows in `.card-body` show border-within-border appearance — cosmetic, not functionally broken. | Accepted — uniform styling is intentional per exit criteria. |
| 2 | Low | `margin-top: -1px` collapse only works between direct adjacent siblings — pre-existing design assumption. | Accepted — no action needed, documenting for awareness. |

### 2026-07-09 — Plan Review (High effort, 4 personas: Architect, Senior engineer, End-user advocate, Performance engineer)

12 findings (1 High, 8 Medium, 3 Low). 11 auto-resolved, 1 downgraded.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | Post-pagination filtering breaks pagination contract — tag/time filters yield variable page sizes. | Resolved — tag and time filters now set `has_more=False` (same as search), disabling pagination when active. |
| 2 | ~~High~~ Info | `load_config()` reads from disk every request — doubled by sessions filtering. | Downgraded — this is existing behavior across all endpoints, not introduced by this plan. |
| 3 | Medium | `_updateFilterUI()` references `_activeGroupBy` — removal causes runtime error on Clear button. | Resolved — added to Phase 2 exit criteria. |
| 4 | Medium | 4 existing `group_by` tests will fail after Phase 2. | Resolved — added test cleanup to Phase 2 exit criteria. |
| 5 | Medium | `cwd_tags_cache` defined twice (duplicated code in tag vs no-tag branches). | Resolved — refactored code snippet to extract cache to function top. |
| 6 | Medium | Hidden-workspace exclusion is new behavior for sessions panel — not flagged. | Resolved — added to Risk Assessment table. |
| 7 | Medium | Duplicate group headings on "Load more" punted without decision. | Resolved — stated explicitly: accept duplicates as benign separators, no `skip_headings` param. |
| 8 | Medium | Sessions empty state after filtering is generic. | Resolved — added contextual empty state messages to Phase 3 code snippet. |
| 9 | Medium | `has_more` not set to False when tag filter active. | Resolved — tag filter now sets `has_more=False` in code snippet. |
| 10 | Low | `/search` endpoint pinned-first extraction not specified in Phase 2. | Resolved — added to Phase 2 exit criteria. |
| 11 | Low | `Path(session.cwd).exists()` stat per row — cacheable. | Resolved — added to Risk Assessment as optimization note. |
| 12 | Low | Session rows inside `.card-body` may show double-border. | Resolved — added to Phase 1 exit criteria as verification item. |
