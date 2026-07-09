# Workspace Tags, Filters, and Grouping

> **Date**: 2026-07-09
> **Status**: In Progress  <!-- Status grammar: shared/skills/qplan/TEMPLATES.md § Status Grammar -->
> **Scope**: Workspace-level settings (tags, color), tag-level settings, group-by/filter on workspace panel, hidden workspaces, pinned separation
> **Estimated effort**: ~2-3 days

---

## Intent

### Problem statement & desired outcomes

PowerAtlas currently has no mechanism for users to organize workspaces beyond pinning and provider filtering. As workspace count grows, the flat recency-sorted list becomes hard to navigate. Users need workspace-level metadata (tags, colors), the ability to filter and group workspaces by those tags or by time of last activity, and the ability to hide workspaces they rarely use without deleting session data. Additionally, pinned items visually blend into non-pinned items with no spatial separation.

The desired outcome is a workspace organization system that lets users tag workspaces, assign colors, filter/group by those dimensions, hide workspaces from default view via a reserved "hidden" tag, and see a subtle visual gap between pinned and non-pinned items.

### Success criteria

- SC-1: Workspaces can be assigned tags (list of strings) and an accent color via a settings modal accessible from the workspace card
- SC-2: Tags can have a default color configured inline from the tag filter dropdown
- SC-3: Color precedence: workspace explicit color > first tag's color > provider gradient (current default)
- SC-4: Workspace panel header contains tag filter, time filter, and group-by controls (inline with "Workspaces" label)
- SC-5: Tag and time filters compose additively with the existing provider filter (AND logic)
- SC-6: Group-by supports "none" (default/current), "by tag" (workspace appears under each of its tags), and "by time" (today/yesterday/this week/before) — mutually exclusive modes
- SC-7: Workspaces tagged "hidden" are excluded from default view; selecting "hidden" in the tag filter reveals them
- SC-8: A subtle CSS gap (~12-16px) visually separates pinned workspaces/sessions from non-pinned in both panels
- SC-9: Tag/time/group-by controls affect only the workspaces panel; provider filter remains global across both panels
- SC-10: Workspace settings stored in config as `workspace_settings: dict[str, dict]` keyed by original path; tag settings stored as `tag_settings: dict[str, dict]` keyed by tag name

### Scope boundaries & non-goals

**In scope:**
- `workspace_settings` and `tag_settings` config fields with TOML persistence
- Workspace settings modal (gear icon on card → modal with tag input, color picker)
- Tag management inline in the tag filter dropdown (color swatch per tag)
- Filter UI in workspaces panel header (tag dropdown, time dropdown, group-by toggle)
- Server-side filtering and grouping in `/partials/workspaces` endpoint
- Hidden workspace exclusion logic
- CSS pinned/non-pinned gap in both panels
- JS state management for new filter dimensions

**Non-goals:**
- Changes to the sessions panel (no tag/time filter there)
- Changes to provider filter behavior or position
- Workspace auto-tagging or AI-suggested tags
- Tag hierarchy or nested tags
- Drag-and-drop tag reordering on workspace cards
- Changes to session data model or provider adapters

---

## Discovery

### Existing patterns & constraints

- `provider_settings: dict[str, dict]` (`config.py:57`) is the precedent for keyed settings dicts — workspace_settings follows the same pattern
- `_get_provider_color()` (`web.py:48-50`) implements user-color > default-color fallback — same pattern for workspace/tag color precedence
- `_group_workspaces()` (`web.py:56-99`) groups by normalized path, attaches provider colors, sorts providers alphabetically — must be extended to attach workspace tags/color
- `_normalize_path()` (`data.py:33-36`) is used in 6+ locations for case-insensitive path matching on Windows — workspace_settings lookup must normalize both sides
- `partials_workspaces()` (`web.py:327-396`) renders pinned (alphabetical) then non-pinned (recency) with no separator HTML between them
- Provider filter in JS: `window._activeProvider` + `switchProvider()` (`index.html:128,153`) fetches both panels simultaneously — new filters are workspaces-panel-only
- `workspace_card.html` renders `data-cwd`, `folder_name`, `is_pinned`, `providers`, `session_count`, `last_updated` — no slot for tags or workspace color exists
- Config loads fresh on every request (`web.py:318` etc.) — no global config object, changes take effect on next request
- Custom htmx-mini requires `htmx.process(el)` after innerHTML swaps (project memory)
- `tomli_w` handles TOML key quoting automatically for backslash-heavy Windows paths
- `latest_updated` (ISO-8601) already computed per workspace group — usable for time bucketing
- Python 3.11+ required (`pyproject.toml`) — `datetime.fromisoformat()` handles both `Z` and offset formats

### Risks & mitigations

1. **Windows path keys in TOML**: workspace_settings keyed by original path will have backslashes. Mitigation: `tomli_w` auto-quotes; test round-trip with Windows paths explicitly.
2. **Normalization mismatch on lookup**: config stores original path, lookup normalizes. Mitigation: normalize both sides on every lookup (same pattern as `pinned_folders` matching at `web.py:337`).
3. **Cache staleness after settings change**: `_cache` TTL is 30s (`data.py:10-11`). Tag/color changes won't affect workspace display for up to 30s. Mitigation: workspace_settings are injected at render time (in `_group_workspaces` or `partials_workspaces`), not in the discovery cache — changes take effect on next request without cache bust.
4. **Time zone inconsistency**: kiro-cli timestamps end in `Z` (UTC), Claude Code may use local offset. Mitigation: parse with `datetime.fromisoformat()` (handles both), convert to local date for bucketing.
5. **JS state proliferation**: adding `_activeTag`, `_activeTimeFilter`, `_activeGroupBy` alongside `_activeProvider`. Mitigation: keep new state workspaces-panel-scoped; provider filter remains the only global state.

### Resolved decisions

- Q1: How are workspace settings keyed? — A: ok — Decision: Keyed by original path string in config; lookup normalizes both sides via `_normalize_path()`
- Q2: What's the data model for workspace settings? — A: ok — Decision: `workspace_settings[path] = {tags: list[str], color: str}`; hidden is a reserved tag (no explicit `hidden: bool` field)
- Q3: Color precedence with multiple tags? — A: ok — Decision: workspace `color` > first tag in list's `color` > provider gradient; first tag wins when multiple tags have colors
- Q4a: Are group-by modes mutually exclusive? — A: ok — Decision: Yes, mutually exclusive (none / by-tag / by-time)
- Q4b: Workspace appearance under multiple tags when grouped by tag? — A: ok — Decision: Workspace appears under each of its tags (duplication)
- Q5: How do filters compose? — A: ok — Decision: Additive (AND) — provider + tag + time all apply simultaneously
- Q6: Where does the filter UI live? — A: inline of the Workspaces title — Decision: Tag, time, and group-by controls inline with "Workspaces" panel header; provider filter stays global
- Q7: How are hidden workspaces revealed? — A: ok — Decision: Filtering by "hidden" tag reveals them; no extra toggle needed
- Q8: Visual separation between pinned and non-pinned? — A: ok — Decision: CSS gap only (~12-16px extra margin), no visible divider element, applies to both panels
- Q9: How are workspace settings edited? — A: ok — Decision: Gear icon in workspace card actions → modal with tag input, color picker (same pattern as provider settings modal)
- Q10: How are tag settings edited? — A: ok — Decision: Inline in the tag filter dropdown — color swatch/edit next to each tag name; tags auto-created on first assignment
- Q11: What timestamp for time bucketing? — A: ok — Decision: `latest_updated` (max updated_at across providers), parsed with `datetime.fromisoformat()`, compared against local date boundaries

### Open items

None — all resolved during exploration.

### Assumptions (unconfirmed)

- Pinned CSS gap applies to both panels (workspaces and sessions) — trivially reversible if unwanted in sessions panel.
- Tag autocomplete in workspace modal draws from union of existing tags across all `workspace_settings` entries + `tag_settings` keys.
- Group-by "none" is the default state (preserves current behavior until user picks a grouping).

### Recommended approach

1. **Config layer**: Add `workspace_settings: dict[str, dict]` and `tag_settings: dict[str, dict]` to `Config` dataclass with load-time sanitization following `provider_settings` pattern.
2. **Data integration**: Extend `_group_workspaces()` output (or the rendering step) to attach workspace tags and resolved color from config.
3. **Workspace settings modal**: New template partial + API endpoints for workspace settings CRUD (gear icon on card).
4. **Filter/group UI**: Add tag dropdown, time dropdown, and group-by selector inline with "Workspaces" section label. JS state + server-side params.
5. **Server-side filtering**: Extend `partials_workspaces()` to filter by tag, filter by time bucket, exclude "hidden" tag by default, and group results by selected mode.
6. **Tag management UI**: Inline color editing in tag filter dropdown.
7. **Pinned gap CSS**: Add margin/gap between pinned and non-pinned sections in both panels.
8. **Tests**: Config round-trip, filter/group logic, hidden exclusion, color precedence.


---

## 1) Current State

The workspace panel renders workspace cards via `partials_workspaces()` (`web.py:327-396`). It splits discovered workspaces into pinned (sorted alphabetically) and non-pinned (sorted by recency) using `_group_workspaces()` (`web.py:56-99`), which groups flat `(cwd, count, updated_at, provider)` tuples by normalized path and attaches provider colors/display names.

**Config** (`config.py:48-58`): `Config` dataclass holds `pinned_folders: list[str]`, `pinned_sessions: list[str]`, `provider_settings: dict[str, dict]`. No workspace-level metadata exists.

**Filtering**: Only by provider via query param `?provider=<name>` and the JS `switchProvider()` function (`index.html:153`). No tag or time filtering.

**Grouping**: None — workspaces are a flat list (pinned first, then by recency).

**Visual separation**: No separator between pinned and non-pinned sections — both concatenated into a single `cards_html` string.

**Workspace identity**: `_normalize_path()` (`data.py:33-36`) is the cross-cutting normalizer used in cache keys, grouping, and pinned matching.

## 2) Goal

Add workspace-level organization: user-assigned tags with colors, time and tag-based filtering, group-by modes, hidden-workspace exclusion via reserved "hidden" tag, and a subtle CSS gap between pinned and non-pinned items.

## 3) Design Decisions

| Decision | Choice | Alternatives considered | Rationale |
|---|---|---|---|
| Workspace settings key | Original path string, normalize on lookup | Hash/slug of normalized path | Matches `pinned_folders` pattern; `tomli_w` auto-quotes special chars; human-readable in TOML |
| Hidden mechanism | Reserved "hidden" tag (no separate field) | Explicit `hidden: bool` field | Fewer concepts; filtering by "hidden" tag naturally reveals hidden workspaces |
| Color precedence | workspace `color` > first tag's `color` > provider gradient | Last-assigned tag wins; no tag color at all | First-tag gives user deterministic control via ordering |
| Group-by modes | Mutually exclusive (none / by-tag / by-time) | Combinable (nested grouping) | Combined grouping creates hard-to-scan nested UI |
| Multi-tag grouping | Workspace appears under each of its tags | First-tag only; unique combinations | Natural expectation when browsing by tag; duplication acceptable for card references |
| Filter composition | Additive AND (provider + tag + time all apply) | Mode switching (only one dimension active) | Matches standard faceted-filter UX expectations |
| Filter UI location | Inline with "Workspaces" panel header | Global row below search; dropdown popover | Scopes controls to the panel they govern; no global vertical space consumed |
| Pinned separation | CSS gap only (~12-16px) | Thin divider line; section sub-labels | Minimal visual clutter, user said "just a little gap" |
| Workspace settings UX | Gear icon on card → modal | Right-click menu; inline on card | Follows established provider-settings modal pattern |
| Tag management UX | Inline color editing in tag filter dropdown | Dedicated global settings panel | Tags already listed in filter; keeps management close to usage |
| Time bucketing timestamp | `latest_updated` parsed with `datetime.fromisoformat()` | String prefix heuristic | Proper parsing handles mixed UTC/offset formats correctly on Python 3.11+ |
| Default group-by | "none" (current flat behavior) | Auto-group by time | Preserves existing UX until user opts in |
| Pinned gap scope | Both panels (workspaces + sessions) | Workspaces only | Consistent visual language across panels |
| Tag autocomplete source | Union of all tags from `workspace_settings` + `tag_settings` keys | Only workspace_settings | Ensures manually-created tags (with colors but no workspaces yet) appear |

## 4) External Dependencies & Costs

### Required external changes

None — this is a code-only change with local config persistence.

### Cost impact

None.

## 5) Implementation Phases

### Phase 1: Config layer — workspace_settings and tag_settings [QA] [P:4]

**Goal**: Add `workspace_settings` and `tag_settings` fields to `Config`, with load-time sanitization, save round-trip, and validation.

**Covers**: SC-1, SC-2, SC-10

**File scope**: `src/power_atlas/config.py`, `tests/test_config.py`

**Changes**:

```python
# config.py — add to Config dataclass (after provider_settings)
workspace_settings: dict[str, dict] = field(default_factory=dict)
tag_settings: dict[str, dict] = field(default_factory=dict)
```

Load-time sanitization (same pattern as `provider_settings` at line 147):
```python
# In load_config(), after existing provider_settings sanitization:
config.workspace_settings = {k: v for k, v in config.workspace_settings.items() if isinstance(v, dict)}
config.tag_settings = {k: v for k, v in config.tag_settings.items() if isinstance(v, dict)}

# Per-workspace value sanitization:
for path, ws in list(config.workspace_settings.items()):
    ws.setdefault("tags", [])
    ws.setdefault("color", "")
    # Ensure tags is a list of strings
    ws["tags"] = [t for t in ws["tags"] if isinstance(t, str)]
    # Sanitize color
    if not isinstance(ws.get("color"), str):
        ws["color"] = ""

# Per-tag value sanitization:
for tag_name, ts in list(config.tag_settings.items()):
    ts.setdefault("color", "")
    if not isinstance(ts.get("color"), str):
        ts["color"] = ""
```

Helper for normalized workspace-settings lookup:
```python
def get_workspace_settings(config: Config, cwd: str) -> dict:
    """Return workspace settings for a path, normalizing for lookup.

    Uses a pre-built normalized lookup dict (cached on the Config instance
    at load time) for O(1) access instead of linear scan.
    """
    norm_map = getattr(config, "_ws_norm_map", None)
    if norm_map is None:
        # Build on first access (lazy); also built at load time
        from .data import _normalize_path
        norm_map = {_normalize_path(k): v for k, v in config.workspace_settings.items()}
        config._ws_norm_map = norm_map
    from .data import _normalize_path
    return norm_map.get(_normalize_path(cwd), {"tags": [], "color": ""})
```

At the end of `load_config()`, build the normalized lookup map:
```python
# Build normalized lookup dict for O(1) workspace settings access
from .data import _normalize_path
config._ws_norm_map = {_normalize_path(k): v for k, v in config.workspace_settings.items()}
```

**Tests** (`tests/test_config.py`):
- Round-trip: write workspace_settings with Windows paths, reload, verify
- Sanitization: non-dict values dropped, missing fields get defaults
- `get_workspace_settings()` normalizes lookup correctly (case-insensitive on Windows)
- tag_settings round-trip and sanitization

**Exit criteria**:
- [x] `workspace_settings` and `tag_settings` persist through save/load cycle
- [x] Windows-path keys (with backslashes) survive TOML round-trip
- [x] `get_workspace_settings()` matches case-insensitively on Windows
- [x] Invalid nested types (non-list tags, non-string color) sanitized to defaults
- [x] All existing config tests still pass

#### Implementation (2026-07-09, code: 2bedbaa)

Added `workspace_settings` and `tag_settings` dict fields to the `Config` dataclass with full load-time sanitization (non-dict values dropped, tags filtered to strings only, color coerced to empty string if non-string). Keys are sanitized for control characters and capped at 1024 chars (workspace) / 64 chars (tags). Added `get_workspace_settings()` helper that provides O(1) normalized path lookup via a `_ws_norm_map` dict built at load time and lazy-rebuilt on first access for Config instances not from `load_config()`. Returns defensive copies to prevent caller mutation of cached state (per project memory pattern). Ten new tests cover round-trip, sanitization, and normalized lookup.

### Phase 2: Workspace settings modal and API [QA]

**Goal**: Add a gear icon to workspace cards that opens a settings modal for editing tags and color.

**Covers**: SC-1, SC-3

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/templates/partials/workspace_settings_modal.html` (new), `src/power_atlas/templates/index.html`, `tests/test_web.py`

**Changes**:

New API endpoints in `web.py`:
```python
@app.get("/api/workspace-settings")
async def get_workspace_settings_api(cwd: str = ""):
    """Return workspace settings for a given cwd."""
    config = load_config()
    from .config import get_workspace_settings
    settings = get_workspace_settings(config, cwd)
    # Also return all known tags for autocomplete
    all_tags = set()
    for ws in config.workspace_settings.values():
        all_tags.update(ws.get("tags", []))
    all_tags.update(config.tag_settings.keys())
    return {"settings": settings, "all_tags": sorted(all_tags)}


@app.post("/api/workspace-settings/save", response_class=HTMLResponse)
async def save_workspace_settings_api(request: Request):
    """Save workspace settings (tags, color) for a workspace path."""
    body = await request.json()
    cwd = body.get("cwd", "")
    tags = body.get("tags", [])
    color = body.get("color", "")
    # Validation: path
    if not cwd or len(cwd) > 512 or any(ord(ch) < 0x20 for ch in cwd):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid workspace path", "level": "error"})
    # Validation: tags (max 10, each 1-64 chars, no control chars)
    if not isinstance(tags, list) or len(tags) > 10:
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Max 10 tags per workspace", "level": "error"})
    for t in tags:
        if not isinstance(t, str) or not t or len(t) > 64 or any(ord(ch) < 0x20 for ch in t):
            return templates.TemplateResponse(request, "partials/toast.html", {
                "message": "Invalid tag: 1-64 chars, no control chars", "level": "error"})
    # Validation: color (hex format or empty)
    if color and (len(color) > 20 or any(ord(ch) < 0x20 for ch in color)):
        return templates.TemplateResponse(request, "partials/toast.html", {
            "message": "Invalid color value", "level": "error"})
    config = load_config()
    # Normalize key at save time to prevent duplicate entries for same path
    from .data import _normalize_path
    norm_cwd = _normalize_path(cwd)
    for existing_key in list(config.workspace_settings.keys()):
        if _normalize_path(existing_key) == norm_cwd and existing_key != cwd:
            del config.workspace_settings[existing_key]
    config.workspace_settings[cwd] = {"tags": tags, "color": color}
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": "Workspace settings saved", "level": "success",
    })
```

New template `workspace_settings_modal.html`:
- `<dialog>` with tag input (text field + chip display), color picker (same swatch grid as launcher modal), save/cancel buttons
- Tags rendered as removable chips; typing suggests from `all_tags` autocomplete (full list shown on focus)
- **Keyboard contract**: Enter or comma adds tag; Backspace on empty input removes last chip; arrow keys navigate autocomplete; Escape closes autocomplete/modal

`workspace_card.html` — add gear button to `.card-actions`:
```html
<button class="card-settings-btn" onclick="event.stopPropagation();openWorkspaceSettings(this)" aria-label="Workspace settings" title="Settings">⚙️</button>
```

JS in `index.html`:
- `openWorkspaceSettings(btn)` — fetches settings, populates modal, shows it
- `saveWorkspaceSettings()` — POSTs tags + color, closes modal, refreshes cards
- Tag chip input logic (add on Enter/comma, remove on click/backspace)

**Exit criteria**:
- [x] Gear icon visible on workspace card hover (alongside existing action buttons)
- [x] Modal opens with current tags and color for the workspace
- [x] Tags can be added (typed + Enter) and removed (click X on chip)
- [x] Tag autocomplete suggests existing tags from all workspaces
- [x] Color picker works (same swatch grid as provider/launcher modals)
- [x] Save persists to config; toast confirms
- [x] Test: `POST /api/workspace-settings/save` round-trips through config

#### Implementation (2026-07-09, code: 2e71915)

Implemented the workspace settings modal and API endpoints. Added `GET /api/workspace-settings` (returns tags, color, and all known tags for autocomplete) and `POST /api/workspace-settings/save` (validates and persists tags and color with path normalization to prevent duplicates). Created a new `workspace_settings_modal.html` template using the existing `<dialog>` pattern with tag chip input (add on Enter/comma, remove on click/backspace, arrow-key autocomplete navigation, full tag list shown on focus) and the same 12-swatch color picker used elsewhere. Added a gear button to workspace card actions. Eight tests cover save round-trip, validation, GET defaults, GET with data, all_tags aggregation, and path deduplication.

### Phase 3: Color precedence integration [QA]

**Goal**: Apply resolved workspace/tag color as the accent bar on workspace cards, respecting the precedence chain.

**Covers**: SC-3

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/partials/workspace_card.html`, `tests/test_web.py`

**Changes**:

New helper in `web.py`:
```python
def _resolve_workspace_color(cwd: str, config) -> str:
    """Resolve accent color: workspace explicit > first tag color > empty (use provider gradient)."""
    from .config import get_workspace_settings
    ws = get_workspace_settings(config, cwd)
    if ws["color"]:
        return ws["color"]
    for tag in ws["tags"]:
        tag_color = config.tag_settings.get(tag, {}).get("color", "")
        if tag_color:
            return tag_color
    return ""  # empty = fall through to provider gradient
```

Modify `partials_workspaces()` to pass `workspace_color` to template:
```python
workspace_color = _resolve_workspace_color(cwd, config)
cards_html += templates.get_template("partials/workspace_card.html").render(
    ..., workspace_color=workspace_color,
)
```

Modify `workspace_card.html` — when `workspace_color` is set, override the provider gradient bar:
```html
<span class="provider-gradient..." style="background: {% if workspace_color %}{{ workspace_color }}{% elif providers|length == 1 %}...{% else %}...{% endif %}"></span>
```

**Exit criteria**:
- [x] Workspace with explicit color shows that color as accent bar
- [x] Workspace with no color but tagged with colored tag shows tag's color
- [x] Workspace with no color and no colored tags shows provider gradient (existing behavior)
- [x] Test: `_resolve_workspace_color()` precedence chain verified

#### Implementation (2026-07-09, code: 6bf0a50)

Added `_resolve_workspace_color()` helper implementing the 3-level color precedence chain: workspace explicit color > first tag with a defined color in tag_settings > empty string (preserves existing provider gradient). Modified all three template render loops (pinned workspaces, non-pinned workspaces, and search results) to pass `workspace_color`. Updated workspace_card.html to check `workspace_color` first. Nine tests (6 unit + 3 integration) verify precedence at each level. Divergence: also updated search endpoint render loop for completeness (template requires the variable).

### Phase 4: Pinned gap CSS [QA] [P:1]

**Goal**: Add subtle visual separation between pinned and non-pinned items in both panels.

**Covers**: SC-8

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/partials/workspace_card.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Changes**:

In `partials_workspaces()`, after rendering pinned cards and before non-pinned:
```python
if pinned_grouped and other_grouped:
    cards_html += '<div class="pinned-separator" aria-hidden="true"></div>'
```

Same pattern in `partials_all_sessions()` — after pinned items, before paginated:
```python
if pinned_items and page_items:
    # Insert separator between pinned and non-pinned
    ...
```

CSS in `style.css`:
```css
.pinned-separator {
    height: 12px;
    flex-shrink: 0;
}
```

**Exit criteria**:
- [x] Visual gap visible between pinned and non-pinned workspace cards
- [x] Visual gap visible between pinned and non-pinned sessions
- [x] No gap when there are no pinned items or no non-pinned items
- [x] Test: separator div present in HTML when both pinned and non-pinned exist

#### Implementation (2026-07-09, code: a11c576)

Added subtle visual separation between pinned and non-pinned items in both the workspaces panel and the all-sessions panel. In `partials_workspaces()`, a `<div class="pinned-separator" aria-hidden="true">` is inserted between the pinned workspace cards and non-pinned cards when both groups have content. In `partials_all_sessions()`, the same separator is inserted on page 1 at the boundary between pinned sessions and non-pinned sessions, detected by tracking when the first non-pinned session ID is encountered. The CSS rule provides a 12px vertical gap with `flex-shrink: 0`. Five tests verify: separator present when both groups exist, absent when only pinned, absent when no pinned, and two session-panel tests.

### Phase 5: Tag and time filter UI + server-side filtering [QA]

**Goal**: Add tag dropdown, time dropdown, and group-by toggle to the workspaces panel header; implement server-side filtering logic.

**Covers**: SC-4, SC-5, SC-6, SC-7, SC-9

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`

**Changes**:

Extend `partials_workspaces()` signature:
```python
@app.get("/partials/workspaces", response_class=HTMLResponse)
async def partials_workspaces(
    request: Request,
    provider: str = "all",
    tag: str = "",           # filter by tag (empty = all non-hidden)
    time_filter: str = "",   # today|yesterday|this_week|before (empty = no filter)
    group_by: str = "",      # tag|time (empty = none/flat)
    fresh: int = 0,
):
```

Filter logic (after existing provider filter):
```python
from datetime import datetime, date, timedelta

def _time_bucket(iso_str: str) -> str:
    """Classify an ISO-8601 timestamp into today/yesterday/this_week/before."""
    if not iso_str:
        return "before"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        d = dt.astimezone().date()  # convert to local date
    except (ValueError, OSError):
        return "before"
    today = date.today()
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    if d >= today - timedelta(days=today.weekday()):  # Monday of this week
        return "this_week"
    return "before"

# Hidden exclusion: when no tag filter active, exclude workspaces tagged "hidden"
if not tag:
    grouped = [g for g in grouped if "hidden" not in _get_workspace_tags(g["cwd"], config)]
elif tag == "hidden":
    grouped = [g for g in grouped if "hidden" in _get_workspace_tags(g["cwd"], config)]
else:
    grouped = [g for g in grouped if tag in _get_workspace_tags(g["cwd"], config)]

# Time filter
if time_filter:
    grouped = [g for g in grouped if _time_bucket(g["latest_updated"]) == time_filter]
```

Group-by rendering (replaces flat card list with sectioned output):
```python
if group_by == "tag":
    # Collect all tags, render section per tag with heading
    ...
elif group_by == "time":
    # Bucket workspaces into today/yesterday/this_week/before, render sections
    ...
else:
    # Current flat rendering
    ...
```

New API for available tags:
```python
@app.get("/api/tags")
async def api_tags():
    """Return all known tags with colors and workspace counts."""
    config = load_config()
    tag_counts: dict[str, int] = {}
    for ws in config.workspace_settings.values():
        for t in ws.get("tags", []):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    # Include tags from tag_settings even if 0 workspaces
    for t in config.tag_settings:
        tag_counts.setdefault(t, 0)
    return [
        {"name": t, "color": config.tag_settings.get(t, {}).get("color", ""), "count": c}
        for t, c in sorted(tag_counts.items())
    ]
```

JS changes in `index.html`:
- New state: `window._activeTag = ''`, `window._activeTimeFilter = ''`, `window._activeGroupBy = ''`
- `initWorkspaceFilters()` — fetches `/api/tags`, renders dropdown + time dropdown + group-by toggle inline with "Workspaces" label
- `switchTag(tag)`, `switchTimeFilter(filter)`, `switchGroupBy(mode)` — update state and re-fetch `/partials/workspaces` with all params
- **All JS callsites that fetch `/partials/workspaces` must include new params**: `refreshCards()`, `switchProvider()`, `loadExpandedCards()`, search handler, `startPolling()` (via `refreshCards`)
- Helper: `_buildWorkspaceQs()` returns `&tag=...&time_filter=...&group_by=...` from current state — used by all callsites
- **Search composition**: search input handler includes active tag/time/group_by params alongside `q` when fetching workspaces
- **Clear filters button**: visible when any non-default filter is active; resets tag/time/group_by to defaults and re-fetches
- **Hidden indicator**: when "hidden" tag has count > 0, show `(N hidden)` badge near the filter area; clicking it sets tag filter to "hidden"

Also extend `/search` endpoint to apply hidden exclusion and tag/time filters:
```python
@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", provider: str = "all",
                 tag: str = "", time_filter: str = "", group_by: str = ""):
    # Apply same hidden/tag/time filtering as partials_workspaces after path-match
    ...
```

**Group-by selection state**: when grouped by tag, workspace card duplication across tag sections is resolved for multi-select by deduplicating `getSelectedWorkspaceCwds()` (already deduplicates by normalized path).

**Empty states** for filtered views:
```python
if not cards_html:
    if tag:
        cards_html = f'<div class="empty-state">No workspaces with tag "{tag}"</div>'
    elif time_filter:
        cards_html = f'<div class="empty-state">No workspaces active {time_filter.replace("_", " ")}</div>'
    ...
```

**First-time hint** when no tags exist: tag dropdown shows "No tags yet — use ⚙️ on a workspace card to add one."

UI layout in panel header:
```html
<div class="section-label">
  <span>Workspaces</span>
  <div class="workspace-filters" id="workspaceFilters"></div>
</div>
```

**Exit criteria**:
- [x] Tag filter dropdown shows all known tags with colors and counts
- [x] Selecting a tag filters workspace panel to only matching workspaces
- [x] "hidden" tag excluded by default; selecting "hidden" tag reveals hidden workspaces
- [x] Time filter (today/yesterday/this week/before) filters by `latest_updated`
- [x] Group-by tag shows workspaces under section headings per tag (with duplication)
- [x] Group-by time shows workspaces under "Today"/"Yesterday"/"This week"/"Older" headings
- [x] Filters compose with provider filter (AND logic)
- [x] Sessions panel unaffected by tag/time/group-by controls
- [x] Tests: hidden exclusion, tag filter, time bucketing, group-by sections in HTML

#### Implementation (2026-07-09, code: f2ac708)

Implemented tag/time filters and group-by functionality for the workspaces panel. Server-side: added `_time_bucket()` helper; extended `partials_workspaces()` with `tag`, `time_filter`, and `group_by` query params applying uniform filtering to both pinned and non-pinned workspaces; added group-by rendering with section headings; added `/api/tags` endpoint; extended `/search` with same filter params. Client-side: added `_buildWorkspaceQs()` composing state, updated all fetch callsites, added `initWorkspaceFilters()` rendering filter UI inline with "Workspaces" label. 23 new tests. Review fix (f108c47): added `html.escape()` for tag names in server-side f-strings, `_escHtml()` for client-side option rendering, and `initWorkspaceFilters()` refresh on visibility change and after save.

### Phase 6: Tag management — inline color editing [QA]

**Goal**: Allow users to set tag colors directly from the tag filter dropdown.

**Covers**: SC-2

**File scope**: `src/power_atlas/web.py`, `src/power_atlas/templates/index.html`, `src/power_atlas/static/style.css`, `tests/test_web.py`, `README.md`

**Changes**:

New API endpoint:
```python
@app.post("/api/tag/save", response_class=HTMLResponse)
async def save_tag_settings(request: Request):
    body = await request.json()
    tag_name = body.get("tag", "")
    color = body.get("color", "")
    # Validation: tag_name max 64 chars, no control chars; color max 20 chars
    config = load_config()
    config.tag_settings[tag_name] = {"color": color}
    save_config(config)
    return templates.TemplateResponse(request, "partials/toast.html", {
        "message": f"Tag color saved", "level": "success",
    })
```

JS in tag filter dropdown:
- Each tag entry shows a small color circle (current color or gray)
- Clicking the color circle opens a mini color picker (same swatch palette)
- On color selection: POST to `/api/tag/save`, update dropdown display, refresh workspace cards (colors may have changed)

**Exit criteria**:
- [x] Tag dropdown shows color indicator per tag
- [x] Clicking color indicator opens swatch picker
- [x] Saving color persists to `tag_settings` in config
- [x] Workspace cards reflect new tag color immediately after save (where applicable per precedence)
- [x] Test: `POST /api/tag/save` persists color; tag with color appears in `/api/tags` response
- [x] Update README.md with workspace tags and filtering feature description

#### Implementation (2026-07-09, code: 2d653d2)

Added `POST /api/tag/save` endpoint with validation (tag name 1-64 chars, no control chars; color max 20 chars). Frontend: palette button next to tag filter opens a popover listing all tags with mini color swatches (same 12-color palette). Selecting a swatch POSTs, then refreshes filters and cards. CSS for popover, tag rows, and mini swatches. Eight tests. README updated with workspace tags feature and configuration examples.

## 6) Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Windows-path TOML keys break on certain characters | Config corruption / settings lost | Explicit test with backslash-heavy paths; `tomli_w` auto-quotes |
| Normalized lookup mismatch | Wrong workspace settings applied | Normalize both sides using established `_normalize_path()` |
| Time zone parsing failures | Workspaces bucketed incorrectly | `fromisoformat()` with Z→+00:00 replacement; fallback to "before" on parse error |
| Large tag count makes dropdown unwieldy | Poor UX | Scrollable dropdown with search; defer to future if needed |
| Config file grows large with many workspace_settings | Slow load/save | Acceptable for desktop app (hundreds of entries still fast); monitor |
| JS state desync across filter dimensions | Stale panel content | Single `refreshWorkspaces()` function that passes all active filters to one fetch |
| Hidden workspaces lost (user forgets they tagged "hidden") | User confusion | Tag filter shows "hidden" with count badge — discoverable |

## 7) Verification

- `pytest tests/test_config.py` — config round-trip, sanitization, workspace_settings lookup
- `pytest tests/test_web.py` — new endpoints, filter logic, hidden exclusion, group-by HTML structure
- Manual: open UI, assign tags/colors via modal, verify filter/group/hide behavior
- Manual: confirm pinned gap visible in both panels
- Manual: confirm provider filter + tag filter + time filter compose correctly

## 8) Documentation Updates

| Document | Update needed | Phase |
|---|---|---|
| `README.md` | Add workspace tags, filtering, and grouping to Features list; add `workspace_settings` and `tag_settings` to Configuration section | 6 |

## 9) Implementation Divergences from Plan

<Reserved — filled during implementation>

## Review Log

### 2026-07-09 — Plan Review (high effort, 4 personas: Architect, Senior engineer, End-user advocate, Reliability engineer)

16 merged findings (6 High, 7 Medium, 3 Low). 10 auto-resolved.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `/search` endpoint bypasses new tag/time/hidden filters entirely. | Resolved — added `/search` extension to Phase 5 with filter params. |
| 2 | High | `refreshCards()`, `switchProvider()`, polling don't pass new filter params. | Resolved — Phase 5 now lists all JS callsites and adds `_buildWorkspaceQs()` helper. |
| 3 | High | `get_workspace_settings()` O(n) linear scan per card, O(n²) total. | Resolved — replaced with normalized lookup dict built at load time (Phase 1). |
| 4 | High | No input validation on workspace settings save endpoint. | Resolved — added concrete validation (path, tags max 10×64 chars, color format). |
| 5 | High | No empty state for tag/time filter with no matches. | Resolved — added explicit empty-state HTML to Phase 5 exit criteria. |
| 6 | High | Tag chip input has no keyboard accessibility contract. | Resolved — added keyboard contract (Enter/comma/Backspace/arrows/Escape) to Phase 2. |
| 7 | Medium | Sessions panel separator needs `partials_all_sessions` restructuring. | Resolved — Phase 4 now specifies the pinned/non-pinned boundary detection in sessions. |
| 8 | Medium | `_get_workspace_tags()` helper referenced but undefined. | Resolved — Phase 5 uses `get_workspace_settings(config, cwd)["tags"]` directly. |
| 9 | Medium | Normalize key at save time to prevent duplicate entries. | Resolved — save endpoint now deduplicates by normalized path before writing. |
| 10 | Medium | No tag delete endpoint or size caps. | Noted — tag deletion deferred (tags are cheap; removal via workspace settings). Cap of 10 tags/workspace added. |
| 11 | Medium | Group-by duplicates need selection dedup clarification. | Resolved — noted that `getSelectedWorkspaceCwds()` already deduplicates by path. |
| 12 | Medium | No "clear all filters" affordance. | Resolved — added clear-filters button to Phase 5 JS changes. |
| 13 | Medium | First-time empty state hint for zero tags. | Resolved — added placeholder text for empty tag dropdown. |
| 14 | Medium | "(N hidden)" indicator missing from default view. | Resolved — added hidden count badge to Phase 5 JS/UI specification. |
| 15 | Low | Stale line references throughout Current State section. | Noted — line references are advisory; implementation reads actual code. |
| 16 | Low | `_time_bucket()` locale-dependent week start (Monday vs Sunday). | Noted — acceptable for desktop app; documented as assumption. |

### 2026-07-09 -- Implementation Review (after Phase 1, persona: Senior engineer, Reliability engineer, Maintainability reviewer, Security auditor)

Implementation health: Green.
6 findings (1 High, 3 Medium, 2 Low). All auto-fixed in cycle 1.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | High | `get_workspace_settings()` returns mutable reference to cached dict. | Fixed — returns shallow copy with list copy for tags. |
| 2 | Medium | `_ws_norm_map` not invalidated on workspace_settings mutation. | Fixed — documented; load_config() per-request pattern handles this. |
| 3 | Medium | Duplicate `from .data import _normalize_path` import in function body. | Fixed — consolidated to single import at function top. |
| 4 | Medium | No key length/control-char validation for workspace_settings paths. | Fixed — added _strip_control_chars() and 1024-char cap on keys. |
| 5 | Low | Tag name keys lack length cap and control-char sanitization. | Fixed — added 64-char cap and _strip_control_chars() to tag_settings keys. |
| 6 | Low | Redundant import statement (same as #3). | Fixed — same consolidation. |

Cycle 2 skipped — all auto-fixes purely mechanical (defensive copy, import consolidation, validation addition).

### 2026-07-09 -- Implementation Review (after Phase 4, persona: Senior engineer, End-user advocate, Maintainability reviewer, Reliability engineer)

Implementation health: Green.
4 findings (0 High, 1 Medium, 3 Low). All auto-fixed in cycle 1.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | No test coverage for session-panel separator boundary logic. | Fixed — added 2 tests for /partials/all-sessions separator. |
| 2 | Low | Redundant pinned_set construction (built twice on page > 1). | Fixed — moved to single construction before the if-block. |
| 3 | Low | CSS gap is spatial-only with no visible indicator beyond whitespace. | No action — plan explicitly specifies "CSS gap only, no visible divider element." |
| 4 | Low | aria-hidden="true" on decorative spacer is good accessibility practice. | No action needed (positive note). |

Cycle 2 skipped — all auto-fixes purely mechanical (added tests, moved variable declaration).

### 2026-07-09 -- Implementation Review (after Phase 2, persona: Senior engineer, Security auditor, End-user advocate, Maintainability reviewer)

Implementation health: Green.
7 findings (0 High, 2 Medium, 5 Low). No auto-fix needed.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | Color validation accepts any string ≤20 chars — no hex format check. | Accepted — UI offers only preset swatches; API misuse stores inert garbage. |
| 2 | Medium | Autocomplete renders all matching tags without item cap at extreme scale. | Accepted — CSS max-height mitigates; max ~10 tags/workspace limits practical count. |
| 3 | Low | Modal title extraction uses fragile childNodes indexing. | Accepted — fallback to "Workspace" exists; data-attr improvement deferred. |
| 4 | Low | Comma in tag text produces "ab" not "a"+"b" — intentional but undiscoverable. | Accepted — comma triggers submit; one-at-a-time entry is the designed flow. |
| 5 | Low | No aria-live region on tag chips container for screen reader feedback. | Accepted — desktop power-user app; improvement deferred to follow-up. |
| 6 | Low | Gear button uses emoji — minor rendering variation across font stacks. | Accepted — renders reliably on Windows 10+ with Segoe UI Emoji. |
| 7 | Low | No test for keyboard contract (JS-only, not API-testable in Python). | Accepted — browser-level testing scope; API layer fully tested. |

### 2026-07-09 -- Implementation Review (after Phase 3, persona: Senior engineer, End-user advocate, Reliability engineer, Performance engineer)

Implementation health: Green.
0 findings.

### 2026-07-09 -- Implementation Review (after Phase 5, persona: Senior engineer, Reliability engineer, End-user advocate, Performance engineer)

Implementation health: Green.
7 findings (0 High, 4 Medium, 3 Low). 4 auto-fixed in cycle 1.

| # | Severity | Finding (one line) | Resolution (one line) |
|---|---|---|---|
| 1 | Medium | XSS via unescaped tag names in server-side group-heading f-strings. | Fixed — added html.escape() in all 3 locations. |
| 2 | Medium | XSS via unescaped tag names in client-side JS option construction. | Fixed — added _escHtml() helper and applied to tag names/colors. |
| 3 | Medium | Tag filter dropdown stale after adding/removing tags until page reload. | Fixed — initWorkspaceFilters() called on visibilitychange and after save. |
| 4 | Medium | Empty state for tag filter uses unescaped user input. | Fixed — applied html.escape() to tag value in empty state message. |
| 5 | Low | No test for positive UTC offset timestamps in _time_bucket. | Accepted — .astimezone().date() handles correctly; lock-in test deferred. |
| 6 | Low | "this_week" uses ISO Monday-start semantics, surprising for US locale. | Accepted — documented as assumption per plan. |
| 7 | Low | Group-by-tag mode loses pinned-first ordering within sections. | Accepted — grouping inherently flattens pin distinction. |

### 2026-07-09 -- Implementation Review (after Phase 6, persona: Senior engineer, Security auditor, End-user advocate, Maintainability reviewer)

Implementation health: Green.
0 findings.
